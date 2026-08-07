import { supabase } from "../supabaseClient.js";
import { computeFinalAmount, assertCanRedeem, EARN_RATE } from "./pricing.js";
import { findBookingByPaymentId } from "./bookingService.js";

/**
 * Per-company earn rate, editable via loyalty.html -> PATCH /api/companies/me
 * { settings: { loyalty_earn_rate } } (see routes/companies.js). Falls back
 * to the LOYALTY_EARN_RATE env default when a company hasn't set its own.
 */
export async function getEarnRateForCompany(companyId) {
  const { data } = await supabase
    .from("companies")
    .select("settings_json")
    .eq("company_id", companyId)
    .single();
  const configured = data?.settings_json?.loyalty_earn_rate;
  const rate = configured !== undefined && configured !== null ? Number(configured) : EARN_RATE;
  return Number.isFinite(rate) && rate >= 0 ? rate : 1;
}

/** pet -> customer -> loyalty member, so we can validate/deduct points. */
export async function findMemberForPet(companyId, petId) {
  const { data: pet } = await supabase
    .from("pet")
    .select("customer_id")
    .eq("company_id", companyId)
    .eq("pet_id", petId)
    .maybeSingle();
  if (!pet) return null;

  const { data: member } = await supabase
    .from("loyaltymember")
    .select("*")
    .eq("company_id", companyId)
    .eq("customer_id", pet.customer_id)
    .maybeSingle();
  return member || null;
}

/**
 * pet -> customer -> loyalty member, all three rows. Used by getPaymentDetail
 * so the frontend gets pet_name/customer full_name in one call instead of
 * three separate round trips per row (payment has no direct customer_id —
 * see backend/README.md "Known gaps to come back to").
 */
export async function findPetCustomerMember(companyId, petId) {
  const { data: pet } = await supabase
    .from("pet")
    .select("*")
    .eq("company_id", companyId)
    .eq("pet_id", petId)
    .maybeSingle();
  if (!pet) return { pet: null, customer: null, member: null };

  const [{ data: customer }, { data: member }] = await Promise.all([
    supabase.from("customer").select("*").eq("company_id", companyId).eq("customer_id", pet.customer_id).maybeSingle(),
    supabase.from("loyaltymember").select("*").eq("company_id", companyId).eq("customer_id", pet.customer_id).maybeSingle(),
  ]);

  return { pet, customer: customer || null, member: member || null };
}

export async function getPaymentDetail(companyId, paymentId) {
  const { data: payment, error } = await supabase
    .from("payment")
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
  const { data: pet } = bookingInfo
    ? await supabase.from("pet").select("customer_id").eq("company_id", companyId).eq("pet_id", bookingInfo.booking.pet_id).maybeSingle()
    : { data: null };
  const { data: redemption } = payment.redemption_id
    ? await supabase
      .from("redemption")
      .select("*")
      .eq("company_id", companyId)
      .eq("redemption_id", payment.redemption_id)
      .maybeSingle()
    : { data: null };

  return {
    payment,
    bookingType: bookingInfo?.type || null,
    booking: bookingInfo?.booking || null,
    member,
    redemption: redemption || null,
    customerId: pet?.customer_id || member?.customer_id || null,
  };
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
    const { data, error } = await supabase
      .from("coupon")
      .select("*")
      .eq("company_id", companyId)
      .eq("coupon_id", couponId)
      .single();
    if (error || !data) {
      const err = new Error("Coupon/voucher not found.");
      err.status = 404;
      throw err;
    }
    coupon = data;
    const member = await findMemberForPet(companyId, bookingInfo.booking.pet_id);
    assertCanRedeem(member, coupon);
  }

  return computeFinalAmount(basePrice, addOnPrice, coupon);
}

export async function requestRedemption({ companyId, paymentId, couponId }) {
  if (!Number.isInteger(Number(couponId)) || Number(couponId) <= 0) {
    const err = new Error("Select a voucher to request point redemption.");
    err.status = 400;
    throw err;
  }

  // Keep the fast, user-friendly validation here. The SQL function repeats
  // every important check while rows are locked to prevent race conditions.
  await quoteVoucher(companyId, paymentId, Number(couponId));
  const { data, error } = await supabase.rpc("request_redemption", {
    p_company_id: companyId,
    p_payment_id: Number(paymentId),
    p_coupon_id: Number(couponId),
  });
  if (error) {
    error.status = 400;
    throw error;
  }
  return data;
}

