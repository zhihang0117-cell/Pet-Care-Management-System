import "dotenv/config";
import express from "express";
import cors from "cors";
import path from "path";
import { fileURLToPath } from "url";

import { resolveCompany } from "./middleware/auth.js";
import { requireAuthUser } from "./middleware/authUser.js";
import { apiRateLimit } from "./middleware/rateLimit.js";

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
import { roomsRouter } from "./routes/rooms.js";
import { dashboardRouter } from "./routes/dashboard.js";
import { llmRouter } from "./routes/llm.js";

const app = express();
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const webPath = path.join(__dirname, "../../web");
app.set("trust proxy", 1);

const allowedOrigins = (process.env.CORS_ORIGINS || "")
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean);

app.use(
  cors({
    origin: allowedOrigins.length > 0 ? allowedOrigins : true,
    credentials: true
  })
);

// A 10 MB DOCX becomes ~13.4 MB after base64 encoding.
app.use(express.json({ limit: "15mb" }));
app.use(express.urlencoded({ extended: true }));
app.use("/api", apiRateLimit);

app.get("/health", (_req, res) => {
  res.status(200).json({ ok: true });
});

// Public authentication routes
app.use("/api/auth", authRouter);

// Authenticated manager/staff routes
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
app.use("/api/rooms", requireAuthUser, roomsRouter);
app.use("/api/dashboard", requireAuthUser, dashboardRouter);

// LLM service routes
app.use("/api/llm", resolveCompany, llmRouter);

// Serve the frontend from the repository-level web directory
app.use(express.static(webPath));

app.get("/", (_req, res) => {
  res.sendFile(path.join(webPath, "index.html"));
});

// Central error handler must remain after the routes
app.use((err, _req, res, _next) => {
  console.error(err);
  res.status(err.status || 500).json({
    error: err.message || "Internal server error"
  });
});

const port = process.env.PORT || 4000;
app.listen(port, "0.0.0.0", () => {
  console.log(`Pawfect backend listening on port ${port}`);
});
