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

test("Render passes the Python backend's actual Supabase and tenant env names", async () => {
  const renderConfig = await readFile(new URL("../../render.yaml", import.meta.url), "utf8");
  assert.match(renderConfig, /- key: SUPABASE_SERVICE_ROLE_KEY/);
  assert.doesNotMatch(renderConfig, /- key: SUPABASE_SERVICE_KEY\s*$/m);
  assert.match(renderConfig, /- key: RELATIONAL_COMPANY_ID\s*\n\s*value: "1"/);
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
  assert.match(orchestrator, /"iteration": iteration_index \+ 1/);
  assert.match(orchestrator, /"tool_call_id": tool_call\["id"\]/);
  assert.match(orchestrator, /"execution_mode": execution_mode/);
  assert.match(orchestrator, /"parallel_block_reason": parallel_block_reason/);
  assert.match(orchestrator, /"batch_duration_ms": batch_duration_ms/);
  assert.match(orchestrator, /_TOOL_EXECUTOR\.submit\(run_timed, tool_call\)/);
  assert.match(orchestrator, /future\.result\(timeout=remaining_seconds\)/);
  assert.match(orchestrator, /"executor_max_workers": TOOL_EXECUTOR_MAX_WORKERS/);
  assert.match(orchestrator, /"execution_order": record\["execution_order"\]/);
  assert.match(orchestrator, /"queue_wait_ms": record\["queue_wait_ms"\]/);
  assert.match(orchestrator, /"duration_ms": record\["duration_ms"\]/);
  assert.match(orchestrator, /time_module\.perf_counter\(\)/);
});