/**
 * The "Verify Payment & Redemption" action: recomputes the final amount
 * (applying a voucher if chosen), then atomically deducts/earns loyalty
 * points, logs the redemption, and marks only the payment Paid via the
 * verify_payment() SQL function. Booking workflow status stays independent.
 */
export async function verifyPayment({ companyId, paymentId, couponId, staffId, paymentMethod }) {
  const bookingInfo = await findBookingByPaymentId(companyId, paymentId);
  if (!bookingInfo) {
    const err = new Error("No booking found for this payment.");
    err.status = 404;
    throw err;
  }
  const member = await findMemberForPet(companyId, bookingInfo.booking.pet_id);
  const { data: payment, error: paymentError } = await supabase
    .from("payment")
    .select("redemption_id")
    .eq("company_id", companyId)
    .eq("payment_id", paymentId)
    .single();
  if (paymentError) {
    const err = new Error("Payment not found.");
    err.status = 404;
    throw err;
  }

  const methodMap = {
    Cash: "cash",
    Card: "card",
    "E-Wallet": "qr",
    "Bank Transfer": "online",
    Online: "online",
  };
  if (!methodMap[paymentMethod]) {
    const err = new Error("Select a valid payment method.");
    err.status = 400;
    throw err;
  }
  const { data: company } = await supabase
    .from("companies")
    .select("settings_json")
    .eq("company_id", companyId)
    .single();
  const acceptedMethods = company?.settings_json?.payment_methods;
  if (acceptedMethods && acceptedMethods[methodMap[paymentMethod]] === false) {
    const err = new Error(`${paymentMethod} is disabled in business settings.`);
    err.status = 400;
    throw err;
  }

  let coupon = null;
  let approvedRedemption = null;
  if (payment.redemption_id) {
    const { data, error } = await supabase
      .from("redemption")
      .select("*")
      .eq("company_id", companyId)
      .eq("redemption_id", payment.redemption_id)
      .single();
    if (error || !data || data.status !== "Approved") {
      const err = new Error("No approved point redemption for this payment.");
      err.status = 409;
      throw err;
    }
    approvedRedemption = data;
    if (couponId && Number(couponId) !== Number(data.coupon_id)) {
      const err = new Error("The selected voucher does not match the approved point redemption.");
      err.status = 409;
      throw err;
    }
    const { data: approvedCoupon, error: couponError } = await supabase
      .from("coupon")
      .select("*")
      .eq("company_id", companyId)
      .eq("coupon_id", data.coupon_id)
      .single();
    if (couponError || !approvedCoupon) {
      const err = new Error("The voucher for the approved point redemption no longer exists.");
      err.status = 409;
      throw err;
    }
    coupon = approvedCoupon;
  } else if (couponId) {
    const err = new Error("No approved point redemption for this payment.");
    err.status = 409;
    throw err;
  }

  const basePrice = bookingInfo.booking[bookingInfo.basePriceCol] || 0;
  const addOnPrice = bookingInfo.addOnPriceCol ? bookingInfo.booking[bookingInfo.addOnPriceCol] || 0 : 0;
  const { finalAmount } = computeFinalAmount(basePrice, addOnPrice, coupon);

  const earnRate = await getEarnRateForCompany(companyId);
  const { data: result, error: rpcError } = await supabase.rpc("verify_payment", {
    p_company_id: companyId,
    p_payment_id: paymentId,
    p_loyalty_id: member?.loyalty_id || null,
    p_coupon_id: approvedRedemption?.coupon_id || null,
    p_earn_rate: earnRate,
    p_verified_by: staffId || null,
    p_payment_method: paymentMethod,
    p_final_amount: finalAmount,
  });
  if (rpcError) {
    rpcError.status = 400;
    throw rpcError;
  }

  return result;
}

/**
 * Refunds a paid transaction through one database function so payment status,
 * loyalty points, and the redemption ledger cannot drift apart.
 */
export async function refundPayment({ companyId, paymentId, staffId, reason }) {
  const normalizedReason = String(reason || "").trim();
  if (!normalizedReason) {
    const err = new Error("A refund reason is required.");
    err.status = 400;
    throw err;
  }

  const { data, error } = await supabase.rpc("refund_payment", {
    p_company_id: companyId,
    p_payment_id: paymentId,
    p_refunded_by: staffId || null,
    p_refund_reason: normalizedReason,
  });
  if (error) {
    error.status = 400;
    throw error;
  }
  return data;
}
