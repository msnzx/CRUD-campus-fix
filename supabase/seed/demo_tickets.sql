-- Demo data for the final presentation: ~300 tickets over the last four months.
--
-- Run in the Supabase SQL editor (it runs as postgres, so RLS and the
-- requester-clamping trigger do not interfere). Undo with
-- demo_tickets_cleanup.sql. Every seeded ticket carries a DEMO_SEED history
-- row, which is how the cleanup finds them; real tickets are never touched.
--
-- Requesters are the STUDENT accounts; assignees are active staff of the
-- owning department, falling back to a system admin for departments with no
-- staff yet. AI fields are left empty: routing comes from each issue type's
-- default department, which is what the MVP actually does.
--
-- Change v_count below for more or fewer tickets (the spec's performance
-- check, V-§18, uses 1,000).

do $$
declare
  v_count     integer := 300;
  i           integer;
  v_students  uuid[] := array(
    select u.user_id from public.users u join public.roles r using (role_id)
    where u.active and r.name = 'STUDENT');
  v_fallback  uuid := (
    select u.user_id from public.users u join public.roles r using (role_id)
    where u.active and r.name = 'SYSTEM_ADMIN' limit 1);
  v_samples   jsonb := '{
    "Plumbing": ["Leaking faucet in bathroom", "Toilet will not stop running", "Shower drain clogged", "Water pooling under sink"],
    "Heating / Cooling": ["Radiator banging all night", "Room is freezing, heat not working", "AC blowing warm air", "Thermostat stuck on high"],
    "Electrical": ["Outlet by the desk is dead", "Breaker keeps tripping", "Sparking light switch"],
    "Lighting": ["Hallway light flickering", "Stairwell lights out", "Classroom projector bulb out", "Parking lot light not working"],
    "Door / Lock": ["Door will not latch", "Card reader not reading ID", "Lock sticks, hard to open"],
    "Elevator": ["Elevator stuck on second floor", "Elevator doors closing too fast"],
    "Furniture": ["Broken desk chair", "Bed frame slat snapped", "Wobbly table in study room"],
    "Window": ["Window will not close", "Cracked window pane", "Window blind broken"],
    "Cleaning / Custodial": ["Spill in the lounge needs cleanup", "Trash not collected this week", "Bathroom out of paper towels"],
    "Pest Control": ["Mice in the kitchen area", "Ants near the vending machines"],
    "Structural": ["Ceiling tile stained and sagging", "Loose handrail on stairs", "Crack in the wall getting bigger"],
    "Wi-Fi / Network": ["Wi-Fi drops every few minutes", "No network in the lab", "Ethernet port not working"],
    "Printing": ["Library printer jammed", "Cannot connect to campus printer", "Printer out of toner"],
    "Classroom Technology": ["Projector will not turn on", "HDMI at the podium not working", "Classroom speakers crackling"],
    "Computer / Hardware": ["Lab computer will not boot", "Keyboard missing keys in lab"],
    "Software": ["Cannot install required course software", "Blackboard app crashing"],
    "Account / Login": ["Locked out of campus email", "Password reset not arriving"],
    "Laundry": ["Washer not draining", "Dryer takes payment but will not start"],
    "Residence Maintenance": ["Closet door off its track", "Mold spot in the bathroom"],
    "Housing / Roommate": ["Request to discuss room change"],
    "Parking": ["Car blocking the fire lane", "Parking permit not scanning"],
    "Safety Hazard": ["Ice on the walkway by the entrance", "Exposed wiring near stairwell"],
    "Security Concern": ["Propped-open exterior door at night"],
    "Lost and Found": ["Lost water bottle in the gym"],
    "ID Card": ["ID card stopped working at doors"],
    "Dining Service": ["Dining hall ran out of vegetarian option", "Microwave in the cafe broken"],
    "Vending": ["Vending machine took my money"],
    "Meal Plan": ["Meal swipe charged twice"],
    "Event Support": ["Need extra chairs for club event"],
    "Club / Organization": ["Club room projector request"],
    "Billing": ["Question about lab fee on my bill"],
    "Financial Aid": ["Aid disbursement question"],
    "Other": ["Something else needs attention"]
  }';
  v_notes     text[] := array[
    'Replaced the faulty part and tested.', 'Adjusted and confirmed working with the requester.',
    'Cleaned and restocked.', 'Reset the equipment; working normally now.',
    'Vendor repair completed.', 'Tightened and secured.', 'Replaced bulb and ballast.'];
  v_type      record;
  v_titles    jsonb;
  v_title     text;
  v_created   timestamptz;
  v_age       interval;
  v_status    text;
  v_r         double precision;
  v_resolved  timestamptz;
  v_closed    timestamptz;
  v_floor     integer;
  v_location  integer;
  v_priority  integer;
  v_ticket    bigint;
  v_staff     uuid;
