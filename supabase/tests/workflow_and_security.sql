-- CampusFix security and workflow checks.
--
-- Runs as the real test accounts (see TESTING.md) by simulating their JWTs,
-- exercises the rules the database is responsible for, and ROLLS BACK at the
-- end, so it leaves no data behind. Run it in the Supabase SQL editor or with
-- psql against the project. Every row in the final result should say PASS.
--
-- Re-run after any migration that touches policies, grants, or triggers.

begin;

create temp table results (n serial, test text, pass boolean, detail text);
create temp table ctx (k text primary key, v text);
grant all on results, ctx to authenticated, anon;
grant usage on sequence results_n_seq to authenticated, anon;

create function pg_temp.check(p_test text, p_pass boolean, p_detail text default null)
returns void language sql as $$
  insert into results (test, pass, detail) values (p_test, coalesce(p_pass, false), p_detail);
$$;

-- Records PASS when the statement fails with an error matching p_pattern.
create function pg_temp.expect_error(p_test text, p_sql text, p_pattern text)
returns void language plpgsql as $$
declare
  v_failed boolean := false;
  v_msg text;
begin
  begin
    execute p_sql;
  exception when others then
    v_failed := true;
    v_msg := sqlerrm;
  end;
  perform pg_temp.check(p_test, v_failed and v_msg ~* p_pattern,
                        coalesce(v_msg, 'statement succeeded'));
end;
$$;

create function pg_temp.act_as(p_email text)
returns void language plpgsql as $$
declare
  v uuid;
begin
  reset role;
  select user_id into v from public.users where email = p_email;
  if v is null then
    raise exception 'Test account % is missing (see TESTING.md)', p_email;
  end if;
  perform set_config('request.jwt.claims',
                     json_build_object('sub', v, 'role', 'authenticated')::text, true);
  set local role authenticated;
end;
$$;

create function pg_temp.id(p_key text) returns bigint language sql as $$
  select v::bigint from ctx where k = p_key;
$$;

create function pg_temp.uid(p_email text) returns uuid language sql security definer as $$
  select user_id from public.users where email = p_email;
$$;

create function pg_temp.status_of(p_ticket bigint) returns text language sql security definer as $$
  select s.name from public.tickets t join public.statuses s using (status_id)
  where t.ticket_id = p_ticket;
$$;

-- Fixtures, looked up by name so the script is portable across projects.
insert into ctx values
  ('facilities', (select department_id from public.departments where name = 'Facilities')),
  ('it',         (select department_id from public.departments where name = 'IT')),
  ('fac_issue',  (select issue_type_id from public.issue_types
                  where default_department_id =
                        (select department_id from public.departments where name = 'Facilities')
                  limit 1)),
  ('urgent',     (select priority_id from public.priorities where name = 'URGENT')),
  ('resolved',   (select status_id from public.statuses where name = 'RESOLVED')),
  ('in_progress',(select status_id from public.statuses where name = 'IN_PROGRESS')),
  ('reopened',   (select status_id from public.statuses where name = 'REOPENED'));

-- ---- Student submits ------------------------------------------------------

do $$
declare
  v_ticket bigint;
  r record;
begin
  perform pg_temp.act_as('student2@caldwell.edu');

  insert into public.tickets (reported_by, original_text, title, issue_type_id,
                              status_id, priority_id, department_id)
  values (pg_temp.uid('student2@caldwell.edu'),
          'The radiator in my room has been banging all night.', 'Test radiator',
          pg_temp.id('fac_issue'), pg_temp.id('resolved'), pg_temp.id('urgent'), pg_temp.id('it'))
  returning ticket_id into v_ticket;

  insert into ctx values ('ticket', v_ticket);

  select s.name as status, t.priority_id, t.department_id into r
  from public.tickets t join public.statuses s using (status_id) where t.ticket_id = v_ticket;

  perform pg_temp.check('Submission clamped to NEW with no priority',
                        r.status = 'NEW' and r.priority_id is null, r.status);
  perform pg_temp.check('Submission routed from issue type, ignoring the client value',
                        r.department_id = pg_temp.id('facilities'), r.department_id::text);

  update public.tickets set title = 'hacked' where ticket_id = v_ticket;
  perform pg_temp.check('Student cannot edit their own ticket', not found);

  perform pg_temp.check('Student cannot read another user''s ticket (V-003)',
    not exists (select 1 from public.tickets where reported_by <> pg_temp.uid('student2@caldwell.edu')));

  perform pg_temp.expect_error('Student cannot write history directly',
    format($q$insert into public.ticket_history (ticket_id, action) values (%s, 'FAKE')$q$, v_ticket),
    'permission denied');

  perform pg_temp.expect_error('Student cannot assign directly',
    format($q$insert into public.ticket_assignments (ticket_id, assigned_to, assigned_by)
              values (%s, '%s', '%s')$q$, v_ticket,
           pg_temp.uid('student2@caldwell.edu'), pg_temp.uid('student2@caldwell.edu')),
    'permission denied');

  perform pg_temp.expect_error('Student cannot make themselves an admin',
    format($q$select public.admin_set_user_role('%s', 'SYSTEM_ADMIN')$q$,
           pg_temp.uid('student2@caldwell.edu')),
    'only system admins');

  perform pg_temp.expect_error('Student cannot change their role column',
    format($q$update public.users set role_id = 4 where user_id = '%s'$q$,
           pg_temp.uid('student2@caldwell.edu')),
    'permission denied');
