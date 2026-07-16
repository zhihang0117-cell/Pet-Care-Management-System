import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { getLoyaltyTier } from "../lib/pricing.js";

export const memberInfoRouter = Router();

// GET /api/member-info?tier=Gold&customer_id=3
memberInfoRouter.get(
  "/",
  asyncHandler(async (req, res) => {
    let query = supabase.from("loyaltymember").select("*").eq("company_id", req.companyId);
    for (const [key, value] of Object.entries(req.query)) {
      if (["search", "limit", "offset"].includes(key)) continue;
      query = query.eq(key, value);
    }
    if (req.query.limit) query = query.limit(Number(req.query.limit));

    const { data, error } = await query;
    if (error) return res.status(400).json({ error: error.message });
    res.json(data);
  })
);

memberInfoRouter.get(
  "/:loyaltyId",
  asyncHandler(async (req, res) => {
    const { data, error } = await supabase
      .from("loyaltymember")
      .select("*")
      .eq("company_id", req.companyId)
      .eq("loyalty_id", req.params.loyaltyId)
      .single();
    if (error) return res.status(404).json({ error: "Member not found" });
    res.json(data);
  })
);

/**
 * PATCH /api/member-info/:loyaltyId/manual-adjustment   { points_balance, reason }
 * For correcting data-entry mistakes only. Normal point changes (earning
 * from a payment, spending on a voucher) must go through
 * POST /api/payments/:id/verify so the redemption ledger stays consistent
 * with the member's balance — this endpoint intentionally does NOT write
 * a redemption row, so use it sparingly and keep the `reason`.
 */
memberInfoRouter.patch(
  "/:loyaltyId/manual-adjustment",
  asyncHandler(async (req, res) => {
    const { points_balance: pointsBalance, reason } = req.body;
    if (pointsBalance === undefined) {
      return res.status(400).json({ error: "points_balance is required." });
    }
    const tier = getLoyaltyTier(Number(pointsBalance));

    const { data, error } = await supabase
      .from("loyaltymember")
      .update({ points_balance: pointsBalance, tier })
      .eq("company_id", req.companyId)
      .eq("loyalty_id", req.params.loyaltyId)
      .select()
      .single();

    if (error) return res.status(400).json({ error: error.message });
    res.json({ ...data, adjustment_reason: reason || null });
  })
);
