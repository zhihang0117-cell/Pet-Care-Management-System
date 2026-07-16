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
  return {
    date: `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`,
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
 * Creates a booking row + its linked payment_history row, with the final
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
    .from("payment_history")
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
    await supabase.from("payment_history").delete().eq("payment_id", payment.payment_id);
    bookingError.status = 400;
    throw bookingError;
  }

  return { booking, payment };
}
