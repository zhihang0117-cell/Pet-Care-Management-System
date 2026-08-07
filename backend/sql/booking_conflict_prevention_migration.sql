-- Run once in the Supabase SQL editor. Adds real conflict prevention to
-- create_booking_atomic/update_booking_atomic (see crud_consistency_functions.sql)
-- so a staff member can no longer be double-booked and a boarding room can no
-- longer be over-booked past its configured capacity — confirmed live before
-- this migration that neither was enforced anywhere (Node validation nor the
-- database): two grooming bookings for the same staff at the exact same slot
-- both succeeded silently.
--
-- Enforced at the database layer (not just Node) so every caller is covered,
-- including the AI/WhatsApp booking path (app/tools/booking_tools.py), which
-- calls these atomic functions after its advisory availability check and
-- 15-minute shared SLOT_HOLDS reservation (with an in-process compatibility
-- fallback until booking_slot_holds_migration.sql is applied).
--
-- Conflict windows mirror the duration model already used by that advisory
-- check (app/db/availability_service.py's DEFAULT_SERVICE_DURATION_MINUTES),
-- so the two entry points agree on what "conflicting" means:
--   - grooming: the selected catalogue duration from booking_time (90-minute
--     fallback for older rows/services without explicit duration metadata)
--   - daycare: the complete check-in-to-check-out visit
--   - boarding: only the brief check-in and check-out windows (30 min each),
--     not the full stay; full-stay occupancy belongs to room capacity below
-- Boarding additionally has a SEPARATE room-capacity check (room.capacity vs
-- overlapping boarding_booking rows for that room_type, by the full
-- check_in_date~check_out_date range) — this mirrors
-- app/db/relational_actions.py's _room_capacity_status and is independent of
-- which staff member is assigned.
--
-- Only "Pending" and "Scheduled" bookings count as active/blocking (matches
-- app/db/relational_actions.py's _booking_blocks_availability) — a
-- Cancelled/Done/No Show booking no longer occupies the slot.

-- Both service tables expose add-on columns in the dashboard schema. Keep
-- the migration safe for older projects that only had them on grooming.
alter table daycare_booking add column if not exists add_on text;
alter table daycare_booking add column if not exists add_on_price numeric default 0;
alter table grooming_booking add column if not exists duration_minutes int not null default 90;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'grooming_duration_positive') then
    alter table grooming_booking add constraint grooming_duration_positive
      check (duration_minutes > 0 and duration_minutes <= 1440) not valid;
  end if;
end
$$;

create or replace function staff_has_conflicting_booking(
  p_company_id int,
  p_staff_id int,
  p_start timestamp,
  p_end timestamp,
  p_exclude_type text default null,
  p_exclude_id int default null
)
returns boolean
language plpgsql
stable
as $$
declare
  v_conflict boolean;
begin
  -- coalesce(p_exclude_type, '') matters: when p_exclude_type is NULL (the
  -- default, used by create_booking_atomic which never excludes anything),
  -- `p_exclude_type = 'grooming'` evaluates to NULL rather than false under
  -- normal SQL three-valued logic, and `not (null and ...)` is NULL too —
  -- which a WHERE clause treats as "no match", silently dropping every row
  -- and always returning no-conflict. Confirmed live: this exact bug let the
  -- very double-booking this migration exists to stop go through unnoticed
  -- on first deploy.
  select exists (
    select 1 from grooming_booking b
    where b.company_id = p_company_id and b.staff_id = p_staff_id
      and b.booking_status in ('Pending', 'Scheduled')
      and not (coalesce(p_exclude_type, '') = 'grooming' and b.grooming_booking_id = p_exclude_id)
      and (b.booking_date + b.booking_time,
           (b.booking_date + b.booking_time) + make_interval(mins => coalesce(b.duration_minutes, 90)))
          overlaps (p_start, p_end)
    union all
    select 1 from daycare_booking b
    where b.company_id = p_company_id and b.staff_id = p_staff_id
      and b.booking_status in ('Pending', 'Scheduled')
      and not (coalesce(p_exclude_type, '') = 'daycare' and b.daycare_booking_id = p_exclude_id)
      and (b.booking_date + b.check_in_time, b.booking_date + b.check_out_time)
          overlaps (p_start, p_end)
    union all
    select 1 from boarding_booking b
    where b.company_id = p_company_id and b.staff_id = p_staff_id
      and b.booking_status in ('Pending', 'Scheduled')
      and not (coalesce(p_exclude_type, '') = 'boarding' and b.boarding_booking_id = p_exclude_id)
      and (
        (b.check_in_date + b.check_in_time, (b.check_in_date + b.check_in_time) + interval '30 minutes')
          overlaps (p_start, p_end)
        or (b.check_out_date + b.check_out_time, (b.check_out_date + b.check_out_time) + interval '30 minutes')
          overlaps (p_start, p_end)
      )
  ) into v_conflict;
  return coalesce(v_conflict, false);
end;
$$;

create or replace function room_has_capacity(
  p_company_id int,
  p_room_type text,
  p_check_in date,
  p_check_out date,
  p_exclude_id int default null
)
returns boolean
language plpgsql
stable
as $$
declare
  v_capacity int;
  v_count int;
begin
  select capacity into v_capacity from room
    where company_id = p_company_id and room_type = p_room_type
    limit 1;
  if v_capacity is null then
    -- Unknown room names are invalid, never optimistically available.
    return false;
  end if;

  select count(*) into v_count from boarding_booking b
    where b.company_id = p_company_id and b.room_type = p_room_type
      and b.booking_status in ('Pending', 'Scheduled')
      and (p_exclude_id is null or b.boarding_booking_id <> p_exclude_id)
      and b.check_in_date < p_check_out and p_check_in < b.check_out_date;

  return v_count < v_capacity;
end;
$$;

create or replace function pet_has_conflicting_booking(
  p_company_id int,
  p_pet_id int,
  p_start timestamp,
  p_end timestamp,
  p_exclude_type text default null,
  p_exclude_id int default null
)
returns boolean
language sql
stable
as $$
  select exists (
    select 1 from grooming_booking b
    where b.company_id = p_company_id and b.pet_id = p_pet_id
      and b.booking_status in ('Pending', 'Scheduled')
      and not (coalesce(p_exclude_type, '') = 'grooming' and b.grooming_booking_id = p_exclude_id)
      and (b.booking_date + b.booking_time,
           (b.booking_date + b.booking_time) + make_interval(mins => coalesce(b.duration_minutes, 90)))
          overlaps (p_start, p_end)
    union all
    select 1 from daycare_booking b
    where b.company_id = p_company_id and b.pet_id = p_pet_id
      and b.booking_status in ('Pending', 'Scheduled')
      and not (coalesce(p_exclude_type, '') = 'daycare' and b.daycare_booking_id = p_exclude_id)
      and (b.booking_date + b.check_in_time, b.booking_date + b.check_out_time)
          overlaps (p_start, p_end)
    union all
    select 1 from boarding_booking b
    where b.company_id = p_company_id and b.pet_id = p_pet_id
      and b.booking_status in ('Pending', 'Scheduled')
      and not (coalesce(p_exclude_type, '') = 'boarding' and b.boarding_booking_id = p_exclude_id)
      and (b.check_in_date + b.check_in_time, b.check_out_date + b.check_out_time)
          overlaps (p_start, p_end)
  );
$$;

create or replace function booking_status_transition_allowed(p_old text, p_new text)
returns boolean
language sql
immutable
as $$
  select case
    when p_new = p_old then true
    when p_old = 'Pending' and p_new = 'Scheduled' then true
    when p_old = 'Scheduled' and p_new in ('Done', 'No Show') then true
    else false
  end;
$$;

create or replace function booking_event_within_hours(
  p_company_id int, p_date date, p_start time, p_end time
)
returns boolean
language sql
stable
as $$
  select not exists (
    select 1 from company_closed_dates c
    where c.company_id = p_company_id and c.closed_date = p_date
  ) and exists (
    select 1 from company_business_hours h
    where h.company_id = p_company_id
      and h.day_of_week = extract(dow from p_date)::int
      and not h.is_closed
      and p_start >= h.open_time and p_end <= h.close_time and p_end > p_start
  );
$$;

create or replace function staff_can_book_service(
  p_company_id int, p_staff_id int, p_service_type text, p_date date
)
returns boolean
language sql
stable
as $$
  select exists (
    select 1 from staff s
    where s.company_id = p_company_id and s.staff_id = p_staff_id
      and lower(s.status) = 'active'
      and coalesce(s.provides_service, true)
      and coalesce(s.service_types_json, '["GROOMING","DAYCARE","BOARDING"]'::jsonb)
          ? upper(p_service_type)
      and not (coalesce(s.off_days_json, '[]'::jsonb) ? to_char(p_date, 'FMDay'))
      and not exists (
        select 1 from leave l
        where l.company_id = p_company_id and l.staff_id = p_staff_id
          and l.status = 'Approved' and p_date between l.start_date and l.end_date
      )
  );
$$;

create or replace function pet_vaccination_valid(
  p_company_id int, p_pet_id int, p_service_type text, p_date date
)
returns boolean
language plpgsql
stable
as $$
declare
  v_status text;
  v_expiry_text text;
  v_expiry date;
begin
  if upper(p_service_type) = 'GROOMING' then return true; end if;
  select vaccination_status, vaccination_expired_date
    into v_status, v_expiry_text
  from pet where company_id = p_company_id and pet_id = p_pet_id;
  if not found or lower(coalesce(v_status, '')) <> 'vaccinated' then return false; end if;
  if nullif(btrim(v_expiry_text), '') is null then return true; end if;
  begin
    if v_expiry_text ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
      v_expiry := v_expiry_text::date;
    elsif v_expiry_text ~ '^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}$' then
      v_expiry := to_date(v_expiry_text, 'DD/MM/YYYY');
    else
      return false;
    end if;
  exception when others then
    return false;
  end;
  return v_expiry > p_date;
end;
$$;

revoke all on function staff_has_conflicting_booking(int, int, timestamp, timestamp, text, int) from public, anon, authenticated;
revoke all on function room_has_capacity(int, text, date, date, int) from public, anon, authenticated;
revoke all on function pet_has_conflicting_booking(int, int, timestamp, timestamp, text, int) from public, anon, authenticated;
revoke all on function booking_status_transition_allowed(text, text) from public, anon, authenticated;
revoke all on function booking_event_within_hours(int, date, time, time) from public, anon, authenticated;
revoke all on function staff_can_book_service(int, int, text, date) from public, anon, authenticated;
revoke all on function pet_vaccination_valid(int, int, text, date) from public, anon, authenticated;
grant execute on function staff_has_conflicting_booking(int, int, timestamp, timestamp, text, int) to service_role;
grant execute on function room_has_capacity(int, text, date, date, int) to service_role;
grant execute on function pet_has_conflicting_booking(int, int, timestamp, timestamp, text, int) to service_role;
grant execute on function booking_status_transition_allowed(text, text) to service_role;
grant execute on function booking_event_within_hours(int, date, time, time) to service_role;
grant execute on function staff_can_book_service(int, int, text, date) to service_role;
grant execute on function pet_vaccination_valid(int, int, text, date) to service_role;

-- ---------------------------------------------------------------------------
-- get_room_occupancy: read-only per-room-type status for a given date (every
-- room configured for the company, its capacity, how many active boarding
-- bookings currently occupy it on that date, and whether it's full) — same
-- overlap counting as room_has_capacity above, exposed for the dashboard so
-- staff see live room status instead of cross-referencing bookings by hand.
-- Backs GET /api/rooms (backend/src/routes/rooms.js).
-- ---------------------------------------------------------------------------
create or replace function get_room_occupancy(
  p_company_id int,
  p_date date default current_date
)
returns table (
  room_id text,
  room_type text,
  capacity int,
  price numeric,
  booked_count int,
  available int,
  is_full boolean
)
language sql
stable
as $$
  select
    r.room_id,
    r.room_type,
    r.capacity,
    r.price,
    coalesce(occ.booked_count, 0)::int as booked_count,
    greatest(r.capacity - coalesce(occ.booked_count, 0), 0)::int as available,
    coalesce(occ.booked_count, 0) >= r.capacity as is_full
  from room r
  left join lateral (
    select count(*) as booked_count
    from boarding_booking b
    where b.company_id = p_company_id
      and b.room_type = r.room_type
      and b.booking_status in ('Pending', 'Scheduled')
      and b.check_in_date <= p_date and p_date < b.check_out_date
  ) occ on true
  where r.company_id = p_company_id
  order by r.room_type;
$$;

revoke all on function get_room_occupancy(int, date) from public, anon, authenticated;
grant execute on function get_room_occupancy(int, date) to service_role;

-- ---------------------------------------------------------------------------
-- create_booking_atomic: same as crud_consistency_functions.sql, with a
-- per-staff advisory lock (closes the check-then-insert race between two
-- concurrent requests for the same staff member) and the two conflict checks
-- above, run before any insert.
-- ---------------------------------------------------------------------------
create or replace function create_booking_atomic(
  p_company_id int,
  p_booking_type text,
  p_booking jsonb,
  p_payment jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_payment payment%rowtype;
  v_grooming grooming_booking%rowtype;
  v_daycare daycare_booking%rowtype;
  v_boarding boarding_booking%rowtype;
  v_booking jsonb;
  v_staff_id int := (p_booking->>'staff_id')::int;
  v_pet_id int := (p_booking->>'pet_id')::int;
  v_start timestamp;
  v_end timestamp;
  v_checkout timestamp;
  v_room_type text;
  v_check_in date;
  v_check_out date;
  v_duration int;
begin
  -- Serialize concurrent booking attempts for the same staff member so the
  -- conflict check below and the insert always see a consistent picture.
  perform pg_advisory_xact_lock(hashtextextended(p_company_id::text || ':staff:' || v_staff_id::text, 0));
  perform pg_advisory_xact_lock(hashtextextended(p_company_id::text || ':pet:' || v_pet_id::text, 0));

  if p_booking_type = 'grooming' then
    v_start := (p_booking->>'booking_date')::date + (p_booking->>'booking_time')::time;
    v_duration := coalesce((p_booking->>'duration_minutes')::int, 90);
    if v_duration <= 0 or v_duration > 1440 then
      raise exception 'Invalid grooming duration' using errcode = 'P0001';
    end if;
    v_end := v_start + make_interval(mins => v_duration);
  elsif p_booking_type = 'daycare' then
    v_start := (p_booking->>'booking_date')::date + (p_booking->>'check_in_time')::time;
    v_end := (p_booking->>'booking_date')::date + (p_booking->>'check_out_time')::time;
  elsif p_booking_type = 'boarding' then
    v_start := (p_booking->>'check_in_date')::date + (p_booking->>'check_in_time')::time;
    v_end := v_start + interval '30 minutes';
    v_checkout := (p_booking->>'check_out_date')::date + (p_booking->>'check_out_time')::time;
  else
    raise exception 'Unknown booking type %', p_booking_type using errcode = 'P0001';
  end if;

  if not staff_can_book_service(p_company_id, v_staff_id, p_booking_type, v_start::date)
    or (p_booking_type = 'boarding' and not staff_can_book_service(
      p_company_id, v_staff_id, p_booking_type, v_checkout::date
    )) then
    raise exception 'Selected staff member is not eligible on the requested date' using errcode = 'P0001';
  end if;
  if not booking_event_within_hours(p_company_id, v_start::date, v_start::time, v_end::time)
    or (p_booking_type = 'boarding' and not booking_event_within_hours(
      p_company_id, v_checkout::date, v_checkout::time, (v_checkout + interval '30 minutes')::time
    )) then
    raise exception 'Requested interval is outside configured business hours' using errcode = 'P0001';
  end if;
  if not pet_vaccination_valid(p_company_id, v_pet_id, p_booking_type, v_start::date) then
    raise exception 'Pet vaccination is not valid for this service date' using errcode = 'P0001';
  end if;

  if staff_has_conflicting_booking(p_company_id, v_staff_id, v_start, v_end)
    or (p_booking_type = 'boarding' and staff_has_conflicting_booking(
      p_company_id, v_staff_id, v_checkout, v_checkout + interval '30 minutes'
    )) then
    raise exception 'This staff member already has a booking that overlaps this time' using errcode = 'P0001';
  end if;

  if pet_has_conflicting_booking(
    p_company_id,
    v_pet_id,
    v_start,
    case when p_booking_type = 'boarding' then v_checkout else v_end end
  ) then
    raise exception 'This pet already has a booking that overlaps this time' using errcode = 'P0001';
  end if;

  if p_booking_type = 'boarding' then
    v_room_type := p_booking->>'room_type';
    v_check_in := (p_booking->>'check_in_date')::date;
    v_check_out := (p_booking->>'check_out_date')::date;
    perform pg_advisory_xact_lock(hashtextextended(p_company_id::text || ':room:' || coalesce(v_room_type, ''), 0));
    if not room_has_capacity(p_company_id, v_room_type, v_check_in, v_check_out) then
      raise exception 'This room type is fully booked for the selected dates' using errcode = 'P0001';
    end if;
  end if;

  if p_booking_type = 'grooming' then
    insert into grooming_booking (
      company_id, pet_id, staff_id, service_name, booking_date,
      booking_time, duration_minutes, price, add_on, add_on_price, notes, booking_status,
      created_date, created_time
    ) values (
      p_company_id, (p_booking->>'pet_id')::int, v_staff_id,
      p_booking->>'service_name', (p_booking->>'booking_date')::date,
      (p_booking->>'booking_time')::time, v_duration, (p_booking->>'price')::numeric,
      p_booking->>'add_on', (p_booking->>'add_on_price')::numeric, p_booking->>'notes',
      p_booking->>'booking_status', (p_booking->>'created_date')::date,
      (p_booking->>'created_time')::time
    )
    returning * into v_grooming;
  elsif p_booking_type = 'daycare' then
    insert into daycare_booking (
      company_id, pet_id, staff_id, booking_date, check_in_time,
      check_out_time, package_type, price, add_on, add_on_price,
      special_instruction, booking_status,
      created_date, created_time
    ) values (
      p_company_id, (p_booking->>'pet_id')::int, v_staff_id,
      (p_booking->>'booking_date')::date,
      (p_booking->>'check_in_time')::time, (p_booking->>'check_out_time')::time,
      p_booking->>'package_type', (p_booking->>'price')::numeric,
      p_booking->>'add_on', (p_booking->>'add_on_price')::numeric,
      p_booking->>'special_instruction', p_booking->>'booking_status',
      (p_booking->>'created_date')::date, (p_booking->>'created_time')::time
    )
    returning * into v_daycare;
  elsif p_booking_type = 'boarding' then
    insert into boarding_booking (
      company_id, pet_id, staff_id, check_in_date, check_in_time,
      check_out_date, check_out_time, room_type, price_per_night, total_price,
      feeding_instruction, medical_instruction, notes, booking_status,
      created_date, created_time
    ) values (
      p_company_id, (p_booking->>'pet_id')::int, v_staff_id,
      (p_booking->>'check_in_date')::date,
      (p_booking->>'check_in_time')::time, (p_booking->>'check_out_date')::date,
      (p_booking->>'check_out_time')::time, p_booking->>'room_type',
      (p_booking->>'price_per_night')::numeric, (p_booking->>'total_price')::numeric,
      p_booking->>'feeding_instruction', p_booking->>'medical_instruction',
      p_booking->>'notes', p_booking->>'booking_status',
      (p_booking->>'created_date')::date, (p_booking->>'created_time')::time
    )
    returning * into v_boarding;
  end if;

  insert into payment (
    company_id, service, base_price, add_ons, final_amount,
    payment_method, date, status
  ) values (
    p_company_id, p_payment->>'service', (p_payment->>'base_price')::numeric,
    p_payment->>'add_ons', (p_payment->>'final_amount')::numeric,
    nullif(p_payment->>'payment_method', ''), (p_payment->>'date')::date,
    coalesce(nullif(p_payment->>'status', ''), 'Pending')
  )
  returning * into v_payment;

  if p_booking_type = 'grooming' then
    update grooming_booking
    set payment_id = v_payment.payment_id
    where company_id = p_company_id
      and grooming_booking_id = v_grooming.grooming_booking_id
    returning * into v_grooming;
    v_booking := to_jsonb(v_grooming);
  elsif p_booking_type = 'daycare' then
    update daycare_booking
    set payment_id = v_payment.payment_id
    where company_id = p_company_id
      and daycare_booking_id = v_daycare.daycare_booking_id
    returning * into v_daycare;
    v_booking := to_jsonb(v_daycare);
  else
    update boarding_booking
    set payment_id = v_payment.payment_id
    where company_id = p_company_id
      and boarding_booking_id = v_boarding.boarding_booking_id
    returning * into v_boarding;
    v_booking := to_jsonb(v_boarding);
  end if;

  return jsonb_build_object('booking', v_booking, 'payment', to_jsonb(v_payment));
end;
$$;

-- ---------------------------------------------------------------------------
-- update_booking_atomic: same as crud_consistency_functions.sql, with the
-- same conflict checks, excluding the booking's own row and only applied
-- when the patch actually changes staff/time/date/room fields (a
-- status/notes-only edit skips the check entirely).
-- ---------------------------------------------------------------------------
create or replace function update_booking_atomic(
  p_company_id int,
  p_booking_type text,
  p_booking_id int,
  p_booking_patch jsonb,
  p_payment_patch jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_payment_id int;
  v_result jsonb;
  v_staff_id int;
  v_pet_id int;
  v_current_status text;
  v_new_status text;
  v_start timestamp;
  v_end timestamp;
  v_checkout timestamp;
  v_room_type text;
  v_check_in date;
  v_check_out date;
  v_duration int;
  v_needs_conflict_check boolean;
begin
  if p_booking_type = 'grooming' then
    select payment_id, staff_id, pet_id, booking_status, booking_date + booking_time
      into v_payment_id, v_staff_id, v_pet_id, v_current_status, v_start
      from grooming_booking where company_id = p_company_id and grooming_booking_id = p_booking_id for update;
    if not found then raise exception 'Booking not found' using errcode = 'P0002'; end if;
    if p_booking_patch ? 'booking_status' then
      v_new_status := p_booking_patch->>'booking_status';
      if v_new_status = 'Cancelled' then
        raise exception 'Use cancel_booking_atomic to cancel a booking' using errcode = 'P0001';
      end if;
      if not booking_status_transition_allowed(v_current_status, v_new_status) then
        raise exception 'Invalid booking status transition from % to %', v_current_status, v_new_status using errcode = 'P0001';
      end if;
    end if;
    v_needs_conflict_check := (p_booking_patch ? 'staff_id') or (p_booking_patch ? 'pet_id')
      or (p_booking_patch ? 'booking_date') or (p_booking_patch ? 'booking_time')
      or (p_booking_patch ? 'duration_minutes');
    if v_needs_conflict_check then
      v_staff_id := coalesce((p_booking_patch->>'staff_id')::int, v_staff_id);
      v_pet_id := coalesce((p_booking_patch->>'pet_id')::int, v_pet_id);
      select coalesce((p_booking_patch->>'booking_date')::date, booking_date)
             + coalesce((p_booking_patch->>'booking_time')::time, booking_time)
        into v_start
        from grooming_booking where company_id = p_company_id and grooming_booking_id = p_booking_id;
      select coalesce((p_booking_patch->>'duration_minutes')::int, duration_minutes, 90)
        into v_duration from grooming_booking
        where company_id = p_company_id and grooming_booking_id = p_booking_id;
      if v_duration <= 0 or v_duration > 1440 then
        raise exception 'Invalid grooming duration' using errcode = 'P0001';
      end if;
      v_end := v_start + make_interval(mins => v_duration);
      perform pg_advisory_xact_lock(hashtextextended(p_company_id::text || ':staff:' || v_staff_id::text, 0));
      perform pg_advisory_xact_lock(hashtextextended(p_company_id::text || ':pet:' || v_pet_id::text, 0));
      if not staff_can_book_service(p_company_id, v_staff_id, 'grooming', v_start::date)
        or not booking_event_within_hours(p_company_id, v_start::date, v_start::time, v_end::time)
        or not pet_vaccination_valid(p_company_id, v_pet_id, 'grooming', v_start::date) then
        raise exception 'Updated booking violates staff, hours, or pet eligibility rules' using errcode = 'P0001';
      end if;
      if staff_has_conflicting_booking(p_company_id, v_staff_id, v_start, v_end, 'grooming', p_booking_id) then
        raise exception 'This staff member already has a booking that overlaps this time' using errcode = 'P0001';
      end if;
      if pet_has_conflicting_booking(p_company_id, v_pet_id, v_start, v_end, 'grooming', p_booking_id) then
        raise exception 'This pet already has a booking that overlaps this time' using errcode = 'P0001';
      end if;
    end if;
    update grooming_booking b set
      pet_id = coalesce((p_booking_patch->>'pet_id')::int, b.pet_id),
      staff_id = coalesce((p_booking_patch->>'staff_id')::int, b.staff_id),
      service_name = coalesce(p_booking_patch->>'service_name', b.service_name),
      booking_date = coalesce((p_booking_patch->>'booking_date')::date, b.booking_date),
      booking_time = coalesce((p_booking_patch->>'booking_time')::time, b.booking_time),
      duration_minutes = coalesce((p_booking_patch->>'duration_minutes')::int, b.duration_minutes),
      price = coalesce((p_booking_patch->>'price')::numeric, b.price),
      add_on = coalesce(p_booking_patch->>'add_on', b.add_on),
      add_on_price = coalesce((p_booking_patch->>'add_on_price')::numeric, b.add_on_price),
      notes = coalesce(p_booking_patch->>'notes', b.notes),
      booking_status = coalesce(p_booking_patch->>'booking_status', b.booking_status)
      where company_id = p_company_id and grooming_booking_id = p_booking_id
      returning to_jsonb(b) into v_result;
  elsif p_booking_type = 'daycare' then
    select payment_id, staff_id, pet_id, booking_status
      into v_payment_id, v_staff_id, v_pet_id, v_current_status from daycare_booking
      where company_id = p_company_id and daycare_booking_id = p_booking_id for update;
    if not found then raise exception 'Booking not found' using errcode = 'P0002'; end if;
    if p_booking_patch ? 'booking_status' then
      v_new_status := p_booking_patch->>'booking_status';
      if v_new_status = 'Cancelled' then
        raise exception 'Use cancel_booking_atomic to cancel a booking' using errcode = 'P0001';
      end if;
      if not booking_status_transition_allowed(v_current_status, v_new_status) then
        raise exception 'Invalid booking status transition from % to %', v_current_status, v_new_status using errcode = 'P0001';
      end if;
    end if;
    v_needs_conflict_check := (p_booking_patch ? 'staff_id') or (p_booking_patch ? 'pet_id') or (p_booking_patch ? 'booking_date')
      or (p_booking_patch ? 'check_in_time') or (p_booking_patch ? 'check_out_time');
    if v_needs_conflict_check then
      v_staff_id := coalesce((p_booking_patch->>'staff_id')::int, v_staff_id);
      v_pet_id := coalesce((p_booking_patch->>'pet_id')::int, v_pet_id);
      select coalesce((p_booking_patch->>'booking_date')::date, booking_date) + coalesce((p_booking_patch->>'check_in_time')::time, check_in_time),
             coalesce((p_booking_patch->>'booking_date')::date, booking_date) + coalesce((p_booking_patch->>'check_out_time')::time, check_out_time)
        into v_start, v_end
        from daycare_booking where company_id = p_company_id and daycare_booking_id = p_booking_id;
      perform pg_advisory_xact_lock(hashtextextended(p_company_id::text || ':staff:' || v_staff_id::text, 0));
      perform pg_advisory_xact_lock(hashtextextended(p_company_id::text || ':pet:' || v_pet_id::text, 0));
      if not staff_can_book_service(p_company_id, v_staff_id, 'daycare', v_start::date)
        or not booking_event_within_hours(p_company_id, v_start::date, v_start::time, v_end::time)
        or not pet_vaccination_valid(p_company_id, v_pet_id, 'daycare', v_start::date) then
        raise exception 'Updated booking violates staff, hours, or pet eligibility rules' using errcode = 'P0001';
      end if;
      if staff_has_conflicting_booking(p_company_id, v_staff_id, v_start, v_end, 'daycare', p_booking_id) then
        raise exception 'This staff member already has a booking that overlaps this time' using errcode = 'P0001';
      end if;
      if pet_has_conflicting_booking(p_company_id, v_pet_id, v_start, v_end, 'daycare', p_booking_id) then
        raise exception 'This pet already has a booking that overlaps this time' using errcode = 'P0001';
      end if;
    end if;
    update daycare_booking b set
      pet_id = coalesce((p_booking_patch->>'pet_id')::int, b.pet_id),
      staff_id = coalesce((p_booking_patch->>'staff_id')::int, b.staff_id),
      booking_date = coalesce((p_booking_patch->>'booking_date')::date, b.booking_date),
      check_in_time = coalesce((p_booking_patch->>'check_in_time')::time, b.check_in_time),
      check_out_time = coalesce((p_booking_patch->>'check_out_time')::time, b.check_out_time),
      package_type = coalesce(p_booking_patch->>'package_type', b.package_type),
      price = coalesce((p_booking_patch->>'price')::numeric, b.price),
      add_on = coalesce(p_booking_patch->>'add_on', b.add_on),
      add_on_price = coalesce((p_booking_patch->>'add_on_price')::numeric, b.add_on_price),
      special_instruction = coalesce(p_booking_patch->>'special_instruction', b.special_instruction),
      booking_status = coalesce(p_booking_patch->>'booking_status', b.booking_status)
      where company_id = p_company_id and daycare_booking_id = p_booking_id
      returning to_jsonb(b) into v_result;
  elsif p_booking_type = 'boarding' then
    select payment_id, staff_id, pet_id, room_type, booking_status
      into v_payment_id, v_staff_id, v_pet_id, v_room_type, v_current_status from boarding_booking
      where company_id = p_company_id and boarding_booking_id = p_booking_id for update;
    if not found then raise exception 'Booking not found' using errcode = 'P0002'; end if;
    if p_booking_patch ? 'booking_status' then
      v_new_status := p_booking_patch->>'booking_status';
      if v_new_status = 'Cancelled' then
        raise exception 'Use cancel_booking_atomic to cancel a booking' using errcode = 'P0001';
      end if;
      if not booking_status_transition_allowed(v_current_status, v_new_status) then
        raise exception 'Invalid booking status transition from % to %', v_current_status, v_new_status using errcode = 'P0001';
      end if;
    end if;
    v_needs_conflict_check := (p_booking_patch ? 'staff_id') or (p_booking_patch ? 'pet_id') or (p_booking_patch ? 'check_in_date') or (p_booking_patch ? 'check_in_time')
      or (p_booking_patch ? 'check_out_date') or (p_booking_patch ? 'check_out_time');
    if v_needs_conflict_check then
      v_staff_id := coalesce((p_booking_patch->>'staff_id')::int, v_staff_id);
      v_pet_id := coalesce((p_booking_patch->>'pet_id')::int, v_pet_id);
      select coalesce((p_booking_patch->>'check_in_date')::date, check_in_date) + coalesce((p_booking_patch->>'check_in_time')::time, check_in_time),
             coalesce((p_booking_patch->>'check_out_date')::date, check_out_date) + coalesce((p_booking_patch->>'check_out_time')::time, check_out_time)
        into v_start, v_checkout
        from boarding_booking where company_id = p_company_id and boarding_booking_id = p_booking_id;
      v_end := v_start + interval '30 minutes';
      perform pg_advisory_xact_lock(hashtextextended(p_company_id::text || ':staff:' || v_staff_id::text, 0));
      perform pg_advisory_xact_lock(hashtextextended(p_company_id::text || ':pet:' || v_pet_id::text, 0));
      if not staff_can_book_service(p_company_id, v_staff_id, 'boarding', v_start::date)
        or not staff_can_book_service(p_company_id, v_staff_id, 'boarding', v_checkout::date)
        or not booking_event_within_hours(p_company_id, v_start::date, v_start::time, v_end::time)
        or not booking_event_within_hours(
          p_company_id, v_checkout::date, v_checkout::time, (v_checkout + interval '30 minutes')::time
        )
        or not pet_vaccination_valid(p_company_id, v_pet_id, 'boarding', v_start::date) then
        raise exception 'Updated booking violates staff, hours, or pet eligibility rules' using errcode = 'P0001';
      end if;
      if staff_has_conflicting_booking(p_company_id, v_staff_id, v_start, v_end, 'boarding', p_booking_id)
        or staff_has_conflicting_booking(
          p_company_id, v_staff_id, v_checkout, v_checkout + interval '30 minutes', 'boarding', p_booking_id
        ) then
        raise exception 'This staff member already has a booking that overlaps this time' using errcode = 'P0001';
      end if;
      if pet_has_conflicting_booking(p_company_id, v_pet_id, v_start, v_checkout, 'boarding', p_booking_id) then
        raise exception 'This pet already has a booking that overlaps this stay' using errcode = 'P0001';
      end if;
    end if;
    if (p_booking_patch ? 'room_type') or (p_booking_patch ? 'check_in_date') or (p_booking_patch ? 'check_out_date') then
      select coalesce(p_booking_patch->>'room_type', room_type),
             coalesce((p_booking_patch->>'check_in_date')::date, check_in_date),
             coalesce((p_booking_patch->>'check_out_date')::date, check_out_date)
        into v_room_type, v_check_in, v_check_out
        from boarding_booking where company_id = p_company_id and boarding_booking_id = p_booking_id;
      perform pg_advisory_xact_lock(hashtextextended(p_company_id::text || ':room:' || coalesce(v_room_type, ''), 0));
      if not room_has_capacity(p_company_id, v_room_type, v_check_in, v_check_out, p_booking_id) then
        raise exception 'This room type is fully booked for the selected dates' using errcode = 'P0001';
      end if;
    end if;
    update boarding_booking b set
      pet_id = coalesce((p_booking_patch->>'pet_id')::int, b.pet_id),
      staff_id = coalesce((p_booking_patch->>'staff_id')::int, b.staff_id),
      check_in_date = coalesce((p_booking_patch->>'check_in_date')::date, b.check_in_date),
      check_in_time = coalesce((p_booking_patch->>'check_in_time')::time, b.check_in_time),
      check_out_date = coalesce((p_booking_patch->>'check_out_date')::date, b.check_out_date),
      check_out_time = coalesce((p_booking_patch->>'check_out_time')::time, b.check_out_time),
      room_type = coalesce(p_booking_patch->>'room_type', b.room_type),
      price_per_night = coalesce((p_booking_patch->>'price_per_night')::numeric, b.price_per_night),
      total_price = coalesce((p_booking_patch->>'total_price')::numeric, b.total_price),
      feeding_instruction = coalesce(p_booking_patch->>'feeding_instruction', b.feeding_instruction),
      medical_instruction = coalesce(p_booking_patch->>'medical_instruction', b.medical_instruction),
      notes = coalesce(p_booking_patch->>'notes', b.notes),
      booking_status = coalesce(p_booking_patch->>'booking_status', b.booking_status)
      where company_id = p_company_id and boarding_booking_id = p_booking_id
      returning to_jsonb(b) into v_result;
  else
    raise exception 'Unknown booking type %', p_booking_type using errcode = 'P0001';
  end if;

  if p_payment_patch <> '{}'::jsonb then
    perform 1 from payment where company_id = p_company_id and payment_id = v_payment_id for update;
    if not found then raise exception 'Linked payment not found' using errcode = 'P0002'; end if;
    update payment p set
      service = coalesce(p_payment_patch->>'service', p.service),
      base_price = coalesce((p_payment_patch->>'base_price')::numeric, p.base_price),
      add_ons = coalesce(p_payment_patch->>'add_ons', p.add_ons),
      final_amount = coalesce((p_payment_patch->>'final_amount')::numeric, p.final_amount)
    where company_id = p_company_id and payment_id = v_payment_id
      and status in ('Pending', 'Unpaid', 'Cancelled');
    if not found then
      raise exception 'A completed or refunded booking price cannot be changed' using errcode = 'P0001';
    end if;
  end if;
  return v_result;
end;
$$;

revoke all on function create_booking_atomic(int, text, jsonb, jsonb) from public, anon, authenticated;
revoke all on function update_booking_atomic(int, text, int, jsonb, jsonb) from public, anon, authenticated;
grant execute on function create_booking_atomic(int, text, jsonb, jsonb) to service_role;
grant execute on function update_booking_atomic(int, text, int, jsonb, jsonb) to service_role;
