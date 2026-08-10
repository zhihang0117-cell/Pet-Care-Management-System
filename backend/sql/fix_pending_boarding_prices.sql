-- One-off data fix, NOT a standing rule. Run once in the Supabase SQL
-- editor after review. Do NOT run automatically.
--
-- Confirmed live (2026-08-09): 106 Pending/Scheduled boarding_booking rows
-- have a price_per_night that doesn't match the room's current listed
-- price (`room.price`) — e.g. a Neptune Room booking stored at 88/night
-- while `room` now lists Neptune Room at 108/night. Recomputes
-- price_per_night and total_price (= price_per_night * nights) from the
-- room table's real current rate for those rows only.
--
-- Scope is deliberately narrow — ONLY booking_status in ('Pending',
-- 'Scheduled') is touched. Done/No Show/Cancelled bookings are real
-- completed transactions; what they actually charged at the time is a
-- historical fact, not something to silently rewrite to match today's
-- room rate just because the two numbers don't match now. (183 of the 219
-- mismatched rows found are in that completed/closed state and are
-- intentionally left untouched by this script.)
--
-- half-open [check_in_date, check_out_date) matches how nights is
-- computed everywhere else in this codebase (app/db/relational_actions.py).

update boarding_booking b
set price_per_night = r.price,
    total_price = r.price * (b.check_out_date - b.check_in_date)
from room r
where b.company_id = r.company_id
  and b.room_type = r.room_type
  and b.company_id = 1
  and b.booking_status in ('Pending', 'Scheduled')
  and b.price_per_night is distinct from r.price;

-- Review before/after:
--   select b.boarding_booking_id, b.room_type, b.booking_status,
--          b.price_per_night as old_or_new_rate, r.price as room_rate,
--          b.check_in_date, b.check_out_date, b.total_price
--   from boarding_booking b
--   join room r on r.company_id = b.company_id and r.room_type = b.room_type
--   where b.company_id = 1 and b.booking_status in ('Pending', 'Scheduled')
--   order by b.boarding_booking_id;
--
-- Any payment row already linked to one of these bookings (payment.
-- final_amount) is NOT touched by this script — if a payment was already
-- generated/quoted at the old (wrong) total, that payment record needs
-- its own separate review; this only corrects the booking's own stored
-- price fields.
