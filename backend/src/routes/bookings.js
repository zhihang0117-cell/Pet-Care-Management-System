import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { BOOKING_TYPES, createBooking, updateBooking, deleteBooking } from "../lib/bookingService.js";

const SEARCHABLE = {
  grooming: ["service_name", "notes"],
  daycare: ["package_type", "special_instruction"],
  boarding: ["room_type", "notes"],
};

function requireBookingType(req, res, next) {
  const config = BOOKING_TYPES[req.params.type];
  if (!config) {
    return res
      .status(404)
      .json({ error: `Unknown booking type "${req.params.type}". Use grooming, daycare, or boarding.` });
  }
  req.bookingConfig = config;
  next();
}

export const bookingsRouter = Router();
bookingsRouter.use("/:type", requireBookingType);

// GET /api/bookings/:type?booking_status=pending&staff_id=2&pet_id=5
bookingsRouter.get(
  "/:type",
  asyncHandler(async (req, res) => {
    const { table, idColumn } = req.bookingConfig;
    let query = supabase.from(table).select("*").eq("company_id", req.companyId);

    for (const [key, value] of Object.entries(req.query)) {
      if (["search", "limit", "offset", "order"].includes(key)) continue;
      query = query.eq(key, value);
    }

    const searchableColumns = SEARCHABLE[req.params.type] || [];
    if (req.query.search && searchableColumns.length) {
      query = query.or(searchableColumns.map((c) => `${c}.ilike.%${req.query.search}%`).join(","));
    }

    query = query.order(req.query.order || idColumn, { ascending: false });
    if (req.query.limit) query = query.limit(Number(req.query.limit));

    const { data, error } = await query;
    if (error) return res.status(400).json({ error: error.message });
    res.json(data);
  })
);

bookingsRouter.get(
  "/:type/:id",
  asyncHandler(async (req, res) => {
    const { table, idColumn } = req.bookingConfig;
    const { data, error } = await supabase
      .from(table)
      .select("*")
      .eq("company_id", req.companyId)
      .eq(idColumn, req.params.id)
      .single();
    if (error) return res.status(404).json({ error: "Booking not found" });
    res.json(data);
  })
);

/**
 * POST /api/bookings/:type
 * Creates the booking AND its payment row in one call, with the
 * final amount auto-computed from base price + add-on. See lib/bookingService.js
 * for the full field list per type and the pricing logic itself.
 */
bookingsRouter.post(
  "/:type",
  asyncHandler(async (req, res) => {
    const result = await createBooking(req.params.type, req.companyId, req.body);
    res.status(201).json(result);
  })
);

bookingsRouter.patch(
  "/:type/:id",
  asyncHandler(async (req, res) => {
    const data = await updateBooking(req.params.type, req.companyId, req.params.id, req.body);
    res.json(data);
  })
);

bookingsRouter.delete(
  "/:type/:id",
  asyncHandler(async (req, res) => {
    await deleteBooking(req.params.type, req.companyId, req.params.id);
    res.status(204).end();
  })
);
