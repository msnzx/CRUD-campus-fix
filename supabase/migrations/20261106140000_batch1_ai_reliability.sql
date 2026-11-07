-- Batch 1: approved routing-independent urgency, versioned writes, and fair recovery.
-- Additive schema; existing RPC signatures, grants and emergency triggers retained.
alter table public.tickets
  add column ai_revision bigint not null default 0,
  add column ai_report_revision bigint not null default 0;

create function private.track_ai_revisions() returns trigger
language plpgsql security invoker set search_path = '' as $$
begin
  if tg_op = 'INSERT' then
    new.ai_revision := 0; new.ai_report_revision := 0;
  else
    new.ai_revision := old.ai_revision + 1;
    new.ai_report_revision := old.ai_report_revision + case when
      row(new.original_title,new.original_text,new.title,new.description)
      is distinct from row(old.original_title,old.original_text,old.title,old.description)
      then 1 else 0 end;
  end if;
  return new;
end;
$$;
-- Runs after enforce_ticket_update and preserve_ticket_original_title.
create trigger track_ai_revisions before insert or update on public.tickets
  for each row execute function private.track_ai_revisions();
revoke all on function private.track_ai_revisions() from public,anon,authenticated;

alter table public.ticket_ai_jobs
  add column snapshot_revision bigint,
  add column snapshot_report_revision bigint,
  add column attempt_limit integer not null default 3 check (attempt_limit > 0),
  add column next_attempt_at timestamptz default now(),
  add column last_analysis_id uuid,
  add column enrichment_revision bigint;
create index ix_ai_jobs_due on public.ticket_ai_jobs(next_attempt_at) where next_attempt_at is not null;

alter table public.ticket_safety_escalations
  add column analysis_id uuid,
  add column analysis_version text not null default 'legacy-layer7.2',
  add column reason text not null default 'LEGACY_AI_URGENT',
  add column report_revision bigint,
  add column human_baseline_history_id bigint not null default 0,
  add column follow_current_department boolean not null default false;
-- Do not fabricate historical analysis IDs. Legacy intent retains its existing audience.
update public.ticket_safety_escalations e set report_revision=t.ai_report_revision
  from public.tickets t where t.ticket_id=e.ticket_id;

create or replace function private.ai_clear_claim(p_ticket_id bigint, p_result text, p_error text default null)
returns void language plpgsql security definer set search_path = '' as $$
declare j public.ticket_ai_jobs;
begin
  update public.ticket_ai_jobs set claim_token=null,claimed_by=null,claimed_until=null,
    last_result=p_result,last_error=left(coalesce(p_error,
      case when p_result='COMMITTED_FAILED' then 'Classifier unavailable or invalid output' end),1000),
    next_attempt_at=case
      when p_result in ('COMMITTED_FAILED','STALE_SNAPSHOT','RELEASED_AFTER_ERROR','CRASHED')
        and attempts < attempt_limit then clock_timestamp()+interval '1 minute'*power(2,least(attempts,8))
      when p_result in ('COMMITTED_FAILED','STALE_SNAPSHOT','RELEASED_AFTER_ERROR','CRASHED','ATTEMPTS_EXHAUSTED') then null
      else next_attempt_at end
    where ticket_id=p_ticket_id returning * into j;
  if p_result in ('STALE_SNAPSHOT','RELEASED_AFTER_ERROR','CRASHED','ATTEMPTS_EXHAUSTED')
     and j.attempts >= j.attempt_limit then
    if private.ai_human_routing_after(p_ticket_id,j.human_baseline_history_id)
       or not private.ai_routing_stage_open(p_ticket_id) then
      update public.tickets set ai_routing_status='OVERRIDDEN'
        where ticket_id=p_ticket_id and ai_routing_status in ('PENDING','FAILED');
    else
      update public.tickets set ai_routing_status='FAILED',
        status_id=case when is_emergency then status_id else
          (select status_id from public.statuses where name='NEEDS_REVIEW') end
        where ticket_id=p_ticket_id and ai_routing_status in ('PENDING','FAILED');
    end if;
  end if;
