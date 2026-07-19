import { supabase } from "../supabaseClient.js";
import { computeFinalAmount } from "./pricing.js";

export const BOOKING_TYPES = {
  grooming: {
    table: "grooming_booking",
    idColumn: "grooming_booking_id",
    hasAddOn: true,
    basePriceCol: "price",
    addOnPriceCol: "add_on_price",
    paymentIdColumn: "payment_id",
  },
  daycare: {
    table: "daycare_booking",
    idColumn: "daycare_booking_id",
    hasAddOn: false,
    basePriceCol: "price",
    addOnPriceCol: null,
    paymentIdColumn: "payment_id",
  },
  boarding: {
    table: "boarding_booking",
    idColumn: "boarding_booking_id",
    hasAddOn: false,
    basePriceCol: "total_price",
    addOnPriceCol: null,
    multiNight: true,
    paymentIdColumn: "payment_id",
  },
};

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
  const config = BOOKING_TYPES[type];
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
    };
  }

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
    await supabase.from("payment").delete().eq("payment_id", payment.payment_id);
    bookingError.status = 400;
    throw bookingError;
  }

  return { booking, payment };
}
