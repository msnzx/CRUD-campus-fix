-- Campus map positions.
--
-- The map is a static campus image rather than a geographic tile layer, so a
-- building's pin is stored as a percentage of the image's width and height
-- rather than as latitude/longitude. Percentages keep the pins correct at any
-- rendered size and on any screen, and survive the image being re-exported at
-- a different resolution.
--
-- latitude/longitude stay on the table, unused for now. They are what a real
-- OpenStreetMap layer would need later; nothing reads them today.

alter table public.buildings
  add column if not exists map_x numeric
    check (map_x is null or map_x between 0 and 100),
  add column if not exists map_y numeric
    check (map_y is null or map_y between 0 and 100);

comment on column public.buildings.map_x is
  'Pin position as a percent of campus map image width (0-100). NULL = unplaced.';
comment on column public.buildings.map_y is
  'Pin position as a percent of campus map image height (0-100). NULL = unplaced.';

-- Only a system admin may move pins. Everyone signed in can read them, which
-- the existing read_buildings policy already allows.
create policy admin_update_building_map on public.buildings
  for update to authenticated
  using (private.is_system_admin())
  with check (private.is_system_admin());

grant update (map_x, map_y) on public.buildings to authenticated;
