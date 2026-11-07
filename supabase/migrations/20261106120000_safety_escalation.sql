-- Layer 7.2: extend 7.1 paging, without changing ticket routing or permissions.
-- Durable intent and notification rows commit together with classification.
create table public.ticket_safety_escalations (
  ticket_id bigint primary key references public.tickets(ticket_id),
  department_id integer references public.departments(department_id),
  status text not null default 'PENDING' check (status in ('PENDING','SENT','FAILED','CANCELLED')),
  attempts integer not null default 0 check (attempts >= 0),
  last_attempt_at timestamptz,
  next_attempt_at timestamptz not null default now(),
  error_message text,
  created_at timestamptz not null default now()
);
alter table public.ticket_safety_escalations enable row level security;
revoke all on public.ticket_safety_escalations from public, anon, authenticated;
grant select on public.ticket_safety_escalations to authenticated;
grant all on public.ticket_safety_escalations to service_role;
create policy staff_read_safety_escalations on public.ticket_safety_escalations
  for select to authenticated
  using (private.is_staff() and private.can_access_ticket(ticket_id));
create index ix_safety_escalations_due on public.ticket_safety_escalations(next_attempt_at)
  where status in ('PENDING','FAILED');

-- Service only; a call cannot invent an escalation: it must have durable intent
-- written by the guarded classification commit. Lock order: ticket, intent.
create function public.escalate_ticket_safety(p_ticket_id bigint)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  t public.tickets;
  e public.ticket_safety_escalations;
  v_rows integer := 0;
  v_error text;
begin
  select * into t from public.tickets where ticket_id = p_ticket_id for update;
  select * into e from public.ticket_safety_escalations where ticket_id = p_ticket_id for update;
  if not found or e.status in ('SENT','CANCELLED') then
    return jsonb_build_object('notifications', 0);
  end if;
  if t.deleted_at is not null then
    update public.ticket_safety_escalations set status = 'CANCELLED' where ticket_id = p_ticket_id;
    return jsonb_build_object('notifications', 0);
  end if;
  update public.ticket_safety_escalations set attempts = attempts + 1,
    last_attempt_at = clock_timestamp() where ticket_id = p_ticket_id;
  begin
    -- A missing safety team is actionable failure, not successful delivery.
    if not exists (
      select 1 from public.user_departments ud
      join public.departments d using (department_id)
      join public.users u using (user_id)
      join public.roles r using (role_id)
      where d.name = 'Campus Safety' and d.active and u.active
        and r.name in ('DEPARTMENT_STAFF','DEPARTMENT_ADMIN','SYSTEM_ADMIN')
    ) then raise exception 'No active Campus Safety staff recipients'; end if;

    insert into public.notifications(user_id, ticket_id, type, message)
    select distinct u.user_id, p_ticket_id, 'AI_URGENT',
      'Ticket ' || private.ticket_label(p_ticket_id) || ' was classified URGENT. Please review it now.'
    from public.user_departments ud
    join public.departments d using (department_id)
    join public.users u using (user_id)
    join public.roles r using (role_id)
    where d.active and u.active
      and r.name in ('DEPARTMENT_STAFF','DEPARTMENT_ADMIN','SYSTEM_ADMIN')
      and (d.name = 'Campus Safety' or d.department_id = e.department_id)
      and not exists (select 1 from public.notifications n
        where n.ticket_id = p_ticket_id and n.user_id = u.user_id and n.type = 'EMERGENCY')
    on conflict (ticket_id, user_id) where type = 'AI_URGENT' do nothing;
    get diagnostics v_rows = row_count;
    update public.ticket_safety_escalations set status = 'SENT', error_message = null
      where ticket_id = p_ticket_id;
    update public.ticket_ai_jobs set urgent_notified_at = coalesce(urgent_notified_at, clock_timestamp())
      where ticket_id = p_ticket_id;
  exception when others then
    v_rows := 0;
    v_error := left(sqlerrm, 500);
    update public.ticket_safety_escalations set status = 'FAILED', error_message = v_error,
      next_attempt_at = clock_timestamp() + interval '1 minute' * power(2, least(e.attempts + 1, 8))
      where ticket_id = p_ticket_id;
  end;
  return jsonb_build_object('notifications', v_rows, 'error', v_error);
