// Shared helper for calling the Python LangChain/GPT-4o-mini AI backend
// (app/, main.py) from this Node dashboard server — extracted out of
// routes/companies.js (which had its own private copy) so routes/payments.js
// can call it too (invoice generation, see routes/payments.js mark-paid)
// without duplicating the same fetch/error-handling logic a second time.

function resolveAiBackendBaseUrl() {
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
  return /^https?:\/\//i.test(configuredUrl)
    ? configuredUrl
    : `${looksLocal ? "http" : "https"}://${configuredUrl}`;
}

async function postToAiBackend(path, body, headers, timeoutMs) {
  const baseUrl = resolveAiBackendBaseUrl();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers },
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

export async function callAiBackend(path, body) {
  const internalKey = process.env.CLOUD_RUN_AI_BACKEND_INTERNAL_KEY
    || process.env.AI_BACKEND_INTERNAL_KEY;
  if (!internalKey) {
    throw new Error("AI backend internal key is not configured; refusing an unauthenticated request");
  }
  const timeoutMs = Number(process.env.AI_BACKEND_TIMEOUT_MS || 15_000);
  return postToAiBackend(path, body, { "X-Internal-Key": internalKey }, Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 15_000);
}

// Used only by the public Demo page's server-side proxy (routes/demoChat.js)
// so a browser visitor never needs — or can ever see — the real
// X-Chat-Key. Longer default timeout than callAiBackend() above: a chat
// turn can run several GPT-4o-mini tool-call round trips before the agent
// returns a final reply, unlike the single-shot document-generation calls.
export async function callAiBackendChat(path, body) {
  const chatKey = process.env.CLOUD_RUN_AI_BACKEND_CHAT_KEY || process.env.CHAT_API_KEY;
  if (!chatKey) {
    throw new Error("AI backend chat key is not configured on this Node service");
  }
  const timeoutMs = Number(process.env.AI_BACKEND_CHAT_TIMEOUT_MS || 45_000);
  return postToAiBackend(path, body, { "X-Chat-Key": chatKey }, Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 45_000);
}

// Streams back a binary response (the PDF preview endpoints) — these have
// no auth of their own on the Python side, so this just needs the same
// base-URL resolution as the calls above.
export async function fetchAiBackendBinary(path) {
  const baseUrl = resolveAiBackendBaseUrl();
  const timeoutMs = Number(process.env.AI_BACKEND_TIMEOUT_MS || 15_000);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 15_000);
  let response;
  try {
    response = await fetch(`${baseUrl}${path}`, { signal: controller.signal });
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(`AI backend request timed out after ${timeoutMs}ms`);
    }
    const cause = error?.cause?.message || error?.message || "Unknown connection error";
    throw new Error(`Could not reach AI backend at ${baseUrl}: ${cause}`);
  } finally {
    clearTimeout(timeout);
  }
  if (!response.ok) throw new Error(`AI backend failed (${response.status})`);
  const arrayBuffer = await response.arrayBuffer();
  return { buffer: Buffer.from(arrayBuffer), contentType: response.headers.get("content-type") || "application/octet-stream" };
}
