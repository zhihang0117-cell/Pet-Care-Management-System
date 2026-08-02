import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { requireManager } from "../middleware/authUser.js";
import { assertAllowedQueryKeys, parsePagination } from "../lib/queryValidation.js";
import { callAiBackend } from "../lib/aiBackend.js";

// Direct generic creation is intentionally unavailable: rows are created by
// request_redemption()/verify_payment() so every ledger entry is linked to a
// real payment. This router exposes reads and guarded lifecycle decisions.
export const redemptionsRouter = Router();

redemptionsRouter.get(
  "/",
  asyncHandler(async (req, res) => {
    assertAllowedQueryKeys(req.query, ["search", "limit", "offset", "status", "loyalty_id", "coupon_id"]);
    const { limit, offset } = parsePagination(req.query);
    let query = supabase.from("redemption").select("*").eq("company_id", req.companyId);
    for (const [key, value] of Object.entries(req.query)) {
      if (["search", "limit", "offset"].includes(key)) continue;
      query = query.eq(key, value);
    }
    query = query.order("redemption_id", { ascending: false });
    if (limit !== null) query = query.range(offset, offset + limit - 1);

    const { data, error } = await query;
    if (error) return res.status(400).json({ error: error.message });
    res.json(data);
  })
);

redemptionsRouter.post(
  "/:id/decision",
  requireManager,
  asyncHandler(async (req, res) => {
    const status = String(req.body.status || "");
    if (!["Approved", "Rejected"].includes(status)) {
      return res.status(400).json({ error: "status must be Approved or Rejected." });
    }
    const { data, error } = await supabase.rpc("decide_redemption", {
      p_company_id: req.companyId,
      p_redemption_id: Number(req.params.id),
      p_status: status,
    });
    if (error) return res.status(400).json({ error: error.message });

    callAiBackend("/documents/redemption-notice", {
      company_id: req.companyId,
      redemption_id: Number(req.params.id),
      status,
    }).catch((err) => {
      console.warn(`redemption-notice failed for redemption #${req.params.id}:`, err.message);
    });

    res.json(data);
  })
);

redemptionsRouter.post(
  "/:id/cancel",
  requireManager,
  asyncHandler(async (req, res) => {
    const reason = String(req.body.reason || "").trim();
    if (!reason) return res.status(400).json({ error: "A cancellation reason is required." });
    const { data, error } = await supabase.rpc("cancel_approved_redemption", {
      p_company_id: req.companyId,
      p_redemption_id: Number(req.params.id),
      p_reason: reason,
    });
    if (error) return res.status(400).json({ error: error.message });
    res.json(data);
  })
);

redemptionsRouter.get(
  "/:id",
  asyncHandler(async (req, res) => {
    const { data, error } = await supabase
      .from("redemption")
      .select("*")
      .eq("company_id", req.companyId)
      .eq("redemption_id", req.params.id)
      .single();
    if (error) return res.status(404).json({ error: "Redemption not found" });
    const { data: payment } = await supabase
      .from("payment")
      .select("payment_id, status")
      .eq("company_id", req.companyId)
      .eq("redemption_id", req.params.id)
      .maybeSingle();
    res.json({ ...data, payment_id: payment?.payment_id || null, payment_status: payment?.status || null });
  })
);
