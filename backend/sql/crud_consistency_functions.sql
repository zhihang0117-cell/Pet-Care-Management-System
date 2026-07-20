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
