-- Install in Supabase SQL Editor before using the transactional customer delete.
-- PostgreSQL functions execute atomically: a foreign-key failure rolls back both
-- the pet deletions and the customer deletion.

create or replace function delete_customer_with_pets(
  p_company_id int,
  p_customer_id int
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not exists (
    select 1 from customer
    where company_id = p_company_id and customer_id = p_customer_id
  ) then
    raise exception 'Customer % not found', p_customer_id using errcode = 'P0002';
  end if;

  delete from pet
  where company_id = p_company_id and customer_id = p_customer_id;

  delete from customer
  where company_id = p_company_id and customer_id = p_customer_id;
end;
$$;

revoke all on function delete_customer_with_pets(int, int)
  from public, anon, authenticated;
grant execute on function delete_customer_with_pets(int, int)
  to service_role;

-- Legacy base implementations retained only as reference/upgrade helpers.
-- The callable create_booking_atomic/update_booking_atomic functions live in
-- booking_conflict_prevention_migration.sql. Keeping distinct names here
-- prevents an out-of-order rerun from removing concurrency protection.
alter table daycare_booking add column if not exists add_on text;
alter table daycare_booking add column if not exists add_on_price numeric default 0;
alter table grooming_booking add column if not exists duration_minutes int not null default 90;

create or replace function create_booking_atomic_base(
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
begin
  if p_booking_type = 'grooming' then
    insert into grooming_booking (
      company_id, pet_id, staff_id, service_name, booking_date,
      booking_time, duration_minutes, price, add_on, add_on_price, notes, booking_status,
      created_date, created_time
    ) values (
      p_company_id, (p_booking->>'pet_id')::int, (p_booking->>'staff_id')::int,
      p_booking->>'service_name', (p_booking->>'booking_date')::date,
      (p_booking->>'booking_time')::time,
      coalesce((p_booking->>'duration_minutes')::int, 90), (p_booking->>'price')::numeric,
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
      p_company_id, (p_booking->>'pet_id')::int, (p_booking->>'staff_id')::int,
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
      p_company_id, (p_booking->>'pet_id')::int, (p_booking->>'staff_id')::int,
      (p_booking->>'check_in_date')::date,
      (p_booking->>'check_in_time')::time, (p_booking->>'check_out_date')::date,
      (p_booking->>'check_out_time')::time, p_booking->>'room_type',
      (p_booking->>'price_per_night')::numeric, (p_booking->>'total_price')::numeric,
      p_booking->>'feeding_instruction', p_booking->>'medical_instruction',
      p_booking->>'notes', p_booking->>'booking_status',
      (p_booking->>'created_date')::date, (p_booking->>'created_time')::time
    )
    returning * into v_boarding;
  else
    raise exception 'Unknown booking type %', p_booking_type using errcode = 'P0001';
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

create or replace function update_booking_atomic_base(
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
begin
  if p_booking_type = 'grooming' then
    select payment_id into v_payment_id from grooming_booking
      where company_id = p_company_id and grooming_booking_id = p_booking_id for update;
    if not found then raise exception 'Booking not found' using errcode = 'P0002'; end if;
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
    select payment_id into v_payment_id from daycare_booking
      where company_id = p_company_id and daycare_booking_id = p_booking_id for update;
    if not found then raise exception 'Booking not found' using errcode = 'P0002'; end if;
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
    select payment_id into v_payment_id from boarding_booking
      where company_id = p_company_id and boarding_booking_id = p_booking_id for update;
    if not found then raise exception 'Booking not found' using errcode = 'P0002'; end if;
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

create or replace function delete_booking_atomic(
  p_company_id int,
  p_booking_type text,
  p_booking_id int
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_payment payment%rowtype;
begin
  if p_booking_type = 'grooming' then
    select p.* into v_payment from payment p join grooming_booking b on b.payment_id = p.payment_id
      where b.company_id = p_company_id and b.grooming_booking_id = p_booking_id for update;
  elsif p_booking_type = 'daycare' then
    select p.* into v_payment from payment p join daycare_booking b on b.payment_id = p.payment_id
      where b.company_id = p_company_id and b.daycare_booking_id = p_booking_id for update;
  elsif p_booking_type = 'boarding' then
    select p.* into v_payment from payment p join boarding_booking b on b.payment_id = p.payment_id
      where b.company_id = p_company_id and b.boarding_booking_id = p_booking_id for update;
  else
    raise exception 'Unknown booking type %', p_booking_type using errcode = 'P0001';
  end if;
  if not found then raise exception 'Booking or linked payment not found' using errcode = 'P0002'; end if;
  if v_payment.status not in ('Pending', 'Unpaid', 'Cancelled') then
    raise exception 'Completed or refunded payments cannot be deleted' using errcode = 'P0001';
  end if;
  if v_payment.redemption_id is not null and exists (
    select 1 from redemption where company_id = p_company_id
      and redemption_id = v_payment.redemption_id and status not in ('Rejected', 'Cancelled')
  ) then
    raise exception 'Reject or refund the linked point redemption before deleting this booking' using errcode = 'P0001';
  end if;

  if p_booking_type = 'grooming' then
    delete from grooming_booking where company_id = p_company_id and grooming_booking_id = p_booking_id;
  elsif p_booking_type = 'daycare' then
    delete from daycare_booking where company_id = p_company_id and daycare_booking_id = p_booking_id;
  else
    delete from boarding_booking where company_id = p_company_id and boarding_booking_id = p_booking_id;
  end if;
  delete from payment where company_id = p_company_id and payment_id = v_payment.payment_id;
end;
$$;

revoke all on function create_booking_atomic_base(int, text, jsonb, jsonb) from public, anon, authenticated;
revoke all on function update_booking_atomic_base(int, text, int, jsonb, jsonb) from public, anon, authenticated;
revoke all on function delete_booking_atomic(int, text, int) from public, anon, authenticated;
grant execute on function delete_booking_atomic(int, text, int) to service_role;
