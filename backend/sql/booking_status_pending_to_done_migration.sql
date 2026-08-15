-- Allows a booking to move directly from "Pending" to "Done", per explicit
-- request 2026-08-11 ("为什么我的kanban依旧不能从pending换成done呢" —
-- confirmed live this was previously disallowed by design, not a bug: the
-- intended path was the two-step Pending -> Scheduled -> Done. Business
-- decision made to also allow the direct one-step path.
--
-- Context: booking_status_transition_allowed(p_old, p_new) and the RPCs
-- that enforce it (update_booking_atomic, called by both the Python AI
-- backend and this Node dashboard's PATCH /api/bookings/:type/:id — see
-- bookingService.js) exist ONLY in the live database — neither function is
-- in any committed migration in this repo. This file is the first time
-- either has been captured in version control.
--
-- booking_status_transition_allowed's full CURRENT behavior was verified
-- directly against the live database (every ordered pair of the 5 real
-- statuses tested via the RPC itself, not guessed) before writing this:
--   Scheduled -> Done        allowed
--   Scheduled -> No Show     allowed
--   Pending   -> Scheduled   allowed
--   every other ordered pair blocked (Cancelled is handled by a separate,
--   dedicated cancel_booking_atomic path this function is never consulted
--   for — confirmed live, cancelling from any status succeeds through
--   that RPC regardless of what this function returns for it).
--
-- This migration is deliberately scoped to ONLY this small checker
-- function, not update_booking_atomic itself — its exact current source
-- was not available to reconstruct with full confidence (not captured in
-- any committed migration either), and it is not necessary to touch: the
-- error text update_booking_atomic raises for a blocked transition exactly
-- matches what this function's own rejections would produce, indicating
-- update_booking_atomic calls this function internally as its single
-- source of truth for the rule, rather than duplicating the check itself.
--
-- RUN THIS FILE DIRECTLY IN THE SUPABASE SQL EDITOR (or via a direct
-- Postgres connection/psql) — there is no automated migration runner in
-- this repo, and the coding agent that authored this file has no DDL
-- execution access, only the same REST API your app already uses.
create or replace function booking_status_transition_allowed(p_old text, p_new text)
returns boolean
language sql
stable
as $$
  select (p_old, p_new) in (
    ('Scheduled', 'Done'),
    ('Scheduled', 'No Show'),
    ('Pending', 'Scheduled'),
    ('Pending', 'Done')
  );
$$;