end;
$$;

create or replace function public.ai_claim_ticket(
  p_ticket_id bigint,
  p_worker text,
  p_lease_seconds integer,
  p_allow_failed boolean default false,
  p_max_attempts integer default 3)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  t public.tickets;
  j public.ticket_ai_jobs;
  v_token uuid := gen_random_uuid();
begin
  if p_lease_seconds is null or p_lease_seconds < 1 or p_max_attempts is null or p_max_attempts < 1 then
    raise exception 'Lease and attempt limit must be positive';
  end if;

  -- Lock order everywhere: ticket row, then job row.
  select * into t from public.tickets where ticket_id = p_ticket_id for update;
  if not found then
    return jsonb_build_object('claimed', false, 'reason', 'not_found');
  end if;
  insert into public.ticket_ai_jobs (ticket_id) values (p_ticket_id) on conflict (ticket_id) do nothing;
  select * into j from public.ticket_ai_jobs where ticket_id = p_ticket_id for update;

  if t.deleted_at is not null then
    return jsonb_build_object('claimed', false, 'reason', 'withdrawn');
  end if;
  if not (t.ai_routing_status = 'PENDING' or (p_allow_failed and t.ai_routing_status = 'FAILED')) then
    return jsonb_build_object('claimed', false, 'reason', 'not_pending',
                              'ai_routing_status', t.ai_routing_status);
  end if;
  if j.claimed_until is not null and j.claimed_until > clock_timestamp() then
    return jsonb_build_object('claimed', false, 'reason', 'in_progress');
  end if;

  -- A person already routed this ticket. Their decision stands; take the ticket
  -- out of the pending queue so no worker keeps retrying it.
  if private.ai_human_routing_after(p_ticket_id, j.human_baseline_history_id)
     or not private.ai_routing_stage_open(p_ticket_id) then
    update public.tickets set ai_routing_status = 'OVERRIDDEN' where ticket_id = p_ticket_id;
    perform private.ai_clear_claim(p_ticket_id, 'SUPERSEDED_BY_HUMAN');
    return jsonb_build_object('claimed', false, 'reason', 'superseded_by_human');
  end if;

  -- Crash loops and repeated stale snapshots end in human review, never in an
  -- endlessly pending ticket.
  if j.attempts >= p_max_attempts then
    update public.tickets
       set ai_routing_status = 'FAILED',
           status_id = case when is_emergency then status_id
                            else (select status_id from public.statuses where name = 'NEEDS_REVIEW') end
     where ticket_id = p_ticket_id;
    perform private.ai_clear_claim(p_ticket_id, 'ATTEMPTS_EXHAUSTED');
    return jsonb_build_object('claimed', false, 'reason', 'attempts_exhausted', 'attempts', j.attempts);
  end if;

  update public.ticket_ai_jobs
     set claim_token = v_token,
         claimed_by = left(coalesce(p_worker, 'unknown'), 200),
         claimed_until = clock_timestamp() + make_interval(secs => p_lease_seconds),
         attempts = attempts + 1,
         last_attempt_at = clock_timestamp(),
         snapshot_updated_at = t.updated_at,
         snapshot_revision = t.ai_revision,
         snapshot_report_revision = t.ai_report_revision,
         attempt_limit = p_max_attempts,
         last_analysis_id = null, enrichment_revision = null,
         last_error = null
   where ticket_id = p_ticket_id;

  return jsonb_build_object('claimed', true, 'claim_token', v_token,
                            'attempts', j.attempts + 1, 'ticket', to_jsonb(t));
end;
$$;

create or replace function public.escalate_ticket_safety(p_ticket_id bigint)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  t public.tickets;
  e public.ticket_safety_escalations;
  v_rows integer := 0;
  v_error text;
  v_department integer;
