-- Run once in the Supabase SQL editor.
--
-- Today, every `active` staff row is treated as capable of every service
-- (GROOMING/DAYCARE/BOARDING) and eligible for auto-assignment to any
-- booking — app/db/relational_actions.py's _staff_day_roster has no
-- service-type filtering at all. This adds the two columns needed to
-- express real staff capability:
--   - provides_service: false for staff who never perform bookable
--     services at all (e.g. reception/admin-only) — never auto-assigned
--     or offered for ANY service.
--   - service_types_json: which of GROOMING/DAYCARE/BOARDING this staff
--     member is actually qualified to perform.
--
-- Defaults are chosen to exactly preserve today's real behavior for every
-- existing row (provides_service=true, all three services) — this
-- migration does not narrow anyone's eligibility on its own; a manager
-- has to explicitly edit a staff member's capabilities (web/staff.html)
-- for this to change anything.
--
-- Same jsonb-array-of-strings convention as the existing off_days_json
-- column on this same table (read defensively via `row.get(x) or []` in
-- Python / `member.x || []` in JS) — consistent access pattern, and a
-- company's staff table is always small, so no native array type or
-- separate junction table is warranted here.

alter table staff add column if not exists provides_service boolean not null default true;
alter table staff add column if not exists service_types_json jsonb not null default '["GROOMING","DAYCARE","BOARDING"]'::jsonb;
