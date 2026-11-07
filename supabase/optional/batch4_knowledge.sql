-- Pending additive Batch 4 schema. CLI-generated migration required before rollout.
-- Only disposable local tests apply this file. No production documents seeded.
create schema if not exists extensions;
create extension if not exists vector with schema extensions;
grant usage on schema extensions to authenticated,service_role;

create table public.kb_documents (
  document_id uuid primary key,
  version integer not null check(version>0),
  title text not null check(length(title) between 1 and 200),
  source_uri text not null check(length(source_uri) between 1 and 400),
  source_sha256 text not null check(source_sha256 ~ '^[a-f0-9]{64}$'),
  origin text not null check(origin in ('SYNTHETIC','APPROVED_REAL')),
  approval_ref text,
  publication text not null check(publication in ('PUBLISHED','DRAFT','WITHDRAWN')),
  kind text not null check(kind in ('POLICY','AVAILABILITY')),
  audience text not null check(audience in ('CAMPUS','STAFF','DEPARTMENT')),
  department_id integer references public.departments(department_id),
  effective_at timestamptz not null,
  expires_at timestamptz not null check(expires_at>effective_at),
  check(audience<>'DEPARTMENT' or department_id is not null),
  check(kind<>'AVAILABILITY' or department_id is not null),
  check(origin<>'APPROVED_REAL' or (approval_ref is not null and length(approval_ref)>0)),
  check(origin<>'SYNTHETIC' or (title like 'SYNTHETIC FIXTURE:%' and source_uri like 'campusfix-fixture://%'))
);
create unique index ux_kb_published_source on public.kb_documents(source_uri) where publication='PUBLISHED';
create table public.kb_chunks (
  chunk_id uuid primary key,
  document_id uuid not null references public.kb_documents(document_id) on delete cascade,
  ordinal integer not null check(ordinal>=0),
  section text not null check(length(section) between 1 and 100),
  content text not null check(length(content) between 1 and 1600),
  content_sha256 text not null check(content_sha256 ~ '^[a-f0-9]{64}$'),
  embedding extensions.vector(64) not null,
  embedding_model text not null check(length(embedding_model) between 1 and 100),
  embedding_revision text not null check(length(embedding_revision) between 1 and 100),
  quarantined boolean not null default false,
  search_vector tsvector generated always as (to_tsvector('english'::regconfig,content)) stored,
  unique(document_id,ordinal),
  check(extensions.vector_norm(embedding)>0),
  check(content_sha256=encode(sha256(convert_to(content,'UTF8')),'hex'))
);
create index ix_kb_chunks_document on public.kb_chunks(document_id);
create index ix_kb_chunks_fts on public.kb_chunks using gin(search_vector);
create index ix_kb_documents_scope on public.kb_documents(publication,audience,department_id,expires_at);
-- Exact vector distance over a materialized authorized set is intentional.
-- Shared ANN filtering can lose allowed neighbors; 0.6.2 lacks iterative scans.

create function private.kb_active() returns boolean language sql stable security invoker set search_path='' as $$
  select coalesce((select auth.uid()) is not null and private.current_role_name() in
    ('STUDENT','DEPARTMENT_STAFF','DEPARTMENT_ADMIN','SYSTEM_ADMIN'),false);
$$;
create function private.kb_audience(p_audience text,p_department integer) returns boolean
language sql stable security invoker set search_path='' as $$
  select coalesce(private.kb_active() and (
    p_audience='CAMPUS' or private.current_role_name()='SYSTEM_ADMIN' or
    (private.current_role_name() in ('DEPARTMENT_STAFF','DEPARTMENT_ADMIN') and (
      p_audience='STAFF' or (p_audience='DEPARTMENT' and exists(
        select 1 from public.user_departments ud join public.departments d using(department_id)
        where ud.user_id=(select auth.uid()) and ud.department_id=p_department and d.active))
    ))),false);
