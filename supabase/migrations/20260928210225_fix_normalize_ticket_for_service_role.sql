-- The normalisation trigger must only clamp fields for end users acting
-- through the Data API. Backend callers (service_role, migrations, seed
-- scripts) have no auth.uid() and must be able to write tickets directly,
-- including historical demo data with non-default status and priority.

create or replace function public.normalize_new_ticket()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_new_status integer;
begin
  -- No JWT context: this is a trusted backend caller, not a requester.
  if (select auth.uid()) is null then
    return new;
  end if;

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

revoke execute on function public.normalize_new_ticket() from public, anon, authenticated;