begin
  if coalesce(array_length(v_students, 1), 0) = 0 then
    raise exception 'No active STUDENT accounts to act as requesters. Create one first.';
  end if;

  for i in 1..v_count loop
    v_staff := null;
    select * into v_type from public.issue_types order by random() limit 1;
    v_titles := coalesce(v_samples -> v_type.name, '["General issue"]'::jsonb);
    v_title  := v_titles ->> floor(random() * jsonb_array_length(v_titles))::int;

    -- Skewed toward recent weeks, like real traffic.
    v_created := now() - (power(random(), 1.6) * interval '120 days');
    v_age := now() - v_created;
    v_r := random();

    if v_age > interval '10 days' then
      v_status := case when v_r < 0.80 then 'CLOSED' when v_r < 0.90 then 'IN_PROGRESS'
                       when v_r < 0.95 then 'WAITING_FOR_USER' else 'ASSIGNED' end;
    elsif v_age > interval '2 days' then
      v_status := case when v_r < 0.35 then 'RESOLVED' when v_r < 0.65 then 'IN_PROGRESS'
                       when v_r < 0.85 then 'ASSIGNED' else 'NEEDS_REVIEW' end;
    else
      v_status := case when v_r < 0.40 then 'NEW' when v_r < 0.70 then 'ASSIGNED'
                       when v_r < 0.90 then 'IN_PROGRESS' else 'NEEDS_REVIEW' end;
    end if;

    v_resolved := null;
    v_closed := null;
    if v_status in ('RESOLVED', 'CLOSED') then
      v_resolved := least(now(), v_created + interval '2 hours' + random() * interval '6 days');
      if v_status = 'CLOSED' then
        v_closed := least(now(), v_resolved + interval '7 days');
      elsif v_resolved < now() - interval '7 days' then
        v_status := 'CLOSED';
        v_closed := v_resolved + interval '7 days';
      end if;
    end if;

    select floor_id into v_floor from public.floors order by random() limit 1;
    insert into public.locations (floor_id, area_type_id, room_number)
    values (
      v_floor,
      (select area_type_id from public.area_types order by random() limit 1),
      case when random() < 0.6 then (100 + floor(random() * 300))::int::text end)
    returning location_id into v_location;

    v_r := random();
    select priority_id into v_priority from public.priorities
     where name = case when v_r < 0.30 then 'LOW' when v_r < 0.75 then 'MEDIUM'
                       when v_r < 0.95 then 'HIGH' else 'URGENT' end;

    insert into public.tickets (
      reported_by, original_text, title, description, issue_type_id, department_id,
      priority_id, status_id, location_id, created_at, updated_at, resolved_at, closed_at,
      resolution_notes)
    values (
      v_students[1 + floor(random() * array_length(v_students, 1))::int],
      v_title || '. Please take a look when you can.',
      v_title,
      v_title || '. Please take a look when you can.',
      v_type.issue_type_id,
      v_type.default_department_id,
      case when v_status = 'NEW' then null else v_priority end,
      (select status_id from public.statuses where name = v_status),
      v_location, v_created, coalesce(v_closed, v_resolved, v_created), v_resolved, v_closed,
      case when v_resolved is not null then v_notes[1 + floor(random() * array_length(v_notes, 1))::int] end)
    returning ticket_id into v_ticket;

    update public.ticket_history set created_at = v_created where ticket_id = v_ticket;
    insert into public.ticket_history (ticket_id, action, created_at)
    values (v_ticket, 'DEMO_SEED', v_created);

    if v_status not in ('NEW', 'NEEDS_REVIEW') then
      select ud.user_id into v_staff
      from public.user_departments ud
      join public.users u on u.user_id = ud.user_id and u.active
      where ud.department_id = v_type.default_department_id
      order by random() limit 1;
      v_staff := coalesce(v_staff, v_fallback);

      if v_staff is not null then
        insert into public.ticket_assignments (ticket_id, assigned_to, assigned_by, assigned_at)
        values (v_ticket, v_staff, v_staff, v_created + interval '30 minutes');
        update public.ticket_history set created_at = v_created + interval '30 minutes'
         where ticket_id = v_ticket and action = 'ASSIGNED';
      end if;
    end if;

    if v_resolved is not null then
      insert into public.ticket_history (ticket_id, changed_by, action, new_value, created_at)
      values (v_ticket, v_staff, 'RESOLVED',
              (select resolution_notes from public.tickets where ticket_id = v_ticket), v_resolved);
    end if;
  end loop;

  -- Seeding assignments fires the assignment trigger; nobody needs 300 pings.
  delete from public.notifications n
   using public.ticket_history h
   where h.action = 'DEMO_SEED' and h.ticket_id = n.ticket_id;

  raise notice 'Seeded % demo tickets.', v_count;
end $$;
