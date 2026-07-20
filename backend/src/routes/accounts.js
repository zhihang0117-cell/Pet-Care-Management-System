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
      .select("company_id, company_name, country, city, logo_path, settings_json")
      .eq("company_id", req.companyId)
      .single();
    if (error) return res.status(400).json({ error: error.message });

    res.json({
      account_id: req.accountId,
      email: req.authUser?.email || null,
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
    const accounts = await Promise.all((data || []).map(async account => {
      const { data: userData } = await supabase.auth.admin.getUserById(account.auth_user_id);
      return { ...account, email: userData?.user?.email || null };
    }));
    res.json(accounts);
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
    if (String(password).length < 8) {
      return res.status(400).json({ error: "password must be at least 8 characters." });
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

    if (payload.role && !["manager", "staff"].includes(payload.role)) {
      return res.status(400).json({ error: "role must be 'manager' or 'staff'." });
    }
    if (payload.account_status && !["active", "inactive"].includes(payload.account_status)) {
      return res.status(400).json({ error: "account_status must be 'active' or 'inactive'." });
    }

    if (payload.role === undefined && payload.account_status === undefined) {
      return res.status(400).json({ error: "Nothing to update." });
    }

    const { data: target } = await supabase
      .from("accounts")
      .select("account_id, role, account_status")
      .eq("company_id", req.companyId)
      .eq("account_id", req.params.accountId)
      .maybeSingle();
    if (!target) return res.status(404).json({ error: "Account not found." });
    if (Number(target.account_id) === Number(req.accountId) && payload.account_status === "inactive") {
      return res.status(400).json({ error: "You cannot deactivate the account currently signed in." });
    }

    // Guard: a company must always retain at least one active manager.
    const removesActiveManager = target.role === "manager" && target.account_status === "active"
      && (payload.role === "staff" || payload.account_status === "inactive");
    if (removesActiveManager) {
        const { count } = await supabase
          .from("accounts")
          .select("*", { count: "exact", head: true })
          .eq("company_id", req.companyId)
          .eq("role", "manager")
          .eq("account_status", "active");
        if ((count || 0) <= 1) {
          return res.status(400).json({ error: "The company must keep at least one active manager account." });
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
    if (Number(target.account_id) === Number(req.accountId)) {
      return res.status(400).json({ error: "You cannot remove the account currently signed in." });
    }

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

    const { data: deletedAccount, error } = await supabase
      .from("accounts")
      .delete()
      .eq("company_id", req.companyId)
      .eq("account_id", req.params.accountId)
      .select()
      .single();
    if (error) return res.status(400).json({ error: error.message });

    const { error: authDeleteError } = await supabase.auth.admin.deleteUser(target.auth_user_id);
    if (authDeleteError) {
      await supabase.from("accounts").insert(deletedAccount);
      return res.status(400).json({ error: `Account removal was rolled back: ${authDeleteError.message}` });
    }
    res.status(204).end();
  })
);
