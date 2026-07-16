-- Run this once in the Supabase SQL editor (Database > SQL Editor).
-- It adds one column and one function; it does not touch your existing data.

-- 1. payment needs somewhere to record *when it was verified*,
--    separate from the "date" column (which is the booking/service date).
alter table payment
  add column if not exists paid_at timestamptz;

alter table payment
  add column if not exists verified_by_staff_id int references staff(staff_id);

-- 2. The atomic verification function.
--    p_payment_id      : payment.payment_id to verify
--    p_loyalty_id      : the loyaltymember.loyalty_id who this payment belongs to
--    p_coupon_id       : coupon.coupon_id if a voucher is being redeemed, else null
--    p_earn_rate       : points earned per RM1 of final_amount (backend passes this in
--                        from LOYALTY_EARN_RATE so it's configurable without touching SQL)
--    p_verified_by     : staff_id of whoever clicked "Verify"
create or replace function verify_payment(
  p_payment_id int,
  p_loyalty_id int,
  p_coupon_id int default null,
  p_earn_rate numeric default 1,
  p_verified_by int default null
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
  for update;

  if not found then
    raise exception 'Payment % not found', p_payment_id using errcode = 'P0002';
  end if;

  if v_payment.status = 'Paid' then
    raise exception 'Payment % has already been verified', p_payment_id using errcode = 'P0001';
  end if;

  -- Lock the member row so points can't be double-spent by a concurrent request.
  select * into v_member
  from loyaltymember
  where loyalty_id = p_loyalty_id
  for update;

  if not found then
    raise exception 'Loyalty member % not found', p_loyalty_id using errcode = 'P0002';
  end if;

  -- Validate + resolve the voucher, if one is being redeemed at verification time.
  if p_coupon_id is not null then
    select * into v_coupon from coupon where coupon_id = p_coupon_id;

    if not found then
      raise exception 'Coupon % not found', p_coupon_id using errcode = 'P0002';
    end if;

    if v_member.points_balance < v_coupon.points_required then
      raise exception 'Not enough points: member has %, voucher needs %',
        v_member.points_balance, v_coupon.points_required using errcode = 'P0001';
    end if;

    v_spend := v_coupon.points_required;
  end if;

  -- Points earned from this payment (based on the already-discounted final_amount).
  v_earn := round(v_payment.final_amount * p_earn_rate);

  v_new_balance := v_member.points_balance - v_spend + v_earn;

  v_new_tier := case
    when v_new_balance >= 1200 then 'Platinum'
    when v_new_balance >= 700  then 'Gold'
    when v_new_balance >= 300  then 'Silver'
    else 'Bronze'
  end;

  -- Record the loyalty transaction.
  insert into redemption (company_id, loyalty_id, loyalty_earn, loyalty_spend, coupon_id)
  values (v_payment.company_id, p_loyalty_id, v_earn, v_spend, p_coupon_id)
  returning redemption_id into v_redemption_id;

  -- Apply the point change + possible tier change.
  update loyaltymember
  set points_balance  = v_new_balance,
      tier            = v_new_tier,
      redemption_made = redemption_made + case when v_spend > 0 then 1 else 0 end
  where loyalty_id = p_loyalty_id;

  -- Mark the payment as paid and link it to the redemption record we just made.
  update payment
  set status             = 'Paid',
      paid_at             = now(),
      redemption_id       = v_redemption_id,
      verified_by_staff_id = p_verified_by
  where payment_id = p_payment_id;

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
