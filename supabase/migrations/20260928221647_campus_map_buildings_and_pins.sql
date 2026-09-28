-- Reconcile the building list with the official campus map legend (14 entries),
-- and seed pin positions estimated from that image.
--
-- Positions are percentages of the FULL map image as supplied, legend included.
-- They are estimates read off the artwork, not survey data — a System Admin can
-- drag them right with the Move control on the map page.

-- 1. Renames, done as updates so existing floors survive.
update public.buildings set name = 'Jennings Library' where name = 'Library';
update public.buildings set name = 'Newman Center'    where name = 'Newman Centre';

-- 2. The buildings still missing, including two removed earlier in error.
insert into public.buildings (name) values
  ('Motherhouse'),
  ('Mount St. Dominic Athletic Center'),
  ('Sienna House'),
  ('St. Catherine Convent'),
  ('Student Center')
on conflict (name) do nothing;

-- 3. Floors for anything new.
insert into public.floors (building_id, floor_number)
select b.building_id, f.n
from public.buildings b
cross join (values ('G'), ('1'), ('2'), ('3')) as f(n)
on conflict (building_id, floor_number) do nothing;

-- 4. Pin positions, keyed to the numbered markers in the legend.
update public.buildings as b
   set map_x = v.x, map_y = v.y
from (values
  ('Motherhouse',                       66.7, 44.5),  -- 1
  ('Jennings Library',                  52.4, 26.5),  -- 2
  ('Rosary Hall',                       58.0, 36.9),  -- 3
  ('Mount St. Dominic Athletic Center', 71.9, 39.8),  -- 4
  ('Sienna House',                      66.4, 27.8),  -- 5
  ('Werner Hall',                       55.7, 28.5),  -- 6
  ('St. Catherine Convent',             84.7, 38.4),  -- 7
  ('Raymond Hall',                      60.6, 25.3),  -- 8
  ('Student Center',                    55.3, 15.7),  -- 9
  ('Newman Center',                     48.8, 15.3),  -- 10
  ('Mother Joseph Residence Hall',      25.7, 32.6),  -- 11
  ('Dominican Hall',                    48.1, 41.7),  -- 12
  ('Aquinas Hall',                      66.4, 32.4),  -- 13
  ('Visceglia Hall',                    64.5, 17.0)   -- 14
) as v(name, x, y)
where b.name = v.name;
