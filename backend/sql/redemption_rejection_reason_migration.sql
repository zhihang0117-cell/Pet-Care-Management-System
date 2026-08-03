-- Run once in the Supabase SQL editor.
--
-- decide_redemption() (backend/sql/verify_payment_function.sql) previously
-- had no way to record WHY a redemption was rejected — the Node route
-- (backend/src/routes/redemptions.js POST /:id/decision) only ever sent
-- {status}, and the function itself had no p_reason parameter or column to
-- put one in, unlike cancel_approved_redemption (backend/sql/
-- crud_hardening_migration.sql), which already requires and stores a
-- cancellation_reason. A rejected customer got a bare "Rejected" with no
-- explanation anywhere in the system, and staff had no field to record one
-- either. This adds the same kind of column for the reject path and makes
-- decide_redemption accept an optional reason, required only when rejecting.

alter table redemption add column if not exists rejection_reason text;

-- Drop the original 3-argument signature explicitly first — appending a
-- parameter changes the function's identity (argument list), so plain
-- CREATE OR REPLACE below could otherwise leave both the old and new
-- signatures installed as overloads instead of cleanly replacing it.
drop function if exists decide_redemption(int, int, text);

create or replace function decide_redemption(
  p_company_id int,
  p_redemption_id int,
  p_status text,
  p_reason text default null
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
  if p_status = 'Rejected' and nullif(btrim(p_reason), '') is null then
    raise exception 'A rejection reason is required' using errcode = 'P0001';
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
        approved_time = null,
        rejection_reason = btrim(p_reason)
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
