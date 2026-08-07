-- Security, status, and tenant-integrity repair identified by the 2026-08-07
-- schema/action audit. Run after the existing migrations and before
-- cancel_booking_atomic_migration.sql.

-- -------------------------------------------------------------------------
-- 1. RAG chunks and accidental backup tables are backend-only.
-- -------------------------------------------------------------------------
do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'chunks_bge_large',
    'chunks_bge_large_backup_20260716',
    'chunks_bge_large_status_backup_20260805'
  ] loop
    if to_regclass(format('public.%I', table_name)) is not null then
      execute format('alter table public.%I enable row level security', table_name);
      execute format(
        'revoke all on table public.%I from public, anon, authenticated',
        table_name
      );
    end if;
  end loop;
end
$$;

-- -------------------------------------------------------------------------
-- 2. Canonical workflow statuses. Existing lowercase rows were invisible to
-- exact Pending/Scheduled/Paid checks in the SQL functions and dashboards.
-- -------------------------------------------------------------------------
update grooming_booking
set booking_status = case lower(regexp_replace(btrim(booking_status), '[_-]+', ' ', 'g'))
  when 'pending' then 'Pending'
  when 'scheduled' then 'Scheduled'
  when 'done' then 'Done'
  when 'completed' then 'Done'
  when 'no show' then 'No Show'
  when 'noshow' then 'No Show'
  when 'cancelled' then 'Cancelled'
  when 'canceled' then 'Cancelled'
  else booking_status end;

update daycare_booking
set booking_status = case lower(regexp_replace(btrim(booking_status), '[_-]+', ' ', 'g'))
  when 'pending' then 'Pending'
  when 'scheduled' then 'Scheduled'
  when 'done' then 'Done'
  when 'completed' then 'Done'
  when 'no show' then 'No Show'
  when 'noshow' then 'No Show'
  when 'cancelled' then 'Cancelled'
  when 'canceled' then 'Cancelled'
  else booking_status end;

update boarding_booking
set booking_status = case lower(regexp_replace(btrim(booking_status), '[_-]+', ' ', 'g'))
  when 'pending' then 'Pending'
  when 'scheduled' then 'Scheduled'
  when 'done' then 'Done'
  when 'completed' then 'Done'
  when 'no show' then 'No Show'
  when 'noshow' then 'No Show'
  when 'cancelled' then 'Cancelled'
  when 'canceled' then 'Cancelled'
  else booking_status end;

update payment
set status = case lower(btrim(status))
  when 'pending' then 'Pending'
  when 'unpaid' then 'Unpaid'
  when 'paid' then 'Paid'
  when 'refunded' then 'Refunded'
  when 'cancelled' then 'Cancelled'
  when 'canceled' then 'Cancelled'
  else status end;

-- Null redemption statuses are retained because legacy earn-only ledger rows
-- are not equivalent to a new Pending request. Known values are canonicalized.
update redemption
set status = case lower(btrim(status))
  when 'pending' then 'Pending'
  when 'approved' then 'Approved'
  when 'rejected' then 'Rejected'
  when 'cancelled' then 'Cancelled'
  when 'canceled' then 'Cancelled'
  when 'refunded' then 'Refunded'
  else status end
where status is not null;

do $$
declare
  table_name text;
  invalid_count bigint;
begin
  foreach table_name in array array[
    'grooming_booking', 'daycare_booking', 'boarding_booking'
  ] loop
    execute format(
      'select count(*) from public.%I where booking_status is null or booking_status not in (''Pending'', ''Scheduled'', ''Done'', ''No Show'', ''Cancelled'')',
      table_name
    ) into invalid_count;
    if invalid_count > 0 then
      raise exception '% has % null/unsupported booking statuses; repair them before rerunning',
        table_name, invalid_count;
    end if;
  end loop;

  select count(*) into invalid_count
  from payment
  where status is null
    or status not in ('Pending', 'Unpaid', 'Paid', 'Refunded', 'Cancelled');
  if invalid_count > 0 then
    raise exception 'payment has % null/unsupported statuses; repair them before rerunning', invalid_count;
  end if;

  select count(*) into invalid_count
  from redemption
  where status is not null
    and status not in ('Pending', 'Approved', 'Rejected', 'Cancelled', 'Refunded');
  if invalid_count > 0 then
    raise exception 'redemption has % unsupported statuses; repair them before rerunning', invalid_count;
  end if;
end
$$;

alter table grooming_booking alter column booking_status set default 'Pending';
alter table grooming_booking alter column booking_status set not null;
alter table daycare_booking alter column booking_status set default 'Pending';
alter table daycare_booking alter column booking_status set not null;
alter table boarding_booking alter column booking_status set default 'Pending';
alter table boarding_booking alter column booking_status set not null;
alter table payment alter column status set default 'Pending';
alter table payment alter column status set not null;

