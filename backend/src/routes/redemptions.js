import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";

// Read-only on purpose: rows here are created exclusively by the
// verify_payment() SQL function so the ledger always matches a real,
// verified payment. If you need to inspect or export it, GET is all you need.
export const redemptionsRouter = Router();

redemptionsRouter.get(
  "/",
  asyncHandler(async (req, res) => {
    let query = supabase.from("redemption").select("*").eq("company_id", req.companyId);
    for (const [key, value] of Object.entries(req.query)) {
      if (["search", "limit", "offset"].includes(key)) continue;
      query = query.eq(key, value);
    }
    query = query.order("redemption_id", { ascending: false });
    if (req.query.limit) query = query.limit(Number(req.query.limit));

    const { data, error } = await query;
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
    res.json(data);
  })
);
