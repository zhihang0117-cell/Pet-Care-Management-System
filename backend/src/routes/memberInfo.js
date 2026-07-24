import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { getLoyaltyTier } from "../lib/pricing.js";
import { requireManager } from "../middleware/authUser.js";
import { assertAllowedQueryKeys, parsePagination } from "../lib/queryValidation.js";

export const memberInfoRouter = Router();

// GET /api/member-info?tier=Gold&customer_id=3
memberInfoRouter.get(
  "/",
  asyncHandler(async (req, res) => {
    assertAllowedQueryKeys(req.query, ["search", "limit", "offset", "tier", "customer_id"]);
    const { limit, offset } = parsePagination(req.query);
    let query = supabase.from("loyaltymember").select("*").eq("company_id", req.companyId);
    for (const [key, value] of Object.entries(req.query)) {
      if (["search", "limit", "offset"].includes(key)) continue;
      query = query.eq(key, value);
    }
    if (limit !== null) query = query.range(offset, offset + limit - 1);

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
 * POST /api/member-info   { customer_id, points_balance? }
 * Enrolls an existing customer into the loyalty program. There's no
 * separate "member registration request" step in this system (unlike the
 * old mock's pending-approval queue) — a customer either has a
 * loyaltymember row or doesn't, and a staff member creates one directly,
 * defaulting to 0 points / the matching tier unless a starting balance is
 * given.
 */
memberInfoRouter.post(
  "/",
  asyncHandler(async (req, res) => {
    const { customer_id: customerId, points_balance: pointsBalance } = req.body;
    if (!customerId) {
      return res.status(400).json({ error: "customer_id is required." });
    }
    const numericCustomerId = Number(customerId);
    if (!Number.isInteger(numericCustomerId) || numericCustomerId <= 0) {
      return res.status(400).json({ error: "customer_id must be a positive integer." });
    }
    const { data: companyCustomer, error: customerError } = await supabase
      .from("customer")
      .select("customer_id")
      .eq("company_id", req.companyId)
      .eq("customer_id", numericCustomerId)
      .maybeSingle();
    if (customerError) return res.status(400).json({ error: customerError.message });
    if (!companyCustomer) return res.status(404).json({ error: "Customer not found for this company." });

    const { data: existing } = await supabase
      .from("loyaltymember")
      .select("loyalty_id")
      .eq("company_id", req.companyId)
      .eq("customer_id", numericCustomerId)
      .maybeSingle();
    if (existing) {
      return res.status(400).json({ error: "This customer is already a loyalty member." });
    }

    const balance = pointsBalance === undefined ? 0 : Number(pointsBalance);
    if (!Number.isInteger(balance) || balance < 0) {
      return res.status(400).json({ error: "points_balance must be a non-negative integer." });
    }
    const { data, error } = await supabase
      .from("loyaltymember")
      .insert({
        company_id: req.companyId,
        customer_id: numericCustomerId,
        points_balance: balance,
        tier: getLoyaltyTier(balance),
        redemption_made: 0,
      })
      .select()
      .single();

    if (error) return res.status(400).json({ error: error.message });
    res.status(201).json(data);
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
  requireManager,
  asyncHandler(async (req, res) => {
    const { points_balance: pointsBalance, reason } = req.body;
    if (pointsBalance === undefined) {
      return res.status(400).json({ error: "points_balance is required." });
    }
    const numericBalance = Number(pointsBalance);
    const normalizedReason = String(reason || "").trim();
    if (!normalizedReason) {
      return res.status(400).json({ error: "A reason is required for every manual points adjustment." });
    }
    if (!Number.isInteger(numericBalance) || numericBalance < 0) {
      return res.status(400).json({ error: "points_balance must be a non-negative integer." });
    }
    const { data, error } = await supabase.rpc("adjust_loyalty_points", {
      p_company_id: req.companyId,
      p_loyalty_id: Number(req.params.loyaltyId),
      p_new_balance: numericBalance,
      p_reason: normalizedReason,
      p_account_id: req.accountId,
    });

    if (error) return res.status(400).json({ error: error.message });
    res.json(data);
  })
);
