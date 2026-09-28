-- Completes the seed set. roles, statuses, priorities and departments
-- were already seeded with the original schema.

insert into public.area_types (name) values
  ('Classroom'), ('Office'), ('Restroom'), ('Hallway'), ('Stairwell'),
  ('Elevator'), ('Residence Room'), ('Common Area'), ('Kitchen / Dining'),
  ('Laboratory'), ('Library'), ('Gymnasium'), ('Parking Lot'),
  ('Walkway / Exterior'), ('Other')
on conflict (name) do nothing;

-- student_types describes the requester population, for analytics
-- segmentation. It is optional on users.
insert into public.student_types (name) values
  ('Undergraduate'), ('Graduate'), ('Faculty'), ('Staff'), ('Other')
on conflict (name) do nothing;

-- Issue types, each mapped to the department that owns it by default.
-- Departments are looked up by name rather than by generated id.
insert into public.issue_types (name, description, default_department_id)
select v.name, v.description, d.department_id
from (values
  ('Plumbing',               'Leaks, clogs, water damage, fixtures',        'Facilities'),
  ('Electrical',             'Outlets, wiring, power loss',                 'Facilities'),
  ('Heating / Cooling',      'HVAC, temperature, ventilation',              'Facilities'),
  ('Lighting',               'Burnt-out or flickering lights',              'Facilities'),
  ('Furniture',              'Broken or missing desks, chairs, fixtures',   'Facilities'),
  ('Door / Lock',            'Doors, locks, access hardware',               'Facilities'),
  ('Window',                 'Broken, stuck, or leaking windows',           'Facilities'),
  ('Structural',             'Ceilings, floors, walls, stairs',             'Facilities'),
  ('Cleaning / Custodial',   'Spills, trash, sanitation',                   'Facilities'),
  ('Pest Control',           'Insects, rodents, infestations',              'Facilities'),
  ('Elevator',               'Elevator faults or outages',                  'Facilities'),
  ('Wi-Fi / Network',        'Connectivity and network access',             'IT'),
  ('Classroom Technology',   'Projectors, displays, podium equipment',      'IT'),
  ('Computer / Hardware',    'Desktops, laptops, peripherals',              'IT'),
  ('Account / Login',        'Passwords, access, authentication',           'IT'),
  ('Software',               'Applications and licensing',                  'IT'),
  ('Printing',               'Printers, copiers, print quotas',             'IT'),
  ('Residence Maintenance',  'Residence hall repairs and upkeep',           'Residence Life'),
  ('Housing / Roommate',     'Room assignments and housing concerns',       'Residence Life'),
  ('Laundry',                'Washers and dryers',                          'Residence Life'),
  ('Event Support',          'Setup and support for campus events',         'Student Engagement'),
  ('Club / Organization',    'Student organization requests',               'Student Engagement'),
  ('Parking',                'Parking permits, spaces, violations',         'Campus Safety'),
  ('ID Card',                'Campus ID issuance and access',               'Campus Safety'),
  ('Safety Hazard',          'Unsafe conditions requiring attention',       'Campus Safety'),
  ('Lost and Found',         'Lost or recovered property',                  'Campus Safety'),
  ('Security Concern',       'Non-emergency security matters',              'Campus Safety'),
  ('Dining Service',         'Food quality, service, dining facilities',    'Dining'),
  ('Meal Plan',              'Meal plan and swipe issues',                  'Dining'),
  ('Vending',                'Vending machines',                            'Dining'),
  ('Billing',                'Tuition, fees, payments',                     'Finance'),
  ('Financial Aid',          'Aid, scholarships, disbursement',             'Finance'),
  ('Other',                  'Does not fit an existing category',           'Other / Review')
) as v(name, description, dept_name)
join public.departments d on d.name = v.dept_name
on conflict (name) do nothing;

-- PLACEHOLDER BUILDINGS. Replace these with the real Caldwell University
-- building list before demo data is generated.
insert into public.buildings (name) values
  ('Mother Joseph Residence Hall'),
  ('Student Center'),
  ('Library'),
  ('Science Building'),
  ('Administration Building'),
  ('Athletic Center')
on conflict (name) do nothing;

-- Floors 1-3 plus a ground floor for every building.
insert into public.floors (building_id, floor_number)
select b.building_id, f.n
from public.buildings b
cross join (values ('G'), ('1'), ('2'), ('3')) as f(n)
on conflict (building_id, floor_number) do nothing;
