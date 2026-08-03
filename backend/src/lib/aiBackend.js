// Shared helper for calling the Python LangChain/GPT-4o-mini AI backend
// (app/, main.py) from this Node dashboard server — extracted out of
// routes/companies.js (which had its own private copy) so routes/payments.js
// can call it too (invoice generation, see routes/payments.js mark-paid)
// without duplicating the same fetch/error-handling logic a second time.
export async function callAiBackend(path, body) {
  const configuredUrl = String(
    process.env.CLOUD_RUN_AI_BACKEND_URL
      || process.env.AI_BACKEND_URL
      || "http://127.0.0.1:8000"
  ).trim().replace(/\/$/, "");
  // A bare hostname with no scheme (e.g. Render's own `fromService:
  // property: host` output, or a Cloud Run hostname pasted without its
  // scheme) means a real cloud service, which is HTTPS-only — defaulting
  // to http:// here used to silently send a plaintext POST to a host that
  // only accepts HTTPS. Cloud Run/Render both redirect http -> https for a
  // GET, but a POST's body does not survive a redirect the same way, so
  // this would have looked like a working connection that mysteriously
  // failed or silently lost data. Only bare "localhost"/"127.0.0.1" (the
  // only legitimate reason to ever omit a scheme) still defaults to http.
  const looksLocal = /^(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$/i.test(configuredUrl);
  const baseUrl = /^https?:\/\//i.test(configuredUrl)
    ? configuredUrl
    : `${looksLocal ? "http" : "https"}://${configuredUrl}`;
  const internalKey = process.env.CLOUD_RUN_AI_BACKEND_INTERNAL_KEY
    || process.env.AI_BACKEND_INTERNAL_KEY;
  if (!internalKey) {
    throw new Error("AI backend internal key is not configured; refusing an unauthenticated request");
  }
  const timeoutMs = Number(process.env.AI_BACKEND_TIMEOUT_MS || 15_000);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 15_000);
  let response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Internal-Key": internalKey,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(`AI backend request timed out after ${timeoutMs}ms`);
    }
    const cause = error?.cause?.message || error?.message || "Unknown connection error";
    throw new Error(`Could not reach AI backend at ${baseUrl}: ${cause}`);
  } finally {
    clearTimeout(timeout);
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || payload.error || `AI backend failed (${response.status})`);
  return payload;
}