end $$;

-- ---- Other department cannot see it ---------------------------------------

do $$
begin
  perform pg_temp.act_as('it.staff@caldwell.edu');
  perform pg_temp.check('Staff outside the department cannot read the ticket',
    not exists (select 1 from public.tickets where ticket_id = pg_temp.id('ticket')));
end $$;

-- ---- Department staff work it ---------------------------------------------

do $$
declare
  v_ticket bigint := pg_temp.id('ticket');
  v_resolved timestamptz;
begin
  perform pg_temp.act_as('fac.staff@caldwell.edu');

  perform pg_temp.expect_error('Status rules: NEW cannot jump to RESOLVED',
    format('update public.tickets set status_id = %s where ticket_id = %s',
           pg_temp.id('resolved'), v_ticket),
    'cannot move from NEW to RESOLVED');

  perform pg_temp.expect_error('Department changes must go through transfer_ticket',
    format('update public.tickets set department_id = %s where ticket_id = %s',
           pg_temp.id('it'), v_ticket),
    'transfer_ticket');

  perform pg_temp.expect_error('Plain staff cannot transfer departments',
    format('select public.transfer_ticket(%s, %s)', v_ticket, pg_temp.id('it')),
    'only department admins');

  perform pg_temp.expect_error('Cannot assign someone outside the department',
    format($q$select public.assign_ticket(%s, '%s')$q$, v_ticket, pg_temp.uid('it.staff@caldwell.edu')),
    'not active staff');

  perform public.assign_ticket(v_ticket, pg_temp.uid('fac.staff@caldwell.edu'));
  perform pg_temp.check('Claiming a ticket moves it to ASSIGNED',
                        pg_temp.status_of(v_ticket) = 'ASSIGNED', pg_temp.status_of(v_ticket));

  update public.tickets set status_id = pg_temp.id('in_progress') where ticket_id = v_ticket;
  update public.tickets set status_id = pg_temp.id('resolved'), resolution_notes = 'Bled the radiator.'
   where ticket_id = v_ticket
  returning resolved_at into v_resolved;
  perform pg_temp.check('Resolving through a plain status change sets resolved_at',
                        v_resolved is not null);

  perform pg_temp.expect_error('Staff cannot reopen without an approved request',
    format('update public.tickets set status_id = %s where ticket_id = %s',
           pg_temp.id('reopened'), v_ticket),
    'cannot move from RESOLVED to REOPENED');
end $$;

-- ---- Private chat and internal notes --------------------------------------
-- State here: ticket is RESOLVED and assigned to fac.staff.

do $$
declare
  v_ticket bigint := pg_temp.id('ticket');
begin
  perform pg_temp.act_as('student2@caldwell.edu');

  perform pg_temp.expect_error('Requester cannot start the chat before staff speaks',
    format($q$select public.send_ticket_message(%s, 'hello?')$q$, v_ticket),
    'will message you here first');

  perform pg_temp.expect_error('Messages cannot be inserted directly',
    format($q$insert into public.ticket_messages (ticket_id, sender_id, message)
              values (%s, '%s', 'sneaky')$q$, v_ticket, pg_temp.uid('student2@caldwell.edu')),
    'permission denied');

  perform pg_temp.expect_error('Requester cannot post a comment',
    format($q$insert into public.comments (ticket_id, user_id, message)
              values (%s, '%s', 'hi')$q$, v_ticket, pg_temp.uid('student2@caldwell.edu')),
    'row-level security');
end $$;

do $$
declare
  v_ticket bigint := pg_temp.id('ticket');
