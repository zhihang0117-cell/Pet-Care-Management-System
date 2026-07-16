import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { requireManager } from "../middleware/authUser.js";

export const accountsRouter = Router();

// GET /api/accounts/me — the caller's own account + company info. requireAuthUser
// already resolved these from the Supabase token (see middleware/authUser.js);
// this just hands them back to the frontend so it can render the sidebar
// (role, business name) and know which dashboard to redirect to after login.
accountsRouter.get(
  "/me",
  asyncHandler(async (req, res) => {
    const { data: company, error } = await supabase
      .from("companies")
      .select("company_id, company_name, country, city, logo_path")
      .eq("company_id", req.companyId)
      .single();
    if (error) return res.status(400).json({ error: error.message });

    res.json({
      account_id: req.accountId,
      role: req.accountRole,
      company_id: req.companyId,
      company,
    });
  })
);

// Any logged-in account (manager or staff) can see the company's account roster.
accountsRouter.get(
  "/",
  asyncHandler(async (req, res) => {
    const { data, error } = await supabase
      .from("accounts")
      .select("account_id, auth_user_id, role, account_status, created_at, updated_at")
      .eq("company_id", req.companyId)
      .order("account_id", { ascending: true });
    if (error) return res.status(400).json({ error: error.message });
    res.json(data);
  })
);

/**
 * POST /api/accounts   (manager only)   { email, password, role }
 * Creates a brand new login for someone joining this company (e.g. a staff
 * member), plus their accounts row, in this company/tenant.
 */
accountsRouter.post(
  "/",
  requireManager,
  asyncHandler(async (req, res) => {
    const { email, password, role } = req.body;
    if (!email || !password || !role) {
      return res.status(400).json({ error: "email, password, and role are required." });
    }
    if (!["manager", "staff"].includes(role)) {
      return res.status(400).json({ error: "role must be 'manager' or 'staff'." });
    }

    const { data: userData, error: userError } = await supabase.auth.admin.createUser({
      email,
      password,
      email_confirm: true,
    });
    if (userError) return res.status(400).json({ error: userError.message });

    const { data: account, error: accountError } = await supabase
      .from("accounts")
      .insert({ auth_user_id: userData.user.id, company_id: req.companyId, role, account_status: "active" })
      .select()
      .single();

    if (accountError) {
      await supabase.auth.admin.deleteUser(userData.user.id); // rollback the orphaned login
      return res.status(400).json({ error: accountError.message });
    }

    res.status(201).json(account);
  })
);

// PATCH /api/accounts/:accountId   (manager only)   { role?, account_status? }
accountsRouter.patch(
  "/:accountId",
  requireManager,
  asyncHandler(async (req, res) => {
    const { role, account_status } = req.body;
    const payload = {};
    if (role) payload.role = role;
    if (account_status) payload.account_status = account_status;

    if (payload.role === undefined && payload.account_status === undefined) {
      return res.status(400).json({ error: "Nothing to update." });
    }

    // Guard: don't let the last manager demote themselves (or be demoted).
    if (payload.role === "staff") {
      const { data: target } = await supabase
        .from("accounts")
        .select("role")
        .eq("company_id", req.companyId)
        .eq("account_id", req.params.accountId)
        .single();
      if (target?.role === "manager") {
        const { count } = await supabase
          .from("accounts")
          .select("*", { count: "exact", head: true })
          .eq("company_id", req.companyId)
          .eq("role", "manager");
        if ((count || 0) <= 1) {
          return res.status(400).json({ error: "Can't demote the only manager account for this company." });
        }
      }
    }

    const { data, error } = await supabase
      .from("accounts")
      .update(payload)
      .eq("company_id", req.companyId)
      .eq("account_id", req.params.accountId)
      .select()
      .single();
    if (error) return res.status(400).json({ error: error.message });
    res.json(data);
  })
);

// DELETE /api/accounts/:accountId   (manager only) — never removes the last manager.
accountsRouter.delete(
  "/:accountId",
  requireManager,
  asyncHandler(async (req, res) => {
    const { data: target, error: findError } = await supabase
      .from("accounts")
      .select("*")
      .eq("company_id", req.companyId)
      .eq("account_id", req.params.accountId)
      .single();
    if (findError || !target) return res.status(404).json({ error: "Account not found." });

    if (target.role === "manager") {
      const { count } = await supabase
        .from("accounts")
        .select("*", { count: "exact", head: true })
        .eq("company_id", req.companyId)
        .eq("role", "manager");
      if ((count || 0) <= 1) {
        return res.status(400).json({ error: "Can't remove the only manager account for this company." });
      }
    }

    await supabase.auth.admin.deleteUser(target.auth_user_id);
    const { error } = await supabase
      .from("accounts")
      .delete()
      .eq("company_id", req.companyId)
      .eq("account_id", req.params.accountId);
    if (error) return res.status(400).json({ error: error.message });
    res.status(204).end();
  })
);
