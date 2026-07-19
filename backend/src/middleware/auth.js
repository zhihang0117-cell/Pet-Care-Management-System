import "dotenv/config";

/**
 * Every table in your schema has a company_id column (multi-tenant). This
 * middleware is ONLY used for the /api/llm/* routes (see server.js) — an LLM
 * agent isn't a logged-in human with a session, so it has no token to derive
 * a company from. Whatever system prompt/session sets up the agent must tell
 * it which business it's acting for, and it must send that as `x-company-id`.
 *
 * No default/fallback on purpose: this is a B2B multi-tenant product, so a
 * request that doesn't say which company it's for should fail loudly, not
 * silently fall back to some company and risk touching the wrong tenant's
 * data. Every other route uses requireAuthUser instead (middleware/authUser.js),
 * which derives company_id from the real logged-in user's token server-side.
 */
export function resolveCompany(req, res, next) {
  const headerValue = req.header("x-company-id");
  if (!headerValue) {
    return res.status(400).json({ error: "Missing x-company-id header." });
  }
  req.companyId = Number(headerValue);
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