begin
  perform pg_temp.act_as('fac.staff@caldwell.edu');

  perform public.send_ticket_message(v_ticket, 'Hi, I am coming by tomorrow. Is 10am ok?');
  perform pg_temp.check('Assignee can start the chat',
    (select count(*) from public.ticket_messages where ticket_id = v_ticket) = 1);

  perform pg_temp.expect_error('Staff cannot post a non-internal comment',
    format($q$insert into public.comments (ticket_id, user_id, message, is_internal)
              values (%s, '%s', 'public?', false)$q$, v_ticket, pg_temp.uid('fac.staff@caldwell.edu')),
    'row-level security|check constraint');

  insert into public.comments (ticket_id, user_id, message)
  values (v_ticket, pg_temp.uid('fac.staff@caldwell.edu'), 'Valve looks corroded.');
  perform pg_temp.check('Staff can add an internal note (defaults to internal)',
    exists (select 1 from public.comments where ticket_id = v_ticket and is_internal));
end $$;

do $$
declare
  v_ticket bigint := pg_temp.id('ticket');
begin
  perform pg_temp.act_as('student2@caldwell.edu');

  perform public.send_ticket_message(v_ticket, 'Yes, 10am works.');
  perform pg_temp.check('Requester can reply after staff has spoken, and sees both messages',
    (select count(*) from public.ticket_messages where ticket_id = v_ticket) = 2);

  perform pg_temp.check('Requester cannot see internal notes',
    not exists (select 1 from public.comments where ticket_id = v_ticket));

  perform pg_temp.check('Requester was notified of the staff message',
    exists (select 1 from public.notifications
            where ticket_id = v_ticket and type = 'MESSAGE'));
end $$;

do $$
declare
  v_ticket bigint := pg_temp.id('ticket');
begin
  perform pg_temp.act_as('fac.staff@caldwell.edu');
  perform pg_temp.check('Assignee was notified of the requester reply',
    exists (select 1 from public.notifications
            where ticket_id = v_ticket and type = 'MESSAGE'));

  -- A staff member in another department sees and sends nothing.
  perform pg_temp.act_as('it.staff@caldwell.edu');
  perform pg_temp.check('Staff outside the department cannot read the chat',
    not exists (select 1 from public.ticket_messages where ticket_id = v_ticket));
  perform pg_temp.expect_error('Staff outside the department cannot send',
    format($q$select public.send_ticket_message(%s, 'let me in')$q$, v_ticket),
    'ticket not found');

  -- Department admin can audit the chat but not post.
  perform pg_temp.act_as('fac.admin@caldwell.edu');
  perform pg_temp.check('Department admin can read the chat',
    (select count(*) from public.ticket_messages where ticket_id = v_ticket) = 2);
  perform pg_temp.expect_error('Department admin cannot send unless assigned',
    format($q$select public.send_ticket_message(%s, 'admin here')$q$, v_ticket),
    'only the assigned staff member');
  perform pg_temp.check('Department admin can read internal notes',
    exists (select 1 from public.comments where ticket_id = v_ticket));
end $$;

-- Messages page list: each role sees only the threads it may open.
do $$
declare
  v_ticket bigint := pg_temp.id('ticket');
begin
  perform pg_temp.act_as('student2@caldwell.edu');
  perform pg_temp.check('Messages list: requester sees their thread as REQUESTER',
    exists (select 1 from public.my_message_threads()
            where ticket_id = v_ticket and role = 'REQUESTER' and message_count = 2));

  perform pg_temp.act_as('it.staff@caldwell.edu');
  perform pg_temp.check('Messages list: other department sees nothing of it',
    not exists (select 1 from public.my_message_threads() where ticket_id = v_ticket));

  perform pg_temp.act_as('fac.admin@caldwell.edu');
  perform pg_temp.check('Messages list: department admin sees it as OBSERVER',
    exists (select 1 from public.my_message_threads()
            where ticket_id = v_ticket and role = 'OBSERVER'));
end $$;

do $$
begin
  reset role;
  perform set_config('request.jwt.claims', '{"role":"anon"}', true);
  set local role anon;
  perform pg_temp.expect_error('Anonymous users cannot read messages',
    'select count(*) from public.ticket_messages', 'permission denied');
  reset role;
end $$;

-- ---- Requester asks to reopen ---------------------------------------------

do $$
declare
  v_ticket bigint := pg_temp.id('ticket');
  v_request bigint;
begin
  perform pg_temp.act_as('student2@caldwell.edu');

  perform pg_temp.check('Requester was notified of progress',
    exists (select 1 from public.notifications
            where ticket_id = v_ticket and type = 'STATUS_CHANGED'));

  perform pg_temp.check('Requester sees only their own notifications',
    not exists (select 1 from public.notifications
                where user_id <> pg_temp.uid('student2@caldwell.edu')));

  insert into public.ticket_reopen_requests (ticket_id, requested_by, reason)
  values (v_ticket, pg_temp.uid('student2@caldwell.edu'), 'Still banging.')
  returning request_id into v_request;
  insert into ctx values ('request', v_request);

  perform pg_temp.check('Reopen request is recorded in history',
    exists (select 1 from public.ticket_history
            where ticket_id = v_ticket and action = 'REOPEN_REQUESTED'));
