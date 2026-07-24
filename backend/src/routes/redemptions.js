import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { requireManager } from "../middleware/authUser.js";
import { assertAllowedQueryKeys, parsePagination } from "../lib/queryValidation.js";

// Read-only on purpose: rows here are created exclusively by the
// verify_payment() SQL function so the ledger always matches a real,
// verified payment. If you need to inspect or export it, GET is all you need.
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
