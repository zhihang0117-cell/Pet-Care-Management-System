-- ============================================================================
-- MULTI-TENANT RLS POLICIES
-- ============================================================================
-- Run this in Supabase SQL Editor. It's safe to re-run (uses drop-if-exists).
--
-- DESIGN: tenancy is decided by accounts (auth_user_id -> company_id),
-- NOT by staff.staff_id. staff rows have no auth_user_id column at all — they
-- are scheduling/display data (name, role, off-days), not login identities.
-- One company can have several accounts entries (manager, staff logins),
-- and a company's staff.csv roster is a separate, larger list that may not
-- map 1:1 to accounts at all. So every policy below resolves the
-- caller's company through accounts, never through staff.
--
-- NOTE ON YOUR EXPRESS BACKEND: it connects with the service_role key, which
-- bypasses RLS entirely — so enabling RLS here does not break anything your
-- backend already does; company scoping there is still done in application
-- code (see resolveCompany middleware). These policies are what protect you
-- if/when a client ever talks to Supabase directly with a user's JWT
-- (anon/authenticated key) — e.g. a future direct-read dashboard, Supabase
-- Realtime subscriptions, or simply as defense-in-depth if the service_role
-- key were ever mistakenly exposed.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. Helper functions
-- ----------------------------------------------------------------------------
-- SECURITY DEFINER is required here: without it, a policy on accounts
-- that calls current_company_id() (which itself queries accounts) would
-- recheck RLS on that inner query and recurse/deadlock. With SECURITY
-- DEFINER, the function runs with the privileges of its owner (bypassing RLS
-- for this one lookup), which is the standard Supabase pattern for this.

create or replace function current_company_id()
returns int
language sql
stable
security definer
set search_path = public
as $$
  select company_id
  from accounts
  where auth_user_id = auth.uid()
  limit 1;
$$;

create or replace function current_account_role()
returns text
language sql
stable
security definer
set search_path = public
as $$
  select role
  from accounts
  where auth_user_id = auth.uid()
  limit 1;
$$;

create or replace function is_current_account_manager()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select current_account_role() = 'manager';
$$;


-- ----------------------------------------------------------------------------
-- 2. companies — a user may only read their own company
-- ----------------------------------------------------------------------------
alter table companies enable row level security;

drop policy if exists tenant_select on companies;
create policy tenant_select on companies
  for select
  using (company_id = current_company_id());

-- No insert/update/delete policy for `authenticated` on purpose: company
-- record changes (name, address, logo, etc.) should go through your backend
-- (service_role), which already exists as saveBusinessSettings-style logic.


-- ----------------------------------------------------------------------------
-- 3. accounts — special-cased (this IS the tenancy table)
--
-- IMPORTANT: the policies below assume the caller already HAS an
-- accounts row (i.e. they belong to an existing company). They
-- deliberately do NOT support "create the very first company + manager
-- account" — that bootstrap step has no tenant to scope to yet, so it's
-- handled separately, server-side, by sql/register_company_function.sql
-- (called from the backend with service_role, never by a direct client
-- insert). Run that file too — this file alone is not the full picture.
-- ----------------------------------------------------------------------------
alter table accounts enable row level security;

-- Anyone can see their own row, and anyone can see the roster of accounts
-- within their own company (needed for a "team accounts" settings page).
drop policy if exists tenant_select on accounts;
create policy tenant_select on accounts
  for select
  using (
    auth_user_id = auth.uid()
    or company_id = current_company_id()
  );

-- Only a manager may add/remove/edit accounts, and only within their own company.
drop policy if exists manager_write on accounts;
create policy manager_write on accounts
  for all
  using (company_id = current_company_id() and is_current_account_manager())
  with check (company_id = current_company_id() and is_current_account_manager());


-- ----------------------------------------------------------------------------
-- 4. Ordinary tenant tables — standard company-scoped CRUD for any
--    authenticated member of that company.
-- ----------------------------------------------------------------------------
do $$
declare
  t text;
  ordinary_tables text[] := array[
    'customer',
    'pet',
    'staff',
    'coupon',
    'leave',
    'messages'
  ];
begin
  foreach t in array ordinary_tables loop
    execute format('alter table %I enable row level security;', t);

    execute format('drop policy if exists tenant_select on %I;', t);
    execute format(
      'create policy tenant_select on %I for select using (company_id = current_company_id());',
      t
    );

    execute format('drop policy if exists tenant_insert on %I;', t);
    execute format(
      'create policy tenant_insert on %I for insert with check (company_id = current_company_id());',
      t
    );

    execute format('drop policy if exists tenant_update on %I;', t);
    execute format(
      'create policy tenant_update on %I for update using (company_id = current_company_id()) with check (company_id = current_company_id());',
      t
    );

    execute format('drop policy if exists tenant_delete on %I;', t);
    execute format(
      'create policy tenant_delete on %I for delete using (company_id = current_company_id());',
      t
    );
  end loop;
end $$;


-- ----------------------------------------------------------------------------
-- 5. Booking tables — read for the company, but INSERT is intentionally NOT
--    granted to `authenticated`. Bookings must be created via the backend
--    (create_booking), which also writes the linked payment row with
--    the correct computed price — an authenticated user inserting a raw
--    booking row directly would create a booking with no matching payment.
--    UPDATE is allowed (staff changing booking_status, adding notes, etc.).
-- ----------------------------------------------------------------------------
do $$
declare
  t text;
  booking_tables text[] := array['grooming_booking', 'daycare_booking', 'boarding_booking'];
begin
  foreach t in array booking_tables loop
    execute format('alter table %I enable row level security;', t);

    execute format('drop policy if exists tenant_select on %I;', t);
    execute format(
      'create policy tenant_select on %I for select using (company_id = current_company_id());',
      t
    );

    execute format('drop policy if exists tenant_update on %I;', t);
    execute format(
      'create policy tenant_update on %I for update using (company_id = current_company_id()) with check (company_id = current_company_id());',
      t
    );

    -- No insert/delete policy for `authenticated` — service_role only (via the backend).
  end loop;
end $$;


-- ----------------------------------------------------------------------------
-- 6. Money/points ledger tables — READ ONLY for `authenticated`, no matter
--    the role. Balances and the redemption ledger must only ever change
--    through verify_payment() (called by your backend with service_role),
--    which is the one place that keeps points_balance and the redemption
--    log consistent with each other. This mirrors llm/tableAllowlist.js in
--    the backend, which locks these same three tables down for the LLM too
--    — same reasoning, same three tables, enforced at two different layers.
-- ----------------------------------------------------------------------------
do $$
declare
  t text;
  ledger_tables text[] := array['loyaltymember', 'redemption', 'payment'];
begin
  foreach t in array ledger_tables loop
    execute format('alter table %I enable row level security;', t);

    execute format('drop policy if exists tenant_select on %I;', t);
    execute format(
      'create policy tenant_select on %I for select using (company_id = current_company_id());',
      t
    );

    -- Deliberately no insert/update/delete policy for `authenticated` here.
  end loop;
end $$;
