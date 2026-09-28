-- Closes the gaps documented in architecture.md section 7.

-- 7.5 — Maintain updated_at on UPDATE. The column default only fires on INSERT.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger set_updated_at before update on public.users
  for each row execute function public.set_updated_at();
create trigger set_updated_at before update on public.departments
  for each row execute function public.set_updated_at();
create trigger set_updated_at before update on public.tickets
  for each row execute function public.set_updated_at();
create trigger set_updated_at before update on public.comments
  for each row execute function public.set_updated_at();

-- 7.4 — Categories map to a default department. This is the deterministic
--       routing fallback used whenever AI is unavailable or unconfident.
alter table public.issue_types
  add column default_department_id integer references public.departments(department_id);

create index ix_issue_types_default_department
  on public.issue_types(default_department_id);

-- 7.2 — Notification delivery tracking, required by V-032.
alter table public.notifications
  add column delivery_status varchar not null default 'PENDING'
    check (delivery_status in ('PENDING', 'SENT', 'FAILED')),
  add column attempts integer not null default 0 check (attempts >= 0),
  add column last_attempt_at timestamptz,
  add column error_message text;

-- Retry worker scans for pending/failed deliveries only.
create index ix_notifications_pending
  on public.notifications(created_at)
  where delivery_status in ('PENDING', 'FAILED');

-- 7.6 — At most one pending reopen request per ticket, mirroring the
--       existing ux_ticket_one_active_assignment constraint.
create unique index ux_one_pending_reopen
  on public.ticket_reopen_requests(ticket_id)
  where decision = 'PENDING';

-- 7.7 — Emergency submissions must be auditable and reportable,
--       not merely a client-side warning.
alter table public.tickets
  add column is_emergency boolean not null default false;

create index ix_tickets_emergency
  on public.tickets(created_at)
  where is_emergency;

-- Staff queue hot path: "open tickets in my department".
create index ix_tickets_department_status
  on public.tickets(department_id, status_id)
  where deleted_at is null;
