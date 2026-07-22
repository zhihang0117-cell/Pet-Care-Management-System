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
    bookingRow = {
      ...bookingRow,
      booking_date: body.booking_date,
      check_in_time: body.check_in_time,
      check_out_time: body.check_out_time,
      package_type: body.package_type,
      price: basePriceForPayment,
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

  const { finalAmount } = computeFinalAmount(basePriceForPayment, addOnPrice, null);

  const { data: payment, error: paymentError } = await supabase
    .from("payment")
    .insert({
      company_id: companyId,
      service: bookingRow.service_name || bookingRow.package_type || bookingRow.room_type,
      base_price: basePriceForPayment,
      add_ons: config.hasAddOn && body.add_on ? `${body.add_on} (+RM${addOnPrice})` : "",
      final_amount: finalAmount,
      payment_method: null,
      date: createdDate,
      status: "Pending",
    })
    .select()
    .single();

  if (paymentError) {
    paymentError.status = 400;
    throw paymentError;
  }

  const { data: booking, error: bookingError } = await supabase
    .from(config.table)
    .insert({ ...bookingRow, [config.paymentIdColumn]: payment.payment_id })
    .select()
    .single();

  if (bookingError) {
    await supabase.from("payment").delete().eq("company_id", companyId).eq("payment_id", payment.payment_id);
    bookingError.status = 400;
    throw bookingError;
  }

  return { booking, payment };
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
  } else {
    service = booking.room_type;
    basePrice = Number(booking.total_price) || 0;
  }

  const { finalAmount } = computeFinalAmount(basePrice, addOnPrice, null);
  return { service, base_price: basePrice, add_ons: addOns, final_amount: finalAmount };
}

function isAwaitingPayment(status) {
  return status === "Pending" || status === "Unpaid";
}

function isUnsettledPayment(status) {
  return isAwaitingPayment(status) || status === "Cancelled";
}

/** Updates a booking and keeps its pending payment pricing/service in sync. */
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

  const { data: payment, error: paymentFindError } = await supabase
    .from("payment")
    .select("*")
    .eq("company_id", companyId)
    .eq("payment_id", existing[config.paymentIdColumn])
    .single();
  if (paymentFindError || !payment) {
    const err = new Error("Linked payment not found");
    err.status = 409;
    throw err;
  }
  if (payload.booking_status === "Done" && payment.status !== "Paid") {
    const err = new Error("Verify the linked payment before marking this booking Done.");
    err.status = 409;
    throw err;
  }
  if (payment.status === "Paid" && payload.booking_status && payload.booking_status !== "Done") {
    const err = new Error("A paid booking must remain Done. Refund handling is required before changing its status.");
    err.status = 409;
    throw err;
  }

  const nextPayment = paymentPayloadForBooking(type, next);
  const pricingChanged = ["service", "base_price", "add_ons", "final_amount"]
    .some((key) => String(nextPayment[key] ?? "") !== String(payment[key] ?? ""));
  if (!isUnsettledPayment(payment.status) && pricingChanged) {
    const err = new Error("A completed or refunded booking's service or price cannot be changed.");
    err.status = 409;
    throw err;
  }

  const { data: updated, error: bookingError } = await supabase
    .from(config.table)
    .update(payload)
    .eq("company_id", companyId)
    .eq(config.idColumn, bookingId)
    .select()
    .single();
  if (bookingError) {
    bookingError.status = 400;
    throw bookingError;
  }

  const paymentUpdate = isUnsettledPayment(payment.status) && pricingChanged ? { ...nextPayment } : {};
  if (payload.booking_status === "Cancelled" && isAwaitingPayment(payment.status)) {
    paymentUpdate.status = "Cancelled";
  } else if (payment.status === "Cancelled" && payload.booking_status && payload.booking_status !== "Cancelled") {
    paymentUpdate.status = "Unpaid";
  }

  if (Object.keys(paymentUpdate).length > 0) {
    const { error: paymentError } = await supabase
      .from("payment")
      .update(paymentUpdate)
      .eq("company_id", companyId)
      .eq("payment_id", payment.payment_id);
    if (paymentError) {
      const rollback = { ...existing };
      delete rollback[config.idColumn];
      delete rollback.company_id;
      await supabase.from(config.table).update(rollback).eq("company_id", companyId).eq(config.idColumn, bookingId);
      paymentError.status = 400;
      throw paymentError;
    }
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

  const { error: bookingError } = await supabase
    .from(config.table)
    .delete()
    .eq("company_id", companyId)
    .eq(config.idColumn, bookingId);
  if (bookingError) {
    bookingError.status = 400;
    throw bookingError;
  }

  const { error: paymentError } = await supabase
    .from("payment")
    .delete()
    .eq("company_id", companyId)
    .eq("payment_id", paymentId);
  if (paymentError) {
    await supabase.from(config.table).insert(booking);
    paymentError.status = 400;
    throw paymentError;
  }
}
