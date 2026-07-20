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
      settings,
      teamAccounts = [],
    } = req.body;

    if (!email || !password || !businessName) {
      return res.status(400).json({ error: "email, password, and businessName are required." });
    }
    if (String(password).length < 8) {
      return res.status(400).json({ error: "Manager password must be at least 8 characters." });
    }
    if (!Array.isArray(teamAccounts)) {
      return res.status(400).json({ error: "teamAccounts must be an array." });
    }

    const normalizedManagerEmail = String(email).trim().toLowerCase();
    const normalizedTeamAccounts = teamAccounts.map((account) => ({
      email: String(account?.email || "").trim().toLowerCase(),
      password: String(account?.password || ""),
      role: String(account?.role || "staff").toLowerCase(),
    }));
    const allEmails = [normalizedManagerEmail, ...normalizedTeamAccounts.map((account) => account.email)];
    if (normalizedTeamAccounts.some((account) => !account.email || account.password.length < 8 || !["manager", "staff"].includes(account.role))) {
      return res.status(400).json({ error: "Every team account needs a valid email, an 8-character password, and a manager/staff role." });
    }
    if (new Set(allEmails).size !== allEmails.length) {
      return res.status(400).json({ error: "Account emails must be unique." });
    }

    // 1. Create the actual login (Supabase Auth).
    const { data: userData, error: userError } = await supabase.auth.admin.createUser({
      email: normalizedManagerEmail,
      password,
      email_confirm: true,
    });
    if (userError) return res.status(400).json({ error: userError.message });

    const createdTeamUserIds = [];
    let companyId = null;

    // 2. Create the company + manager account, persist its setup, then create
    // each staged teammate as a real Auth user and accounts row.
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
      companyId = data.company_id;

      const companyPayload = {};
      if (settings && typeof settings === "object" && !Array.isArray(settings)) {
        companyPayload.settings_json = settings;
      }
      if (Object.keys(companyPayload).length) {
        const { error: settingsError } = await supabase
          .from("companies")
          .update(companyPayload)
          .eq("company_id", companyId);
        if (settingsError) throw settingsError;
      }

      for (const account of normalizedTeamAccounts) {
        const { data: teamUserData, error: teamUserError } = await supabase.auth.admin.createUser({
          email: account.email,
          password: account.password,
          email_confirm: true,
        });
        if (teamUserError) throw teamUserError;
        createdTeamUserIds.push(teamUserData.user.id);

        const { error: teamAccountError } = await supabase.from("accounts").insert({
          auth_user_id: teamUserData.user.id,
          company_id: companyId,
          role: account.role,
          account_status: "active",
        });
        if (teamAccountError) throw teamAccountError;
      }

      res.status(201).json({
        message: "Company registered.",
        ...data,
        email: normalizedManagerEmail,
        team_accounts_created: normalizedTeamAccounts.length,
      });
    } catch (err) {
      // Compensating rollback: leave neither working logins nor a partial company.
      for (const userId of createdTeamUserIds) {
        await supabase.auth.admin.deleteUser(userId);
      }
      if (companyId) {
        await supabase.from("accounts").delete().eq("company_id", companyId);
        await supabase.from("companies").delete().eq("company_id", companyId);
      }
      await supabase.auth.admin.deleteUser(userData.user.id);
      res.status(400).json({ error: err.message });
    }
  })
);
