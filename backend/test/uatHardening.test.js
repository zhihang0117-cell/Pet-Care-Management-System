import test from "node:test";
import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";

import { BOOKING_TYPES } from "../src/lib/bookingTypes.js";
import { safePostgrestSearch } from "../src/lib/queryValidation.js";

test("daycare add-ons are first-class billable fields", () => {
  assert.equal(BOOKING_TYPES.daycare.hasAddOn, true);
  assert.equal(BOOKING_TYPES.daycare.addOnPriceCol, "add_on_price");
});

test("PostgREST search cannot inject an additional OR filter", () => {
  assert.equal(safePostgrestSearch("nail clipping"), "nail clipping");
  assert.throws(() => safePostgrestSearch("foo%,booking_status.eq.Scheduled"), /unsupported filter/);
  assert.throws(() => safePostgrestSearch("foo)"), /unsupported filter/);
});

test("AI backend calls fail closed and have an abort timeout", async () => {
  const source = await readFile(new URL("../src/lib/aiBackend.js", import.meta.url), "utf8");
  assert.match(source, /internal key is not configured/);
  assert.match(source, /AbortController/);
  assert.match(source, /AI_BACKEND_TIMEOUT_MS/);
});

test("Render passes this backend's actual Supabase env name, and Cloud Run gets the Python tenant env", async () => {
  // The Python AI service (app/, main.py) is deployed on Google Cloud Run,
  // not Render (see render.yaml's own comments and README.md's "Deployment
  // (Google Cloud Run)" section) — RELATIONAL_COMPANY_ID belongs in that
  // gcloud command's --set-env-vars, not in render.yaml.
  const renderConfig = await readFile(new URL("../../render.yaml", import.meta.url), "utf8");
  assert.match(renderConfig, /- key: SUPABASE_SERVICE_ROLE_KEY/);
  assert.doesNotMatch(renderConfig, /- key: SUPABASE_SERVICE_KEY\s*$/m);
  assert.match(renderConfig, /CLOUD_RUN_AI_BACKEND_URL/);
  assert.match(renderConfig, /CLOUD_RUN_AI_BACKEND_INTERNAL_KEY/);

  const rootReadme = await readFile(new URL("../../README.md", import.meta.url), "utf8");
  assert.match(rootReadme, /RELATIONAL_COMPANY_ID=1/);
  assert.match(rootReadme, /gcloud run deploy/);
});

test("mark-paid validates payment_method and deprecated auth shims are gone", async () => {
  const payments = await readFile(new URL("../src/routes/payments.js", import.meta.url), "utf8");
  assert.match(payments, /PAYMENT_METHODS\.has/);
  await assert.rejects(access(new URL("../src/auth.js", import.meta.url)));
  await assert.rejects(access(new URL("../src/authUser.js", import.meta.url)));
});

test("SQL migration index documents the live booking conflict migration", async () => {
  const readme = await readFile(new URL("../sql/README.md", import.meta.url), "utf8");
  assert.match(readme, /booking_conflict_prevention_migration\.sql/);
  assert.match(readme, /\[check_in_date, check_out_date\)/);
});

