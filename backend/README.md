# Pawfect Backend (Express + Supabase)

A Node/Express API that sits between your Supabase database and (a) your
HTML frontend, and (b) an LLM/agent — so neither one talks to Supabase
directly. This matters for two reasons: your `service_role` key (which
bypasses Row Level Security) must never reach the browser or a public LLM
prompt, and money-critical logic (pricing, voucher validation, point
deduction) needs to live in one trusted place, not be re-implemented in
JavaScript running on someone's laptop.

```
Browser (HTML/common.js)  ──┐
                             ├──►  Express API (this project)  ──►  Supabase (Postgres)
LLM / agent  ────────────────┘
```

## 1. One-time Supabase setup

Open your Supabase project → SQL Editor and run these files in order:

1. `sql/company_settings_migration.sql`
2. `sql/verify_payment_function.sql`
3. `sql/enquiry_refund_logo_migration.sql`
4. `sql/crud_consistency_functions.sql`
5. `sql/rls_policies.sql`
6. `sql/register_company_function.sql`
7. `sql/company_documents_migration.sql`
8. `../ai-backend/supabase/migrations/002_add_document_id_to_chunks_bge_large.sql`

These migrations add company settings/logo storage, enquiry reply audit data,
payment verification/refund audit data, and the atomic payment functions. They
do not delete any existing rows.

### Company policy documents and RAG

Set `AI_BACKEND_URL` and `AI_BACKEND_INTERNAL_KEY` in `backend/.env`. Set the
same secret as `INTERNAL_API_KEY` in `ai-backend/.env`, then start FastAPI
before Express.

Policy DOCX files use the private `company-documents` bucket with paths shaped
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
#   LLM_API_KEY                  - any long random string
npm start
```

Visit `http://localhost:4000/health` — you should see `{"ok":true,"companyId":1}`.

## 3. Where to deploy it (recommendation)

**Render.com** (or Railway — same idea), free/cheap tier, always-on Node process:

- Simplest mental model for a small team: it's just `npm start`, same as local.
- Environment variables are set once in the dashboard (your Supabase service
  key never touches your frontend's hosting, e.g. Vercel/Netlify/GitHub Pages,
  where your static HTML can live separately).
- Comfortably handles the atomic verify-payment transaction, generic CRUD,
  and the LLM tool endpoint without any serverless cold-start/timeout concerns.
- Free tier is fine for a single pet-care business's traffic; upgrade only if
  you outgrow it.

Steps: push this `backend/` folder to its own GitHub repo → Render → New Web
Service → point at the repo → build command `npm install`, start command
`npm start` → add the same env vars from `.env` in Render's dashboard.

(Serverless functions or Supabase Edge Functions would also work, but you'd
need to split this into many small functions and be careful about connection
pooling; not worth it at this scale.)

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
- `frontend-integration/api-client.js` — a small `fetch` wrapper (`api.get`,
  `api.post`, etc. plus convenience methods) to drop into every page.
- `web/api-client.js` and `web/common.js` wire the booking, daily overview,
  CRM, loyalty, payment, enquiry, staff, settings, and analytical dashboards
  to the authenticated API.

**④ CRUD for an LLM to update the database:**
- `src/llm/tools.js` — tool schema (Anthropic/OpenAI function-calling shaped)
  + dispatcher. `GET /api/llm/tools-schema` returns the schema; `POST
  /api/llm/execute` with `{ "tool": "...", "input": {...} }` runs it.
  Both require an `x-llm-api-key` header (see `.env`'s `LLM_API_KEY`).
- `src/llm/tableAllowlist.js` — deliberately restricts which tables/actions
  the LLM's generic `create_record`/`update_record`/`delete_record` tools
  can touch. Bookings can only be *updated* (not created/deleted) through
  the generic tool — real bookings must go through `create_booking` so
  pricing stays correct. `member_info`, `redemption`, and `payment_history`
  are **read-only** for the LLM — points and the ledger can only change via
  the `verify_payment` tool, so the balance and the transaction log can
  never drift apart, even if the LLM is instructed to do something unusual.

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
- **The LLM's `/api/llm/*` routes are a separate trust model** — they use
  `x-llm-api-key` + `x-company-id` header, not a user session, since an
  agent isn't a logged-in human. See `middleware/auth.js`.

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
| Redemption ledger | `GET /api/redemptions` (read-only) |
| Bookings | `GET/POST /api/bookings/:type`, `GET/PATCH/DELETE /api/bookings/:type/:id` (`:type` = grooming/daycare/boarding) |
| Payments | `GET /api/payments`, `GET /api/payments/:id`, `POST /api/payments/:id/quote-voucher`, `POST /api/payments/:id/verify`, `POST /api/payments/:id/refund` (manager) |
| Dashboard | `GET /api/dashboard/summary`, `GET /api/dashboard/revenue?period=` |
| LLM tools | `GET /api/llm/tools-schema`, `POST /api/llm/execute` (needs `x-llm-api-key`) |

## Known gaps to come back to

- **Run the SQL files in the one-time setup order above** before using the
  matching CRUD buttons.
- **Customer name on payment rows**: `payment` has no direct
  `customer_id`, only reachable via booking → pet → customer. Fine at small
  scale (see the note in `payment-page.js`); consider denormalizing later.
