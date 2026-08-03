# Developer Guide — Pawfect Backend

This is the "how do I work on this codebase" guide. `README.md` covers setup
and deployment; this covers architecture, conventions, and step-by-step
recipes for extending it.

---

## 1. Mental model

```
HTTP request
   │
   ▼
requireAuthUser (middleware/authUser.js) ── verifies the Supabase token, attaches req.companyId/req.role
   │
   ▼
route handler   (routes/*.js) ── thin: parses input, calls a service, shapes the response
   │
   ▼
service layer   (lib/*.js) ── the actual business logic (pricing, validation, multi-step writes)
   │
   ▼
Supabase client (supabaseClient.js) ── talks to Postgres with the service_role key
```

**Rule of thumb: routes are thin, services hold logic.** A route file should
mostly be "read the request, call a function, send the response". If you
find yourself writing `if` statements about prices, points, or multi-table
writes inside a `routes/*.js` file, that logic belongs in `lib/*.js` instead.
`bookingService.js` and `paymentService.js` are the reference examples of
this pattern.

## 2. Folder structure

```
src/
  server.js              — Express app setup, middleware, route mounting, error handler
  supabaseClient.js       — the one Supabase client (service_role key), imported everywhere
  middleware/
    auth.js               — asyncHandler
    authUser.js            — requireAuthUser (verified-session tenant scoping), requireManager
  lib/
    pricing.js            — computeFinalAmount, assertCanRedeem, getLoyaltyTier — pure functions, no I/O
    crudFactory.js         — makeCrudRouter(): generates list/get/create/update/delete for a simple table
    bookingService.js      — createBooking(), findBookingByPaymentId() — the booking+payment write path
    paymentService.js      — verifyPayment(), quoteVoucher(), getPaymentDetail() — the payment write path
  routes/
    customers.js, pets.js, staff.js, coupons.js, chatMessages.js   — thin, built with makeCrudRouter
    leaveRequests.js       — makeCrudRouter + one custom action (/decision)
    memberInfo.js          — custom (read-only + a guarded manual-adjustment endpoint)
    redemptions.js         — GET / and GET /:id are read-only, but POST
                             /:id/decision (manager-only — approve/reject,
                             this is what actually deducts points, via
                             decide_redemption) and POST /:id/cancel
                             (manager-only) are real writes. Rows are first
                             created by request_redemption (see payments.js
                             /:id/redemption-request), not verify_payment.
    rooms.js               — read-only room catalog (room_id is a manually
                             assigned text code, not a DB identity column,
                             so create/update/delete aren't wired up)
    bookings.js            — thin wrapper around bookingService
    payments.js            — thin wrapper around paymentService
    dashboard.js           — aggregation queries, no writes
sql/
  verify_payment_function.sql — atomic payment/loyalty/booking completion (see §4)
  crud_consistency_functions.sql — transactional customer deletion and booking/payment CRUD
  crud_hardening_migration.sql — points-adjustment audit and serialized account guards
frontend integration/
  api-client.js, payment-page.js — example of how an HTML page consumes this API
```

## 3. Multi-tenancy: `company_id`

Every table (except `companies_rows` itself) has a `company_id` column.
Every query in this codebase filters on it — that's what keeps two
different pet-care businesses' data from leaking into each other.

- `requireAuthUser` (`middleware/authUser.js`) resolves `req.companyId` for
  every route. Verifies the `Authorization: Bearer <token>` against Supabase,
  then looks up `company_id`/`role` from **`accounts_rows`** (never from
  `staff` — `staff` rows have no `auth_user_id` and aren't login identities
  at all, they're just scheduling/display data). This is what every business
  route (`/api/customers`, `/api/bookings/...`, etc.) uses — there's no
  header-trust fallback anywhere in this codebase; a request that can't be
  verified against a real Supabase session is rejected, never quietly
  defaulted to some company.
- **Every** service function takes `companyId` as an explicit parameter and
  passes it to every `.eq("company_id", companyId)` call. When you add a new
  query, copy this pattern — don't rely on RLS to do it for you, because
  this backend uses the `service_role` key, which bypasses RLS entirely. RLS
  (see `sql/rls_policies.sql`) is defense-in-depth for if a client ever talks
  to Supabase directly, not what protects the API layer itself.
- **Registering a brand new company is special** — see
  `sql/register_company_function.sql` and `routes/auth.js`. There's no
  existing `accounts_rows` row to resolve a `company_id` from at that point,
  so it can't go through `requireAuthUser` or ordinary RLS-governed writes;
  it's a dedicated, atomic, server-side bootstrap step instead.

## 4. Why one thing lives in SQL instead of Node

`verify_payment()` is a Postgres function, not a JS function, for one
reason: **row locking**. Two staff members could click "Verify" on the same
payment (or two payments for the same member) within milliseconds of each
other. `SELECT ... FOR UPDATE` inside a Postgres transaction guarantees the
second request waits for the first to finish before it reads the member's
points balance — so points can never be double-spent or double-earned. You
cannot get that guarantee by doing sequential `.select()` then `.update()`
calls from Node; there's a race-condition window between the read and the
write. If you ever need another "read-check-write, must not race" operation,
follow this same pattern (a `plpgsql` function called via `supabase.rpc()`)
rather than trying to do it with plain client calls.

## 5. Recipes

### Add a new simple CRUD table (no special logic)

1. Add the table to Supabase with a `company_id` column.
2. Create `src/routes/yourTable.js`:
   ```js
   import { makeCrudRouter } from "../lib/crudFactory.js";
   export const yourTableRouter = makeCrudRouter({
     table: "your_table",
     idColumn: "your_table_id",
     searchableColumns: ["some_text_column"],
     defaultOrder: { column: "your_table_id", ascending: true },
   });
   ```
3. Mount it in `server.js`: `app.use("/api/your-table", yourTableRouter);`

You now have list (with filters/search/limit), get, create, update, delete —
all company-scoped — for free.

### Add a custom business-logic endpoint

Follow `bookingService.js`/`paymentService.js`:

1. Write the logic as an exported function in `src/lib/yourFeatureService.js`
   that takes `companyId` plus whatever else it needs, and throws
   `Error`s with a `.status` property for expected failures (not-found,
   validation, etc.) — the central error handler in `server.js` reads that.
2. Import it in a thin route handler, wrapped in `asyncHandler`:
   ```js
   router.post("/:id/do-thing", asyncHandler(async (req, res) => {
     const result = await doThing(req.companyId, req.params.id, req.body);
     res.json(result);
   }));
   ```

### Wire up another frontend page

Follow the `payment-page.js` example:

1. Identify which `common.js` functions build that page's data (usually
   `initXPage`, `renderXLists`, `renderXTable`, plus any action handlers
   like approve/verify/remove).
