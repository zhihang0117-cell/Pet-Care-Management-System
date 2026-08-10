-- Run this once in the Supabase SQL editor (Database > SQL Editor).
-- It adds one column and one function; it does not touch your existing data.

-- 1. payment needs somewhere to record *when it was verified*,
--    separate from the "date" column (which is the booking/service date).
alter table payment
  add column if not exists paid_at timestamptz;

alter table payment
  add column if not exists verified_by_staff_id int references staff(staff_id);

create index if not exists payment_company_redemption_idx
  on payment (company_id, redemption_id)
  where redemption_id is not null;

create index if not exists redemption_company_status_idx
  on redemption (company_id, status);

-- A voucher request is created as Pending and linked to one payment. No
-- points move until a manager approves it.
create or replace function request_redemption(
  p_company_id int,
  p_payment_id int,
  p_coupon_id int
)
returns jsonb
language plpgsql
as $$
declare
  v_payment payment%rowtype;
  v_existing redemption%rowtype;
  v_coupon coupon%rowtype;
  v_member loyaltymember%rowtype;
  v_pet_id int;
  v_redemption_id int;
begin
  select * into v_payment
  from payment
  where company_id = p_company_id and payment_id = p_payment_id
  for update;

  if not found then
    raise exception 'Payment % not found', p_payment_id using errcode = 'P0002';
  end if;
  if v_payment.status not in ('Pending', 'Unpaid') then
    raise exception 'Point redemption can only be requested for an unpaid payment' using errcode = 'P0001';
  end if;

  if v_payment.redemption_id is not null then
    select * into v_existing
    from redemption
    where company_id = p_company_id and redemption_id = v_payment.redemption_id
    for update;
    if found and v_existing.status in ('Pending', 'Approved') then
      raise exception 'This payment already has a % point redemption', lower(v_existing.status) using errcode = 'P0001';
    end if;
  end if;

  select linked.pet_id into v_pet_id
  from (
    select pet_id from grooming_booking where company_id = p_company_id and payment_id = p_payment_id
    union all
    select pet_id from daycare_booking where company_id = p_company_id and payment_id = p_payment_id
    union all
    select pet_id from boarding_booking where company_id = p_company_id and payment_id = p_payment_id
  ) linked
  limit 1;

  if v_pet_id is null then
    raise exception 'No booking found for payment %', p_payment_id using errcode = 'P0002';
  end if;

  select lm.* into v_member
  from loyaltymember lm
  join pet p on p.customer_id = lm.customer_id and p.company_id = lm.company_id
  where lm.company_id = p_company_id and p.pet_id = v_pet_id
  for update of lm;

  if not found then
    raise exception 'No loyalty member found for this payment' using errcode = 'P0002';
  end if;

  select * into v_coupon
  from coupon
  where company_id = p_company_id and coupon_id = p_coupon_id
  for share;

  if not found then
    raise exception 'Coupon % not found', p_coupon_id using errcode = 'P0002';
  end if;
  if v_coupon.expiry_date is not null and v_coupon.expiry_date < current_date then
    raise exception 'This voucher has expired' using errcode = 'P0001';
  end if;
  if v_member.points_balance < v_coupon.points_required then
    raise exception 'Not enough points: member has %, voucher needs %',
      v_member.points_balance, v_coupon.points_required using errcode = 'P0001';
  end if;

  insert into redemption (
    company_id, loyalty_id, loyalty_earn, loyalty_spend, coupon_id,
    status, create_date, create_time
  )
  values (
    p_company_id, v_member.loyalty_id, 0, v_coupon.points_required, p_coupon_id,
    'Pending', current_date, localtime
  )
  returning redemption_id into v_redemption_id;

  update payment
  set redemption_id = v_redemption_id
  where company_id = p_company_id and payment_id = p_payment_id;

  return jsonb_build_object(
    'redemption_id', v_redemption_id,
    'payment_id', p_payment_id,
    'status', 'Pending',
    'points_requested', v_coupon.points_required
  );
end;
$$;

-- Approval revalidates and locks the member/coupon before deducting exactly
-- once. Rejection never changes the member balance.
create or replace function decide_redemption(
  p_company_id int,
  p_redemption_id int,
  p_status text
)
returns jsonb
language plpgsql
as $$
declare
  v_redemption redemption%rowtype;
  v_coupon coupon%rowtype;
  v_member loyaltymember%rowtype;
  v_payment_id int;
  v_spend int;
  v_new_balance int;
  v_new_tier text;
