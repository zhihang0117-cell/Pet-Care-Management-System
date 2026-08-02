import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { BOOKING_TYPES, createBooking, updateBooking, deleteBooking } from "../lib/bookingService.js";
import { assertAllowedQueryKeys, parsePagination, safePostgrestSearch } from "../lib/queryValidation.js";
import { callAiBackend } from "../lib/aiBackend.js";

const SEARCHABLE = {
  grooming: ["service_name", "notes"],
  daycare: ["package_type", "special_instruction"],
  boarding: ["room_type", "notes"],
};
const FILTERABLE = {
  grooming: ["booking_status", "staff_id", "pet_id", "booking_date", "service_name"],
  daycare: ["booking_status", "staff_id", "pet_id", "booking_date", "package_type"],
  boarding: ["booking_status", "staff_id", "pet_id", "check_in_date", "check_out_date", "room_type"],
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

async function resolveEnabledBookingType(req, res, next) {
  try {
    const { data, error } = await supabase
      .from("companies")
      .select("settings_json")
      .eq("company_id", req.companyId)
      .single();
    if (error) throw error;
    const configured = data?.settings_json?.selected_services;
    // Missing legacy setting means all services; an explicit [] means none.
    const enabledServices = Array.isArray(configured)
      ? configured
      : Object.keys(BOOKING_TYPES);
    req.bookingTypeEnabled = enabledServices.includes(req.params.type);
    next();
  } catch (error) {
    next(error);
  }
}

export const bookingsRouter = Router();
bookingsRouter.use("/:type", requireBookingType);
bookingsRouter.use("/:type", resolveEnabledBookingType);

// GET /api/bookings/:type?booking_status=pending&staff_id=2&pet_id=5
bookingsRouter.get(
  "/:type",
  asyncHandler(async (req, res) => {
    if (!req.bookingTypeEnabled) return res.json([]);
    const { table, idColumn } = req.bookingConfig;
    const filterable = FILTERABLE[req.params.type] || [];
    assertAllowedQueryKeys(req.query, ["search", "limit", "offset", "order", ...filterable]);
    const { limit, offset } = parsePagination(req.query);
    let query = supabase.from(table).select("*").eq("company_id", req.companyId);

    for (const [key, value] of Object.entries(req.query)) {
      if (["search", "limit", "offset", "order"].includes(key)) continue;
      query = query.eq(key, value);
    }

    const searchableColumns = SEARCHABLE[req.params.type] || [];
    if (req.query.search && searchableColumns.length) {
      const search = safePostgrestSearch(req.query.search);
      query = query.or(searchableColumns.map((c) => `${c}.ilike.%${search}%`).join(","));
    }

    const orderColumn = req.query.order || idColumn;
    if (![idColumn, ...filterable, ...searchableColumns].includes(orderColumn)) {
      return res.status(400).json({ error: `Unsupported order column: ${orderColumn}` });
    }
    query = query.order(orderColumn, { ascending: false });
    if (limit !== null) query = query.range(offset, offset + limit - 1);

    const { data, error } = await query;
    if (error) return res.status(400).json({ error: error.message });
    res.json(data);
  })
);

bookingsRouter.get(
  "/:type/:id",
  asyncHandler(async (req, res) => {
    if (!req.bookingTypeEnabled) return res.status(404).json({ error: "This service module is not enabled." });
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
    if (!req.bookingTypeEnabled) return res.status(403).json({ error: "This service module is not enabled for your company." });
    const result = await createBooking(req.params.type, req.companyId, req.body);
    res.status(201).json(result);
  })
);

bookingsRouter.patch(
  "/:type/:id",
  asyncHandler(async (req, res) => {
    if (!req.bookingTypeEnabled) return res.status(403).json({ error: "This service module is not enabled for your company." });

    // Fetched BEFORE the update so we know what status transition (if any)
    // just happened — req.body.booking_status is only the new value.
    let previousStatus = null;
    if (req.body.booking_status) {
      const { table, idColumn } = req.bookingConfig;
      const { data: before } = await supabase
        .from(table)
        .select("booking_status")
        .eq("company_id", req.companyId)
        .eq(idColumn, req.params.id)
        .maybeSingle();
      previousStatus = before?.booking_status || null;
    }

    const data = await updateBooking(req.params.type, req.companyId, req.params.id, req.body);

    // Fire-and-forget: a customer notice failing must never block the
    // staff member's status update from succeeding — that already happened
    // for real by the time this runs. See main.py's
    // /documents/booking-status-notice for which transitions actually
    // produce a message (most don't, and this is a no-op for those).
    if (previousStatus && data.booking_status && previousStatus !== data.booking_status) {
      callAiBackend("/documents/booking-status-notice", {
        company_id: req.companyId,
        service_type: req.params.type.toUpperCase(),
        booking_id: Number(req.params.id),
        old_status: previousStatus,
        new_status: data.booking_status,
      }).catch((error) => {
        console.warn(`booking-status-notice failed for ${req.params.type} #${req.params.id}:`, error.message);
      });
    }

    res.json(data);
  })
);

bookingsRouter.delete(
  "/:type/:id",
  asyncHandler(async (req, res) => {
    if (!req.bookingTypeEnabled) return res.status(403).json({ error: "This service module is not enabled for your company." });
    await deleteBooking(req.params.type, req.companyId, req.params.id);
    res.status(204).end();
  })
);
