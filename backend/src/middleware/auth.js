import "dotenv/config";

const DEFAULT_COMPANY_ID = Number(process.env.DEFAULT_COMPANY_ID || 1);

/**
 * Every table in your schema has a company_id column (multi-tenant). Real
 * logins should set req.companyId from the authenticated account's
 * company_id (see the accounts table) instead of trusting a header. For now,
 * this reads it from an `x-company-id` header so you can test multiple
 * companies from Postman/curl, and falls back to DEFAULT_COMPANY_ID.
 *
 * TODO when you wire up real auth: replace this with middleware that
 * verifies a session/JWT and looks up accounts.company_id server-side.
 */
export function resolveCompany(req, _res, next) {
  const headerValue = req.header("x-company-id");
  req.companyId = headerValue ? Number(headerValue) : DEFAULT_COMPANY_ID;
  next();
}

/**
 * Separate secret for LLM/agent access, so it can be rotated or revoked
 * independently of whatever your human users use. The LLM must send:
 *   x-llm-api-key: <LLM_API_KEY>
 */
export function requireLlmKey(req, res, next) {
  const key = req.header("x-llm-api-key");
  if (!key || key !== process.env.LLM_API_KEY) {
    return res.status(401).json({ error: "Missing or invalid x-llm-api-key." });
  }
  next();
}

/** Small helper so route handlers don't all repeat the same try/catch. */
export function asyncHandler(fn) {
  return (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);
}
