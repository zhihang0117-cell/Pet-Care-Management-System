import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { assertAllowedQueryKeys, parsePagination } from "../lib/queryValidation.js";
import {
  getPaymentDetail,
  quoteVoucher,
  requestRedemption,
  verifyPayment,
  refundPayment,
} from "../lib/paymentService.js";
import { requireManager } from "../middleware/authUser.js";
import { callAiBackend } from "../lib/aiBackend.js";

export const paymentsRouter = Router();
const PAYMENT_METHODS = new Set(["Cash", "Card", "E-Wallet", "Bank Transfer", "Online"]);

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
    assertAllowedQueryKeys(req.query, ["search", "limit", "offset", "status", "payment_method"]);
    const { limit, offset } = parsePagination(req.query);
    let query = supabase.from("payment").select("*").eq("company_id", req.companyId);
    for (const [key, value] of Object.entries(req.query)) {
      if (["search", "limit", "offset"].includes(key)) continue;
      query = query.eq(key, value);
    }
    query = query.order("payment_id", { ascending: false });
    if (limit !== null) query = query.range(offset, offset + limit - 1);

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

paymentsRouter.post(
  "/:id/redemption-request",
  asyncHandler(async (req, res) => {
    const result = await requestRedemption({
      companyId: req.companyId,
      paymentId: Number(req.params.id),
      couponId: Number(req.body.coupon_id),
    });
    res.status(201).json(result);
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

    callAiBackend("/documents/refund-notice", {
      company_id: req.companyId,
      payment_id: Number(req.params.id),
    }).catch((err) => {
      console.warn(`refund-notice failed for payment #${req.params.id}:`, err.message);
    });

    res.json(result);
  })
);

// POST /api/payments/:id/verify   { coupon_id?, staff_id }
// The "Verify Payment & Redemption" button — this is the route the real
// dashboard button (web copy/common.js confirmVerifyPayment) actually
// calls. It recomputes final_amount from the real booking price server-side
// (see paymentService.js's verifyPayment()), unlike /mark-paid below, which
// trusts a client-supplied final_amount.
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

    // Previously missing entirely on this route — /mark-paid below had this,
    // but nothing in the actual dashboard ever calls /mark-paid, so invoices
    // were never generated/sent for a real "Verify Payment & Redemption"
    // click. Fire-and-forget, same pattern as the refund/booking-status
    // notices — a notification failure must never block the staff action.
    callAiBackend("/documents/invoice", {
      company_id: req.companyId,
      payment_id: Number(req.params.id),
    }).catch((err) => {
      console.warn(`invoice generation failed for payment #${req.params.id}:`, err.message);
    });

    res.json(result);
  })
);

// POST /api/payments/:id/mark-paid   { payment_method?, final_amount? }
// An alternate "money changed hands at the counter" action that calls the
// same verify_payment() SQL RPC as /verify above, but resolves loyalty_id/
// coupon_id itself (from an existing Approved redemption, or straight from
// the pet's loyalty member) instead of requiring the caller to already know
// them, and lets a manager override final_amount directly (e.g. a manual
// discount/adjustment at the counter) rather than only ever the recomputed
// booking price. Both routes are real and both call the real RPC — this one
// exists for that manual-override case; /verify is what the dashboard's
// standard "Verify Payment & Redemption" button uses day to day.
paymentsRouter.post(
  "/:id/mark-paid",
  requireManager,
  asyncHandler(async (req, res) => {
    const paymentId = Number(req.params.id);
    const { data: existing, error: fetchError } = await supabase
      .from("payment")
      .select("payment_id, status, redemption_id")
      .eq("company_id", req.companyId)
      .eq("payment_id", paymentId)
      .maybeSingle();
    if (fetchError) return res.status(400).json({ error: fetchError.message });
    if (!existing) return res.status(404).json({ error: `Payment ${paymentId} not found.` });
    if (existing.status === "Paid") {
      return res.status(400).json({ error: "This payment is already marked Paid." });
    }
    if (req.body?.final_amount != null) {
      const overrideAmount = Number(req.body.final_amount);
      if (!Number.isFinite(overrideAmount) || overrideAmount <= 0) {
        return res.status(400).json({ error: "final_amount must be a positive number." });
      }
    }
    if (!PAYMENT_METHODS.has(String(req.body?.payment_method || ""))) {
      return res.status(400).json({
        error: "payment_method must be Cash, Card, E-Wallet, Bank Transfer, or Online.",
      });
    }

    // verify_payment() requires p_loyalty_id/p_coupon_id to exactly match an
    // Approved redemption already linked to this payment (see decide_redemption
    // in the same SQL file — approval happens BEFORE this call, never here).
    // If there's no (or no Approved) redemption, we still resolve loyalty_id
    // alone so the customer still earns points on this payment.
    let loyaltyId = null;
    let couponId = null;
    if (existing.redemption_id) {
      const { data: redemption } = await supabase
        .from("redemption")
        .select("loyalty_id, coupon_id, status")
        .eq("company_id", req.companyId)
        .eq("redemption_id", existing.redemption_id)
        .maybeSingle();
      if (redemption?.status === "Approved") {
        loyaltyId = redemption.loyalty_id;
        couponId = redemption.coupon_id;
      }
    }
    if (loyaltyId === null) {
      const bookingLookups = ["grooming_booking", "daycare_booking", "boarding_booking"].map((table) =>
        supabase.from(table).select("pet_id").eq("company_id", req.companyId).eq("payment_id", paymentId).maybeSingle()
      );
      const bookingResults = await Promise.all(bookingLookups);
      const petId = bookingResults.map((r) => r.data?.pet_id).find((id) => id != null);
      if (petId != null) {
        const { data: pet } = await supabase
          .from("pet")
          .select("customer_id")
          .eq("company_id", req.companyId)
          .eq("pet_id", petId)
          .maybeSingle();
        if (pet) {
          const { data: member } = await supabase
            .from("loyaltymember")
            .select("loyalty_id")
            .eq("company_id", req.companyId)
            .eq("customer_id", pet.customer_id)
            .maybeSingle();
          loyaltyId = member?.loyalty_id ?? null;
        }
      }
    }

    const { data: reviewer } = await supabase
      .from("staff")
      .select("staff_id")
      .eq("company_id", req.companyId)
      .ilike("email", String(req.authUser?.email || "").trim())
      .maybeSingle();

    const { data: verified, error: verifyError } = await supabase.rpc("verify_payment", {
      p_company_id: req.companyId,
      p_payment_id: paymentId,
      p_loyalty_id: loyaltyId,
      p_coupon_id: couponId,
      p_earn_rate: Number(process.env.LOYALTY_EARN_RATE || 1),
      p_verified_by: reviewer?.staff_id || null,
      p_payment_method: req.body?.payment_method || null,
      p_final_amount: req.body?.final_amount ?? null,
    });
    if (verifyError) return res.status(400).json({ error: verifyError.message });

    let invoice = { status: "error", error: "invoice generation was not attempted" };
    try {
      invoice = await callAiBackend("/documents/invoice", {
        company_id: req.companyId,
        payment_id: paymentId,
      });
    } catch (error) {
      invoice = { status: "error", error: error.message };
    }

    res.json({ payment: verified, invoice });
  })
);
