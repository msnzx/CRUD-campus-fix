-- Removes everything demo_tickets.sql created, and nothing else.
-- Seeded tickets are identified by their DEMO_SEED history row.
--
-- This hard-deletes, which the app never does to real tickets (they use
-- deleted_at). Demo rows are not records anyone needs to audit.

begin;

create temp table demo_ids on commit drop as
  select distinct h.ticket_id, t.location_id
  from public.ticket_history h
  join public.tickets t using (ticket_id)
  where h.action = 'DEMO_SEED';

delete from public.notifications          where ticket_id in (select ticket_id from demo_ids);
delete from public.comments               where ticket_id in (select ticket_id from demo_ids);
delete from public.attachments            where ticket_id in (select ticket_id from demo_ids);
delete from public.ticket_assignments     where ticket_id in (select ticket_id from demo_ids);
delete from public.ticket_reopen_requests where ticket_id in (select ticket_id from demo_ids);
delete from public.ticket_history         where ticket_id in (select ticket_id from demo_ids);
delete from public.tickets                where ticket_id in (select ticket_id from demo_ids);
delete from public.locations l
 where l.location_id in (select location_id from demo_ids)
   and not exists (select 1 from public.tickets t where t.location_id = l.location_id);

select count(*) as demo_tickets_removed from demo_ids;

commit;