begin
  select * into t from public.tickets where ticket_id = p_ticket_id for update;
  select * into e from public.ticket_safety_escalations where ticket_id = p_ticket_id for update;
  if not found or e.status in ('SENT','CANCELLED') then
    return jsonb_build_object('notifications', 0);
  end if;
  if t.deleted_at is not null or e.report_revision is distinct from t.ai_report_revision
     or (select name from public.statuses where status_id=t.status_id) in ('RESOLVED','CLOSED') then
    update public.ticket_safety_escalations set status = 'CANCELLED' where ticket_id = p_ticket_id;
    return jsonb_build_object('notifications', 0);
  end if;
  v_department := case when e.follow_current_department
    or private.ai_human_routing_after(p_ticket_id,e.human_baseline_history_id)
    then t.department_id else e.department_id end;
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
      and (d.name = 'Campus Safety' or d.department_id = v_department)
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
  v_department integer;
  v_reason text;
  v_enrichment_revision bigint;
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
  elsif t.ai_report_revision is distinct from j.snapshot_report_revision then
    v_result := 'STALE_SNAPSHOT';
  elsif (select name from public.statuses where status_id=t.status_id) in ('RESOLVED','CLOSED') then
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
  elsif t.ai_routing_status not in ('PENDING', 'FAILED') then
    v_result := 'ALREADY_FINAL';
  elsif t.updated_at is distinct from j.snapshot_updated_at
        or t.ai_revision is distinct from j.snapshot_revision then
    -- Something changed while the model ran. Discard; the ticket stays pending
    -- and is re-read on the next attempt.
    v_result := 'STALE_SNAPSHOT';
  else
    if v_outcome='ROUTED' and (select name from public.issue_types where issue_type_id=p_issue_type_id)='Other' then
      v_outcome := 'REVIEW';
    end if;
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

  -- Capture the post-routing revision. A later staff action fences enrichment.
  if v_result = 'COMMITTED_AUTO_ROUTED' then
    select ai_revision into v_enrichment_revision from public.tickets where ticket_id=p_ticket_id;
    update public.ticket_ai_jobs set last_analysis_id=p_claim_token,
      enrichment_revision=v_enrichment_revision where ticket_id=p_ticket_id;
  end if;

  if v_result in ('COMMITTED_AUTO_ROUTED','COMMITTED_NEEDS_REVIEW','SUPERSEDED_BY_HUMAN')
     and p_outcome <> 'FAILED' and p_priority_id=v_urgent
     and (not t.is_emergency or v_result='SUPERSEDED_BY_HUMAN') then
    v_reason := case when v_result='SUPERSEDED_BY_HUMAN' then 'TEXT_URGENT_STAFF_ROUTING_PRESERVED'
      when (select name from public.issue_types where issue_type_id=p_issue_type_id)='Other'
        then 'TEXT_URGENT_OTHER_REVIEW' else 'TEXT_PRIORITY_URGENT' end;
    select department_id into v_department from public.tickets where ticket_id=p_ticket_id;
    -- An unresolved/missing model department has no responsible audience yet.
    if v_result <> 'SUPERSEDED_BY_HUMAN' and
       (v_reason='TEXT_URGENT_OTHER_REVIEW' or not coalesce(
         (select active from public.departments where department_id=p_department_id),false)) then
      v_department := null;
    end if;
    insert into public.ticket_safety_escalations(ticket_id,department_id,analysis_id,
      analysis_version,reason,report_revision,human_baseline_history_id,follow_current_department)
    values (p_ticket_id,v_department,p_claim_token,'campusfix-text-v2/safety-v1',v_reason,
      t.ai_report_revision,(select coalesce(max(history_id),0) from public.ticket_history where ticket_id=p_ticket_id),
      v_department is not null)
    on conflict (ticket_id) do update set department_id=excluded.department_id,
      analysis_id=excluded.analysis_id,analysis_version=excluded.analysis_version,reason=excluded.reason,
      report_revision=excluded.report_revision,human_baseline_history_id=excluded.human_baseline_history_id,
      follow_current_department=excluded.follow_current_department,status='PENDING',
      next_attempt_at=clock_timestamp(),error_message=null
    where public.ticket_safety_escalations.status <> 'SENT'
       or public.ticket_safety_escalations.department_id is distinct from excluded.department_id;
    get diagnostics v_rows=row_count;
    if v_rows > 0 then
      insert into public.ticket_history(ticket_id,action,new_value)
        values (p_ticket_id,'SAFETY_ESCALATED',jsonb_build_object('analysis_id',p_claim_token,
          'analysis_version','campusfix-text-v2/safety-v1','reason',v_reason)::text);
    end if;
    v_page := public.escalate_ticket_safety(p_ticket_id);
    v_notified := (v_page->>'notifications')::integer;
    v_notify_error := v_page->>'error';
  end if;

  perform private.ai_clear_claim(p_ticket_id, v_result, v_notify_error);
  return jsonb_build_object('result', v_result, 'outcome', v_outcome,
                            'urgent_notifications', v_notified,
                            'notification_error', v_notify_error, 'analysis_id', p_claim_token,
                            'enrichment_revision', v_enrichment_revision);
