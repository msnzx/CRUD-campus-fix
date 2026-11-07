-- SMTP is an external side effect: leases prevent ordinary concurrent sends,
-- not the SMTP-accepted / database-uncommitted crash window. Preserve ambiguity
-- and retry rather than silently treating an unknown outcome as delivered.
alter table public.notifications
  add column email_message_id uuid not null default gen_random_uuid(),
  add column email_claim_token uuid,
  add column email_claimed_until timestamptz,
  add column email_started_at timestamptz,
  add column email_completed_token uuid,
  add column email_next_attempt_at timestamptz not null default now(),
  add column email_total_attempts integer not null default 0 check (email_total_attempts >= 0),
  add column email_ambiguous_attempts integer not null default 0 check (email_ambiguous_attempts >= 0),
  add column email_manual_retries integer not null default 0 check (email_manual_retries >= 0),
  add constraint notifications_email_claim_consistent
    check ((email_claim_token is null) = (email_claimed_until is null));

update public.notifications set email_total_attempts = attempts,
  email_next_attempt_at = case when delivery_status = 'FAILED' and last_attempt_at is not null
    then last_attempt_at + interval '1 minute' * power(2, least(attempts, 8)) else now() end;

-- Existing policies are personal; explicitly enable RLS in migration history.
alter table public.notifications enable row level security;
-- Existing authenticated UPDATE is column-scoped to read_at; never broaden it.
create index ix_notifications_email_due on public.notifications(email_next_attempt_at, notification_id)
  where delivery_status in ('PENDING','FAILED') and attempts < 5;
create index ix_notifications_email_expired on public.notifications(email_claimed_until)
  where email_claim_token is not null;

create function public.email_claim_notification(p_lease_seconds integer default 120)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare n public.notifications; v_token uuid := gen_random_uuid();
begin
  if p_lease_seconds is null or p_lease_seconds < 1 or p_lease_seconds > 600 then
    raise exception 'Email lease must be between 1 and 600 seconds';
  end if;
  -- Reap even exhausted attempts so a crash on attempt five remains detectable.
  with expired as (
    select notification_id from public.notifications
    where email_claimed_until <= clock_timestamp()
    order by email_claimed_until, notification_id limit 50 for update skip locked
  )
  update public.notifications expired_row set
    delivery_status = 'FAILED',
    error_message = case when expired_row.email_started_at is null then 'Worker lease expired before SMTP start'
      else 'SMTP outcome unknown after worker lease expired; retry may duplicate email' end
      || case when expired_row.attempts >= 5 then '; automatic attempts exhausted; operator retry required' else '' end,
    email_ambiguous_attempts = expired_row.email_ambiguous_attempts + case when expired_row.email_started_at is null then 0 else 1 end,
    email_next_attempt_at = clock_timestamp() + interval '1 minute' * power(2, least(expired_row.attempts, 8)),
    email_claim_token = null, email_claimed_until = null, email_started_at = null
  from expired e where expired_row.notification_id = e.notification_id;

  select * into n from public.notifications
    where delivery_status in ('PENDING','FAILED') and attempts < 5
      and email_claim_token is null and email_next_attempt_at <= clock_timestamp()
    order by email_next_attempt_at, notification_id limit 1 for update skip locked;
  if not found then return jsonb_build_object('claimed', false); end if;
  update public.notifications set email_claim_token = v_token,
    email_claimed_until = clock_timestamp() + make_interval(secs => p_lease_seconds),
    email_started_at = null, email_completed_token = null, attempts = attempts + 1,
    email_total_attempts = email_total_attempts + 1, last_attempt_at = clock_timestamp()
    where notification_id = n.notification_id;
  return jsonb_build_object('claimed', true, 'notification_id', n.notification_id, 'claim_token', v_token);
end;
$$;

create function public.email_start_delivery(p_notification_id bigint, p_claim_token uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare n public.notifications; u public.users;
begin
  select * into n from public.notifications where notification_id = p_notification_id for update;
  if not found or p_claim_token is null or n.email_claim_token is distinct from p_claim_token
     or n.email_claimed_until is null or n.email_claimed_until <= clock_timestamp()
     or n.email_started_at is not null or n.delivery_status = 'SENT' then
    return jsonb_build_object('started', false);
  end if;
  select * into u from public.users where user_id = n.user_id;
  update public.notifications set email_started_at = clock_timestamp(),
    email_claimed_until = clock_timestamp() + interval '120 seconds'
    where notification_id = p_notification_id;
  return jsonb_build_object('started', true, 'ticket_id', n.ticket_id, 'message', n.message,
    'email', u.email, 'active', coalesce(u.active, false), 'attempts', n.attempts,
    'email_message_id', n.email_message_id);
end;
$$;

create function public.email_finish_delivery(
  p_notification_id bigint, p_claim_token uuid, p_sent boolean,
  p_error text default null, p_ambiguous boolean default false)
returns boolean language plpgsql security definer set search_path = '' as $$
declare n public.notifications;
begin
  if p_sent is null or p_ambiguous is null then raise exception 'Outcome flags must not be null'; end if;
  select * into n from public.notifications where notification_id = p_notification_id for update;
  if p_claim_token is not null and n.email_completed_token = p_claim_token
     and n.email_claim_token is null
     and n.delivery_status = (case when p_sent then 'SENT' else 'FAILED' end) then
    return true; -- completion RPC response may have been lost
  end if;
  if not found or p_claim_token is null or n.email_claim_token is distinct from p_claim_token
     or (p_sent and n.email_started_at is null) then return false; end if;
  -- A late acknowledgement may win before reaping; a replaced token never can.
  update public.notifications set delivery_status = case when p_sent then 'SENT' else 'FAILED' end,
    error_message = case when p_sent then null else left(coalesce(p_error, 'Email delivery failed'), 400)
      || case when n.attempts >= 5 then '; automatic attempts exhausted; operator retry required' else '' end end,
    email_ambiguous_attempts = email_ambiguous_attempts + case when p_ambiguous then 1 else 0 end,
    email_next_attempt_at = clock_timestamp() + interval '1 minute' * power(2, least(attempts, 8)),
    email_claim_token = null, email_claimed_until = null, email_started_at = null,
    email_completed_token = p_claim_token
    where notification_id = p_notification_id;
  return true;
end;
$$;

-- Explicit operator recovery after inspecting SMTP logs and fixing configuration.
-- Never requeue a delivered notification or steal an outstanding lease.
create function public.email_retry_notification(p_notification_id bigint)
returns boolean language plpgsql security definer set search_path = '' as $$
begin
  update public.notifications set delivery_status = 'PENDING', attempts = 0,
    email_next_attempt_at = clock_timestamp(), email_manual_retries = email_manual_retries + 1,
    email_completed_token = null
    where notification_id = p_notification_id and delivery_status = 'FAILED' and email_claim_token is null;
  return found;
end;
$$;

revoke all on function public.email_claim_notification(integer),
  public.email_start_delivery(bigint, uuid), public.email_finish_delivery(bigint, uuid, boolean, text, boolean),
  public.email_retry_notification(bigint) from public, anon, authenticated;
grant execute on function public.email_claim_notification(integer),
  public.email_start_delivery(bigint, uuid), public.email_finish_delivery(bigint, uuid, boolean, text, boolean),
  public.email_retry_notification(bigint) to service_role;
