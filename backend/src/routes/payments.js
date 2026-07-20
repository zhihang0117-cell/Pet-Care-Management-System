import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { getPaymentDetail, quoteVoucher, verifyPayment, refundPayment } from "../lib/paymentService.js";
import { requireManager } from "../middleware/authUser.js";

export const paymentsRouter = Router();

async function enrichPaymentsWithCustomer(payments, companyId) {
  const paymentIds = [...new Set((payments || []).map(row => row.payment_id).filter(Boolean))];
  if (!paymentIds.length) return payments || [];

  const bookingQueries = ["grooming_booking", "daycare_booking", "boarding_booking"].map(table =>
    supabase.from(table).select("payment_id, pet_id").eq("company_id", companyId).in("payment_id", paymentIds)
  );
  const bookingResults = await Promise.all(bookingQueries);
  const bookingError = bookingResults.find(result => result.error)?.error;
  if (bookingError) throw bookingError;

  const bookingByPayment = new Map();
  bookingResults.flatMap(result => result.data || []).forEach(booking => {
    bookingByPayment.set(String(booking.payment_id), booking);
  });
  const petIds = [...new Set([...bookingByPayment.values()].map(booking => booking.pet_id).filter(Boolean))];
  const { data: pets, error: petsError } = petIds.length
    ? await supabase.from("pet").select("pet_id, customer_id, pet_name").eq("company_id", companyId).in("pet_id", petIds)
    : { data: [], error: null };
  if (petsError) throw petsError;

  const petById = new Map((pets || []).map(pet => [String(pet.pet_id), pet]));
  const customerIds = [...new Set((pets || []).map(pet => pet.customer_id).filter(Boolean))];
  const { data: customers, error: customersError } = customerIds.length
    ? await supabase.from("customer").select("customer_id, full_name").eq("company_id", companyId).in("customer_id", customerIds)
    : { data: [], error: null };
  if (customersError) throw customersError;

  const customerById = new Map((customers || []).map(customer => [String(customer.customer_id), customer]));
  return (payments || []).map(payment => {
    const booking = bookingByPayment.get(String(payment.payment_id));
    const pet = booking ? petById.get(String(booking.pet_id)) : null;
    const customer = pet ? customerById.get(String(pet.customer_id)) : null;
    return {
      ...payment,
      pet_id: pet?.pet_id || null,
      pet_name: pet?.pet_name || null,
      customer_id: customer?.customer_id || null,
      customer_name: customer?.full_name || null,
    };
  });
}

// GET /api/payments?status=Pending&payment_method=Cash
// Each row is enriched with customer_name/pet_name via a handful of bulk
// queries (see findNamesForPayments) — payment has no direct customer_id
// (see backend/README.md "Known gaps to come back to"), and doing that join
// per-row would mean 3+ queries PER payment on a history list that can run
// into the hundreds of rows.
paymentsRouter.get(
  "/",
  asyncHandler(async (req, res) => {
    let query = supabase.from("payment").select("*").eq("company_id", req.companyId);
    for (const [key, value] of Object.entries(req.query)) {
      if (["search", "limit", "offset"].includes(key)) continue;
      query = query.eq(key, value);
    }
    query = query.order("payment_id", { ascending: false });
    if (req.query.limit) query = query.limit(Number(req.query.limit));

    const { data, error } = await query;
    if (error) return res.status(400).json({ error: error.message });
    res.json(await enrichPaymentsWithCustomer(data, req.companyId));
  })
);

// GET /api/payments/:id — payment + which booking it's for + the customer's loyalty info.
paymentsRouter.get(
  "/:id",
  asyncHandler(async (req, res) => {
    const detail = await getPaymentDetail(req.companyId, req.params.id);
    res.json(detail);
  })
);

// POST /api/payments/:id/quote-voucher   { coupon_id }
// Read-only preview of what the customer would pay with a given voucher applied.
paymentsRouter.post(
  "/:id/quote-voucher",
  asyncHandler(async (req, res) => {
    const quote = await quoteVoucher(req.companyId, req.params.id, req.body.coupon_id);
    res.json(quote);
  })
);

// POST /api/payments/:id/refund   { reason }
// Manager-only because this reverses revenue and any linked loyalty movement.
paymentsRouter.post(
  "/:id/refund",
  requireManager,
  asyncHandler(async (req, res) => {
    const { data: reviewer } = await supabase
      .from("staff")
      .select("staff_id")
      .eq("company_id", req.companyId)
      .ilike("email", String(req.authUser?.email || "").trim())
      .maybeSingle();
    const result = await refundPayment({
      companyId: req.companyId,
      paymentId: Number(req.params.id),
      staffId: reviewer?.staff_id || null,
      reason: req.body.reason,
    });
    res.json(result);
  })
);

// POST /api/payments/:id/verify   { coupon_id?, staff_id }
// The "Verify Payment & Redemption" button.
paymentsRouter.post(
  "/:id/verify",
  asyncHandler(async (req, res) => {
    const { data: reviewer } = await supabase
      .from("staff")
      .select("staff_id")
      .eq("company_id", req.companyId)
      .ilike("email", String(req.authUser?.email || "").trim())
      .maybeSingle();
    const result = await verifyPayment({
      companyId: req.companyId,
      paymentId: Number(req.params.id),
      couponId: req.body.coupon_id || null,
      staffId: reviewer?.staff_id || null,
      paymentMethod: req.body.payment_method,
    });
    res.json(result);
  })
);