end;
$$;


create or replace function public.ai_release_claim(p_ticket_id bigint,p_claim_token uuid,p_error text default null)
returns boolean language plpgsql security definer set search_path = '' as $$
declare j public.ticket_ai_jobs;
begin
  perform 1 from public.tickets where ticket_id=p_ticket_id for update;
  select * into j from public.ticket_ai_jobs where ticket_id=p_ticket_id for update;
  if not found or p_claim_token is null or j.claim_token is distinct from p_claim_token then return false; end if;
  perform private.ai_clear_claim(p_ticket_id,'RELEASED_AFTER_ERROR',p_error);
  return true;
end;
$$;

create or replace function public.ai_request_reclassification(p_ticket_id bigint, p_requested_by uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  t public.tickets;
  j public.ticket_ai_jobs;
begin
  select * into t from public.tickets where ticket_id = p_ticket_id for update;
  if not found then
    return jsonb_build_object('ok', false, 'reason', 'not_found');
  end if;
  if t.deleted_at is not null then
    return jsonb_build_object('ok', false, 'reason', 'withdrawn');
  end if;
  insert into public.ticket_ai_jobs (ticket_id) values (p_ticket_id) on conflict (ticket_id) do nothing;
  select * into j from public.ticket_ai_jobs where ticket_id = p_ticket_id for update;
  if j.claimed_until is not null and j.claimed_until > clock_timestamp() then
    return jsonb_build_object('ok', false, 'reason', 'in_progress');
  end if;

  update public.tickets set ai_routing_status = 'PENDING'
   where ticket_id = p_ticket_id and ai_routing_status <> 'PENDING';

  update public.ticket_ai_jobs
     set attempts = 0,
         human_baseline_history_id =
           (select coalesce(max(h.history_id), 0) from public.ticket_history h where h.ticket_id = p_ticket_id),
         reclassify_requested_by = p_requested_by,
         reclassify_requested_at = clock_timestamp(),
         last_result = 'RECLASSIFY_REQUESTED',
         next_attempt_at = clock_timestamp(), last_analysis_id = null, enrichment_revision = null,
         last_error = null
   where ticket_id = p_ticket_id;
  update public.ticket_safety_escalations set status='CANCELLED'
    where ticket_id=p_ticket_id and status in ('PENDING','FAILED');
  return jsonb_build_object('ok', true);
end;
$$;

create function public.ai_commit_enrichment(
  p_ticket_id bigint,p_analysis_token uuid,p_expected_revision bigint,p_text jsonb)
returns boolean language plpgsql security definer set search_path = '' as $$
declare t public.tickets; j public.ticket_ai_jobs; v_report_revision bigint;
begin
  select * into t from public.tickets where ticket_id=p_ticket_id for update;
  if not found then return false; end if;
  select * into j from public.ticket_ai_jobs where ticket_id=p_ticket_id for update;
  if not found or p_analysis_token is null or p_expected_revision is null
     or j.last_analysis_id is distinct from p_analysis_token
     or j.enrichment_revision is distinct from p_expected_revision
     or t.ai_revision <> p_expected_revision or t.deleted_at is not null
     or j.claim_token is not null or j.last_result <> 'COMMITTED_AUTO_ROUTED'
     or private.ai_human_routing_after(p_ticket_id,j.human_baseline_history_id) then return false; end if;
  if jsonb_typeof(p_text) <> 'object' or p_text is null then return false; end if;
  if exists (select 1 from jsonb_object_keys(p_text) as k where k not in ('title','description','original_text','ai_explanation'))
     or nullif(trim(p_text->>'title'),'') is null or nullif(trim(p_text->>'description'),'') is null
     or ((nullif(trim(t.original_text),'') is not null) and t.original_text is distinct from p_text->>'original_text')
     then return false; end if;
  update public.tickets set title=p_text->>'title',description=p_text->>'description',
    original_text=coalesce(p_text->>'original_text',original_text),ai_explanation=p_text->>'ai_explanation'
    where ticket_id=p_ticket_id returning ai_report_revision into v_report_revision;
  update public.ticket_ai_jobs set enrichment_revision=null where ticket_id=p_ticket_id;
  -- A validated derived rewrite is part of this analysis, not a staff report edit.
  -- Carry its own pending intent forward; never revalidate another analysis.
  update public.ticket_safety_escalations set report_revision=v_report_revision
    where ticket_id=p_ticket_id and analysis_id=p_analysis_token and status in ('PENDING','FAILED');
  return true;
end;
$$;
revoke all on function public.ai_commit_enrichment(bigint,uuid,bigint,jsonb) from public,anon,authenticated;
grant execute on function public.ai_commit_enrichment(bigint,uuid,bigint,jsonb) to service_role;

create function public.ai_claim_next_ticket(
  p_worker text,p_lease_seconds integer,p_max_attempts integer,p_min_age_seconds integer default 5)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v_ticket bigint; j public.ticket_ai_jobs; v_decision jsonb; v_retry integer;
begin
  if p_min_age_seconds is null or p_min_age_seconds < 0 or p_lease_seconds is null
     or p_lease_seconds < 1 or p_max_attempts is null or p_max_attempts < 1 then
    raise exception 'Invalid worker claim limits';
  end if;
  -- A LEFT JOIN candidate snapshot can predate another committed claim. Recheck
  -- fresh job state under the established ticket -> job lock order and retry a
  -- different due candidate; never mistake a still-live token for a crash.
  for v_retry in 1..32 loop
  select t.ticket_id into v_ticket from public.tickets t
    left join public.ticket_ai_jobs job on job.ticket_id=t.ticket_id
    where t.deleted_at is null and t.ai_routing_status in ('PENDING','FAILED')
      and t.created_at <= clock_timestamp()-make_interval(secs=>p_min_age_seconds)
      and (job.ticket_id is null or job.next_attempt_at <= clock_timestamp())
      and (job.claimed_until is null or job.claimed_until <= clock_timestamp())
    order by t.created_at,t.ticket_id limit 1 for update of t skip locked;
  if not found then return jsonb_build_object('claimed',false,'reason','empty'); end if;
  select * into j from public.ticket_ai_jobs where ticket_id=v_ticket for update;
  if found and (j.next_attempt_at is null or j.next_attempt_at>clock_timestamp()
     or j.claimed_until>clock_timestamp()) then
    continue;
  end if;
  if found and j.claim_token is not null then
    perform private.ai_clear_claim(v_ticket,'CRASHED','Worker lease expired; result invalidated');
    return jsonb_build_object('claimed',false,'reason','crash_backoff','ticket_id',v_ticket);
  end if;
  v_decision:=public.ai_claim_ticket(v_ticket,p_worker,p_lease_seconds,true,p_max_attempts);
  if coalesce((v_decision->>'claimed')::boolean,false)
     or coalesce(v_decision->>'reason','') not in ('in_progress','not_pending') then
    return v_decision;
  end if;
  end loop;
  return jsonb_build_object('claimed',false,'reason','contention');
end;
$$;
revoke all on function public.ai_claim_next_ticket(text,integer,integer,integer) from public,anon,authenticated;
grant execute on function public.ai_claim_next_ticket(text,integer,integer,integer) to service_role;