test("member enrollment is unique and atomic across AI and dashboard writes", async () => {
  const sql = await readFile(new URL("../sql/loyalty_member_registration_migration.sql", import.meta.url), "utf8");
  const route = await readFile(new URL("../src/routes/memberInfo.js", import.meta.url), "utf8");

  assert.match(sql, /unique \(company_id, customer_id\)/);
  assert.match(sql, /create or replace function register_loyalty_member_atomic/);
  assert.match(sql, /pg_advisory_xact_lock/);
  assert.match(route, /rpc\("register_loyalty_member_atomic"/);
});

test("grooming conflicts persist and use the selected duration", async () => {
  const sql = await readFile(new URL("../sql/booking_conflict_prevention_migration.sql", import.meta.url), "utf8");
  const service = await readFile(new URL("../src/lib/bookingService.js", import.meta.url), "utf8");

  // The SQL column/RPC default is deliberately still 90 (2026-08-10: a
  // live schema/RPC default change is a separate decision from the app's
  // own default duration, and every row Node/Python actually write always
  // carries an explicit duration_minutes — this SQL fallback is only for
  // a row that somehow has none at all).
  assert.match(sql, /duration_minutes int not null default 90/);
  assert.match(sql, /make_interval\(mins => coalesce\(b\.duration_minutes, 90\)\)/);
  assert.match(service, /duration_minutes: body\.duration_minutes/);
  // 60, not 90 (2026-08-10): grooming's own default duration when none is
  // given — see app/db/availability_service.py's matching Python default.
  assert.match(service, /endMinutes: Number\(booking\.duration_minutes\) \|\| 60/);
});

test("eval console has a persistent structured tool-call inspector", async () => {
  const consoleHtml = await readFile(new URL("../../frontend/eval_console.html", import.meta.url), "utf8");
  assert.match(consoleHtml, /id="traceHistory"/);
  assert.match(consoleHtml, /data-filter="errors"/);
  assert.match(consoleHtml, /data-filter="writes"/);
  assert.match(consoleHtml, /Executed args/);
  assert.match(consoleHtml, /Tool result/);
  assert.match(consoleHtml, /function recordTraceTurn/);
  assert.match(consoleHtml, /execution_order/);
  assert.match(consoleHtml, /agent_state/);
  assert.match(consoleHtml, /X-Chat-Key/);
});

test("confirmation documents have a resend tool and a typed console attachment contract", async () => {
  const consoleHtml = await readFile(new URL("../../frontend/eval_console.html", import.meta.url), "utf8");
  const main = await readFile(new URL("../../main.py", import.meta.url), "utf8");
  const orchestrator = await readFile(new URL("../../app/orchestrator.py", import.meta.url), "utf8");
  const prompt = await readFile(new URL("../../app/prompts/system_prompt.py", import.meta.url), "utf8");
  const documentTool = await readFile(new URL("../../app/tools/document_tools.py", import.meta.url), "utf8");

  assert.match(documentTool, /def send_booking_confirmation/);
  assert.match(documentTool, /delivery_status in _DELIVERED_STATUSES/);
  assert.match(orchestrator, /send_booking_confirmation/);
  assert.match(prompt, /call send_booking_confirmation/);
  assert.match(main, /"documents": _documents_from_trace/);
  assert.match(consoleHtml, /function documentsFromResponse/);
  assert.match(consoleHtml, /function appendDocumentCards/);
  assert.match(consoleHtml, /document-card/);
});

test("Python tool trace records iteration, execution mode, tool id, and per-call duration", async () => {
  const orchestrator = await readFile(new URL("../../app/orchestrator.py", import.meta.url), "utf8");
  // tool_batch_execution.py was merged into tool_loop.py during the
  // app/agent module consolidation (12 single-purpose files -> a few
  // grouped-by-concern ones) — this test still needs the same combined
  // source text, just from its new home.
  const toolLoop = await readFile(new URL("../../app/agent/tool_loop.py", import.meta.url), "utf8");
  const runtime = `${orchestrator}\n${toolLoop}`;
  assert.match(runtime, /"iteration": iteration_index \+ 1/);
  assert.match(runtime, /"tool_call_id": tool_call\["id"\]/);
  assert.match(runtime, /"execution_mode": execution_mode/);
  assert.match(runtime, /"parallel_block_reason": parallel_block_reason/);
  assert.match(runtime, /"batch_duration_ms": batch_duration_ms/);
  assert.match(runtime, /_TOOL_EXECUTOR\.submit\(run_timed, tool_call\)/);
  assert.match(runtime, /future\.result\(timeout=remaining_seconds\)/);
  assert.match(runtime, /"executor_max_workers": TOOL_EXECUTOR_MAX_WORKERS/);
  assert.match(runtime, /"execution_order": record\["execution_order"\]/);
  assert.match(runtime, /"queue_wait_ms": record\["queue_wait_ms"\]/);
  assert.match(runtime, /"duration_ms": record\["duration_ms"\]/);
  assert.match(runtime, /perf_counter\(\)/);
});
