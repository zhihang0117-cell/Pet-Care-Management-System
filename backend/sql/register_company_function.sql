-- ============================================================================
-- COMPANY REGISTRATION (bootstrap) FUNCTION
-- ============================================================================
-- This is called ONLY from the backend (routes/auth.js), using the
-- service_role key, right after supabase.auth.admin.createUser() creates the
-- login. It is intentionally NOT reachable through a normal RLS-governed
-- client insert: at registration time there is no existing accounts row
-- for this user yet, so current_company_id()/is_current_account_manager()
-- (used by rls_policies.sql) would both resolve to null/false — there is no
-- tenant to scope the client to yet. Bootstrapping a brand new tenant is a
-- privileged, one-time, atomic operation, so it lives here instead.
--
-- SECURITY DEFINER so it can insert into companies/accounts
-- regardless of the RLS policies on them (same reasoning as
-- current_company_id() in rls_policies.sql).
-- ============================================================================

create or replace function register_company(
  p_auth_user_id uuid,
  p_company_name text,
  p_country text default null,
  p_street_address text default null,
  p_city text default null,
  p_state text default null,
  p_postcode text default null,
  p_business_description text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_company_id int;
  v_account_id int;
begin
  -- Guard against a user who already has a company registering a second one
  -- through this path (they should use the "add staff account" flow instead,
  -- or log into their existing company).
  if exists (select 1 from accounts where auth_user_id = p_auth_user_id) then
    raise exception 'This login is already linked to a company.' using errcode = 'P0001';
  end if;

  insert into companies (
    manager_user_id, company_name, country, street_address, city, state, postcode, business_description
  )
  values (
    p_auth_user_id, p_company_name, p_country, p_street_address, p_city, p_state, p_postcode, p_business_description
  )
  returning company_id into v_company_id;

  insert into accounts (auth_user_id, company_id, role, account_status)
  values (p_auth_user_id, v_company_id, 'manager', 'active')
  returning account_id into v_account_id;

  return jsonb_build_object(
    'company_id', v_company_id,
    'account_id', v_account_id,
    'role', 'manager'
  );
end;
$$;