end;
$$;
revoke all on function public.escalate_ticket_safety(bigint) from public, anon, authenticated;
grant execute on function public.escalate_ticket_safety(bigint) to service_role;

create function public.retry_safety_escalations()
returns integer language plpgsql security definer set search_path = '' as $$
declare v_ticket bigint; v_count integer := 0;
begin
  -- Do not lock intent before ticket: commits acquire ticket first.
  for v_ticket in select ticket_id from public.ticket_safety_escalations
    where status in ('PENDING','FAILED') and next_attempt_at <= clock_timestamp()
    order by ticket_id limit 50
  loop
    perform public.escalate_ticket_safety(v_ticket);
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$$;
revoke all on function public.retry_safety_escalations() from public, anon, authenticated;
grant execute on function public.retry_safety_escalations() to service_role;

create or replace function public.ai_commit_classification(
  p_ticket_id bigint,
  p_claim_token uuid,
  p_outcome text,               -- 'ROUTED' | 'REVIEW' | 'FAILED'
  p_department_id integer default null,
  p_issue_type_id integer default null,
  p_priority_id integer default null,
  p_confidence numeric default null)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  t public.tickets;
  j public.ticket_ai_jobs;
  v_outcome text := p_outcome;
  v_result text;
  v_review_status integer := (select status_id from public.statuses where name = 'NEEDS_REVIEW');
  v_assigned_status integer := (select status_id from public.statuses where name = 'ASSIGNED');
  v_urgent integer := (select priority_id from public.priorities where name = 'URGENT');
  v_rows integer;
  v_notified integer := 0;
  v_notify_error text;
  v_page jsonb;