2. Rewrite them to call `api.get/post/patch/del(...)` (from
   `api-client.js`) instead of reading the mock arrays, keeping the same
   DOM element IDs so the existing CSS/HTML don't need to change.
3. Load `api-client.js` before your rewritten script, before `common.js`.
4. Ask me to do this for a specific page if you want — booking.html,
   loyalty.html, staff.html, dashboard.html, and profile.html all follow the
   same recipe.

## 6. Testing endpoints locally

With the server running (`npm start`), use curl or a REST client. Every
request needs `Content-Type: application/json` for bodies, and a real
`Authorization: Bearer <supabase access token>` from a logged-in test
account (get one via `supabase.auth.signInWithPassword` or the login page) —
there is no header-based company override for local testing.

```bash
# List today's pending grooming bookings
curl "http://localhost:4000/api/bookings/grooming?booking_status=Pending"

# Create a grooming booking (auto-computes final_amount = price + add_on_price)
curl -X POST http://localhost:4000/api/bookings/grooming \
  -H "Content-Type: application/json" \
  -d '{"pet_id":1,"staff_id":2,"service_name":"Bathing","booking_date":"2026/8/1","booking_time":"10:00:00","price":80,"add_on":"Nail Clipping","add_on_price":15}'

# Preview a voucher without applying it
curl -X POST http://localhost:4000/api/payments/1/quote-voucher \
  -H "Content-Type: application/json" -d '{"coupon_id":1}'

# Verify a payment (deducts/earns points, marks Paid)
curl -X POST http://localhost:4000/api/payments/1/verify \
  -H "Content-Type: application/json" -d '{"coupon_id":1,"staff_id":1}'

# Dashboard summary
curl http://localhost:4000/api/dashboard/summary
```

If you get `{"error":"..."}` with a 4xx status, that's an *expected*
failure (validation, not-found, insufficient points) — read the message,
it's written for a human. A 500 means something unexpected broke; check the
server's console log (the error handler in `server.js` logs the full error
before responding).

## 7. Common gotchas

- **"relation does not exist" / "column does not exist"** — your Supabase
  table/column name doesn't match what's hardcoded in the route/service.
  This actually happened twice during development: `daycare_booking`'s
  payment-link column turned out to be `ment_id` (now renamed to
  `payment_id` in Supabase) and its primary key is `daycare_booking_id`
  (not `daycare_booking`, despite that being the literal CSV header) — both
  are now correctly set in `BOOKING_TYPES` (`lib/bookingService.js`). If you
  add new tables, always double-check the *actual* Supabase schema, not just
  the CSV header, before hardcoding a column name.
- **CORS errors in the browser console** — add your frontend's origin to
  `CORS_ORIGINS` in `.env` and restart the server.
- **Points look wrong after testing `/verify` repeatedly** — the SQL
  function refuses to verify an already-`Paid` payment (throws "already been
  verified"), specifically so you can't double-click your way into extra
  points during testing. If you need to re-test, reset that payment's
  `status` back to `Pending` in Supabase first.

## 8. Before going to production

- [ ] Run all three SQL files, in order (see README).
- [x] `DEFAULT_COMPANY_ID`/`resolveCompany` — resolved: the generic
      header-trust `/api/llm/*` surface (and `resolveCompany`, which only
      ever backed it) was removed entirely rather than hardened, since
      nothing in the codebase called it anyway. Every remaining route
      resolves `company_id` from a verified Supabase session
      (`requireAuthUser`), never from client-supplied input.
- [ ] Set `CORS_ORIGINS` to your real deployed frontend URL(s) only.
- [ ] Consider Supabase RLS policies as defense-in-depth even though this
      backend uses `service_role` — it protects you if that key ever leaks
      or if you add a second, less-trusted client later.
