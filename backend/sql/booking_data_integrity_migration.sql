-- Monetary precision and fail-closed booking invariants.
-- Apply before booking_conflict_prevention_migration.sql, then re-run that
-- conflict migration so its latest RPC definitions remain active.

alter table grooming_booking
  alter column price type numeric(12,2) using price::numeric,
  alter column add_on_price type numeric(12,2) using add_on_price::numeric;

alter table daycare_booking
  alter column price type numeric(12,2) using price::numeric,
  alter column add_on_price type numeric(12,2) using add_on_price::numeric;

alter table boarding_booking
  alter column price_per_night type numeric(12,2) using price_per_night::numeric,
  alter column total_price type numeric(12,2) using total_price::numeric;

alter table payment
  alter column base_price type numeric(12,2) using base_price::numeric,
  alter column final_amount type numeric(12,2) using final_amount::numeric;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'grooming_price_nonnegative') then
    alter table grooming_booking add constraint grooming_price_nonnegative
      check (price >= 0 and coalesce(add_on_price, 0) >= 0) not valid;
  end if;
  if not exists (select 1 from pg_constraint where conname = 'daycare_price_nonnegative') then
    alter table daycare_booking add constraint daycare_price_nonnegative
      check (price >= 0 and coalesce(add_on_price, 0) >= 0) not valid;
  end if;
  if not exists (select 1 from pg_constraint where conname = 'boarding_price_nonnegative') then
    alter table boarding_booking add constraint boarding_price_nonnegative
      check (price_per_night >= 0 and total_price >= 0) not valid;
  end if;
  if not exists (select 1 from pg_constraint where conname = 'payment_amount_nonnegative') then
    alter table payment add constraint payment_amount_nonnegative
      check (base_price >= 0 and final_amount >= 0) not valid;
  end if;
  if not exists (select 1 from pg_constraint where conname = 'daycare_active_interval_valid') then
    alter table daycare_booking add constraint daycare_active_interval_valid
      check (
        booking_status not in ('Pending', 'Scheduled')
        or (booking_date is not null and check_in_time is not null
            and check_out_time is not null and check_out_time > check_in_time)
      ) not valid;
  end if;
  if not exists (select 1 from pg_constraint where conname = 'boarding_active_interval_valid') then
    alter table boarding_booking add constraint boarding_active_interval_valid
      check (
        booking_status not in ('Pending', 'Scheduled')
        or (check_in_date is not null and check_out_date is not null
            and check_in_time is not null and check_out_time is not null
            and (check_out_date + check_out_time) > (check_in_date + check_in_time))
      ) not valid;
  end if;
end
$$;

notify pgrst, 'reload schema';