end $$;

do $$
begin
  perform pg_temp.act_as('fac.staff@caldwell.edu');
  perform pg_temp.expect_error('Plain staff cannot decide reopen requests',
    format('select public.decide_reopen(%s, true)', pg_temp.id('request')),
    'only department admins');
end $$;

-- ---- Department admin decides, reassigns, transfers -----------------------

do $$
declare
  v_ticket bigint := pg_temp.id('ticket');
  v_resolved timestamptz;
begin
  perform pg_temp.act_as('fac.admin@caldwell.edu');

  perform pg_temp.check('Department admin was notified of the reopen request',
    exists (select 1 from public.notifications
            where ticket_id = v_ticket and type = 'REOPEN_REQUESTED'));

  perform public.decide_reopen(pg_temp.id('request'), true, 'Sending someone back.');
  select resolved_at into v_resolved from public.tickets where ticket_id = v_ticket;
  perform pg_temp.check('Approved reopen moves the ticket to REOPENED and clears resolved_at',
    pg_temp.status_of(v_ticket) = 'REOPENED' and v_resolved is null, pg_temp.status_of(v_ticket));

  perform public.assign_ticket(v_ticket, pg_temp.uid('fac.admin@caldwell.edu'));
  perform pg_temp.check('Reassignment leaves exactly one active assignment',
    (select count(*) from public.ticket_assignments
      where ticket_id = v_ticket and unassigned_at is null) = 1);
  perform pg_temp.check('Reassignment logs the unassignment',
    exists (select 1 from public.ticket_history where ticket_id = v_ticket and action = 'UNASSIGNED'));

  perform public.transfer_ticket(v_ticket, pg_temp.id('it')::integer, 'Turns out it is the thermostat controller.');
  perform pg_temp.check('After transfer the old department no longer sees the ticket',
    not exists (select 1 from public.tickets where ticket_id = v_ticket));
end $$;

do $$
declare
  v_ticket bigint := pg_temp.id('ticket');
begin
  perform pg_temp.act_as('it.staff@caldwell.edu');
  perform pg_temp.check('The receiving department sees the transferred ticket, unassigned',
    exists (select 1 from public.tickets where ticket_id = v_ticket)
    and not exists (select 1 from public.ticket_assignments
                    where ticket_id = v_ticket and unassigned_at is null));
end $$;

-- ---- Anonymous traffic ----------------------------------------------------

do $$
begin
  reset role;
  perform set_config('request.jwt.claims', '{"role":"anon"}', true);
  set local role anon;
  perform pg_temp.expect_error('Anonymous users cannot read tickets',
    'select count(*) from public.tickets', 'permission denied');
  reset role;
end $$;

-- ---- Auto-close after the reopen window -----------------------------------

do $$
declare
  v_ticket bigint;
begin
  reset role;
  perform set_config('request.jwt.claims', '', true);
  select ticket_id into v_ticket from public.tickets
   where reported_by = pg_temp.uid('student2@caldwell.edu') and ticket_id = pg_temp.id('ticket');
  update public.tickets
     set status_id = pg_temp.id('resolved'), resolved_at = now() - interval '8 days'
   where ticket_id = v_ticket;
  perform private.close_expired_resolved_tickets();
  perform pg_temp.check('Resolved tickets close automatically after 7 days',
                        pg_temp.status_of(v_ticket) = 'CLOSED', pg_temp.status_of(v_ticket));
end $$;

-- ---- Chat ends with the ticket; a new ticket starts a fresh chat ------------

do $$
declare
  v_new bigint;
begin
  perform pg_temp.act_as('student2@caldwell.edu');

  perform pg_temp.expect_error('Nobody can message on a closed ticket',
    format($q$select public.send_ticket_message(%s, 'one more thing')$q$, pg_temp.id('ticket')),
    'closed');

  insert into public.tickets (reported_by, original_text, title, issue_type_id)
  values (pg_temp.uid('student2@caldwell.edu'), 'A second, unrelated problem.',
          'Test second ticket', pg_temp.id('fac_issue'))
  returning ticket_id into v_new;

  perform pg_temp.check('A new ticket starts with an empty chat',
    not exists (select 1 from public.ticket_messages where ticket_id = v_new));
  perform pg_temp.expect_error('A new unassigned ticket has no chat yet',
    format($q$select public.send_ticket_message(%s, 'anyone?')$q$, v_new),
    'once the ticket is assigned');
end $$;

reset role;
select n, case when pass then 'PASS' else 'FAIL' end as result, test, detail
from results order by n;

rollback;
