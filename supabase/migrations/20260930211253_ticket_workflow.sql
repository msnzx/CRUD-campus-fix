-- Ticket workflow: status rules, assignment, department transfer, reopen
-- decisions, in-app notifications, automatic closure, and user management.
--
-- The browser talks to the database directly, so the database is the only
-- place a rule can live that the client cannot skip. Each multi-step action
-- is an RPC that does the whole job in one transaction; the tables behind
-- them lose their direct write grants so the RPC is the only door.

-- ---------------------------------------------------------------------------
-- Part 0: a transaction-local flag the workflow RPCs raise when they make a
-- move ordinary updates may not (a reopen, a transfer, an unassignment).
-- PostgREST exposes only the public schema, so a client cannot call
-- set_config() itself.
-- ---------------------------------------------------------------------------

create or replace function private.in_workflow_rpc()
returns boolean
language sql
stable
set search_path = ''
as $$
  select coalesce(current_setting('campusfix.workflow_rpc', true), '') = 'on';
$$;

-- ---------------------------------------------------------------------------
-- Part 1: status transitions. Mirrors ALLOWED_TRANSITIONS in
-- frontend/src/lib/domain.ts; change both together.
-- RESOLVED -> REOPENED is deliberately absent: it happens only through an
-- approved reopen request (decide_reopen).
-- ---------------------------------------------------------------------------

create or replace function private.status_transition_allowed(p_from text, p_to text)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select case p_from
    when 'NEW'              then p_to in ('NEEDS_REVIEW', 'ASSIGNED')
    when 'AI_PROCESSING'    then p_to in ('NEEDS_REVIEW', 'ASSIGNED')
    when 'NEEDS_REVIEW'     then p_to in ('ASSIGNED')
    when 'ASSIGNED'         then p_to in ('IN_PROGRESS', 'NEEDS_REVIEW', 'RESOLVED')
    when 'IN_PROGRESS'      then p_to in ('WAITING_FOR_USER', 'RESOLVED')
    when 'WAITING_FOR_USER' then p_to in ('IN_PROGRESS', 'RESOLVED')
    when 'RESOLVED'         then p_to in ('CLOSED')
    when 'REOPENED'         then p_to in ('ASSIGNED', 'IN_PROGRESS', 'RESOLVED')
    else false
  end;
$$;

create or replace function public.enforce_ticket_update()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_from text;
  v_to   text;
begin
  -- The backend (service_role) and scheduled jobs carry no user. They are
  -- trusted to keep the rules themselves.
  if (select auth.uid()) is null then
    return new;
  end if;

  -- Facts about the original report never change.
  new.ticket_id     := old.ticket_id;
  new.reported_by   := old.reported_by;
  new.original_text := old.original_text;
  new.created_at    := old.created_at;

  if new.department_id is distinct from old.department_id
     and not private.in_workflow_rpc() then
    raise exception 'Use transfer_ticket() to change a ticket''s department';
  end if;

  if new.status_id is distinct from old.status_id then
    select name into v_from from public.statuses where status_id = old.status_id;
    select name into v_to   from public.statuses where status_id = new.status_id;

    if not private.in_workflow_rpc()
       and not private.status_transition_allowed(v_from, v_to) then
      raise exception 'A ticket cannot move from % to %', v_from, v_to;
    end if;

    -- Lifecycle timestamps follow the status. The reopen window and the
    -- resolution-time analytics both depend on these being right.
    if v_to = 'RESOLVED' then
      new.resolved_at := now();
      new.closed_at   := null;
    elsif v_to = 'CLOSED' then
      new.closed_at := now();
    elsif v_to = 'REOPENED' then
      new.resolved_at := null;
      new.closed_at   := null;
    end if;
  else
    new.resolved_at := old.resolved_at;
    new.closed_at   := old.closed_at;
  end if;

  return new;
end;
$$;

create trigger enforce_ticket_update
  before update on public.tickets
  for each row execute function public.enforce_ticket_update();

-- ---------------------------------------------------------------------------
-- Part 2: creation. Staff filing a ticket are requesters like anyone else,
-- so they now get the same clamping and routing. Inactive departments are
-- never chosen by the fallback.
-- ---------------------------------------------------------------------------