$$;
create function private.kb_ticket_allowed(p_ticket bigint) returns boolean
language sql stable security invoker set search_path='' as $$
  select coalesce(private.kb_active() and exists(select 1 from public.tickets t where t.ticket_id=p_ticket
    and t.deleted_at is null and (t.reported_by=(select auth.uid()) or
      private.current_role_name()='SYSTEM_ADMIN' or
      (t.department_id is not null and private.current_role_name() in ('DEPARTMENT_STAFF','DEPARTMENT_ADMIN')
        and exists(select 1 from public.user_departments ud join public.departments d using(department_id)
          where ud.user_id=(select auth.uid()) and ud.department_id=t.department_id and d.active)))),false);
$$;
revoke all on function private.kb_active(),private.kb_audience(text,integer),private.kb_ticket_allowed(bigint) from public,anon;
grant execute on function private.kb_active(),private.kb_audience(text,integer),private.kb_ticket_allowed(bigint) to authenticated,service_role;

alter table public.kb_documents enable row level security;
alter table public.kb_chunks enable row level security;
revoke all on public.kb_documents,public.kb_chunks from public,anon,authenticated;
grant select on public.kb_documents,public.kb_chunks to authenticated;
grant all on public.kb_documents,public.kb_chunks to service_role;
create policy kb_read_documents on public.kb_documents for select to authenticated using(
  private.kb_audience(audience,department_id) and publication='PUBLISHED'
  and effective_at<=statement_timestamp() and expires_at>statement_timestamp()
  and (kind<>'AVAILABILITY' or exists(select 1 from public.departments d where d.department_id=kb_documents.department_id and d.active)));
create policy kb_read_chunks on public.kb_chunks for select to authenticated using(
  not quarantined and exists(select 1 from public.kb_documents d where d.document_id=kb_chunks.document_id));

create function public.kb_search(p_query text,p_vector extensions.vector,p_model text,p_revision text,
  p_kind text default 'POLICY',p_department integer default null,p_limit integer default 20,p_mode text default 'HYBRID')
returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare result jsonb;
begin
  if p_query is null or length(p_query) not between 1 and 500 or p_limit is null or p_limit not between 1 and 20
    or p_kind is null or p_kind not in ('POLICY','AVAILABILITY') or p_vector is null
    or extensions.vector_dims(p_vector)<>64 or extensions.vector_norm(p_vector)<=0
    or p_model is null or p_revision is null or p_mode is null or p_mode not in ('HYBRID','LEXICAL','VECTOR')
    then raise exception 'Invalid knowledge search bounds'; end if;
  if not private.kb_active() then return '[]'::jsonb; end if;
  with allowed as materialized (
    select c.*,d.version,d.title,d.source_uri,d.source_sha256,d.origin,d.kind,d.audience,d.department_id,d.expires_at
    from public.kb_chunks c join public.kb_documents d using(document_id)
    where private.kb_audience(d.audience,d.department_id) and d.publication='PUBLISHED'
      and d.effective_at<=statement_timestamp() and d.expires_at>statement_timestamp() and not c.quarantined
      and d.kind=p_kind and (p_department is null or d.department_id=p_department)
      and c.embedding_model=p_model and c.embedding_revision=p_revision limit 2001
  ), lexical as (
    select chunk_id,row_number() over(order by ts_rank_cd(search_vector,websearch_to_tsquery('english',p_query)) desc,chunk_id) r
    from allowed where search_vector@@websearch_to_tsquery('english',p_query) limit 20
  ), semantic as (
    select chunk_id,row_number() over(order by embedding operator(extensions.<=>) p_vector,chunk_id) r,
      1-(embedding operator(extensions.<=>) p_vector) similarity
    from allowed order by embedding operator(extensions.<=>) p_vector,chunk_id limit 20
  ), fused as (
    select coalesce(l.chunk_id,s.chunk_id) chunk_id,
      case when p_mode='VECTOR' then 0 else coalesce(1.0/(60+l.r),0) end +
      case when p_mode='LEXICAL' then 0 else coalesce(1.0/(60+s.r),0) end hybrid_score,s.similarity
    from lexical l full join semantic s using(chunk_id) where (select count(*) from allowed)<=2000
      and (p_mode<>'LEXICAL' or l.chunk_id is not null) and (p_mode<>'VECTOR' or s.chunk_id is not null)
  ), ranked as (
    select a.*,f.hybrid_score,f.similarity from fused f join allowed a using(chunk_id)
    order by f.hybrid_score desc,a.chunk_id limit p_limit
  ) select coalesce(jsonb_agg(jsonb_build_object(
    'chunk_id',chunk_id,'document_id',document_id,'version',version,'title',title,'source_uri',source_uri,
    'source_sha256',source_sha256,'content_sha256',content_sha256,'section',section,'content',content,
    'origin',origin,'kind',kind,'expires_at',expires_at,'hybrid_score',hybrid_score,'similarity',similarity)
    order by hybrid_score desc,chunk_id),'[]'::jsonb) into result from ranked;
  return result;
