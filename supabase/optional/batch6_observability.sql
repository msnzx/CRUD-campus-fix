-- Pending local-only Batch 6 schema. Additive; never changes business workflows.
begin;
create table public.observability_workers (
  instance_id uuid not null,
  worker text not null check (worker in ('classification','safety_retry','notifications','vision')),
  enabled boolean not null,
  state text not null check (state in ('DISABLED','UNKNOWN','STALE','ALIVE','HEALTHY','DEGRADED')),
  progress_at timestamptz,
  observed_at timestamptz not null default clock_timestamp(),
  primary key (instance_id,worker)
);
create index ix_observability_workers_observed on public.observability_workers(observed_at desc);
-- Receipt timestamps come from PostgreSQL, never a worker wall clock.
create function public.observability_receipt_time() returns trigger
language plpgsql security invoker set search_path='' as $$
begin new.observed_at=clock_timestamp(); return new; end;
$$;
revoke all on function public.observability_receipt_time() from public,anon,authenticated;
grant execute on function public.observability_receipt_time() to service_role;
create trigger observability_receipt_time before insert or update on public.observability_workers
for each row execute function public.observability_receipt_time();
alter table public.observability_workers enable row level security;
revoke all on public.observability_workers from public,anon,authenticated;
grant select,insert,update on public.observability_workers to service_role;

create function public.observability_snapshot() returns jsonb
language sql security invoker set search_path='' as $$
 select jsonb_build_object(
 'observed_at',clock_timestamp(),
 'classification',(select jsonb_build_object(
   'pending',count(*) filter(where t.ai_routing_status='PENDING'),
   'failed',count(*) filter(where t.ai_routing_status='FAILED'),
   'due',count(*) filter(where t.created_at<=clock_timestamp()-interval '5 seconds'
                      and (j.ticket_id is null or j.next_attempt_at<=clock_timestamp())
                      and (j.claimed_until is null or j.claimed_until<=clock_timestamp())),
   'leased',count(*) filter(where j.claimed_until>clock_timestamp()),
   'expired_leases',count(*) filter(where j.claimed_until<=clock_timestamp()),
   'backoff',count(*) filter(where j.next_attempt_at>clock_timestamp()),
   'terminal',count(*) filter(where j.ticket_id is not null and j.next_attempt_at is null),
   'oldest_created_at',min(t.created_at))
   from public.tickets t left join public.ticket_ai_jobs j using(ticket_id)
   where t.deleted_at is null and t.ai_routing_status in ('PENDING','FAILED')),
 'safety',(select jsonb_build_object(
   'pending',count(*) filter(where status='PENDING'),
   'failed',count(*) filter(where status='FAILED'),
   'due',count(*) filter(where next_attempt_at<=clock_timestamp()))
   from public.ticket_safety_escalations where status in ('PENDING','FAILED')),
 'email',(select jsonb_build_object(
   'pending',count(*) filter(where delivery_status='PENDING'),
   'failed',count(*) filter(where delivery_status='FAILED'),
   'exhausted',count(*) filter(where attempts>=5),
   'ambiguous',count(*) filter(where email_ambiguous_attempts>0),
   'leased',count(*) filter(where email_claimed_until>clock_timestamp()),
   'expired_leases',count(*) filter(where email_claimed_until<=clock_timestamp()),
   'due',count(*) filter(where attempts<5 and email_claim_token is null and email_next_attempt_at<=clock_timestamp()))
   from public.notifications where delivery_status in ('PENDING','FAILED')),
 'workers',(select coalesce(jsonb_agg(to_jsonb(w)),'[]'::jsonb) from
   (select instance_id,worker,enabled,
      case when not enabled then 'DISABLED'
           when observed_at<clock_timestamp()-interval '60 seconds' then 'STALE' else state end as state,
      progress_at,observed_at from public.observability_workers
      order by observed_at desc,instance_id,worker limit 256) w),
 'workers_truncated',(select count(*)>256 from public.observability_workers));
$$;
revoke all on function public.observability_snapshot() from public,anon,authenticated;
grant execute on function public.observability_snapshot() to service_role;
commit;