create or replace function public.normalize_new_ticket()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_new_status integer;
begin
  if (select auth.uid()) is null then
    return new;
  end if;

  select status_id into v_new_status from public.statuses where name = 'NEW';

  new.reported_by       := (select auth.uid());
  new.status_id         := v_new_status;
  new.department_id     := null;
  new.priority_id       := null;
  new.ai_routing_status := 'PENDING';
  new.ai_confidence     := null;
  new.ai_suggested_department_id := null;
  new.ai_suggested_issue_type_id := null;
  new.ai_suggested_priority_id   := null;
  new.resolution_notes  := null;
  new.resolved_at       := null;
  new.closed_at         := null;
  new.deleted_at        := null;

  select it.default_department_id into new.department_id
  from public.issue_types it
  join public.departments d on d.department_id = it.default_department_id and d.active
  where it.issue_type_id = new.issue_type_id;

  if new.is_emergency then
    select department_id into new.department_id
      from public.departments where name = 'Campus Safety';
    select priority_id into new.priority_id
      from public.priorities where name = 'URGENT';
  end if;

  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Part 3: the audit trail is written only by triggers. Previously any user
-- who could see a ticket could append arbitrary history rows to it.
-- ---------------------------------------------------------------------------

drop policy if exists insert_ticket_history on public.ticket_history;
revoke insert on public.ticket_history from authenticated;

create or replace function public.log_unassignment()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if old.unassigned_at is null and new.unassigned_at is not null then
    insert into public.ticket_history (ticket_id, changed_by, action, old_value)
    values (new.ticket_id, (select auth.uid()), 'UNASSIGNED',
            (select email from public.users where user_id = new.assigned_to));
  end if;
  return new;
end;
$$;

create trigger log_unassignment
  after update on public.ticket_assignments
  for each row execute function public.log_unassignment();

create or replace function public.log_reopen_requested()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.ticket_history (ticket_id, changed_by, action, new_value)
  values (new.ticket_id, new.requested_by, 'REOPEN_REQUESTED', new.reason);
  return new;
end;
$$;

create trigger log_reopen_requested
  after insert on public.ticket_reopen_requests
  for each row execute function public.log_reopen_requested();

-- ---------------------------------------------------------------------------
-- Part 4: notifications. In-app rows, picked up for email by the backend
-- dispatcher when SMTP is configured (delivery_status tracks that).
-- A notification must never roll back the change that caused it, so every
-- insert runs in its own sub-transaction and failures are only logged.
-- ---------------------------------------------------------------------------

