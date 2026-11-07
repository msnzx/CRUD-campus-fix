-- Batch 3 pending additive schema. NOT applied to any shared database.
-- Supabase CLI is unavailable locally: generate a proper migration with the CLI
-- and review it before a future rollout. Tests apply this file only to disposable PG.
-- Service-only shadow state. No ticket/history/notification writes, grants to
-- students/staff, storage policy changes, triggers or scheduling changes.

create table public.ticket_vision_jobs (
  ticket_id bigint primary key references public.tickets(ticket_id),
  generation bigint not null default 1,
  input_snapshot jsonb not null,
  status text not null default 'PENDING' check (status in ('PENDING','PROCESSING','SUCCEEDED','REVIEW','WITHDRAWN')),
  attempts integer not null default 0 check (attempts between 0 and 3),
  claim_token uuid,
  claimed_until timestamptz,
  next_attempt_at timestamptz default now(),
  last_error text,
  check ((claim_token is null) = (claimed_until is null))
);
create index ix_vision_due on public.ticket_vision_jobs(next_attempt_at,ticket_id)
  where next_attempt_at is not null;
create table public.ticket_vision_runs (
  run_id uuid primary key,
  ticket_id bigint not null references public.tickets(ticket_id),
  generation bigint not null,
  attempt integer not null,
  status text not null check (status in ('PROCESSING','SUCCEEDED','REVIEW','RETRY','CRASHED','SUPERSEDED','WITHDRAWN')),
  provider text not null default 'MOCK' check (provider = 'MOCK'),
  provenance jsonb not null,
  findings jsonb,
  error_code text,
  started_at timestamptz not null default clock_timestamp(),
  finished_at timestamptz,
  unique(ticket_id,generation,attempt)
);
alter table public.ticket_vision_jobs enable row level security;
alter table public.ticket_vision_runs enable row level security;
revoke all on public.ticket_vision_jobs,public.ticket_vision_runs from public,anon,authenticated;
grant select,insert,update,delete on public.ticket_vision_jobs,public.ticket_vision_runs to service_role;

create function private.vision_shadow_snapshot(p_ticket bigint) returns jsonb
language sql stable security invoker set search_path='' as $$
  select jsonb_build_object('pipeline_version','vision-shadow-1.1','report_revision',t.ai_report_revision,
    'text_analysis_id',(select last_analysis_id from public.ticket_ai_jobs where ticket_id=t.ticket_id),
    'text_issue_id',t.ai_suggested_issue_type_id,'text_priority_id',t.ai_suggested_priority_id,
    'attachment_count',(select count(*) from public.attachments where ticket_id=t.ticket_id),'attachments',coalesce((
    select jsonb_agg(jsonb_build_object(
      'attachment_id',a.attachment_id,'ticket_id',a.ticket_id,
      'file_url',case when length(a.file_url)<=200 then a.file_url else null end,
      'file_type',case when length(a.file_type)<=32 then a.file_type else null end,
      'file_size_bytes',a.file_size_bytes,'uploaded_by',a.uploaded_by,
      'created_at',a.created_at,'object_id',o.id,'owner_id',o.owner_id,
      'object_revision',encode(sha256(convert_to(jsonb_build_array(o.created_at,to_jsonb(o)->'updated_at',
                                        to_jsonb(o)->'version',to_jsonb(o)->'metadata')::text,'UTF8')),'hex')) order by a.attachment_id)
    from (select * from public.attachments where ticket_id=t.ticket_id order by attachment_id limit 5) a
    left join storage.objects o on o.bucket_id='ticket-attachments' and o.name=a.file_url
  ),'[]'::jsonb)) from public.tickets t where t.ticket_id=p_ticket;
$$;
revoke all on function private.vision_shadow_snapshot(bigint) from public,anon,authenticated;
grant execute on function private.vision_shadow_snapshot(bigint) to service_role;
-- Supabase normally grants service_role storage access; make the dependency explicit.
grant select on storage.objects to service_role;

