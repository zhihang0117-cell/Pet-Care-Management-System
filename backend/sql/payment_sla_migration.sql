-- Run this once in the Supabase SQL editor (Database > SQL Editor).
-- It adds one column; it does not touch or delete any existing data.

-- ============================================================================
-- PAYMENT SLA ANCHOR
-- ============================================================================
-- payment.date is the booking/service date (day only, no time), and paid_at
-- is only set once verified. Neither can answer "how long has this payment
-- been sitting unverified?" — that needs a real creation timestamp.
--
-- Existing rows get `now()` as a one-time backfill (there's no better value
-- to reconstruct history from), so they won't show a false SLA breach the
-- moment this runs. Every new payment gets an accurate value going forward
-- since bookingService.js's INSERT never sets this column — it's always the
-- DB default.
--
-- A redemption row is created atomically at the same instant its linked
-- payment is verified (see verify_payment_function.sql), so there's no
-- separate "pending redemption" state to track — the payment SLA below
-- covers both.
-- ============================================================================

alter table payment
  add column if not exists created_at timestamptz not null default now();
