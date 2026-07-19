import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { getPaymentDetail, quoteVoucher, verifyPayment } from "../lib/paymentService.js";
import { findNamesForPayments } from "../lib/bookingService.js";

export const paymentsRouter = Router();

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

    const names = await findNamesForPayments(req.companyId, data.map((p) => p.payment_id));
    const enriched = data.map((p) => ({ ...p, ...(names.get(p.payment_id) || { petName: null, customerName: null }) }));
    res.json(enriched);
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

// POST /api/payments/:id/verify   { coupon_id?, staff_id }
// The "Verify Payment & Redemption" button.
paymentsRouter.post(
  "/:id/verify",
  asyncHandler(async (req, res) => {
    const result = await verifyPayment({
      companyId: req.companyId,
      paymentId: Number(req.params.id),
      couponId: req.body.coupon_id || null,
      staffId: req.body.staff_id || null,
    });
    res.json(result);
  })
);