create function public.vision_shadow_discover(p_after bigint default 0,p_limit integer default 50)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare t record; v jsonb; j public.ticket_vision_jobs; n bigint:=p_after;
begin
  if p_after is null or p_after<0 or p_limit is null or p_limit not between 1 and 100 then
    raise exception 'Invalid discovery bounds';
  end if;
  -- Include existing jobs so removed final attachments and withdrawals invalidate work.
  -- Lock order: ticket, job, run. Cursor advances over locked rows too; wrap next pass.
  for t in select x.ticket_id from public.tickets x where x.ticket_id>p_after and
      (exists(select 1 from public.attachments a where a.ticket_id=x.ticket_id)
       or exists(select 1 from public.ticket_vision_jobs q where q.ticket_id=x.ticket_id))
      order by x.ticket_id limit p_limit
  loop
    n:=t.ticket_id;
    perform 1 from public.tickets where ticket_id=t.ticket_id for update skip locked;
    if not found then continue; end if;
    v:=private.vision_shadow_snapshot(t.ticket_id);
    insert into public.ticket_vision_jobs(ticket_id,input_snapshot) values(t.ticket_id,v)
      on conflict(ticket_id) do nothing;
    select * into j from public.ticket_vision_jobs where ticket_id=t.ticket_id for update;
    if (select deleted_at is not null from public.tickets where ticket_id=t.ticket_id) then
      update public.ticket_vision_runs set status='WITHDRAWN',finished_at=clock_timestamp()
        where run_id=j.claim_token and status='PROCESSING';
      update public.ticket_vision_jobs set status='WITHDRAWN',claim_token=null,claimed_until=null,next_attempt_at=null
        where ticket_id=t.ticket_id;
    elsif j.input_snapshot is distinct from v then
      update public.ticket_vision_runs set status='SUPERSEDED',finished_at=clock_timestamp()
        where run_id=j.claim_token and status='PROCESSING';
      update public.ticket_vision_jobs set generation=generation+1,input_snapshot=v,status='PENDING',attempts=0,
        claim_token=null,claimed_until=null,next_attempt_at=clock_timestamp(),last_error=null where ticket_id=t.ticket_id;
    end if;
  end loop;
  return jsonb_build_object('cursor',case when n=p_after then 0 else n end);
end;
$$;

create function public.vision_shadow_claim(p_worker text,p_lease_seconds integer,p_provenance jsonb)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare t public.tickets; j public.ticket_vision_jobs; q record; token uuid:=gen_random_uuid(); v jsonb;
begin
  if p_worker is null or length(p_worker) not between 1 and 128 or p_lease_seconds is null or
     p_lease_seconds not between 1 and 300 or p_provenance->>'provider' is distinct from 'MOCK'
     or p_provenance->>'pipeline_version' is distinct from 'vision-shadow-1.1' then
    raise exception 'Invalid shadow claim';
  end if;
  for q in select x.ticket_id from public.ticket_vision_jobs x where
      (x.status='PENDING' or (x.status='PROCESSING' and x.claimed_until<=clock_timestamp()))
      and x.next_attempt_at<=clock_timestamp() order by x.next_attempt_at,x.ticket_id
  loop
    select * into t from public.tickets where ticket_id=q.ticket_id for update skip locked;
    if not found then continue; end if;
    select * into j from public.ticket_vision_jobs where ticket_id=q.ticket_id for update skip locked;
    if not found or j.next_attempt_at is null or j.next_attempt_at>clock_timestamp()
       or (j.claim_token is not null and j.claimed_until>clock_timestamp())
       or j.status not in ('PENDING','PROCESSING') then continue; end if;
    if t.deleted_at is not null then
      update public.ticket_vision_runs set status='WITHDRAWN',finished_at=clock_timestamp()
        where run_id=j.claim_token and status='PROCESSING';
      update public.ticket_vision_jobs set status='WITHDRAWN',claim_token=null,claimed_until=null,next_attempt_at=null where ticket_id=t.ticket_id;
      continue;
    end if;
    v:=private.vision_shadow_snapshot(t.ticket_id);
    if v is distinct from j.input_snapshot then
      -- Refresh on discovery; never execute stale inputs or block the next job.
      continue;
    end if;
    if j.claim_token is not null then
      update public.ticket_vision_runs set status='CRASHED',error_code='LEASE_EXPIRED',finished_at=clock_timestamp()
        where run_id=j.claim_token and status='PROCESSING';
      update public.ticket_vision_jobs set status=case when attempts>=3 then 'REVIEW' else 'PENDING' end,
        claim_token=null,claimed_until=null,last_error='LEASE_EXPIRED',
        next_attempt_at=case when attempts>=3 then null else clock_timestamp()+interval '1 second'*power(2,attempts) end
        where ticket_id=t.ticket_id;
      continue; -- Crash recovery observes the same bounded backoff as a retry.
    end if;
    if j.attempts>=3 then
      update public.ticket_vision_jobs set status='REVIEW',next_attempt_at=null,last_error='ATTEMPTS_EXHAUSTED' where ticket_id=t.ticket_id;
      continue;
    end if;
    update public.ticket_vision_jobs set status='PROCESSING',attempts=attempts+1,claim_token=token,
      claimed_until=clock_timestamp()+make_interval(secs=>p_lease_seconds) where ticket_id=t.ticket_id;
    insert into public.ticket_vision_runs(run_id,ticket_id,generation,attempt,status,provenance)
      values(token,t.ticket_id,j.generation,j.attempts+1,'PROCESSING',p_provenance||jsonb_build_object(
        'worker',p_worker,'input_sha256',encode(sha256(convert_to(v::text,'UTF8')),'hex'),
        'report_revision',t.ai_report_revision,'ticket_revision',t.ai_revision));
    return jsonb_build_object('claimed',true,'ticket_id',t.ticket_id,'token',token,'generation',j.generation,
      'attachments',v->'attachments','text',left(coalesce(t.original_title,t.title,'')||E'\n'||coalesce(t.original_text,t.description,''),16001),
      'text_issue',(select name from public.issue_types where issue_type_id=t.ai_suggested_issue_type_id),
      'text_urgent',coalesce((select name='URGENT' from public.priorities where priority_id=t.ai_suggested_priority_id),false));
  end loop;
  return jsonb_build_object('claimed',false);