end;
$$;

-- Revalidate every selected citation against fresh RLS/publication/version state.
create function public.kb_read_chunks(p_ids uuid[]) returns jsonb
language plpgsql stable security invoker set search_path='' as $$
begin
  if p_ids is null or cardinality(p_ids)>20 then raise exception 'Invalid citation bounds'; end if;
  if not private.kb_active() then return '[]'::jsonb; end if;
  return (select coalesce(jsonb_agg(jsonb_build_object('chunk_id',c.chunk_id,'document_id',d.document_id,
    'version',d.version,'title',d.title,'source_uri',d.source_uri,'source_sha256',d.source_sha256,
    'content_sha256',c.content_sha256,'section',c.section,'origin',d.origin,'expires_at',d.expires_at)),'[]'::jsonb)
    from public.kb_chunks c join public.kb_documents d using(document_id) where c.chunk_id=any(p_ids)
      and not c.quarantined and d.publication='PUBLISHED' and d.effective_at<=statement_timestamp()
      and d.expires_at>statement_timestamp() and private.kb_audience(d.audience,d.department_id));
end;
$$;
create function public.kb_departments(p_query text,p_limit integer default 5) returns jsonb
language plpgsql stable security invoker set search_path='' as $$
begin
  if p_query is null or length(p_query) not between 1 and 100 or p_limit is null or p_limit not between 1 and 5 then
    raise exception 'Invalid department bounds'; end if;
  if not private.kb_active() then return '[]'::jsonb; end if;
  return (select coalesce(jsonb_agg(jsonb_build_object('department_id',department_id,'name',name)),'[]'::jsonb)
    from (select department_id,name from public.departments where active and strpos(lower(name),lower(p_query))>0
      order by department_id limit p_limit) x);
end;
$$;
create function public.kb_ticket_history(p_ticket bigint,p_limit integer default 10) returns jsonb
language plpgsql stable security invoker set search_path='' as $$
begin
  if p_ticket is null or p_ticket<=0 or p_limit is null or p_limit not between 1 and 10 then
    raise exception 'Invalid history bounds'; end if;
  if not private.kb_ticket_allowed(p_ticket) then return '[]'::jsonb; end if;
  -- No names, old/new values, report text, comments, resolution prose or internal notes.
  return (select coalesce(jsonb_agg(jsonb_build_object('history_id',history_id,'action',action,
    'field_name',field_name,'created_at',created_at) order by history_id desc),'[]'::jsonb)
    from (select history_id,action,case when field_name in ('status_id','priority_id','department_id','ai_routing_status')
      then field_name else null end field_name,created_at from public.ticket_history where ticket_id=p_ticket
      and action in ('CREATED','STATUS_CHANGED','PRIORITY_CHANGED','DEPARTMENT_CHANGED','ASSIGNED','RESOLVED','WITHDRAWN')
      order by history_id desc limit p_limit) x);
end;
$$;

revoke all on function public.kb_search(text,extensions.vector,text,text,text,integer,integer,text),
 public.kb_read_chunks(uuid[]),public.kb_departments(text,integer),public.kb_ticket_history(bigint,integer)
 from public,anon,service_role;
grant execute on function public.kb_search(text,extensions.vector,text,text,text,integer,integer,text),
 public.kb_read_chunks(uuid[]),public.kb_departments(text,integer),public.kb_ticket_history(bigint,integer)
 to authenticated;
