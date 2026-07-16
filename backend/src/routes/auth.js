import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";

export const authRouter = Router();

/**
 * POST /api/auth/register-company
 * Body: { email, password, businessName, country, streetAddress, city, state, postcode, businessDescription }
 *
 * This is the ONLY place a company + its first (manager) account get
 * created. It's intentionally not reachable through ordinary RLS-governed
 * client writes — see sql/register_company_function.sql for why. No
 * requireAuthUser here: the caller doesn't have a session yet, that's the
 * whole point of this endpoint.
 */
authRouter.post(
  "/register-company",
  asyncHandler(async (req, res) => {
    const {
      email,
      password,
      businessName,
      country,
      streetAddress,
      city,
      state,
      postcode,
      businessDescription,
    } = req.body;

    if (!email || !password || !businessName) {
      return res.status(400).json({ error: "email, password, and businessName are required." });
    }

    // 1. Create the actual login (Supabase Auth).
    const { data: userData, error: userError } = await supabase.auth.admin.createUser({
      email,
      password,
      email_confirm: true,
    });
    if (userError) return res.status(400).json({ error: userError.message });

    // 2. Atomically create the company + the manager account for it.
    try {
      const { data, error } = await supabase.rpc("register_company", {
        p_auth_user_id: userData.user.id,
        p_company_name: businessName,
        p_country: country || null,
        p_street_address: streetAddress || null,
        p_city: city || null,
        p_state: state || null,
        p_postcode: postcode || null,
        p_business_description: businessDescription || null,
      });
      if (error) throw error;

      res.status(201).json({
        message: "Company registered.",
        ...data,
        email,
      });
    } catch (err) {
      // Compensating rollback: don't leave a dangling login with no company.
      await supabase.auth.admin.deleteUser(userData.user.id);
      res.status(400).json({ error: err.message });
    }
  })
);
