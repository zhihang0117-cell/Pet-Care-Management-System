import { supabase } from "../supabaseClient.js";
import { computeFinalAmount, assertCanRedeem, EARN_RATE } from "./pricing.js";
import { findBookingByPaymentId } from "./bookingService.js";

/** pet -> customer -> loyalty member, so we can validate/deduct points. */
export async function findMemberForPet(companyId, petId) {
  const { data: pet } = await supabase
    .from("pet_profiles")
    .select("customer_id")
    .eq("company_id", companyId)
    .eq("pet_id", petId)
    .maybeSingle();
  if (!pet) return null;

  const { data: member } = await supabase
    .from("member_info")
    .select("*")
    .eq("company_id", companyId)
    .eq("customer_id", pet.customer_id)
    .maybeSingle();
  return member || null;
}

export async function getPaymentDetail(companyId, paymentId) {
  const { data: payment, error } = await supabase
    .from("payment_history")
    .select("*")
    .eq("company_id", companyId)
    .eq("payment_id", paymentId)
    .single();
  if (error) {
    const err = new Error("Payment not found");
    err.status = 404;
    throw err;
  }

  const bookingInfo = await findBookingByPaymentId(companyId, paymentId);
  const member = bookingInfo ? await findMemberForPet(companyId, bookingInfo.booking.pet_id) : null;

  return { payment, bookingType: bookingInfo?.type || null, booking: bookingInfo?.booking || null, member };
}

export async function quoteVoucher(companyId, paymentId, couponId) {
  const bookingInfo = await findBookingByPaymentId(companyId, paymentId);
  if (!bookingInfo) {
    const err = new Error("No booking found for this payment.");
    err.status = 404;
    throw err;
  }

  const basePrice = bookingInfo.booking[bookingInfo.basePriceCol] || 0;
  const addOnPrice = bookingInfo.addOnPriceCol ? bookingInfo.booking[bookingInfo.addOnPriceCol] || 0 : 0;

  let coupon = null;
  if (couponId) {
    const { data } = await supabase
      .from("coupon_type")
      .select("*")
      .eq("company_id", companyId)
      .eq("coupon_id", couponId)
      .single();
    coupon = data;
    const member = await findMemberForPet(companyId, bookingInfo.booking.pet_id);
    assertCanRedeem(member, coupon);
  }

  return computeFinalAmount(basePrice, addOnPrice, coupon);
}

/**
 * The "Verify Payment & Redemption" action: recomputes the final amount
 * (applying a voucher if chosen), then atomically deducts/earns loyalty
 * points, logs the redemption, marks the payment Paid, and marks the
 * underlying booking Done — via the verify_payment() SQL function.
 */
export async function verifyPayment({ companyId, paymentId, couponId, staffId }) {
  const bookingInfo = await findBookingByPaymentId(companyId, paymentId);
  if (!bookingInfo) {
    const err = new Error("No booking found for this payment.");
    err.status = 404;
    throw err;
  }

  const member = await findMemberForPet(companyId, bookingInfo.booking.pet_id);
  if (!member) {
    const err = new Error("No loyalty member found for this customer.");
    err.status = 404;
    throw err;
  }

  let coupon = null;
  if (couponId) {
    const { data } = await supabase
      .from("coupon_type")
      .select("*")
      .eq("company_id", companyId)
      .eq("coupon_id", couponId)
      .single();
    coupon = data;
    assertCanRedeem(member, coupon);
  }

  const basePrice = bookingInfo.booking[bookingInfo.basePriceCol] || 0;
  const addOnPrice = bookingInfo.addOnPriceCol ? bookingInfo.booking[bookingInfo.addOnPriceCol] || 0 : 0;
  const { finalAmount } = computeFinalAmount(basePrice, addOnPrice, coupon);

  const { error: updateError } = await supabase
    .from("payment_history")
    .update({ final_amount: finalAmount })
    .eq("company_id", companyId)
    .eq("payment_id", paymentId);
  if (updateError) {
    updateError.status = 400;
    throw updateError;
  }

  const { data: result, error: rpcError } = await supabase.rpc("verify_payment", {
    p_payment_id: paymentId,
    p_loyalty_id: member.loyalty_id,
    p_coupon_id: couponId || null,
    p_earn_rate: EARN_RATE,
    p_verified_by: staffId || null,
  });
  if (rpcError) {
    rpcError.status = 400;
    throw rpcError;
  }

  await supabase
    .from(bookingInfo.table)
    .update({ booking_status: "Done" })
    .eq("company_id", companyId)
    .eq(bookingInfo.idColumn, bookingInfo.booking[bookingInfo.idColumn]);

  return result;
}
