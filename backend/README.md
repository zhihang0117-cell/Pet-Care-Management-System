# Pawfect Backend (Express + Supabase)

A Node/Express API that sits between your Supabase database and your HTML
frontend — so the frontend never talks to Supabase directly. This matters
because your `service_role` key (which bypasses Row Level Security) must
never reach the browser, and money-critical logic (pricing, voucher
validation, point deduction) needs to live in one trusted place, not be
re-implemented in JavaScript running on someone's laptop.

```
Browser (HTML/common.js)  ──►  Express API (this project)  ──►  Supabase (Postgres)
```

The conversational AI agent (WhatsApp/eval-console chat) is a separate
Python service (see the repository root's `app/orchestrator.py`) with its
own direct Supabase access for booking/customer/loyalty logic — it does not
go through this Express API for that. This backend and the Python service
only talk to each other for document generation (booking confirmations,
invoices, status notices — see `src/lib/aiBackend.js` and the Python
service's `/documents/*` endpoints), gated by a separate internal key. A
previous generic LLM-CRUD surface on this Express API (`/api/llm/*`) was
removed — nothing in the codebase ever called it, and it stayed reachable
behind only a single shared secret with no real per-tenant authorization,
which became a real cross-tenant risk the moment more than one company's
data exists in the same Supabase project.

## 1. One-time Supabase setup

Open your Supabase project → SQL Editor and run these files in order:

1. `sql/company_settings_migration.sql`
2. `sql/verify_payment_function.sql`
3. `sql/enquiry_refund_logo_migration.sql`
4. `sql/crud_consistency_functions.sql`
5. `sql/rls_policies.sql`
6. `sql/crud_hardening_migration.sql`
7. `sql/register_company_function.sql`
8. `sql/company_documents_migration.sql`
9. `sql/002_add_document_id_to_chunks_bge_large.sql`
10. `sql/booking_conflict_prevention_migration.sql`
11. `sql/redemption_rejection_reason_migration.sql` — adds `rejection_reason`
    to `redemption` and a matching `decide_redemption` parameter; without
    this, rejecting a loyalty redemption from `loyalty.html` fails.
12. `sql/chat_api_key_migration.sql` — adds the `company_chat_key` table the
    Python `/chat` endpoint uses to resolve which company an X-Chat-Key
    belongs to (see `app/db/customer_context.py`
    `resolve_company_id_from_chat_key`). Optional for a single-company
    deployment (the legacy single `CHAT_API_KEY` env var still works without
    it), required before onboarding a second company.

These migrations add company settings/logo storage, enquiry reply audit data,
payment verification/refund audit data, atomic booking/payment functions,
points-adjustment audit history, guarded account functions, redemption
rejection reasons, and per-company chat authentication. They do not delete
any existing rows.

### Company policy documents and RAG

Set `AI_BACKEND_URL` and `AI_BACKEND_INTERNAL_KEY` in `backend/.env`. Set the
same secret as `INTERNAL_API_KEY` in `ai-backend/.env`, then start FastAPI
before Express.

Business DOCX files use the private `business-documents` bucket with paths shaped
as `<company_id>/<document_id>/<file-name>`. Express derives `company_id` from
the verified Supabase login, records processing state in `company_documents`,
and asks Python to chunk, tag, embed, and atomically replace only that
document's vectors. Deletion is scoped by both `company_id` and `document_id`.

Logos use the public `business-assets` bucket and a company-prefixed path,
which is suitable for images displayed publicly. Put non-public company images
in a private bucket and return short-lived signed URLs instead.

## 2. Configure and run the backend

```bash
cd backend
npm install
cp .env.example .env
# then edit .env:
#   SUPABASE_URL                 - Project Settings > API
#   SUPABASE_SERVICE_ROLE_KEY    - Project Settings > API > service_role (SECRET)
#   CORS_ORIGINS                 - your frontend's URL(s)
npm start
```

Visit `http://localhost:4000/health` — you should see `{"ok":true,"companyId":1}`.

## 3. Where to deploy it (recommendation)

**This backend + `web/` on Render.com, the Python AI service on Google Cloud
Run** — the split reflected in the repository root's `render.yaml`:

- `pawfectai` (this backend) serves both the Express API and the static
  `web/` frontend (`express.static`, see `src/server.js`) from ONE Render
  web service — same origin, so `web/api-client.js` needs no cross-origin
  configuration for this half of the split.
- The Python AI service (`app/`, `main.py`, repository root) is **not** a
  Render service at all — it runs on Google Cloud Run instead (see the
  repository root `README.md`'s "Deployment (Google Cloud Run)" section for
  the exact `gcloud` command). BGE-Large's memory footprint and Cloud Run's
  per-request billing/scaling model fit that service better than Render.

After deploying the Python service to Cloud Run, set on `pawfectai` (Render
dashboard → Environment, or `render.yaml` if you prefer committing the
reference — these two are `sync: false` since Cloud Run isn't a
Render-managed service render.yaml can cross-reference automatically):

- `CLOUD_RUN_AI_BACKEND_URL` — the Cloud Run service's HTTPS URL.
- `CLOUD_RUN_AI_BACKEND_INTERNAL_KEY` — must match `INTERNAL_API_KEY` set on
  that Cloud Run service.

Create or sync a Render Blueprint from `render.yaml` for the `pawfectai`
service, or deploy it manually: push `backend/` to its own GitHub repo →
Render → New Web Service → point at the repo → build command `npm install`,
start command `npm start` → add the env vars from `.env` in Render's
dashboard (plus the two `CLOUD_RUN_*` ones above once the Cloud Run service
exists).

After the first deploy, open the service in the Render dashboard and
**confirm its actual assigned URL matches `CORS_ORIGINS` in render.yaml
exactly** — Render only grants the exact `<name>.onrender.com` hostname if
that name isn't already taken by another Render account; otherwise it
silently appends a random suffix, and a stale `CORS_ORIGINS` value breaks
every browser request from `web/` with no obvious error message pointing at
the cause.

(Serverless functions or Supabase Edge Functions would also work for this
Node backend, but you'd need to split it into many small functions and be
careful about connection pooling; not worth it at this scale.)

## 4. How each of your 4 asks maps to this code

**① Auto price calc + voucher deduction + point validation + staff verify:**
- `src/lib/pricing.js` — `computeFinalAmount()` (base + add-on − voucher),
  `assertCanRedeem()` (points check), `pointsEarnedFor()`.
- `src/lib/bookingService.js` — `createBooking()` auto-computes the price
  when a booking is made and creates the linked `payment` row.
- `src/lib/paymentService.js` — `verifyPayment()` is the "Verify Payment &
  Redemption" action: recomputes the total with any chosen voucher, then
  calls the atomic SQL function, which deducts/earns points, writes the
  `redemption` ledger row, stamps `paid_at`, and sets `status = 'Paid'`.
- `sql/verify_payment_function.sql` — the atomic transaction itself.

**② Dashboard calculations + status filters:**
- `src/routes/dashboard.js` — `/api/dashboard/summary` (status breakdown per
  booking type + today's counts + pending payments/leave) and
  `/api/dashboard/revenue?period=today|week|month`.
- Every list endpoint (`/api/bookings/:type`, `/api/payments`, etc.) accepts
  arbitrary `?column=value` filters, e.g. `?booking_status=Pending`.
- Booking/payment dates use ISO `YYYY-MM-DD` values, so range comparisons
  remain correctly ordered in both JavaScript and Postgres.

**③ Linking Supabase to your HTML pages:**
- `frontend integration/api-client.js` — a small `fetch` wrapper (`api.get`,
  `api.post`, etc. plus convenience methods) to drop into every page.
- `web/api-client.js` and `web/common.js` wire the booking, daily overview,
  CRM, loyalty, payment, enquiry, staff, settings, and analytical dashboards
  to the authenticated API.

**④ CRUD for an LLM to update the database:**
- Removed. This used to be a generic `src/llm/tools.js` + `tableAllowlist.js`
  CRUD surface on this Express API, but nothing in the codebase ever called
  it — the real conversational agent (`app/orchestrator.py`, Python) has its
  own typed, validated tool set with its own direct Supabase access (see
  `app/tools/*.py`), which already enforces price verification, booking-write
  guardrails, and two-step confirmation for destructive actions the way this
  generic surface never did. Keeping an unused, weakly-authenticated
  duplicate write path mounted was a real risk, not a convenience.

## Auth & registration flow

- **Registering a new company**: `POST /api/auth/register-company` (no
  session needed — this is the one endpoint that creates a tenant from
  scratch). It creates the Supabase Auth login, then atomically creates the
  `companies_rows` row + the first `accounts_rows` row (`role: 'manager'`)
  via the `register_company()` SQL function. See
  `sql/register_company_function.sql` for why this can't just be a normal
  RLS-governed client insert.
- **Logging in**: the frontend calls `supabase.auth.signInWithPassword(...)`
  **directly**, using the Supabase anon key — login itself doesn't need to
  go through this backend at all. That gives you back an access token.
- **Every other request**: send that token as `Authorization: Bearer
  <token>`. `requireAuthUser` middleware (`middleware/authUser.js`) verifies
  it with Supabase, looks up the caller's `company_id`/`role` from
  `accounts_rows`, and attaches them to `req` — the same source of truth the
  RLS policies use (see `sql/rls_policies.sql`). **staff.staff_id is never
  used to resolve tenancy** — see DEVELOPER_GUIDE.md §3 for why.
- **Manager sets up staff logins**: `POST /api/accounts` (manager-only,
  guarded by `requireManager`) creates a new login + `accounts_rows` row for
  a staff member under the manager's own company. `PATCH`/`DELETE
  /api/accounts/:id` edit/remove accounts, with a guard against removing the
  last manager. Both manager and staff accounts can `GET /api/accounts` to
  see the roster.

## API quick reference

Every route below (except `/api/auth/register-company`) requires
`Authorization: Bearer <supabase access token>`, and is automatically scoped
to that token's `company_id` — you don't need to pass a company id yourself.

| Resource | Endpoints |
|---|---|
| Auth | `POST /api/auth/register-company` (public) |
| Accounts (team) | `GET /api/accounts`, `POST /api/accounts` (manager), `PATCH/DELETE /api/accounts/:id` (manager) |
| Company settings | `GET/PATCH /api/companies/me`, `POST /api/companies/me/logo` (manager) |
| Customers | `GET/POST /api/customers`, `GET/PATCH/DELETE /api/customers/:id` |
| Pets | `GET/POST /api/pets`, `GET/PATCH/DELETE /api/pets/:id` |
| Staff | `GET/POST /api/staff`, `GET/PATCH/DELETE /api/staff/:id` |
| Coupons | `GET/POST /api/coupons`, `GET/PATCH/DELETE /api/coupons/:id` |
| Chat messages | `GET/POST /api/chat-messages`, `GET/PATCH/DELETE /api/chat-messages/:id` (delete is manager-only) |
| Leave requests | `GET/POST /api/leave-requests`, `POST /api/leave-requests/:id/decision` |
| Member info | `GET /api/member-info`, `PATCH /api/member-info/:id/manual-adjustment` |
| Redemption ledger | `GET /api/redemptions`, `GET /api/redemptions/:id`, `POST /api/redemptions/:id/decision` (manager — approve/reject, deducts points on approval), `POST /api/redemptions/:id/cancel` (manager) |
| Rooms | `GET /api/rooms` (read-only) |
| Bookings | `GET/POST /api/bookings/:type`, `GET/PATCH/DELETE /api/bookings/:type/:id` (`:type` = grooming/daycare/boarding) |
| Payments | `GET /api/payments`, `GET /api/payments/:id`, `POST /api/payments/:id/quote-voucher`, `POST /api/payments/:id/redemption-request`, `POST /api/payments/:id/verify` (the dashboard's "Verify Payment & Redemption" button), `POST /api/payments/:id/mark-paid` (manager — same underlying RPC, allows a manual `final_amount` override), `POST /api/payments/:id/refund` (manager) |
| Dashboard | `GET /api/dashboard/summary`, `GET /api/dashboard/revenue?period=` |

## Known gaps to come back to

- **Run the SQL files in the one-time setup order above** before using the
  matching CRUD buttons.
- **Customer name on payment rows**: `payment` has no direct
  `customer_id`, only reachable via booking → pet → customer. Fine at small
  scale (see the note in `payment-page.js`); consider denormalizing later.