do $$
declare
  item record;
begin
  for item in
    select * from (values
      ('grooming_booking', 'grooming_booking_status_check',
       'booking_status in (''Pending'', ''Scheduled'', ''Done'', ''No Show'', ''Cancelled'')'),
      ('daycare_booking', 'daycare_booking_status_check',
       'booking_status in (''Pending'', ''Scheduled'', ''Done'', ''No Show'', ''Cancelled'')'),
      ('boarding_booking', 'boarding_booking_status_check',
       'booking_status in (''Pending'', ''Scheduled'', ''Done'', ''No Show'', ''Cancelled'')'),
      ('payment', 'payment_status_check',
       'status in (''Pending'', ''Unpaid'', ''Paid'', ''Refunded'', ''Cancelled'')'),
      ('redemption', 'redemption_status_check',
       'status is null or status in (''Pending'', ''Approved'', ''Rejected'', ''Cancelled'', ''Refunded'')')
    ) as checks(table_name, constraint_name, expression)
  loop
    if not exists (
      select 1 from pg_constraint
      where conrelid = format('public.%I', item.table_name)::regclass
        and conname = item.constraint_name
    ) then
      execute format(
        'alter table public.%I add constraint %I check (%s)',
        item.table_name, item.constraint_name, item.expression
      );
    end if;
  end loop;
end
$$;

create index if not exists grooming_booking_company_status_slot_idx
  on grooming_booking(company_id, booking_status, booking_date, booking_time);
create index if not exists daycare_booking_company_status_slot_idx
  on daycare_booking(company_id, booking_status, booking_date, check_in_time);
create index if not exists boarding_booking_company_status_stay_idx
  on boarding_booking(company_id, booking_status, check_in_date, check_out_date);
create index if not exists payment_company_status_idx
  on payment(company_id, status, payment_id);

-- -------------------------------------------------------------------------
-- 3. Enforce that linked rows belong to the same company. Single-column FKs
-- prove that an ID exists but do not prevent a cross-tenant link.
-- -------------------------------------------------------------------------
do $$
declare
  item record;
begin
  for item in
    select * from (values
      ('customer', 'customer_company_customer_unique', 'company_id, customer_id'),
      ('pet', 'pet_company_pet_unique', 'company_id, pet_id'),
      ('staff', 'staff_company_staff_unique', 'company_id, staff_id'),
      ('payment', 'payment_company_payment_unique', 'company_id, payment_id'),
      ('loyaltymember', 'loyaltymember_company_loyalty_unique', 'company_id, loyalty_id'),
      ('coupon', 'coupon_company_coupon_unique', 'company_id, coupon_id'),
      ('redemption', 'redemption_company_redemption_unique', 'company_id, redemption_id')
    ) as constraints_to_add(table_name, constraint_name, columns_sql)
  loop
    if not exists (select 1 from pg_constraint where conname = item.constraint_name) then
      execute format(
        'alter table public.%I add constraint %I unique (%s)',
        item.table_name, item.constraint_name, item.columns_sql
      );
    end if;
  end loop;
end
$$;

do $$
declare
  item record;
begin
  for item in
    select * from (values
      ('customer', 'customer_company_fk', 'company_id', 'companies', 'company_id'),
      ('pet', 'pet_customer_same_company_fk', 'company_id, customer_id', 'customer', 'company_id, customer_id'),
      ('loyaltymember', 'loyalty_customer_same_company_fk', 'company_id, customer_id', 'customer', 'company_id, customer_id'),
      ('grooming_booking', 'grooming_pet_same_company_fk', 'company_id, pet_id', 'pet', 'company_id, pet_id'),
      ('grooming_booking', 'grooming_staff_same_company_fk', 'company_id, staff_id', 'staff', 'company_id, staff_id'),
      ('grooming_booking', 'grooming_payment_same_company_fk', 'company_id, payment_id', 'payment', 'company_id, payment_id'),
      ('daycare_booking', 'daycare_pet_same_company_fk', 'company_id, pet_id', 'pet', 'company_id, pet_id'),
      ('daycare_booking', 'daycare_staff_same_company_fk', 'company_id, staff_id', 'staff', 'company_id, staff_id'),
      ('daycare_booking', 'daycare_payment_same_company_fk', 'company_id, payment_id', 'payment', 'company_id, payment_id'),
      ('boarding_booking', 'boarding_pet_same_company_fk', 'company_id, pet_id', 'pet', 'company_id, pet_id'),
      ('boarding_booking', 'boarding_staff_same_company_fk', 'company_id, staff_id', 'staff', 'company_id, staff_id'),
      ('boarding_booking', 'boarding_payment_same_company_fk', 'company_id, payment_id', 'payment', 'company_id, payment_id'),
      ('leave', 'leave_staff_same_company_fk', 'company_id, staff_id', 'staff', 'company_id, staff_id'),
      ('leave', 'leave_reviewer_same_company_fk', 'company_id, reviewed_by_staff_id', 'staff', 'company_id, staff_id'),
      ('redemption', 'redemption_member_same_company_fk', 'company_id, loyalty_id', 'loyaltymember', 'company_id, loyalty_id'),
      ('redemption', 'redemption_coupon_same_company_fk', 'company_id, coupon_id', 'coupon', 'company_id, coupon_id'),
      ('payment', 'payment_redemption_same_company_fk', 'company_id, redemption_id', 'redemption', 'company_id, redemption_id'),
      ('payment', 'payment_verifier_same_company_fk', 'company_id, verified_by_staff_id', 'staff', 'company_id, staff_id'),
      ('payment', 'payment_refunder_same_company_fk', 'company_id, refunded_by_staff_id', 'staff', 'company_id, staff_id'),
      ('messages', 'messages_replier_same_company_fk', 'company_id, replied_by_staff_id', 'staff', 'company_id, staff_id')
    ) as foreign_keys(table_name, constraint_name, local_columns, target_table, target_columns)
  loop
    if not exists (select 1 from pg_constraint where conname = item.constraint_name) then
      execute format(
        'alter table public.%I add constraint %I foreign key (%s) references public.%I (%s) not valid',
        item.table_name,
        item.constraint_name,
        item.local_columns,
        item.target_table,
        item.target_columns
      );
      execute format(
        'alter table public.%I validate constraint %I',
        item.table_name,
        item.constraint_name
      );
    end if;
  end loop;
