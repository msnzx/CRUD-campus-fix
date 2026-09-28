-- Replace the placeholder building list with the real Caldwell University
-- buildings, supplied by the project owner.

-- The campus map needs a coordinate per building. locations already carries
-- lat/long for a precise spot; this is the building's own pin, used when a
-- ticket has no finer location than "somewhere in Werner Hall".
alter table public.buildings
  add column if not exists latitude  numeric
    check (latitude is null or latitude between -90 and 90),
  add column if not exists longitude numeric
    check (longitude is null or longitude between -180 and 180);

comment on column public.buildings.latitude is
  'Building centroid. NULL until surveyed; the map skips buildings without one.';

-- Remove placeholders. Safe: no location references any of their floors.
delete from public.floors
 where building_id in (
   select building_id from public.buildings
    where name in ('Student Center', 'Science Building',
                   'Administration Building', 'Athletic Center')
 );

delete from public.buildings
 where name in ('Student Center', 'Science Building',
                'Administration Building', 'Athletic Center');

-- The real list. Library and Mother Joseph Residence Hall already exist and
-- are left alone so their floors survive.
insert into public.buildings (name) values
  ('Visceglia Hall'),
  ('Werner Hall'),
  ('Raymond Hall'),
  ('Aquinas Hall'),
  ('Rosary Hall'),
  ('Dominican Hall'),
  ('Newman Centre')
on conflict (name) do nothing;

-- Ground floor plus three upper floors for every building that has none yet.
insert into public.floors (building_id, floor_number)
select b.building_id, f.n
from public.buildings b
cross join (values ('G'), ('1'), ('2'), ('3')) as f(n)
on conflict (building_id, floor_number) do nothing;
