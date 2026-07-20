-- Run this once in the Supabase SQL editor (Database > SQL Editor).
-- It adds one column and one function; it does not touch your existing data.

-- 1. payment needs somewhere to record *when it was verified*,
--    separate from the "date" column (which is the booking/service date).
alter table payment
  add column if not exists paid_at timestamptz;

alter table payment
  add column if not exists verified_by_staff_id int references staff(staff_id);

-- Remove the original five-argument version before installing the complete,
-- company-scoped transaction below.
drop function if exists verify_payment(int, int, int, numeric, int);

-- 2. Atomic verification: amount, payment method, loyalty ledger, payment
-- status and booking status all commit together or all roll back together.
create or replace function verify_payment(
  p_company_id int,
  p_payment_id int,
  p_loyalty_id int default null,
  p_coupon_id int default null,
  p_earn_rate numeric default 1,
  p_verified_by int default null,
  p_payment_method text default null,
  p_final_amount numeric default null
)
returns jsonb
language plpgsql
as $$
declare
  v_payment      payment%rowtype;
  v_member       loyaltymember%rowtype;
  v_coupon       coupon%rowtype;
  v_spend        int := 0;
  v_earn         int;
  v_new_balance  int;
  v_new_tier     text;
  v_redemption_id int;
begin
  -- Lock the payment row so two staff can't verify the same payment twice at once.
  select * into v_payment
  from payment
  where payment_id = p_payment_id
    and company_id = p_company_id
  for update;

  if not found then
    raise exception 'Payment % not found', p_payment_id using errcode = 'P0002';
  end if;

  if v_payment.status not in ('Pending', 'Unpaid') then
    raise exception 'Payment % cannot be verified from status %', p_payment_id, v_payment.status using errcode = 'P0001';
  end if;

  if p_payment_method is null or p_payment_method not in ('Cash', 'Card', 'E-Wallet', 'Bank Transfer', 'Online') then
    raise exception 'A valid payment method is required' using errcode = 'P0001';
  end if;

  v_payment.final_amount := coalesce(p_final_amount, v_payment.final_amount);

  -- A non-member can pay normally. Loyalty locking/earning only applies when
  -- the booking's customer has a loyalty row.
  if p_loyalty_id is not null then
    select * into v_member
    from loyaltymember
    where loyalty_id = p_loyalty_id
      and company_id = p_company_id
    for update;

    if not found then
      raise exception 'Loyalty member % not found', p_loyalty_id using errcode = 'P0002';
    end if;
  end if;

  -- Validate + resolve the voucher, if one is being redeemed at verification time.
  if p_coupon_id is not null then
    if p_loyalty_id is null then
      raise exception 'A loyalty member is required to redeem a voucher' using errcode = 'P0001';
    end if;

    select * into v_coupon from coupon
    where coupon_id = p_coupon_id
      and company_id = p_company_id;

    if not found then
      raise exception 'Coupon % not found', p_coupon_id using errcode = 'P0002';
    end if;

    if v_member.points_balance < v_coupon.points_required then
      raise exception 'Not enough points: member has %, voucher needs %',
        v_member.points_balance, v_coupon.points_required using errcode = 'P0001';
    end if;

    if v_coupon.expiry_date is not null and v_coupon.expiry_date < current_date then
      raise exception 'Coupon % has expired', p_coupon_id using errcode = 'P0001';
    end if;

    v_spend := v_coupon.points_required;
  end if;

  if p_loyalty_id is not null then
    v_earn := round(v_payment.final_amount * p_earn_rate);
    v_new_balance := v_member.points_balance - v_spend + v_earn;

    v_new_tier := case
      when v_new_balance >= 1200 then 'Platinum'
      when v_new_balance >= 700  then 'Gold'
      when v_new_balance >= 300  then 'Silver'
      else 'Bronze'
    end;

    insert into redemption (
      company_id, loyalty_id, loyalty_earn, loyalty_spend, coupon_id,
      status, create_date, create_time, approved_date, approved_time
    )
    values (
      v_payment.company_id, p_loyalty_id, v_earn, v_spend, p_coupon_id,
      'Approved', current_date, localtime, current_date, localtime
    )
    returning redemption_id into v_redemption_id;

    update loyaltymember
    set points_balance  = v_new_balance,
        tier            = v_new_tier,
        redemption_made = redemption_made + case when v_spend > 0 then 1 else 0 end
    where loyalty_id = p_loyalty_id
      and company_id = p_company_id;
  else
    v_earn := 0;
    v_new_balance := null;
    v_new_tier := null;
  end if;

  -- Mark the payment as paid and link it to the redemption record we just made.
  update payment
  set status             = 'Paid',
      paid_at             = now(),
      redemption_id       = v_redemption_id,
      verified_by_staff_id = p_verified_by,
      payment_method       = case
        when v_payment.final_amount = 0 and p_coupon_id is not null then 'Loyalty Redemption'
        else p_payment_method
      end,
      final_amount         = v_payment.final_amount
  where payment_id = p_payment_id
    and company_id = p_company_id;

  -- Exactly one of these tables should contain the payment_id. Keeping all
  -- three updates inside this function makes booking completion transactional.
  update grooming_booking set booking_status = 'Done'
  where company_id = p_company_id and payment_id = p_payment_id;
  update daycare_booking set booking_status = 'Done'
  where company_id = p_company_id and payment_id = p_payment_id;
  update boarding_booking set booking_status = 'Done'
  where company_id = p_company_id and payment_id = p_payment_id;

  return jsonb_build_object(
    'payment_id', p_payment_id,
    'status', 'Paid',
    'paid_at', now(),
    'redemption_id', v_redemption_id,
    'loyalty_id', p_loyalty_id,
    'points_spent', v_spend,
    'points_earned', v_earn,
    'new_points_balance', v_new_balance,
    'new_tier', v_new_tier
  );
end;
$$;

revoke all on function verify_payment(int, int, int, int, numeric, int, text, numeric)
  from public, anon, authenticated;
grant execute on function verify_payment(int, int, int, int, numeric, int, text, numeric)
  to service_role;
