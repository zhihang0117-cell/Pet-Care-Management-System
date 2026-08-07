import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const read = relative => readFile(new URL(relative, import.meta.url), "utf8");

test("authenticated Supabase clients cannot bypass backend write rules", async () => {
  const rls = await read("../sql/rls_policies.sql");
  const ordinary = rls.slice(rls.indexOf("-- 4."), rls.indexOf("-- 5."));
  const bookings = rls.slice(rls.indexOf("-- 5."), rls.indexOf("-- 6."));
  assert.doesNotMatch(ordinary, /create policy tenant_(insert|update|delete)/);
  assert.doesNotMatch(bookings, /create policy tenant_update/);
});

test("booking and payment CRUD uses atomic database functions", async () => {
  const service = await read("../src/lib/bookingService.js");
  const sql = await read("../sql/crud_consistency_functions.sql");
  for (const fn of ["create_booking_atomic", "update_booking_atomic", "delete_booking_atomic"]) {
    assert.match(service, new RegExp(`rpc\\("${fn}"`));
    assert.match(sql, new RegExp(`function ${fn}`));
  }
  assert.match(service, /Selected pet does not belong to this company/);
  assert.match(service, /Selected staff member does not belong to this company/);
  assert.match(sql, /Reject or refund the linked point redemption/);
});