end
$$;

create index if not exists pet_company_customer_idx on pet(company_id, customer_id);
create index if not exists loyaltymember_company_customer_idx on loyaltymember(company_id, customer_id);
create index if not exists grooming_booking_company_pet_idx
  on grooming_booking(company_id, pet_id);
create index if not exists grooming_booking_company_staff_idx
  on grooming_booking(company_id, staff_id);
create index if not exists grooming_booking_company_payment_idx
  on grooming_booking(company_id, payment_id);
create index if not exists daycare_booking_company_pet_idx
  on daycare_booking(company_id, pet_id);
create index if not exists daycare_booking_company_staff_idx
  on daycare_booking(company_id, staff_id);
create index if not exists daycare_booking_company_payment_idx
  on daycare_booking(company_id, payment_id);
create index if not exists boarding_booking_company_pet_idx
  on boarding_booking(company_id, pet_id);
create index if not exists boarding_booking_company_staff_idx
  on boarding_booking(company_id, staff_id);
create index if not exists boarding_booking_company_payment_idx
  on boarding_booking(company_id, payment_id);
create index if not exists leave_company_staff_idx on leave(company_id, staff_id);
create index if not exists leave_company_reviewer_idx on leave(company_id, reviewed_by_staff_id);
create index if not exists redemption_company_loyalty_idx on redemption(company_id, loyalty_id);
create index if not exists redemption_company_coupon_idx on redemption(company_id, coupon_id);
create index if not exists payment_company_redemption_fk_idx
  on payment(company_id, redemption_id);
create index if not exists payment_company_verifier_idx
  on payment(company_id, verified_by_staff_id);
create index if not exists payment_company_refunder_idx
  on payment(company_id, refunded_by_staff_id);
create index if not exists messages_company_replier_idx
  on messages(company_id, replied_by_staff_id);

-- -------------------------------------------------------------------------
-- 4. Reinstall the availability RPC (some live projects had the tables but
-- not this function) and make PostgREST refresh newly added objects.
-- -------------------------------------------------------------------------
create or replace function public.replace_company_availability(
  p_company_id bigint,
  p_business_hours jsonb,
  p_closed_dates jsonb
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  delete from public.company_business_hours where company_id = p_company_id;
  insert into public.company_business_hours(
    company_id, day_of_week, open_time, close_time, is_closed
  )
  select p_company_id, item.day_of_week, item.open_time::time,
    item.close_time::time, item.is_closed
  from jsonb_to_recordset(coalesce(p_business_hours, '[]'::jsonb))
    as item(day_of_week smallint, open_time text, close_time text, is_closed boolean);

  delete from public.company_closed_dates where company_id = p_company_id;
  insert into public.company_closed_dates(company_id, closed_date, reason)
  select p_company_id, item.closed_date::date, nullif(btrim(item.reason), '')
  from jsonb_to_recordset(coalesce(p_closed_dates, '[]'::jsonb))
    as item(closed_date text, reason text);
end;
$$;

revoke all on function public.replace_company_availability(bigint, jsonb, jsonb)
  from public, anon, authenticated;
grant execute on function public.replace_company_availability(bigint, jsonb, jsonb)
  to service_role;

notify pgrst, 'reload schema';