end;
$$;

create function public.vision_shadow_finish(p_ticket bigint,p_token uuid,p_findings jsonb,p_provenance jsonb,
                                         p_error text default null,p_retry boolean default false)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare t public.tickets; j public.ticket_vision_jobs; s text; v jsonb;
begin
  select * into t from public.tickets where ticket_id=p_ticket for update;
  select * into j from public.ticket_vision_jobs where ticket_id=p_ticket for update;
  if not found or p_token is null or j.claim_token is distinct from p_token or j.claimed_until<=clock_timestamp() then
    return jsonb_build_object('result','LEASE_LOST');
  end if;
  if p_provenance is null or jsonb_typeof(p_provenance)<>'object'
     or p_provenance->>'provider' is distinct from 'MOCK' or octet_length(p_provenance::text)>16000 then
    raise exception 'Invalid mock provenance';
  end if;
  if t.deleted_at is not null then s:='WITHDRAWN';
  elsif private.vision_shadow_snapshot(p_ticket) is distinct from j.input_snapshot then s:='SUPERSEDED';
  elsif p_error is not null then
    if p_error !~ '^[A-Z_]{2,64}$' then raise exception 'Invalid error code'; end if;
    s:=case when coalesce(p_retry,false) and j.attempts<3 then 'RETRY' else 'REVIEW' end;
  else
    if p_findings is null or jsonb_typeof(p_findings)<>'object' or
       p_provenance->>'provider' is distinct from 'MOCK' or octet_length(p_findings::text)>16000
       or octet_length(p_provenance::text)>16000 then raise exception 'Invalid mock findings'; end if;
    s:='SUCCEEDED';
  end if;
  update public.ticket_vision_runs set status=s,finished_at=clock_timestamp(),error_code=p_error,
    findings=case when s='SUCCEEDED' then p_findings else null end,
    provenance=provenance||p_provenance
    where run_id=p_token and status='PROCESSING';
  update public.ticket_vision_jobs set status=case when s in ('SUPERSEDED','RETRY') then 'PENDING' else s end,
    claim_token=null,claimed_until=null,last_error=case when s='SUPERSEDED' then 'INPUT_CHANGED' else p_error end,
    next_attempt_at=case when s in ('SUPERSEDED','RETRY') then clock_timestamp()+interval '1 second'*power(2,attempts) else null end
    where ticket_id=p_ticket;
  return jsonb_build_object('result',s);
end;
$$;

revoke all on function public.vision_shadow_discover(bigint,integer) from public,anon,authenticated;
revoke all on function public.vision_shadow_claim(text,integer,jsonb) from public,anon,authenticated;
revoke all on function public.vision_shadow_finish(bigint,uuid,jsonb,jsonb,text,boolean) from public,anon,authenticated;
grant execute on function public.vision_shadow_discover(bigint,integer) to service_role;
grant execute on function public.vision_shadow_claim(text,integer,jsonb) to service_role;
grant execute on function public.vision_shadow_finish(bigint,uuid,jsonb,jsonb,text,boolean) to service_role;
