-- One-off data fix, NOT a standing rule. Run once in the Supabase SQL
-- editor after review. Do NOT run automatically.
--
-- Confirmed live (2026-08-09): boarding_booking_id 204 and 294 are two
-- independent Pending bookings for the same pet (pet_id=1), same room
-- (Mars Room, capacity 1), and the exact same stay (check_in 2026-08-08 to
-- check_out 2026-08-11) — identical price, staff_id, feeding/medical
-- instructions, and notes. Their linked payments (737 and 1007) were
-- created about 7 minutes apart and are both still status='Pending'
-- (nothing was ever actually charged), so cancelling one is a pure
-- booking-record fix, not a refund situation. Their linked redemption rows
-- (642 and 873) are both empty placeholder rows (no coupon, no status) —
-- nothing to unwind there either.
--
-- This is only a fix for THIS existing duplicate — it does not change any
-- future-booking behavior. The room-capacity check
-- (app/db/relational_actions.py's _room_capacity_status /
-- backend/sql/booking_conflict_prevention_migration.sql's
-- room_has_capacity + advisory lock) is what's supposed to prevent a new
-- one of these from being created going forward; this script only cleans
-- up the one pair that already exists.
--
-- Keeping boarding_booking_id 204 (the earlier payment, created first) as
-- the real booking; cancelling 294 as the duplicate. Swap the id below
-- first if you know it should go the other way (e.g. if 294 is the one
-- the customer actually confirmed).
--
-- The safety condition `and booking_status = 'Pending'` means this is a
-- no-op (updates 0 rows) if staff already changed its status through the
-- dashboard before you run this — safe to run even if you're not 100%
-- sure of the current state.

update boarding_booking
set booking_status = 'Cancelled',
    notes = trim(both ' ' from coalesce(notes, '') || ' ' ||
      '[Auto-cancelled 2026-08-09: duplicate of boarding_booking_id 204 — same pet, room, and dates]')
where company_id = 1
  and boarding_booking_id = 294
  and booking_status = 'Pending';

-- Review after running:
--   select boarding_booking_id, booking_status, notes
--   from boarding_booking
--   where company_id = 1 and boarding_booking_id in (204, 294);
--
-- The two linked payments (737 for the kept booking, 1007 for the
-- cancelled one) are still both 'Pending' — payment 1007 no longer has a
-- real booking behind it. Decide separately whether to also mark it
-- Cancelled/void it (not done here — this script only touches the
-- booking, not payment/redemption records, since that's your call).
