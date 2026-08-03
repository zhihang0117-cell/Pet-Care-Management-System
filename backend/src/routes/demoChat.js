import { Router } from "express";
import { callAiBackendChat, fetchAiBackendBinary } from "../lib/aiBackend.js";

// Backs the public "Demo" page (web/console.html), linked from index.html's
// nav. Every route here is intentionally unauthenticated — a visitor only
// ever supplies a phone_number + message. The real X-Chat-Key the Python AI
// backend requires is read from this server's own environment
// (CLOUD_RUN_AI_BACKEND_CHAT_KEY, see aiBackend.js) and never sent to the
// browser, so nobody can extract it from page source or dev tools. The
// global apiRateLimit middleware (mounted on all of /api in server.js)
// already throttles this per real visitor IP before any request reaches
// the AI backend.
export const demoChatRouter = Router();

demoChatRouter.post("/chat", async (req, res) => {
  const phoneNumber = String(req.body?.phone_number || "").trim();
  const message = String(req.body?.message || "").trim();
  if (!phoneNumber || !message) {
    return res.status(400).json({ error: "phone_number and message are required." });
  }
  try {
    const result = await callAiBackendChat("/chat", { phone_number: phoneNumber, message });
    res.json(result);
  } catch (error) {
    res.status(502).json({ error: error.message || "AI backend request failed." });
  }
});

demoChatRouter.post("/reset", async (req, res) => {
  const phoneNumber = String(req.body?.phone_number || "").trim();
  if (!phoneNumber) {
    return res.status(400).json({ error: "phone_number is required." });
  }
  try {
    const result = await callAiBackendChat("/debug/clear-session", { phone_number: phoneNumber });
    res.json(result);
  } catch (error) {
    res.status(502).json({ error: error.message || "AI backend request failed." });
  }
});

demoChatRouter.get("/preview/booking-confirmation", async (_req, res) => {
  try {
    const { buffer, contentType } = await fetchAiBackendBinary("/documents/preview/booking-confirmation");
    res.setHeader("Content-Type", contentType);
    res.send(buffer);
  } catch (error) {
    res.status(502).json({ error: error.message || "AI backend request failed." });
  }
});

demoChatRouter.get("/preview/invoice", async (_req, res) => {
  try {
    const { buffer, contentType } = await fetchAiBackendBinary("/documents/preview/invoice");
    res.setHeader("Content-Type", contentType);
    res.send(buffer);
  } catch (error) {
    res.status(502).json({ error: error.message || "AI backend request failed." });
  }
});
