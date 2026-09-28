-- Part 1: replace Supabase's blanket default grants with least privilege.

revoke all on all tables in schema public from anon, authenticated;

-- anon is unauthenticated traffic. It needs nothing; sign-in is required.
-- (no grants to anon at all)

-- Reference data every signed-in user may read.
grant select on
  public.roles, public.statuses, public.priorities, public.departments,
  public.issue_types, public.area_types, public.buildings, public.floors,
  public.locations, public.student_types, public.user_departments
to authenticated;

-- Operational tables. RLS narrows these to the correct rows.
grant select, update            on public.users                  to authenticated;
grant select, insert, update    on public.tickets                to authenticated;
grant select, insert            on public.comments               to authenticated;
grant select, insert            on public.attachments            to authenticated;
grant select, insert, update    on public.ticket_assignments     to authenticated;
grant select, insert            on public.ticket_history         to authenticated;
grant select, insert, update    on public.ticket_reopen_requests to authenticated;
grant select, update            on public.notifications          to authenticated;

-- No DELETE anywhere: tickets use soft delete, history is immutable.
-- No TRUNCATE anywhere.

-- Future tables must not silently inherit broad grants.
alter default privileges in schema public revoke all on tables from anon, authenticated;

-- Part 2: RLS helper functions.
--
-- These are SECURITY DEFINER because a policy on public.users that itself
-- reads public.users would recurse infinitely. Running as definer bypasses
-- RLS on the lookup and breaks the cycle.
--
-- Each function reports facts about the CALLING user only, derived from
-- auth.uid(). Direct invocation therefore discloses nothing the caller does
-- not already know about themselves.

create schema if not exists private;

create or replace function private.current_role_name()
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select r.name
  from public.users u
  join public.roles r on r.role_id = u.role_id
  where u.user_id = (select auth.uid())
    and u.active;
$$;

create or replace function private.is_system_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select private.current_role_name() = 'SYSTEM_ADMIN';
$$;

create or replace function private.is_dept_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select private.current_role_name() in ('DEPARTMENT_ADMIN', 'SYSTEM_ADMIN');
$$;

create or replace function private.is_staff()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select private.current_role_name()
         in ('DEPARTMENT_STAFF', 'DEPARTMENT_ADMIN', 'SYSTEM_ADMIN');
$$;

-- Departments the caller belongs to. System admins implicitly span all.
create or replace function private.can_access_department(p_department_id integer)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select
    case
      when private.is_system_admin() then true
      when p_department_id is null then private.is_staff()
      else exists (
        select 1
        from public.user_departments ud
        where ud.user_id = (select auth.uid())
          and ud.department_id = p_department_id
      )
    end;
$$;

-- True when the caller may see the ticket at all: as its requester,
-- as a member of its owning department, or as a system admin.
create or replace function private.can_access_ticket(p_ticket_id bigint)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.tickets t
    where t.ticket_id = p_ticket_id
      and (
        t.reported_by = (select auth.uid())
        or private.can_access_department(t.department_id)
      )
  );
$$;

grant usage on schema private to authenticated;
grant execute on all functions in schema private to authenticated;
revoke all on schema private from anon;
