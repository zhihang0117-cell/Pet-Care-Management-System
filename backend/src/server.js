import path from "path";
import { fileURLToPath } from "url";
import "dotenv/config";
import express from "express";
import cors from "cors";
import path from "path";
import { fileURLToPath } from "url";

import "dotenv/config";
import express from "express";
import cors from "cors";

import { resolveCompany } from "./middleware/auth.js";
import { requireAuthUser } from "./middleware/authUser.js";

import { authRouter } from "./routes/auth.js";
import { accountsRouter } from "./routes/accounts.js";
import { companiesRouter } from "./routes/companies.js";
import { customersRouter } from "./routes/customers.js";
import { petsRouter } from "./routes/pets.js";
import { staffRouter } from "./routes/staff.js";
import { couponsRouter } from "./routes/coupons.js";
import { chatMessagesRouter } from "./routes/chatMessages.js";
import { leaveRequestsRouter } from "./routes/leaveRequests.js";
import { memberInfoRouter } from "./routes/memberInfo.js";
import { redemptionsRouter } from "./routes/redemptions.js";
import { bookingsRouter } from "./routes/bookings.js";
import { paymentsRouter } from "./routes/payments.js";
import { dashboardRouter } from "./routes/dashboard.js";
import { llmRouter } from "./routes/llm.js";

const app = express();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// web 文件夹的位置
const webPath = path.join(__dirname, "../../web");

const allowedOrigins = (process.env.CORS_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean);
app.use(cors({ origin: allowedOrigins.length ? allowedOrigins : true }));
// Business-logo uploads are sent as a base64 JSON payload. The frontend caps
// the source file at 2 MB; 3 MB leaves room for base64 expansion and metadata.
app.use(express.json({ limit: "3mb" }));

app.get("/health", (req, res) => res.json({ ok: true }));

// 托管 web 文件夹中的 HTML、CSS、JS、图片
app.use(express.static(webPath));

// --- Public: no session yet (this IS how a session/company gets created) ---
app.use("/api/auth", authRouter);

// --- Everything below is a real logged-in human (manager or staff). ---
// requireAuthUser verifies the Supabase JWT and resolves company_id/role
// from accounts — see middleware/authUser.js and DEVELOPER_GUIDE.md §3.
app.use("/api/accounts", requireAuthUser, accountsRouter);
app.use("/api/companies", requireAuthUser, companiesRouter);
app.use("/api/customers", requireAuthUser, customersRouter);
app.use("/api/pets", requireAuthUser, petsRouter);
app.use("/api/staff", requireAuthUser, staffRouter);
app.use("/api/coupons", requireAuthUser, couponsRouter);
app.use("/api/chat-messages", requireAuthUser, chatMessagesRouter);
app.use("/api/leave-requests", requireAuthUser, leaveRequestsRouter);
app.use("/api/member-info", requireAuthUser, memberInfoRouter);
app.use("/api/redemptions", requireAuthUser, redemptionsRouter);
app.use("/api/bookings", requireAuthUser, bookingsRouter);
app.use("/api/payments", requireAuthUser, paymentsRouter);
app.use("/api/dashboard", requireAuthUser, dashboardRouter);

// --- LLM/agent access: separate trust model (its own API key, not a user
// session), so it keeps the header-based resolveCompany instead. ---
app.use("/api/llm", resolveCompany, llmRouter);

// 网站首页
app.get("/", (req, res) => {
  res.sendFile(path.join(webPath, "index.html"));
});

// 支持直接打开 login.html、dashboard.html 等页面
app.get("/:page.html", (req, res, next) => {
  const requestedPage = req.params.page;

  // 防止路径穿越
  if (!/^[a-zA-Z0-9_-]+$/.test(requestedPage)) {
    return next();
  }

  res.sendFile(path.join(webPath, `${requestedPage}.html`), (error) => {
    if (error) next();
  });
});

// Central error handler.
app.use((err, req, res, _next) => {
  console.error(err);
  res.status(err.status || 500).json({ error: err.message || "Internal server error" });
});

const port = process.env.PORT || 4000;

app.listen(port, "0.0.0.0", () => {
  console.log(`Pawfect backend listening on port ${port}`);
});