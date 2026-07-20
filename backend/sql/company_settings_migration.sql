-- ============================================================================
-- COMPANY SETTINGS COLUMN
-- ============================================================================
-- setting.html (business hours, payment methods accepted, invoice prefix,
-- tax name, language/timezone/currency/units, booking URL, WhatsApp number,
-- booking confirmation rule) and loyalty.html (points-earned-per-RM rate)
-- have no backing columns in `companies` today — only company_name/country/
-- street_address/city/state/postcode/business_description/logo_path exist.
--
-- Rather than adding ~15 narrow columns for fields that are still likely to
-- change shape, this adds ONE flexible `settings_json` column. Every route
-- reads/writes specific keys inside it (see routes/companies.js), so the
-- column can grow new settings later without another migration.
--
-- This does not touch or delete any existing data — safe to run at any time.
-- ============================================================================

alter table companies
  add column if not exists settings_json jsonb not null default '{}'::jsonb;
