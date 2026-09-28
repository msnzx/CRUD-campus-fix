-- Deterministic routing + automatic audit trail.

-- 1. Requester input is clamped, then the ticket is routed from the issue
--    type's default department. This is the non-AI fallback: every ticket
--    lands in a real queue even with the classifier switched off.
create or replace function public.normalize_new_ticket()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_new_status integer;
begin
  if (select auth.uid()) is null or private.is_staff() then
    return new;
  end if;

  select status_id into v_new_status from public.statuses where name = 'NEW';

  new.reported_by       := (select auth.uid());
  new.status_id         := v_new_status;
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

  -- Deterministic fallback routing from the chosen category.
  select it.default_department_id into new.department_id
  from public.issue_types it
  where it.issue_type_id = new.issue_type_id;

  -- Emergencies always go to Campus Safety at top priority.
  if new.is_emergency then
    select department_id into new.department_id
      from public.departments where name = 'Campus Safety';
    select priority_id into new.priority_id
      from public.priorities where name = 'URGENT';
  end if;

  return new;
end;
$$;

-- 2. Audit trail. V-031 requires history for every important change.
create or replace function public.log_ticket_created()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.ticket_history (ticket_id, changed_by, action, new_value)
  values (new.ticket_id, new.reported_by, 'CREATED', new.title);
  return new;
end;
$$;

create trigger log_ticket_created
  after insert on public.tickets
  for each row execute function public.log_ticket_created();

create or replace function public.log_ticket_changes()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor uuid := (select auth.uid());
begin
  if new.status_id is distinct from old.status_id then
    insert into public.ticket_history (ticket_id, changed_by, action, field_name, old_value, new_value)
    values (new.ticket_id, v_actor, 'STATUS_CHANGED', 'status_id',
      (select name from public.statuses where status_id = old.status_id),
      (select name from public.statuses where status_id = new.status_id));
  end if;

  if new.priority_id is distinct from old.priority_id then
    insert into public.ticket_history (ticket_id, changed_by, action, field_name, old_value, new_value)
    values (new.ticket_id, v_actor, 'PRIORITY_CHANGED', 'priority_id',
      (select name from public.priorities where priority_id = old.priority_id),
      (select name from public.priorities where priority_id = new.priority_id));
  end if;

  if new.department_id is distinct from old.department_id then
    insert into public.ticket_history (ticket_id, changed_by, action, field_name, old_value, new_value)
    values (new.ticket_id, v_actor, 'DEPARTMENT_CHANGED', 'department_id',
      (select name from public.departments where department_id = old.department_id),
      (select name from public.departments where department_id = new.department_id));
  end if;

  if new.ai_routing_status is distinct from old.ai_routing_status then
    insert into public.ticket_history (ticket_id, changed_by, action, field_name, old_value, new_value)
    values (new.ticket_id, v_actor, 'AI_ROUTING', 'ai_routing_status',
            old.ai_routing_status, new.ai_routing_status);
  end if;

  if new.resolved_at is distinct from old.resolved_at and new.resolved_at is not null then
    insert into public.ticket_history (ticket_id, changed_by, action, new_value)
    values (new.ticket_id, v_actor, 'RESOLVED', new.resolution_notes);
  end if;

  if new.deleted_at is distinct from old.deleted_at and new.deleted_at is not null then
    insert into public.ticket_history (ticket_id, changed_by, action)
    values (new.ticket_id, v_actor, 'WITHDRAWN');
  end if;

  return new;
end;
$$;

create trigger log_ticket_changes
  after update on public.tickets
  for each row execute function public.log_ticket_changes();

create or replace function public.log_assignment()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.ticket_history (ticket_id, changed_by, action, new_value)
  values (new.ticket_id, new.assigned_by, 'ASSIGNED',
          (select email from public.users where user_id = new.assigned_to));
  return new;
end;
$$;

create trigger log_assignment
  after insert on public.ticket_assignments
  for each row execute function public.log_assignment();

-- 3. Requesters have no UPDATE policy on tickets, so withdrawal goes
--    through a narrow RPC that can only ever set deleted_at.
--
--    This function is DELIBERATELY callable by authenticated users — that is
--    its purpose. The security advisor flags it for that reason; the flag is
--    expected. Ownership and status are checked inside the body.
create or replace function public.withdraw_own_ticket(p_ticket_id bigint)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := (select auth.uid());
  v_status text;
begin
  if v_uid is null then
    raise exception 'Not authenticated';
  end if;

  select s.name into v_status
  from public.tickets t
  join public.statuses s on s.status_id = t.status_id
  where t.ticket_id = p_ticket_id
    and t.reported_by = v_uid
    and t.deleted_at is null;

  if v_status is null then
    raise exception 'Ticket not found or not yours';
  end if;

  if v_status not in ('NEW', 'AI_PROCESSING', 'NEEDS_REVIEW') then
    raise exception 'Ticket can only be withdrawn before work begins (currently %)', v_status;
  end if;

  update public.tickets
     set deleted_at = now()
   where ticket_id = p_ticket_id;
end;
$$;

revoke execute on function public.normalize_new_ticket()  from public, anon, authenticated;
revoke execute on function public.log_ticket_created()    from public, anon, authenticated;
revoke execute on function public.log_ticket_changes()    from public, anon, authenticated;
revoke execute on function public.log_assignment()        from public, anon, authenticated;
revoke execute on function public.withdraw_own_ticket(bigint) from public, anon;
grant   execute on function public.withdraw_own_ticket(bigint) to authenticated;
