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

test("booking cancellation routes through the dedicated atomic RPC, not the generic one", async () => {
  // Real gap confirmed live 2026-08-11 ("kanban不能换status" — the
  // Kanban board's drag-to-Cancelled, and the booking edit form's status
  // dropdown, both silently failed): the live database now rejects any
  // update_booking_atomic call whose patch sets booking_status to
  // "Cancelled" ("Use cancel_booking_atomic to cancel a booking", P0001),
  // requiring this dedicated RPC instead — same fix already applied to
  // the Python AI backend's cancel_booking. Not asserted against a
  // committed SQL migration (unlike update_booking_atomic/create_booking_
  // atomic/delete_booking_atomic above) because cancel_booking_atomic
  // itself isn't in any committed migration — it exists only in the live
  // database, discovered by direct testing, not by reading a migration
  // file — so this only locks in the JS side of the fix.
  const service = await read("../src/lib/bookingService.js");
  assert.match(service, /rpc\("cancel_booking_atomic"/);
  assert.match(service, /booking_status === "Cancelled"/);
});

test("protected state transitions cannot use generic CRUD updates", async () => {
  const leave = await read("../src/routes/leaveRequests.js");
  const enquiry = await read("../src/routes/chatMessages.js");
  assert.match(leave, /must be reviewed through the decision operation/);
  assert.match(enquiry, /Reply text cannot be empty/);
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
