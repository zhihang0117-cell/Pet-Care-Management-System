import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";

// Read-only on purpose: rooms (room_type/capacity/price) are configured
// directly in Supabase today, same as the AI backend's own boarding
// capacity checks (app/db/relational_actions.py) — this just exposes the
// same real `room` table to the dashboard so occupancy/capacity numbers
// are computed from real policy (each room_type's actual capacity), not
// guessed from booking counts alone. Add POST/PATCH/DELETE here later if
// staff need to manage rooms from the dashboard; room_id is a text code
// (e.g. "ROOM002") assigned manually today, not a DB identity column, so a
// generic create route isn't wired until that's decided.
export const roomsRouter = Router();

roomsRouter.get(
  "/",
  asyncHandler(async (req, res) => {
    // booked_count/available/is_full are computed live by get_room_occupancy
    // (backend/sql/booking_conflict_prevention_migration.sql) — same overlap
    // counting create_booking_atomic itself enforces, so what staff see here
    // always matches what would actually be allowed. ?date=YYYY-MM-DD looks
    // at that day's occupancy instead of today (e.g. checking a future date
    // before taking a boarding booking over the phone).
    const date = /^\d{4}-\d{2}-\d{2}$/.test(req.query.date || "") ? req.query.date : undefined;
    const { data, error } = await supabase.rpc("get_room_occupancy", {
      p_company_id: req.companyId,
      ...(date ? { p_date: date } : {}),
    });
    if (error) return res.status(400).json({ error: error.message });
    res.json(data);
  })
);
