import { supabase } from "../supabaseClient.js";
import { computeFinalAmount } from "./pricing.js";
import { BOOKING_TYPES, getBookingTypeConfig } from "./bookingTypes.js";

export { BOOKING_TYPES } from "./bookingTypes.js";

export function todayStamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return {
    // ISO YYYY-MM-DD: your date columns are real Postgres `date` type, which
    // needs this zero-padded format for correct storage/range comparisons.
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
    time: d.toTimeString().slice(0, 8),
  };
}

export function nightsBetween(checkIn, checkOut) {
  const toDate = (s) => new Date(String(s).replace(/\//g, "-"));
  const ms = toDate(checkOut) - toDate(checkIn);
  return Math.max(1, Math.round(ms / (1000 * 60 * 60 * 24)));
}

const BOOKING_STATUSES = new Set(["Pending", "Scheduled", "Done", "No Show", "Cancelled"]);

function invalidBooking(message) {
  const err = new Error(message);
  err.status = 400;
  throw err;
}

function validateBookingInput(type, booking, { creating = false } = {}) {
  if (!Number.isInteger(Number(booking.pet_id)) || Number(booking.pet_id) <= 0) invalidBooking("Select a valid pet.");
  if (!Number.isInteger(Number(booking.staff_id)) || Number(booking.staff_id) <= 0) invalidBooking("Select a valid staff member.");
  if (!BOOKING_STATUSES.has(booking.booking_status)) invalidBooking("Select a valid booking status.");
  if (creating && !["Pending", "Scheduled"].includes(booking.booking_status)) {
    invalidBooking("A new booking must start as Pending or Scheduled.");
  }

  if (type === "grooming") {
    if (!String(booking.service_name || "").trim() || !booking.booking_date || !booking.booking_time) {
      invalidBooking("Service name, booking date, and booking time are required.");
    }
    if (!Number.isFinite(Number(booking.price)) || Number(booking.price) < 0) invalidBooking("Price must be zero or greater.");
    if (booking.add_on_price != null && (!Number.isFinite(Number(booking.add_on_price)) || Number(booking.add_on_price) < 0)) {
      invalidBooking("Add-on price must be zero or greater.");
    }
  } else if (type === "daycare") {
    if (!String(booking.package_type || "").trim() || !booking.booking_date || !booking.check_in_time || !booking.check_out_time) {
      invalidBooking("Package, booking date, check-in time, and check-out time are required.");
    }
    if (booking.check_out_time <= booking.check_in_time) invalidBooking("Check-out time must be after check-in time.");
    if (!Number.isFinite(Number(booking.price)) || Number(booking.price) < 0) invalidBooking("Price must be zero or greater.");
    if (booking.add_on_price != null && (!Number.isFinite(Number(booking.add_on_price)) || Number(booking.add_on_price) < 0)) {
      invalidBooking("Add-on price must be zero or greater.");
    }
  } else if (type === "boarding") {
    if (!String(booking.room_type || "").trim() || !booking.check_in_date || !booking.check_out_date || !booking.check_in_time || !booking.check_out_time) {
      invalidBooking("Room, check-in/out dates, and check-in/out times are required.");
    }
    if (booking.check_out_date < booking.check_in_date ||
        (booking.check_out_date === booking.check_in_date && booking.check_out_time <= booking.check_in_time)) {
      invalidBooking("Check-out must be after check-in.");
    }
    if (!Number.isFinite(Number(booking.price_per_night)) || Number(booking.price_per_night) < 0) {
      invalidBooking("Price per night must be zero or greater.");
    }
  }
}

async function assertBookingRelationsBelongToCompany(companyId, petId, staffId) {
  const [{ data: pet, error: petError }, { data: staff, error: staffError }] = await Promise.all([
    supabase.from("pet").select("pet_id").eq("company_id", companyId).eq("pet_id", petId).maybeSingle(),
    supabase.from("staff").select("staff_id").eq("company_id", companyId).eq("staff_id", staffId).maybeSingle(),
  ]);
  if (petError || staffError) {
    const err = petError || staffError;
    err.status = 400;
    throw err;
  }
  if (!pet) invalidBooking("Selected pet does not belong to this company.");
  if (!staff) invalidBooking("Selected staff member does not belong to this company.");
}

/** Finds which of the 3 booking tables a payment_id belongs to. */
export async function findBookingByPaymentId(companyId, paymentId) {
  for (const [type, cfg] of Object.entries(BOOKING_TYPES)) {
    const { data } = await supabase
      .from(cfg.table)
      .select("*")
      .eq("company_id", companyId)
      .eq(cfg.paymentIdColumn, paymentId)
      .maybeSingle();
    if (data) return { type, ...cfg, booking: data };
  }
  return null;
}

/**
 * Bulk version of findBookingByPaymentId + pet/customer name lookup, for
 * list views (e.g. GET /api/payments) with potentially hundreds of rows.
 * Does a constant ~5 queries total (one per booking table, one for pets,
 * one for customers) instead of 3+ queries PER ROW — avoids an N+1 that
 * would be fine for a handful of pending payments but not for a full
 * transaction history.
 *
 * Returns a Map<payment_id, { petName, customerName }>.
 */
export async function findNamesForPayments(companyId, paymentIds) {
  const result = new Map();
  if (!paymentIds.length) return result;

  const petIdByPaymentId = new Map();
  await Promise.all(
    Object.values(BOOKING_TYPES).map(async (cfg) => {
      const { data } = await supabase
        .from(cfg.table)
        .select(`pet_id, ${cfg.paymentIdColumn}`)
        .eq("company_id", companyId)
        .in(cfg.paymentIdColumn, paymentIds);
      (data || []).forEach((row) => petIdByPaymentId.set(row[cfg.paymentIdColumn], row.pet_id));
    })
  );

  const petIds = [...new Set(petIdByPaymentId.values())];
  if (!petIds.length) return result;

  const { data: pets } = await supabase
    .from("pet")
    .select("pet_id, pet_name, customer_id")
    .eq("company_id", companyId)
    .in("pet_id", petIds);

  const petById = new Map((pets || []).map((p) => [p.pet_id, p]));
  const customerIds = [...new Set((pets || []).map((p) => p.customer_id))];

  const { data: customers } = await supabase
    .from("customer")
    .select("customer_id, full_name")
    .eq("company_id", companyId)
    .in("customer_id", customerIds.length ? customerIds : [-1]);

  const customerById = new Map((customers || []).map((c) => [c.customer_id, c]));

  for (const [paymentId, petId] of petIdByPaymentId.entries()) {
    const pet = petById.get(petId);
    const customer = pet ? customerById.get(pet.customer_id) : null;
    result.set(paymentId, { petName: pet?.pet_name || null, customerName: customer?.full_name || null });
  }

  return result;
}

/**
 * Creates a booking row + its linked payment row, with the final
 * amount auto-computed from base price + add-on (no voucher — a voucher is
 * applied later, at verification time, via paymentService.verifyPayment).
 */
export async function createBooking(type, companyId, body) {
  const config = getBookingTypeConfig(type);
  if (!config) {
    const err = new Error(`Unknown booking type "${type}". Use grooming, daycare, or boarding.`);
    err.status = 404;
    throw err;
  }

  const { date: createdDate, time: createdTime } = todayStamp();
  let basePriceForPayment;
  let addOnPrice = 0;
  let bookingRow = {
    company_id: companyId,
    pet_id: body.pet_id,
    staff_id: body.staff_id,
    booking_status: body.booking_status || "Pending",
    created_date: createdDate,
    created_time: createdTime,
  };

  if (config.hasAddOn) {
    const hasAddOnName = !["", "-"].includes(String(body.add_on || "").trim());
    const hasAddOnPrice = body.add_on_price !== undefined
      && body.add_on_price !== null
      && String(body.add_on_price).trim() !== "";
    if (hasAddOnName !== hasAddOnPrice) {
      invalidBooking("Add-on name and add-on price must be supplied together.");
    }
  } else {
    const hasUnsupportedName = !["", "-"].includes(String(body.add_on || "").trim());
    const hasUnsupportedPrice = body.add_on_price !== undefined
      && body.add_on_price !== null
      && String(body.add_on_price).trim() !== "";
    if (hasUnsupportedName || hasUnsupportedPrice) {
      invalidBooking("Boarding bookings do not support add-ons.");
    }
  }

  if (type === "grooming") {
    addOnPrice = Number(body.add_on_price) || 0;
    basePriceForPayment = Number(body.price) || 0;
    bookingRow = {
      ...bookingRow,
      service_name: body.service_name,
      booking_date: body.booking_date,
      booking_time: body.booking_time,
      price: basePriceForPayment,
      add_on: body.add_on || "-",
      add_on_price: addOnPrice,
      notes: body.notes || "-",
    };
  } else if (type === "daycare") {
    // No "notes" column on daycare_booking — it only has special_instruction.
    basePriceForPayment = Number(body.price) || 0;
    addOnPrice = Number(body.add_on_price) || 0;
    bookingRow = {
      ...bookingRow,
      booking_date: body.booking_date,
      check_in_time: body.check_in_time,
      check_out_time: body.check_out_time,
      package_type: body.package_type,
      price: basePriceForPayment,
      add_on: body.add_on || "-",
      add_on_price: addOnPrice,
      special_instruction: body.special_instruction || "-",
    };
  } else if (type === "boarding") {
    const nights = nightsBetween(body.check_in_date, body.check_out_date);
    const pricePerNight = Number(body.price_per_night) || 0;
    basePriceForPayment = pricePerNight * nights;
    bookingRow = {
      ...bookingRow,
      check_in_date: body.check_in_date,
      check_in_time: body.check_in_time,
      check_out_date: body.check_out_date,
      check_out_time: body.check_out_time,
      room_type: body.room_type,
      price_per_night: pricePerNight,
      total_price: basePriceForPayment,
      feeding_instruction: body.feeding_instruction || "-",
      medical_instruction: body.medical_instruction || "-",
      notes: body.notes || "-",
    };
  }

  validateBookingInput(type, bookingRow, { creating: true });
  await assertBookingRelationsBelongToCompany(companyId, bookingRow.pet_id, bookingRow.staff_id);

  const { finalAmount } = computeFinalAmount(basePriceForPayment, addOnPrice, null);

  const paymentRow = {
    service: bookingRow.service_name || bookingRow.package_type || bookingRow.room_type,
    base_price: basePriceForPayment,
    add_ons: config.hasAddOn && body.add_on ? `${body.add_on} (+RM${addOnPrice})` : "",
    final_amount: finalAmount,
    payment_method: null,
    date: createdDate,
    status: "Pending",
  };
  const { data, error } = await supabase.rpc("create_booking_atomic", {
    p_company_id: companyId,
    p_booking_type: type,
    p_booking: bookingRow,
    p_payment: paymentRow,
  });
  if (error) {
    error.status = 400;
    throw error;
  }
  return data;
}

function paymentPayloadForBooking(type, booking) {
  let service;
  let basePrice;
  let addOnPrice = 0;
  let addOns = "";

  if (type === "grooming") {
    service = booking.service_name;
    basePrice = Number(booking.price) || 0;
    addOnPrice = Number(booking.add_on_price) || 0;
    addOns = booking.add_on && booking.add_on !== "-" ? `${booking.add_on} (+RM${addOnPrice})` : "";
  } else if (type === "daycare") {
    service = booking.package_type;
    basePrice = Number(booking.price) || 0;
    addOnPrice = Number(booking.add_on_price) || 0;
    addOns = booking.add_on && booking.add_on !== "-" ? `${booking.add_on} (+RM${addOnPrice})` : "";
  } else {
    service = booking.room_type;
    basePrice = Number(booking.total_price) || 0;
  }

  const { finalAmount } = computeFinalAmount(basePrice, addOnPrice, null);
  return { service, base_price: basePrice, add_ons: addOns, final_amount: finalAmount };
}

function isUnsettledPayment(status) {
  return status === "Pending" || status === "Unpaid" || status === "Cancelled";
}

/**
 * Updates a booking. A linked unsettled payment may receive refreshed service
 * and pricing details, but its status is deliberately independent.
 */
export async function updateBooking(type, companyId, bookingId, body) {
  const config = getBookingTypeConfig(type);
  const { data: existing, error: findError } = await supabase
    .from(config.table)
    .select("*")
    .eq("company_id", companyId)
    .eq(config.idColumn, bookingId)
    .single();
  if (findError || !existing) {
    const err = new Error("Booking not found");
    err.status = 404;
    throw err;
  }

  const payload = { ...body };
  delete payload[config.idColumn];
  delete payload.company_id;
  delete payload[config.paymentIdColumn];
  delete payload.created_date;
  delete payload.created_time;

  const next = { ...existing, ...payload };
  if (type === "boarding") {
    next.total_price = (Number(next.price_per_night) || 0) * nightsBetween(next.check_in_date, next.check_out_date);
    payload.total_price = next.total_price;
  }
  validateBookingInput(type, next);
  await assertBookingRelationsBelongToCompany(companyId, next.pet_id, next.staff_id);

  const currentPaymentDetails = paymentPayloadForBooking(type, existing);
  const nextPayment = paymentPayloadForBooking(type, next);
  const pricingChanged = ["service", "base_price", "add_ons", "final_amount"]
    .some((key) => String(nextPayment[key] ?? "") !== String(currentPaymentDetails[key] ?? ""));

  // A status/date/staff/note-only booking edit must remain a booking-only
  // operation. Look up the linked payment only when billable details changed.
  let payment = null;
  if (pricingChanged) {
    const { data, error: paymentFindError } = await supabase
      .from("payment")
      .select("*")
      .eq("company_id", companyId)
      .eq("payment_id", existing[config.paymentIdColumn])
      .single();
    if (paymentFindError || !data) {
      const err = new Error("Linked payment not found");
      err.status = 409;
      throw err;
    }
    payment = data;
    if (!isUnsettledPayment(payment.status)) {
      const err = new Error("A completed or refunded booking's service or price cannot be changed.");
      err.status = 409;
      throw err;
    }
  }

  // Booking workflow status, payment settlement status, and redemption status
  // are separate state machines. Never copy booking_status into payment.status.
  const paymentUpdate = payment ? { ...nextPayment } : {};
  const { data: updated, error: updateError } = await supabase.rpc("update_booking_atomic", {
    p_company_id: companyId,
    p_booking_type: type,
    p_booking_id: Number(bookingId),
    p_booking_patch: payload,
    p_payment_patch: paymentUpdate,
  });
  if (updateError) {
    updateError.status = 400;
    throw updateError;
  }
  return updated;
}

/** Deletes an unpaid booking and its linked payment, with compensation on failure. */
export async function deleteBooking(type, companyId, bookingId) {
  const config = getBookingTypeConfig(type);
  const { data: booking, error: findError } = await supabase
    .from(config.table)
    .select("*")
    .eq("company_id", companyId)
    .eq(config.idColumn, bookingId)
    .single();
  if (findError || !booking) {
    const err = new Error("Booking not found");
    err.status = 404;
    throw err;
  }

  const paymentId = booking[config.paymentIdColumn];
  const { data: payment, error: paymentFindError } = await supabase
    .from("payment")
    .select("*")
    .eq("company_id", companyId)
    .eq("payment_id", paymentId)
    .single();
  if (paymentFindError || !payment) {
    const err = new Error("Linked payment not found");
    err.status = 409;
    throw err;
  }
  if (!isUnsettledPayment(payment.status)) {
    const err = new Error("Completed or refunded payments cannot be deleted; retain them for audit history.");
    err.status = 409;
    throw err;
  }
  if (payment.redemption_id) {
    const { data: redemption, error: redemptionError } = await supabase
      .from("redemption")
      .select("status")
      .eq("company_id", companyId)
      .eq("redemption_id", payment.redemption_id)
      .maybeSingle();
    if (redemptionError) {
      redemptionError.status = 400;
      throw redemptionError;
    }
    if (redemption && redemption.status !== "Rejected") {
      const err = new Error(
        redemption.status === "Approved"
          ? "This booking has an approved point redemption. Cancel and refund the redemption before deleting it."
          : "This booking has a pending point redemption. Reject it before deleting the booking.",
      );
      err.status = 409;
      throw err;
    }
  }

  const { error } = await supabase.rpc("delete_booking_atomic", {
    p_company_id: companyId,
    p_booking_type: type,
    p_booking_id: Number(bookingId),
  });
  if (error) {
    error.status = 400;
    throw error;
  }
}
