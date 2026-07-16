import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { getPaymentDetail, quoteVoucher, verifyPayment } from "../lib/paymentService.js";

export const paymentsRouter = Router();

// GET /api/payments?status=Pending&payment_method=Cash
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
    res.json(data);
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
