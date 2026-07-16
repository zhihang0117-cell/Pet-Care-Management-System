import { supabase } from "../supabaseClient.js";

/**
 * Replaces the placeholder resolveCompany (header-trust) for every route a
 * logged-in human (manager or staff) calls. The frontend authenticates
 * directly against Supabase (supabase.auth.signInWithPassword, using the
 * anon key — login itself doesn't need to go through this backend at all)
 * and then sends the resulting access token here as:
 *
 *   Authorization: Bearer <supabase access token>
 *
 * This middleware verifies that token with Supabase, then looks up the
 * caller's company_id and role from accounts — the same source of
 * truth the RLS policies use, so the backend and the database always agree
 * on "who is this and which company do they belong to". staff.staff_id is
 * never used for this — see DEVELOPER_GUIDE.md §3.
 */
export async function requireAuthUser(req, res, next) {
  const authHeader = req.header("authorization") || "";
  const token = authHeader.replace(/^Bearer\s+/i, "").trim();
  if (!token) {
    return res.status(401).json({ error: "Missing Authorization: Bearer <token> header." });
  }

  const { data: userData, error: userError } = await supabase.auth.getUser(token);
  if (userError || !userData?.user) {
    return res.status(401).json({ error: "Invalid or expired session." });
  }

  const { data: account, error: accountError } = await supabase
    .from("accounts")
    .select("account_id, company_id, role, account_status")
    .eq("auth_user_id", userData.user.id)
    .single();

  if (accountError || !account) {
    return res.status(403).json({ error: "This login isn't linked to any company account yet." });
  }
  if (account.account_status !== "active") {
    return res.status(403).json({ error: "This account has been deactivated." });
  }

  req.authUser = userData.user;
  req.companyId = account.company_id;
  req.accountId = account.account_id;
  req.accountRole = account.role;
  next();
}

/** Use after requireAuthUser on routes only a manager may call. */
export function requireManager(req, res, next) {
  if (req.accountRole !== "manager") {
    return res.status(403).json({ error: "Only a manager account can do this." });
  }
  next();
}