create or replace function private.notify(
  p_user_id uuid, p_ticket_id bigint, p_type text, p_message text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- Nobody needs telling about their own action.
  if p_user_id is null or p_user_id = (select auth.uid()) then
    return;
  end if;
  begin
    insert into public.notifications (user_id, ticket_id, type, message)
    values (p_user_id, p_ticket_id, p_type, p_message);
  exception when others then
    raise warning 'notification for ticket % failed: %', p_ticket_id, sqlerrm;
  end;
end;
$$;

create or replace function private.ticket_label(p_ticket_id bigint)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select '#' || t.ticket_id || ' “' || t.title || '”'
  from public.tickets t where t.ticket_id = p_ticket_id;
$$;

create or replace function public.notify_ticket_changes()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_to text;
begin
  if new.status_id is distinct from old.status_id then
    select name into v_to from public.statuses where status_id = new.status_id;
    -- Triage states are internal; the requester hears about real progress.
    if v_to not in ('NEW', 'AI_PROCESSING', 'NEEDS_REVIEW') then
      perform private.notify(
        new.reported_by, new.ticket_id, 'STATUS_CHANGED',
        'Your ticket ' || private.ticket_label(new.ticket_id) || ' is now '
          || lower(replace(v_to, '_', ' ')) || '.');
    end if;
  end if;
  return new;
end;
$$;

create trigger notify_ticket_changes
  after update on public.tickets
  for each row execute function public.notify_ticket_changes();

create or replace function public.notify_ticket_created()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_staff uuid;
begin
  -- Emergencies page every active member of the owning department, so a
  -- human sees them without waiting for someone to open the queue.
  if new.is_emergency and new.department_id is not null then
    for v_staff in
      select ud.user_id from public.user_departments ud
      join public.users u on u.user_id = ud.user_id and u.active
      where ud.department_id = new.department_id
    loop
      perform private.notify(v_staff, new.ticket_id, 'EMERGENCY',
        'Emergency ticket ' || private.ticket_label(new.ticket_id) || ' was just filed.');
    end loop;
  end if;
  return new;
end;
$$;

create trigger notify_ticket_created
  after insert on public.tickets
  for each row execute function public.notify_ticket_created();

create or replace function public.notify_assignment()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform private.notify(new.assigned_to, new.ticket_id, 'ASSIGNED',
    'You were assigned ticket ' || private.ticket_label(new.ticket_id) || '.');
  return new;
end;
$$;

create trigger notify_assignment
  after insert on public.ticket_assignments
  for each row execute function public.notify_assignment();

create or replace function public.notify_comment()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_requester uuid;
  v_assignee  uuid;
begin
  if new.is_internal then
    return new;
  end if;

  select reported_by into v_requester from public.tickets where ticket_id = new.ticket_id;
  select assigned_to into v_assignee from public.ticket_assignments
   where ticket_id = new.ticket_id and unassigned_at is null;

  if new.user_id = v_requester then
    -- A requester's follow-up goes to whoever owns the work.
    perform private.notify(v_assignee, new.ticket_id, 'COMMENT',
      'The requester commented on ' || private.ticket_label(new.ticket_id) || '.');
  else
    perform private.notify(v_requester, new.ticket_id, 'COMMENT',
      'Staff replied on your ticket ' || private.ticket_label(new.ticket_id) || '.');
  end if;
  return new;
end;
$$;

create trigger notify_comment
  after insert on public.comments
  for each row execute function public.notify_comment();

create or replace function public.notify_reopen_requested()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_admin uuid;
begin
  for v_admin in
    select distinct u.user_id
    from public.tickets t
    join public.user_departments ud on ud.department_id = t.department_id
    join public.users u on u.user_id = ud.user_id and u.active
    join public.roles r on r.role_id = u.role_id and r.name = 'DEPARTMENT_ADMIN'
    where t.ticket_id = new.ticket_id
  loop
    perform private.notify(v_admin, new.ticket_id, 'REOPEN_REQUESTED',
      'Reopen requested on ' || private.ticket_label(new.ticket_id) || '.');
  end loop;
  return new;
end;
$$;

create trigger notify_reopen_requested
  after insert on public.ticket_reopen_requests
  for each row execute function public.notify_reopen_requested();

-- ---------------------------------------------------------------------------
-- Part 5: assignment. Direct writes are removed; these RPCs are the only way
-- in, so the one-active-assignment rule and the audit trail always hold.
-- ---------------------------------------------------------------------------

drop policy if exists staff_insert_assignment on public.ticket_assignments;
drop policy if exists staff_update_assignment on public.ticket_assignments;
revoke insert, update on public.ticket_assignments from authenticated;

create or replace function public.assign_ticket(p_ticket_id bigint, p_assignee uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid     uuid := (select auth.uid());
  v_dept    integer;
  v_status  text;
  v_current uuid;
begin
  if v_uid is null or not private.is_staff() then
    raise exception 'Only staff can assign tickets';
  end if;

  select t.department_id, s.name into v_dept, v_status
  from public.tickets t
  join public.statuses s on s.status_id = t.status_id
  where t.ticket_id = p_ticket_id and t.deleted_at is null
  for update of t;

  if not found or not private.can_access_department(v_dept) then
    raise exception 'Ticket not found';
  end if;
  if v_dept is null then
    raise exception 'Route the ticket to a department before assigning it';
  end if;
  if v_status in ('RESOLVED', 'CLOSED') then
    raise exception 'A % ticket cannot be assigned', lower(v_status);
  end if;

  -- The assignee must be active staff in the ticket's department. System
  -- admins span every department.
  if not exists (
    select 1
    from public.users u
    join public.roles r on r.role_id = u.role_id
    where u.user_id = p_assignee
      and u.active
      and (r.name = 'SYSTEM_ADMIN'
           or (r.name in ('DEPARTMENT_STAFF', 'DEPARTMENT_ADMIN')
               and exists (select 1 from public.user_departments ud
                           where ud.user_id = u.user_id and ud.department_id = v_dept)))
  ) then
    raise exception 'That person is not active staff in this ticket''s department';
  end if;

  select assigned_to into v_current
  from public.ticket_assignments
  where ticket_id = p_ticket_id and unassigned_at is null;

  if v_current = p_assignee then
    return;
  end if;

  update public.ticket_assignments
     set unassigned_at = now()
   where ticket_id = p_ticket_id and unassigned_at is null;

  insert into public.ticket_assignments (ticket_id, assigned_to, assigned_by)
  values (p_ticket_id, p_assignee, v_uid);

  if v_status in ('NEW', 'AI_PROCESSING', 'NEEDS_REVIEW', 'REOPENED') then
    update public.tickets
       set status_id = (select status_id from public.statuses where name = 'ASSIGNED')
     where ticket_id = p_ticket_id;
  end if;
end;
$$;

create or replace function public.unassign_ticket(p_ticket_id bigint)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_dept   integer;
  v_status text;
begin
  if (select auth.uid()) is null or not private.is_staff() then
    raise exception 'Only staff can unassign tickets';
  end if;

  select t.department_id, s.name into v_dept, v_status
  from public.tickets t
  join public.statuses s on s.status_id = t.status_id
  where t.ticket_id = p_ticket_id and t.deleted_at is null
  for update of t;

  if not found or not private.can_access_department(v_dept) then
    raise exception 'Ticket not found';
  end if;

  update public.ticket_assignments
     set unassigned_at = now()
   where ticket_id = p_ticket_id and unassigned_at is null;

  -- An open ticket with nobody on it goes back to triage.
  if v_status in ('ASSIGNED', 'IN_PROGRESS', 'WAITING_FOR_USER') then
    perform set_config('campusfix.workflow_rpc', 'on', true);
    update public.tickets
       set status_id = (select status_id from public.statuses where name = 'NEEDS_REVIEW')
     where ticket_id = p_ticket_id;
    perform set_config('campusfix.workflow_rpc', 'off', true);
  end if;
end;
$$;

-- ---------------------------------------------------------------------------
-- Part 6: department transfer. Department admins move tickets between
-- departments (permission matrix, verification.md). Any staff member may
-- route a ticket that has no department yet — that is triage, not transfer.
-- ---------------------------------------------------------------------------

create or replace function public.transfer_ticket(
  p_ticket_id bigint, p_department_id integer, p_reason text default null)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid    uuid := (select auth.uid());
  v_dept   integer;
  v_status text;
  v_to     text;
begin
  if v_uid is null or not private.is_staff() then
    raise exception 'Only staff can route tickets';
  end if;

  select t.department_id, s.name into v_dept, v_status
  from public.tickets t
  join public.statuses s on s.status_id = t.status_id
  where t.ticket_id = p_ticket_id and t.deleted_at is null
  for update of t;

  if not found or not private.can_access_department(v_dept) then
    raise exception 'Ticket not found';
  end if;
  if v_dept is not null and not private.is_dept_admin() then
    raise exception 'Only department admins can transfer a ticket to another department';
  end if;
  if v_status in ('RESOLVED', 'CLOSED') then
    raise exception 'A % ticket cannot be transferred', lower(v_status);
  end if;

  select name into v_to from public.departments
   where department_id = p_department_id and active;
  if v_to is null then
    raise exception 'That department does not exist or is inactive';
  end if;
  if v_dept = p_department_id then
    return;
  end if;

  perform set_config('campusfix.workflow_rpc', 'on', true);

  -- The new department decides who works it.
  update public.ticket_assignments
     set unassigned_at = now()
   where ticket_id = p_ticket_id and unassigned_at is null;

  update public.tickets t
     set department_id = p_department_id,
         ai_routing_status = case
           when t.ai_suggested_department_id is null then t.ai_routing_status
           when t.ai_suggested_department_id = p_department_id then 'ACCEPTED'
           else 'OVERRIDDEN'
         end,
         status_id = case
           when v_status in ('ASSIGNED', 'IN_PROGRESS', 'WAITING_FOR_USER')
             then (select status_id from public.statuses where name = 'NEEDS_REVIEW')
           else t.status_id
         end
   where t.ticket_id = p_ticket_id;

  perform set_config('campusfix.workflow_rpc', 'off', true);

  if nullif(trim(p_reason), '') is not null then
    insert into public.comments (ticket_id, user_id, message, is_internal)
    values (p_ticket_id, v_uid, 'Transferred to ' || v_to || ': ' || trim(p_reason), true);
  end if;
end;
$$;

-- ---------------------------------------------------------------------------
-- Part 7: reopen decisions, atomically. Previously approval was two client
-- writes that could half-succeed.
-- ---------------------------------------------------------------------------

drop policy if exists admin_decides_reopen on public.ticket_reopen_requests;
revoke update on public.ticket_reopen_requests from authenticated;

create or replace function public.decide_reopen(
  p_request_id bigint, p_approve boolean, p_note text default null)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid       uuid := (select auth.uid());
  v_ticket    bigint;
  v_requester uuid;
  v_note      text := nullif(trim(p_note), '');
begin
  if v_uid is null or not private.is_dept_admin() then
    raise exception 'Only department admins can decide reopen requests';
  end if;

  select ticket_id, requested_by into v_ticket, v_requester
  from public.ticket_reopen_requests
  where request_id = p_request_id and decision = 'PENDING'
  for update;

  if v_ticket is null or not private.can_access_ticket(v_ticket) then
    raise exception 'No pending reopen request found';
  end if;

  update public.ticket_reopen_requests
     set decision   = case when p_approve then 'APPROVED' else 'DENIED' end,
         decided_by = v_uid,
         decided_at = now()
   where request_id = p_request_id;

  insert into public.ticket_history (ticket_id, changed_by, action, new_value)
  values (v_ticket, v_uid,
          case when p_approve then 'REOPEN_APPROVED' else 'REOPEN_DENIED' end, v_note);

  if p_approve then
    perform set_config('campusfix.workflow_rpc', 'on', true);
    update public.tickets
       set status_id = (select status_id from public.statuses where name = 'REOPENED')
     where ticket_id = v_ticket;
    perform set_config('campusfix.workflow_rpc', 'off', true);
  else
    perform private.notify(v_requester, v_ticket, 'REOPEN_DENIED',
      'Your reopen request on ' || private.ticket_label(v_ticket) || ' was declined'
        || coalesce(': ' || v_note, '.'));
  end if;
  -- The note is on the history row, which the requester can read.
end;
$$;

-- ---------------------------------------------------------------------------
-- Part 8: resolved tickets close themselves once the reopen window passes.
-- ---------------------------------------------------------------------------

create or replace function private.close_expired_resolved_tickets()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_count integer;
begin
  update public.tickets t
     set status_id = (select status_id from public.statuses where name = 'CLOSED'),
         closed_at = now()
   where t.status_id = (select status_id from public.statuses where name = 'RESOLVED')
     and t.resolved_at < now() - interval '7 days'
     and t.deleted_at is null
     and not exists (select 1 from public.ticket_reopen_requests r
                     where r.ticket_id = t.ticket_id and r.decision = 'PENDING');
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create extension if not exists pg_cron with schema pg_catalog;

select cron.schedule(
  'campusfix-close-resolved',
  '15 3 * * *',
  $$select private.close_expired_resolved_tickets()$$
);

-- ---------------------------------------------------------------------------
-- Part 9: user and staff management.
-- System admins change roles and deactivate accounts. Department admins add
-- and remove staff in their own departments.
-- ---------------------------------------------------------------------------

create or replace function public.admin_set_user_role(p_user_id uuid, p_role text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role_id integer;
begin
  if not private.is_system_admin() then
    raise exception 'Only system admins can change roles';
  end if;
  if p_user_id = (select auth.uid()) and p_role <> 'SYSTEM_ADMIN' then
    raise exception 'You cannot remove your own system admin role';
  end if;

  select role_id into v_role_id from public.roles where name = p_role;
  if v_role_id is null then
    raise exception 'Unknown role %', p_role;
  end if;

  update public.users set role_id = v_role_id where user_id = p_user_id;
  if not found then
    raise exception 'User not found';
  end if;

  -- Students belong to no department. Their old assignments stay on the
  -- tickets and remain reassignable.
  if p_role = 'STUDENT' then
    delete from public.user_departments where user_id = p_user_id;
  end if;
end;
$$;

create or replace function public.admin_set_user_active(p_user_id uuid, p_active boolean)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not private.is_system_admin() then
    raise exception 'Only system admins can deactivate accounts';
  end if;
  if p_user_id = (select auth.uid()) then
    raise exception 'You cannot deactivate your own account';
  end if;

  update public.users set active = p_active where user_id = p_user_id;
  if not found then
    raise exception 'User not found';
  end if;
end;
$$;

create or replace function public.set_department_member(
  p_user_id uuid, p_department_id integer, p_member boolean)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not (private.is_system_admin()
          or (private.is_dept_admin() and private.can_access_department(p_department_id))) then
    raise exception 'You can only manage staff in your own departments';
  end if;

  if p_member then
    if not exists (
      select 1 from public.users u
      join public.roles r on r.role_id = u.role_id
      where u.user_id = p_user_id and r.name <> 'STUDENT'
    ) then
      raise exception 'Only staff accounts can join a department. Change the role first.';
    end if;
    insert into public.user_departments (user_id, department_id)
    values (p_user_id, p_department_id)
    on conflict (user_id, department_id) do nothing;
  else
    delete from public.user_departments
     where user_id = p_user_id and department_id = p_department_id;
  end if;
end;
$$;

-- ---------------------------------------------------------------------------
-- Part 10: execute privileges. Postgres grants EXECUTE to PUBLIC on every
-- new function, which in the public schema makes it an RPC endpoint.
-- Trigger functions are never callable; RPCs only by signed-in users.
-- ---------------------------------------------------------------------------

revoke execute on function public.enforce_ticket_update()   from public, anon, authenticated;
revoke execute on function public.log_unassignment()        from public, anon, authenticated;
revoke execute on function public.log_reopen_requested()    from public, anon, authenticated;
revoke execute on function public.notify_ticket_changes()   from public, anon, authenticated;
revoke execute on function public.notify_ticket_created()   from public, anon, authenticated;
revoke execute on function public.notify_assignment()       from public, anon, authenticated;
revoke execute on function public.notify_comment()          from public, anon, authenticated;
revoke execute on function public.notify_reopen_requested() from public, anon, authenticated;

revoke execute on function private.close_expired_resolved_tickets() from public, anon, authenticated;
revoke execute on function private.notify(uuid, bigint, text, text)  from public, anon, authenticated;

revoke execute on function public.assign_ticket(bigint, uuid)                from public, anon;
revoke execute on function public.unassign_ticket(bigint)                    from public, anon;
revoke execute on function public.transfer_ticket(bigint, integer, text)     from public, anon;
revoke execute on function public.decide_reopen(bigint, boolean, text)       from public, anon;
revoke execute on function public.admin_set_user_role(uuid, text)            from public, anon;
revoke execute on function public.admin_set_user_active(uuid, boolean)       from public, anon;
revoke execute on function public.set_department_member(uuid, integer, boolean) from public, anon;

grant execute on function public.assign_ticket(bigint, uuid)                to authenticated;
grant execute on function public.unassign_ticket(bigint)                    to authenticated;
grant execute on function public.transfer_ticket(bigint, integer, text)     to authenticated;
grant execute on function public.decide_reopen(bigint, boolean, text)       to authenticated;
grant execute on function public.admin_set_user_role(uuid, text)            to authenticated;
grant execute on function public.admin_set_user_active(uuid, boolean)       to authenticated;
grant execute on function public.set_department_member(uuid, integer, boolean) to authenticated;
