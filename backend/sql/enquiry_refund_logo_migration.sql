-- Run once in Supabase SQL Editor before using the new enquiry reply,
-- payment refund, and business-logo upload controls.

-- Store only the public object URL in Postgres. The JPG/PNG bytes live in
-- Supabase Storage's public business-assets bucket.
alter table companies
  add column if not exists logo_path text;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'business-assets',
  'business-assets',
  true,
  2097152,
  array['image/png', 'image/jpeg']
)
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

-- Preserve the actual reply and its staff owner instead of recording only a
-- timestamp. The foreign key uses ON DELETE SET NULL to keep enquiry history.
alter table messages
  add column if not exists reply_text text;

alter table messages
  add column if not exists replied_by_staff_id int;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'messages_replied_by_staff_id_fkey'
      and conrelid = 'messages'::regclass
  ) then
    alter table messages
      add constraint messages_replied_by_staff_id_fkey
      foreign key (replied_by_staff_id) references staff(staff_id) on delete set null;
  end if;
end $$;

create index if not exists messages_replied_by_staff_id_idx
  on messages (replied_by_staff_id);

-- A refund has its own timestamp, manager/staff audit owner, and required
-- reason. Existing rows remain untouched.
alter table payment
  add column if not exists refunded_at timestamptz;

alter table payment
  add column if not exists refunded_by_staff_id int;

alter table payment
  add column if not exists refund_reason text;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'payment_refunded_by_staff_id_fkey'
      and conrelid = 'payment'::regclass
  ) then
    alter table payment
      add constraint payment_refunded_by_staff_id_fkey
      foreign key (refunded_by_staff_id) references staff(staff_id) on delete set null;
  end if;
end $$;

create index if not exists payment_refunded_by_staff_id_idx
  on payment (refunded_by_staff_id);

create or replace function refund_payment(
  p_company_id int,
  p_payment_id int,
  p_refunded_by int default null,
  p_refund_reason text default null
)
returns jsonb
language plpgsql
as $$
declare
  v_payment payment%rowtype;
  v_redemption redemption%rowtype;
  v_member loyaltymember%rowtype;
  v_new_balance int;
  v_new_tier text;
begin
  if nullif(trim(p_refund_reason), '') is null then
    raise exception 'A refund reason is required' using errcode = 'P0001';
  end if;

  select * into v_payment
  from payment
  where company_id = p_company_id and payment_id = p_payment_id
  for update;

  if not found then
    raise exception 'Payment % not found', p_payment_id using errcode = 'P0002';
  end if;
  if v_payment.status <> 'Paid' then
    raise exception 'Only a Paid payment can be refunded (current status: %)', v_payment.status using errcode = 'P0001';
  end if;

  if v_payment.redemption_id is not null then
    select * into v_redemption
    from redemption
    where company_id = p_company_id and redemption_id = v_payment.redemption_id
    for update;

    if found and v_redemption.loyalty_id is not null then
      select * into v_member
      from loyaltymember
      where company_id = p_company_id and loyalty_id = v_redemption.loyalty_id
      for update;

      if found then
        v_new_balance := v_member.points_balance
          - coalesce(v_redemption.loyalty_earn, 0)
          + coalesce(v_redemption.loyalty_spend, 0);
        if v_new_balance < 0 then
          raise exception 'Refund cannot reverse earned points because the member has already spent them' using errcode = 'P0001';
        end if;

        v_new_tier := case
          when v_new_balance >= 1200 then 'Platinum'
          when v_new_balance >= 700 then 'Gold'
          when v_new_balance >= 300 then 'Silver'
          else 'Bronze'
        end;

        update loyaltymember
        set points_balance = v_new_balance,
            tier = v_new_tier,
            redemption_made = greatest(0, coalesce(redemption_made, 0) - case when coalesce(v_redemption.loyalty_spend, 0) > 0 then 1 else 0 end)
        where company_id = p_company_id and loyalty_id = v_redemption.loyalty_id;
      end if;
    end if;

    update redemption
    set status = 'Refunded'
    where company_id = p_company_id and redemption_id = v_payment.redemption_id;
  end if;

  update payment
  set status = 'Refunded',
      refunded_at = now(),
      refunded_by_staff_id = p_refunded_by,
      refund_reason = trim(p_refund_reason)
  where company_id = p_company_id and payment_id = p_payment_id;

  return jsonb_build_object(
    'payment_id', p_payment_id,
    'status', 'Refunded',
    'refunded_at', now(),
    'loyalty_balance', v_new_balance
  );
end;
$$;

revoke all on function refund_payment(int, int, int, text) from public;
grant execute on function refund_payment(int, int, int, text) to service_role;
