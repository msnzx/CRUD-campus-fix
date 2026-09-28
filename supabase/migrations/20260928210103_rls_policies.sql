-- Part 1: narrow two UPDATE grants to specific columns.
-- Row-level policies cannot restrict WHICH columns change, so privilege
-- escalation via self-service role change is blocked at the grant level.

revoke update on public.users from authenticated;
grant update (first_name, last_name, phone) on public.users to authenticated;

revoke update on public.notifications from authenticated;
grant update (read_at) on public.notifications to authenticated;

-- Part 2: reference data — readable by any signed-in user.

create policy read_roles          on public.roles          for select to authenticated using (true);
create policy read_statuses       on public.statuses       for select to authenticated using (true);
create policy read_priorities     on public.priorities     for select to authenticated using (true);
create policy read_departments    on public.departments    for select to authenticated using (true);
create policy read_issue_types    on public.issue_types    for select to authenticated using (true);
create policy read_area_types     on public.area_types     for select to authenticated using (true);
create policy read_buildings      on public.buildings      for select to authenticated using (true);
create policy read_floors         on public.floors         for select to authenticated using (true);
create policy read_locations      on public.locations      for select to authenticated using (true);
create policy read_student_types  on public.student_types  for select to authenticated using (true);

-- Part 3: users and department membership.

create policy read_own_or_staff on public.users
  for select to authenticated
  using ((select auth.uid()) = user_id or private.is_staff());

create policy update_own_profile on public.users
  for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy read_user_departments on public.user_departments
  for select to authenticated
  using ((select auth.uid()) = user_id or private.is_staff());

-- Part 4: tickets.
-- V-003 lives here. A student reaching for another student's ticket id
-- matches no policy and receives zero rows.

create policy read_own_or_department on public.tickets
  for select to authenticated
  using (
    reported_by = (select auth.uid())
    or private.can_access_department(department_id)
  );

create policy insert_own_ticket on public.tickets
  for insert to authenticated
  with check (reported_by = (select auth.uid()));

-- Only staff may modify a ticket after creation. A requester who wants
-- their ticket reopened or withdrawn goes through an RPC, so they cannot
-- silently rewrite priority, department, or status.
create policy staff_update_department_ticket on public.tickets
  for update to authenticated
  using (private.is_staff() and private.can_access_department(department_id))
  with check (private.is_staff() and private.can_access_department(department_id));

-- Requesters must not be able to self-assign priority, department, or a
-- non-initial status on insert. Policies cannot gate columns, so a trigger
-- normalises the row instead.
create or replace function public.normalize_new_ticket()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_new_status integer;
begin
  if private.is_staff() then
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

  return new;
end;
$$;

create trigger normalize_new_ticket
  before insert on public.tickets
  for each row execute function public.normalize_new_ticket();

-- Part 5: comments. Internal notes are invisible to requesters.

create policy read_ticket_comments on public.comments
  for select to authenticated
  using (
    private.can_access_ticket(ticket_id)
    and (not is_internal or private.is_staff())
  );

create policy insert_ticket_comment on public.comments
  for insert to authenticated
  with check (
    private.can_access_ticket(ticket_id)
    and user_id = (select auth.uid())
    and (not is_internal or private.is_staff())
  );

-- Part 6: attachments.

create policy read_ticket_attachments on public.attachments
  for select to authenticated
  using (private.can_access_ticket(ticket_id));

create policy insert_ticket_attachment on public.attachments
  for insert to authenticated
  with check (
    private.can_access_ticket(ticket_id)
    and uploaded_by = (select auth.uid())
  );

-- Part 7: assignments — staff only.

create policy read_assignments on public.ticket_assignments
  for select to authenticated
  using (private.can_access_ticket(ticket_id));

create policy staff_insert_assignment on public.ticket_assignments
  for insert to authenticated
  with check (private.is_staff() and private.can_access_ticket(ticket_id));

create policy staff_update_assignment on public.ticket_assignments
  for update to authenticated
  using (private.is_staff() and private.can_access_ticket(ticket_id))
  with check (private.is_staff() and private.can_access_ticket(ticket_id));

-- Part 8: history — append only. No UPDATE or DELETE policy exists,
-- so the audit trail cannot be rewritten through the Data API.

create policy read_ticket_history on public.ticket_history
  for select to authenticated
  using (private.can_access_ticket(ticket_id));

create policy insert_ticket_history on public.ticket_history
  for insert to authenticated
  with check (private.can_access_ticket(ticket_id));

-- Part 9: reopen requests. The seven-day window is enforced here, in the
-- database, so it holds regardless of what the client sends.

create policy read_reopen_requests on public.ticket_reopen_requests
  for select to authenticated
  using (private.can_access_ticket(ticket_id));

create policy requester_creates_reopen on public.ticket_reopen_requests
  for insert to authenticated
  with check (
    requested_by = (select auth.uid())
    and decision = 'PENDING'
    and exists (
      select 1
      from public.tickets t
      join public.statuses s on s.status_id = t.status_id
      where t.ticket_id = ticket_reopen_requests.ticket_id
        and t.reported_by = (select auth.uid())
        and s.name = 'RESOLVED'
        and t.resolved_at is not null
        and t.resolved_at > now() - interval '7 days'
    )
  );

create policy admin_decides_reopen on public.ticket_reopen_requests
  for update to authenticated
  using (private.is_dept_admin() and private.can_access_ticket(ticket_id))
  with check (private.is_dept_admin() and private.can_access_ticket(ticket_id));

-- Part 10: notifications — strictly personal.

create policy read_own_notifications on public.notifications
  for select to authenticated
  using (user_id = (select auth.uid()));

create policy update_own_notifications on public.notifications
  for update to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));