begin
  if p_outcome is null or p_outcome not in ('ROUTED', 'REVIEW', 'FAILED') then
    raise exception 'Unknown AI outcome %', p_outcome;
  end if;

  select * into t from public.tickets where ticket_id = p_ticket_id for update;
  if not found then
    return jsonb_build_object('result', 'NOT_FOUND');
  end if;
  select * into j from public.ticket_ai_jobs where ticket_id = p_ticket_id for update;

  -- Only the current lease holder may write. An expired lease may already have
  -- been taken over by another worker.
  if not found or p_claim_token is null or j.claimed_until is null
     or j.claim_token is distinct from p_claim_token or j.claimed_until <= clock_timestamp() then
    return jsonb_build_object('result', 'LEASE_LOST');
  end if;

  if t.deleted_at is not null then
    v_result := 'WITHDRAWN';
  elsif t.ai_routing_status not in ('PENDING', 'FAILED') then
    v_result := 'ALREADY_FINAL';
  elsif private.ai_human_routing_after(p_ticket_id, j.human_baseline_history_id)
        or not private.ai_routing_stage_open(p_ticket_id) then
    -- Never overwrite a human decision. Keep the suggestion for audit only.
    update public.tickets
       set ai_suggested_department_id = case when v_outcome = 'FAILED' then ai_suggested_department_id else p_department_id end,
           ai_suggested_issue_type_id = case when v_outcome = 'FAILED' then ai_suggested_issue_type_id else p_issue_type_id end,
           ai_suggested_priority_id   = case when v_outcome = 'FAILED' then ai_suggested_priority_id else p_priority_id end,
           ai_confidence              = case when v_outcome = 'FAILED' then ai_confidence else round(p_confidence, 4) end,
           ai_routing_status          = 'OVERRIDDEN'
     where ticket_id = p_ticket_id;
    v_result := 'SUPERSEDED_BY_HUMAN';
  elsif t.updated_at is distinct from j.snapshot_updated_at then
    -- Something changed while the model ran. Discard; the ticket stays pending
    -- and is re-read on the next attempt.
    v_result := 'STALE_SNAPSHOT';
  else
    if v_outcome = 'ROUTED' and not coalesce(
         (select d.active from public.departments d where d.department_id = p_department_id), false) then
      v_outcome := 'REVIEW';   -- department missing or deactivated since the cache was built
    end if;

    if v_outcome = 'FAILED' then
      update public.tickets
         set ai_routing_status = 'FAILED',
             status_id = case when is_emergency then status_id else v_review_status end
       where ticket_id = p_ticket_id;
      v_result := 'COMMITTED_FAILED';
    elsif v_outcome = 'REVIEW' then
      update public.tickets
         set ai_suggested_department_id = p_department_id,
             ai_suggested_issue_type_id = p_issue_type_id,
             ai_suggested_priority_id   = p_priority_id,
             ai_confidence              = round(p_confidence, 4),
             ai_routing_status          = 'NEEDS_REVIEW',
             status_id = case when is_emergency then status_id else v_review_status end
       where ticket_id = p_ticket_id;
      v_result := 'COMMITTED_NEEDS_REVIEW';
    else
      -- An emergency ticket is never re-routed away from Campus Safety by the model.
      update public.tickets
         set ai_suggested_department_id = p_department_id,
             ai_suggested_issue_type_id = p_issue_type_id,
             ai_suggested_priority_id   = p_priority_id,
             ai_confidence              = round(p_confidence, 4),
             ai_routing_status          = 'AUTO_ROUTED',
             issue_type_id = coalesce(p_issue_type_id, issue_type_id),
             department_id = case when is_emergency then department_id else p_department_id end,
             priority_id   = case when is_emergency then priority_id else p_priority_id end,
             status_id     = case when is_emergency then status_id else v_assigned_status end
       where ticket_id = p_ticket_id;
      v_result := 'COMMITTED_AUTO_ROUTED';


    end if;
  end if;

  -- Both routed and review verdicts can carry an urgent safety signal.
  if v_result in ('COMMITTED_AUTO_ROUTED', 'COMMITTED_NEEDS_REVIEW')
     and p_priority_id = v_urgent and not t.is_emergency then
    insert into public.ticket_safety_escalations(ticket_id, department_id)
      values (p_ticket_id, p_department_id) on conflict (ticket_id) do nothing;
    get diagnostics v_rows = row_count;
    if v_rows > 0 then
      insert into public.ticket_history(ticket_id, action, new_value)
        values (p_ticket_id, 'SAFETY_ESCALATED', 'AI text priority URGENT');
    end if;
    v_page := public.escalate_ticket_safety(p_ticket_id);
    v_notified := (v_page->>'notifications')::integer;
    v_notify_error := v_page->>'error';
  end if;

  perform private.ai_clear_claim(p_ticket_id, v_result, v_notify_error);
  return jsonb_build_object('result', v_result, 'outcome', v_outcome,
                            'urgent_notifications', v_notified,
                            'notification_error', v_notify_error);
end;
$$;


-- Recover earlier 7.1 urgency, including partially paged tickets. The existing
-- unique AI_URGENT index prevents sending the department another notification.
insert into public.ticket_safety_escalations(ticket_id, department_id)
select t.ticket_id, t.ai_suggested_department_id from public.tickets t
join public.priorities p on p.priority_id = t.ai_suggested_priority_id
join public.statuses s on s.status_id = t.status_id
where p.name = 'URGENT' and not t.is_emergency and t.deleted_at is null
  and s.name not in ('RESOLVED','CLOSED')
  and t.ai_routing_status in ('AUTO_ROUTED','NEEDS_REVIEW')
on conflict (ticket_id) do nothing;
