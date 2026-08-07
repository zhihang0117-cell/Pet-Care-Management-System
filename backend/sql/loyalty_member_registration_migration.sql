-- One loyalty account per customer and an atomic registration entry point
-- shared by the AI backend and the staff dashboard.

do $$
begin
  if exists (
    select 1 from loyaltymember
    group by company_id, customer_id
    having count(*) > 1
  ) then
    raise exception 'Duplicate loyaltymember rows exist for company_id/customer_id; merge them before applying this migration';
  end if;
  if not exists (
    select 1 from pg_constraint
    where conname = 'loyaltymember_company_customer_unique'
  ) then
    alter table loyaltymember
      add constraint loyaltymember_company_customer_unique
      unique (company_id, customer_id);
  end if;
end
$$;

create or replace function register_loyalty_member_atomic(
  p_company_id int,
  p_customer_id int,
  p_points_balance int default 0
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_member loyaltymember%rowtype;
begin
  if p_points_balance < 0 then
    raise exception 'points balance must be non-negative' using errcode = 'P0001';
  end if;
  if not exists (
    select 1 from customer
    where company_id = p_company_id and customer_id = p_customer_id
  ) then
    raise exception 'Customer not found for this company' using errcode = 'P0002';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(
    p_company_id::text || ':loyalty-customer:' || p_customer_id::text, 0
  ));
  select * into v_member from loyaltymember
    where company_id = p_company_id and customer_id = p_customer_id;
  if found then
    return jsonb_build_object('member', to_jsonb(v_member), 'already_member', true);
  end if;

  insert into loyaltymember(
    company_id, customer_id, tier, points_balance, redemption_made
  ) values (
    p_company_id, p_customer_id, 'Bronze', p_points_balance, 0
  ) returning * into v_member;
  return jsonb_build_object('member', to_jsonb(v_member), 'already_member', false);
end;
$$;

revoke all on function register_loyalty_member_atomic(int, int, int)
  from public, anon, authenticated;
grant execute on function register_loyalty_member_atomic(int, int, int)
  to service_role;

notify pgrst, 'reload schema';
