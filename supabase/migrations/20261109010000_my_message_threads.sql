-- One list of every private chat the caller can see, for the Messages page.
--
-- SECURITY INVOKER on purpose: tickets, ticket_messages and users are read
-- under the caller's own RLS, so this function cannot reveal anything the
-- caller could not already select. private.can_read_ticket_messages repeats
-- the chat's own read rule so staff only get threads they may actually open.
--
-- A thread is listed when it has at least one message, or when the ticket is
-- assigned and not closed (so an assignee can start the conversation).
-- role: REQUESTER (own ticket), ASSIGNEE (active assignee, can post),
--       OBSERVER (department/system admin, read-only).

create or replace function public.my_message_threads()
returns table (
  ticket_id      bigint,
  title          text,
  status         text,
  reported_by    uuid,
  requester_name text,
  assigned_to    uuid,
  role           text,
  last_message   text,
  last_sender    uuid,
  last_at        timestamptz,
  message_count  bigint
)
language sql
stable
security invoker
set search_path = ''
as $$
  select
    t.ticket_id,
    t.title,
    s.name,
    t.reported_by,
    nullif(trim(coalesce(ru.first_name, '') || ' ' || coalesce(ru.last_name, '')), ''),
    a.assigned_to,
    case
      when t.reported_by = (select auth.uid()) then 'REQUESTER'
      when a.assigned_to = (select auth.uid()) then 'ASSIGNEE'
      else 'OBSERVER'
    end,
    lm.message,
    lm.sender_id,
    lm.created_at,
    coalesce(mc.n, 0)
  from public.tickets t
  join public.statuses s on s.status_id = t.status_id
  left join public.users ru on ru.user_id = t.reported_by
  left join public.ticket_assignments a
         on a.ticket_id = t.ticket_id and a.unassigned_at is null
  left join lateral (
    select m.message, m.sender_id, m.created_at
    from public.ticket_messages m
    where m.ticket_id = t.ticket_id
    order by m.created_at desc, m.message_id desc
    limit 1
  ) lm on true
  left join lateral (
    select count(*) as n from public.ticket_messages m where m.ticket_id = t.ticket_id
  ) mc on true
  where t.deleted_at is null
    and private.can_read_ticket_messages(t.ticket_id)
    and (lm.created_at is not null or (a.assigned_to is not null and s.name <> 'CLOSED'))
  order by coalesce(lm.created_at, a.assigned_at) desc, t.ticket_id desc;
$$;

revoke execute on function public.my_message_threads() from public, anon;
grant  execute on function public.my_message_threads() to authenticated;