begin
  if p_status not in ('Approved', 'Rejected') then
    raise exception 'status must be Approved or Rejected' using errcode = 'P0001';
  end if;

  -- All payment/redemption/member functions lock in this exact order to
  -- prevent approval and verification deadlocks.
  select payment_id into v_payment_id
  from payment
  where company_id = p_company_id and redemption_id = p_redemption_id
  for update;

  if v_payment_id is null then
    raise exception 'No payment is linked to redemption %', p_redemption_id using errcode = 'P0002';
  end if;

  select * into v_redemption
  from redemption
  where company_id = p_company_id and redemption_id = p_redemption_id
  for update;

  if not found then
    raise exception 'Redemption % not found', p_redemption_id using errcode = 'P0002';
  end if;
  if v_redemption.status <> 'Pending' then
    raise exception 'Only a Pending redemption can be decided (current status: %)',
      v_redemption.status using errcode = 'P0001';
  end if;

  if p_status = 'Approved' then
    select * into v_coupon
    from coupon
    where company_id = p_company_id and coupon_id = v_redemption.coupon_id
    for share;
    if not found then
      raise exception 'Voucher for this redemption no longer exists' using errcode = 'P0002';
    end if;
    if v_coupon.expiry_date is not null and v_coupon.expiry_date < current_date then
      raise exception 'This voucher has expired' using errcode = 'P0001';
    end if;

    select * into v_member
    from loyaltymember
    where company_id = p_company_id and loyalty_id = v_redemption.loyalty_id
    for update;
    if not found then
      raise exception 'Loyalty member not found' using errcode = 'P0002';
    end if;

    v_spend := v_coupon.points_required;
    if v_member.points_balance < v_spend then
      raise exception 'Not enough points: member has %, voucher needs %',
        v_member.points_balance, v_spend using errcode = 'P0001';
    end if;

    v_new_balance := v_member.points_balance - v_spend;
    v_new_tier := case
      when v_new_balance >= 1200 then 'Platinum'
      when v_new_balance >= 700 then 'Gold'
      when v_new_balance >= 300 then 'Silver'
      else 'Bronze'
    end;

    update loyaltymember
    set points_balance = v_new_balance,
        tier = v_new_tier,
        redemption_made = coalesce(redemption_made, 0) + 1
    where company_id = p_company_id and loyalty_id = v_redemption.loyalty_id;

    update redemption
    set status = 'Approved',
        loyalty_spend = v_spend,
        approved_date = current_date,
        approved_time = localtime
    where company_id = p_company_id and redemption_id = p_redemption_id;
  else
    update redemption
    set status = 'Rejected',
        approved_date = null,
        approved_time = null
    where company_id = p_company_id and redemption_id = p_redemption_id;
    v_new_balance := null;
  end if;

  return jsonb_build_object(
    'redemption_id', p_redemption_id,
    'payment_id', v_payment_id,
    'status', p_status,
    'points_spent', case when p_status = 'Approved' then v_spend else 0 end,
    'new_points_balance', v_new_balance
  );
end;
$$;

-- Remove the original five-argument version before installing the complete,
-- company-scoped transaction below.
drop function if exists verify_payment(int, int, int, numeric, int);

-- 2. Atomic verification: amount, payment method, loyalty ledger, redemption,
-- and payment status commit together. Booking workflow status is independent.
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
  v_redemption   redemption%rowtype;
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

  -- A payment with a linked redemption cannot be verified until that exact
  -- request has been approved. Points were already deducted at approval.
  if v_payment.redemption_id is not null then
    select * into v_redemption
    from redemption
    where company_id = p_company_id and redemption_id = v_payment.redemption_id
    for update;
    if not found or v_redemption.status <> 'Approved' then
      raise exception 'No approved point redemption for this payment' using errcode = 'P0001';
    end if;
    if p_coupon_id is null or p_coupon_id <> v_redemption.coupon_id then
      raise exception 'Voucher does not match the approved point redemption' using errcode = 'P0001';
    end if;
    if p_loyalty_id is null or p_loyalty_id <> v_redemption.loyalty_id then
      raise exception 'Loyalty member does not match the approved point redemption' using errcode = 'P0001';
    end if;
    v_spend := coalesce(v_redemption.loyalty_spend, 0);
  elsif p_coupon_id is not null then
    raise exception 'No approved point redemption for this payment' using errcode = 'P0001';
  end if;

  -- A non-member can pay normally. Lock member after payment/redemption,
  -- matching request_redemption and decide_redemption.
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

  if p_loyalty_id is not null then
    v_earn := round(v_payment.final_amount * p_earn_rate);
    v_new_balance := v_member.points_balance + v_earn;

    v_new_tier := case
      when v_new_balance >= 1200 then 'Platinum'
      when v_new_balance >= 700  then 'Gold'
      when v_new_balance >= 300  then 'Silver'
      else 'Bronze'
    end;

    if v_payment.redemption_id is not null then
      update redemption
      set loyalty_earn = v_earn
      where company_id = p_company_id and redemption_id = v_payment.redemption_id;
      v_redemption_id := v_payment.redemption_id;
    else
      insert into redemption (
        company_id, loyalty_id, loyalty_earn, loyalty_spend, coupon_id,
        status, create_date, create_time, approved_date, approved_time
      )
      values (
        v_payment.company_id, p_loyalty_id, v_earn, 0, null,
        'Approved', current_date, localtime, current_date, localtime
      )
      returning redemption_id into v_redemption_id;
    end if;

    update loyaltymember
    set points_balance  = v_new_balance,
        tier            = v_new_tier,
        redemption_made = coalesce(redemption_made, 0)
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
revoke all on function request_redemption(int, int, int) from public, anon, authenticated;
grant execute on function request_redemption(int, int, int) to service_role;
revoke all on function decide_redemption(int, int, text) from public, anon, authenticated;
grant execute on function decide_redemption(int, int, text) to service_role;
