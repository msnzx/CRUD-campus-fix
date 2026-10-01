-- Requesters must be able to record where an issue is.
--
-- The submission form has always tried to insert a location, but there was no
-- INSERT grant or policy, so every attempt failed and the error was swallowed.
-- Every ticket submitted so far therefore has location_id NULL, which is why
-- the campus map shows nothing.

grant insert on public.locations to authenticated;
grant usage, select on sequence public.locations_location_id_seq to authenticated;

create policy create_location on public.locations
  for insert to authenticated
  with check (true);

-- Staff may correct a location after the fact; requesters may not.
grant update on public.locations to authenticated;

create policy staff_update_location on public.locations
  for update to authenticated
  using (private.is_staff())
  with check (private.is_staff());

-- A building-level location with no known floor.
--
-- The hierarchy is buildings -> floors -> locations, so a location can only
-- reach its building through a floor. Without a row like this, "somewhere in
-- Werner Hall, floor unknown" cannot be stored at all and the building is
-- silently discarded. This keeps the hierarchy intact and honest rather than
-- reintroducing a redundant locations.building_id that could contradict it.
insert into public.floors (building_id, floor_number)
select b.building_id, 'Unspecified'
from public.buildings b
on conflict (building_id, floor_number) do nothing;