test("protected state transitions cannot use generic CRUD updates", async () => {
  const leave = await read("../src/routes/leaveRequests.js");
  const enquiry = await read("../src/routes/chatMessages.js");
  const bookingService = await read("../src/lib/bookingService.js");
  const bookingSql = await read("../sql/booking_conflict_prevention_migration.sql");
  assert.match(leave, /must be reviewed through the decision operation/);
  assert.match(leave, /rpc\("decide_leave_request"/);
  assert.match(enquiry, /Reply text cannot be empty/);
  assert.match(bookingService, /rpc\("cancel_booking_atomic"/);
  assert.match(bookingService, /BOOKING_STATUS_TRANSITIONS/);
  assert.match(bookingSql, /Use cancel_booking_atomic to cancel a booking/);
  assert.match(bookingSql, /booking_status_transition_allowed/);
});

test("booking conflicts lock staff, pet, and room resources", async () => {
  const sql = await read("../sql/booking_conflict_prevention_migration.sql");
  const holds = await read("../sql/booking_slot_holds_migration.sql");
  assert.match(sql, /pet_has_conflicting_booking/);
  assert.match(sql, /':staff:' \|\| v_staff_id/);
  assert.match(sql, /':pet:' \|\| v_pet_id/);
  assert.match(sql, /':room:' \|\| coalesce\(v_room_type/);
  assert.match(holds, /function public\.acquire_booking_hold/);
  assert.match(holds, /b\.check_in_date < p_check_out_date and p_check_in_date < b\.check_out_date/);
  assert.match(holds, /h\.check_in_date < p_check_out_date and p_check_in_date < h\.check_out_date/);
});

test("staff dashboard booking writes enforce operating and eligibility rules", async () => {
  const service = await read("../src/lib/bookingService.js");
  assert.match(service, /company_business_hours/);
  assert.match(service, /company_closed_dates/);
  assert.match(service, /approved leave/i);
  assert.match(service, /vaccination_status/);
  assert.match(service, /service_types_json/);
});

test("calendar exposes half-hour slots and preserves the moved checkout time", async () => {
  const frontend = await read("../../web/common.js");
  assert.match(frontend, /"17:00", "17:30"/);
  assert.match(frontend, /checkOutTime: movedCheckOutTime/);
  assert.doesNotMatch(frontend, /existingCheckOutTime/);
  assert.doesNotMatch(frontend, /slotBookings\.length < 3/);
});

test("manual points adjustments are atomic and permanently audited", async () => {
  const route = await read("../src/routes/memberInfo.js");
  const sql = await read("../sql/crud_hardening_migration.sql");
  assert.ok(route.includes('rpc("adjust_loyalty_points"'));
  assert.match(sql, /create table if not exists loyalty_points_adjustment/);
  assert.match(sql, /old_balance/);
  assert.match(sql, /changed_by_account_id/);
});

test("approved unpaid redemptions can be cancelled with points returned", async () => {
  const route = await read("../src/routes/redemptions.js");
  const sql = await read("../sql/crud_hardening_migration.sql");
  const frontend = await read("../../web/common.js");
  assert.ok(route.includes('"/:id/cancel"'));
  assert.match(sql, /function cancel_approved_redemption/);
  assert.match(sql, /v_member\.points_balance \+ coalesce\(v_redemption\.loyalty_spend/);
  assert.match(sql, /status = 'Cancelled'/);
  assert.match(frontend, /Cancel &amp; Return Points/);
});

test("last active manager guard is serialized in Postgres", async () => {
  const accounts = await read("../src/routes/accounts.js");
  const sql = await read("../sql/crud_hardening_migration.sql");
  assert.ok(accounts.includes('rpc("update_account_guarded"'));
  assert.ok(accounts.includes('rpc("delete_account_guarded"'));
  assert.match(sql, /order by account_id for update/);
});

test("an explicit empty service selection enables no booking modules", async () => {
  const bookings = await read("../src/routes/bookings.js");
  const dashboard = await read("../src/routes/dashboard.js");
  assert.match(bookings, /Array\.isArray\(configured\)\s*\? configured/);
  assert.match(dashboard, /!Array\.isArray\(configured\)/);
});

test("staff status is saved and key stored HTML values are escaped", async () => {
  const frontend = await read("../../web/common.js");
  assert.match(frontend, /status: formData\.get\("status"\)/);
  assert.match(frontend, /escapeUiText\(customer\.full_name\)/);
  assert.match(frontend, /escapeUiText\(pet\.health_notes\)/);
  assert.match(frontend, /escapeUiText\(s\.staff_name\)/);
});

test("RAG and backup tables are not directly readable by browser roles", async () => {
  const sql = await read("../sql/security_integrity_hardening_migration.sql");
  assert.match(sql, /chunks_bge_large/);
  assert.match(sql, /enable row level security/);
  assert.match(sql, /revoke all on table public\.%I from public, anon, authenticated/);
});

test("workflow statuses and tenant links are enforced in Postgres", async () => {
  const sql = await read("../sql/security_integrity_hardening_migration.sql");
  assert.match(sql, /when 'pending' then 'Pending'/);
  assert.match(sql, /booking_status set not null/);
  assert.match(sql, /pet_customer_same_company_fk/);
  assert.match(sql, /payment_redemption_same_company_fk/);
  assert.match(sql, /replace_company_availability/);
  assert.match(sql, /notify pgrst, 'reload schema'/);
});

test("chat tenant keys are hash-only and backend-only", async () => {
  const migration = await read("../sql/chat_api_key_migration.sql");
  const resolver = await read("../../app/db/customer_context.py");
  assert.match(migration, /chat_api_key_hash text not null/);
  assert.doesNotMatch(migration, /\n\s*chat_api_key text/);
  assert.match(migration, /revoke all on table company_chat_key from public, anon, authenticated/);
  assert.match(resolver, /hashlib\.sha256/);
  assert.match(resolver, /\.eq\("chat_api_key_hash", key_hash\)/);
});

test("booking cancellation and its financial reversal are one transaction", async () => {
  const sql = await read("../sql/cancel_booking_atomic_migration.sql");
  const actions = await read("../../app/db/relational_actions.py");
  assert.match(sql, /function public\.cancel_booking_atomic/);
  assert.match(sql, /refund_payment/);
  assert.match(sql, /cancel_approved_redemption/);
  assert.match(actions, /\.rpc\(\s*"cancel_booking_atomic"/);
  assert.doesNotMatch(actions, /def _void_payment_for_booking/);
});

test("application inserts rely on database identities and mark-paid uses tenant earn rate", async () => {
  const actions = await read("../../app/db/relational_actions.py");
  const identitySql = await read("../sql/fix_missing_identity_columns.sql");
  const payments = await read("../src/routes/payments.js");
  assert.doesNotMatch(actions, /_next_table_id/);
  assert.match(identitySql, /pg_get_serial_sequence/);
  assert.match(identitySql, /max\(%I\)::bigint/);
  assert.match(payments, /const earnRate = await getEarnRateForCompany\(req\.companyId\)/);
  assert.match(payments, /p_earn_rate: earnRate/);
});
