# Constrained Single-Agent Architecture — Refactor Plan

Branch: `refactor/four-layer-architecture` (parallel to `deploy`; `deploy`'s
`/chat` stays live and untouched until an explicit cutover in the final
phase — see "Risk strategy" below).

Source of the design: user's architecture review (session of 2026-08-09).
Diagnosis: the same concern (e.g. booking availability) is currently
managed independently in five places — `system_prompt.py`,
`orchestrator.py`, `guardrails.py`, `ConversationState`, and the tool
layer — which is why every new bug fix this session took the shape of
another `if`/regex bolted onto one of those five, instead of a structural
fix. Target shape:

```
PROMPT -> AGENT/ORCHESTRATOR -> CONTEXT + POLICY -> TOOLS
```

Not rewritten: Supabase schema, RLS/company_id isolation, RAG (BGE-large +
retriever), PDF generation, WhatsApp dispatch, the Node dashboard backend.
Only the Python agent-control layer (`app/agent`, `app/context`,
`app/prompts`, `app/orchestrator.py`, tool *schemas*) is in scope.

## Target directory shape

```
app/
├── agent/
│   ├── runtime.py       # the ~500-800 line core loop (replaces orchestrator.py)
│   ├── policy.py         # guardrails collapsed to ~8 generic categories
│   ├── evidence.py       # evidence store (verified facts, refs, expiry)
│   └── response.py       # grounding/formatting (existing response_grounding.py, trimmed)
├── context/
│   ├── session.py        # ConversationState -> internal SessionState split
│   ├── booking_draft.py  # BookingDraft dataclass (Phase 1, see below)
│   └── builder.py        # AgentContext builder (the small model-facing view)
├── prompts/
│   └── system.py         # ~70-100 line prompt (down from 373)
├── tools/                # reference-based tool schemas (option_ref/slot_ref/preview_ref)
├── rag/  db/  documents/  # unchanged
```

## Phases (each phase: build alongside the old code, prove it with real
Supabase data + pytest, only then touch anything live)

- **Phase 0** — branch + this plan. *(done)*
- **Phase 1** — `BookingDraft` (`app/context/booking_draft.py`) +
  `AgentContext` builder (`app/context/builder.py`), derived read-only from
  the existing `ConversationState`. *(done — tests/test_refactor_phase1_context.py.
  Validated against a real live gpt-4o-mini + real RAG catalogue run, which
  caught a genuine bug in the first draft: `verified_service_options` is an
  unmarked catalogue cache (services + add-ons mixed), so naively trusting
  "the last cached entry" as *the customer's pick* grabbed a stray add-on
  instead. Fixed: only trust the cache when it is genuinely unambiguous
  (exactly one non-add-on entry), and prefer the pending preview's own args
  once one exists — the one place today's state layout captures an actual,
  unambiguous selection. Same fix applied to `verified_availability_slots`.)*
- **Phase 2** — unified tool-result envelope
  (`{ok, code, data, needs, evidence, recoverable, handoff}`,
  `app/agent/envelope.py`, `normalize_tool_result()`). An adapter wraps
  today's tool results into this shape; underlying tool bodies unchanged.
  *(done — tests/test_refactor_phase2_envelope.py, validated against real
  result shapes captured live earlier this session: relational_actions'
  majority `_result()` shape, `check_availability_range`'s different
  top-level shape, inline tool errors with no `"status"` key at all
  (`create_booking`'s `UNVERIFIED_SERVICE_OPTION`, the guardrail's
  `UNVERIFIED_AVAILABILITY_SLOT`), and `resolve_datetime`'s bare data dict
  with neither `"status"` nor `"error"`.)*
- **Phase 3** — reference-based tool args (`option_ref`, `slot_ref`)
  resolved server-side against a short-lived reference registry
  (`app/agent/evidence.py`'s `EvidenceStore`), instead of the model passing
  `company_id`/`customer_id`/`price` directly.
  `app/tools/reference_tools.py`'s `get_service_options`/`check_availability`
  wrap the existing, unmodified `app.tools.customer_tools`/
  `app.tools.availability_tools` logic. *(done —
  tests/test_refactor_phase3_reference_tools.py, validated against real
  Supabase data: minted refs correctly resolve, an unknown ref is rejected,
  refs never cross EvidenceStore instances, and — reusing today's own
  `pet_already_booked` fix — the wrapper correctly carries that field
  through unchanged.)*
- **Phase 4** — split `create_booking` into `preview_booking` /
  `confirm_booking` (`app/tools/reference_tools.py`, `preview_ref`-keyed).
  `preview_booking` computes the exact booking payload from verified
  evidence only and never calls the real write; `confirm_booking` is the
  only path that does, reusing the existing, unmodified
  `app.tools.booking_tools.create_booking` (which already does its own
  fresh staff/room/pet conflict recheck at write time). *(done —
  tests/test_refactor_phase4_preview_confirm.py, including a REAL booking
  created and verified in Supabase, then cleaned up. Caught a genuine bug
  live: `preview_ref` is intentionally content-addressed, but the first
  draft reused it directly as the write's idempotency key — two unrelated
  preview_booking calls describing identical facts then collided on one
  idempotency key, and the second confirm_booking silently replayed the
  first's cached result instead of writing. Fixed by minting a fresh random
  UUID nonce per preview and using THAT as the idempotency key, matching
  `app/orchestrator.py`'s own existing `uuid.uuid4().hex` pattern for
  `create_booking`'s preview — not reinvented. Regression-tested both
  directions: two previews of identical facts now get different
  idempotency keys, and confirming the SAME preview_ref twice still stays
  idempotent (no duplicate booking). Simplified further afterward, per
  explicit "no weird formulas" direction: `EvidenceStore.mint()` no longer
  content-addresses via a SHA1 hash of the payload — every mint() call now
  returns a fresh, unique ref (`uuid.uuid4().hex`-based), which is simpler
  to read AND makes the whole class of "two different things collide on
  the same ref" bug structurally impossible rather than worked around with
  a separate nonce. `preview_ref` is now used directly as the idempotency
  key instead of a second nested nonce field. Same simplification applied
  to `app/context/booking_draft.py`'s `_synthetic_ref` (was a
  `hash(...) % 1_000_000` formula).)*
- **Phase 5** — new slim system prompt (`app/prompts/system_v2.py`,
  `SYSTEM_PROMPT_V2`, 108 lines vs. the live prompt's 382 — a 72% cut,
  matching the proposed 60-70%). Not wired into `app/orchestrator.py`
  (still imports the 382-line prompt) — Phase 6 is the actual cutover.
  *(done — tests/test_refactor_phase5_prompt_smoke.py: a REAL gpt-4o-mini
  call, bound directly to the Phase 3/4 reference tools (decoupled from
  `app/orchestrator.py`'s tool loop, which nothing here has touched),
  proving the specific claim this phase is about — "cheapest one" gets
  reasoned out correctly from verified `option_ref` evidence with NO
  hardcoded "if customer says cheapest, call X" rule in the prompt at all,
  because reference-based tools make the model incapable of restating a
  wrong price/date in the first place. Passed 3/3 real runs.)*
- **Phase 6** — new orchestrator core loop (`app/agent/runtime.py`,
  `handle_turn()`). Reuses existing, unmodified infrastructure
  (`ConversationMemory`, `resolve_identity`, `resolve_datetime`) rather than
  reinventing session/identity handling — only the control-flow/context/
  tool-authorization layer is new. `/chat` still only uses the current
  `PawfectOrchestrator`; `handle_turn()` is directly callable but wired to
  nothing live. *(done — tests/test_refactor_phase6_runtime.py. Two more
  real bugs caught live, on top of Phase 1's and Phase 4's:*
  1. *`_resolve_pet_ref` was validating the model's `pet_ref` against the
     RAW `resolve_identity()` customer dict (keyed `pet_id`) instead of the
     transformed `AgentContext` customer dict (keyed `ref`) the model
     actually saw — the model's perfectly correct `pet_ref="1"` was
     rejected as `UNKNOWN_PET_REF` every time. Fixed by validating against
     `context["customer"]["pets"]` instead.*
  2. *`state.history` only ever stored the final text reply, never tool
     results — so on the turn after a preview, the model had no way to
     recall which `preview_ref` a customer's "yes" pointed at, and tried to
     reconstruct the whole booking from scratch instead of confirming the
     one already made. Fixed with `ConversationState.pending_preview_ref`,
     surfaced back into `AgentContext.pending_action` every turn until
     consumed — the same "hand the model back exactly the one fact it
     needs, not the whole history" principle Phase 1 established.*
  *Also validated the phase's core safety property directly: `confirm_booking`
  is rejected both (a) without a genuine standalone affirmative and (b) when
  targeting a preview minted in the SAME turn — closing the
  "model fills in its own confirmation" loophole structurally instead of
  via a signature-matching cache. Full 3-turn real conversation (greeting →
  preview → confirm) passed twice against real Supabase + real gpt-4o-mini,
  with a real booking created, verified, and cleaned up each time; one rerun
  hit a one-off LLM/network flake and passed clean on retry — not a
  reproducible bug.)*
- **Phase 6.1** — a second external review (same day) checked Phase 1-6
  against real code and found 5 more real gaps, all fixed:
  1. *Date was still not actually constrained: `check_availability`'s
     `date`/`check_out_date` were plain model-supplied strings — the model
     could still pass a self-computed date and get a real, correctly-signed
     `slot_ref` back for the WRONG day. Fixed: `resolve_datetime` now mints
     a `datetime_ref` instead of returning raw fields, and
     `check_availability` only accepts `datetime_ref`/`check_out_datetime_ref`
     — `date`/`check_out_date` removed from the tool schema entirely (not
     just documented against — verified structurally, see
     `test_check_availability_has_no_raw_date_parameter_in_its_schema`).
     The per-turn automatic preprocessing also now mints
     `current_message_datetime_ref` into `AgentContext` so the common
     single-date case doesn't need a redundant explicit `resolve_datetime`
     call — only a genuinely SECOND date expression (BOARDING's check-out)
     needs its own call. Reviewer's suggested fix was "delete
     `resolve_datetime` as a tool entirely and auto-inject the date" — not
     done as-is, because the automatic per-turn resolver can only ever
     resolve one expression per message, and BOARDING genuinely needs two;
     validated live that this still fully closes the raw-date gap while
     keeping BOARDING's two-date case working
     (`test_boarding_flow_resolves_two_separate_dates_via_two_datetime_refs`).*
  2. *`reference_tools.check_availability` had dropped `duration_minutes`,
     `check_out_time`, `preferred_staff`, `selection_target`,
     `check_in_time`, `exclude_booking_id` from the underlying (already
     correct) `availability_tools.check_availability` — meaning DAYCARE
     duration/checkout, BOARDING pickup-time selection, a requested staff
     member, and reschedule (`exclude_booking_id`, or a booking conflicts
     with its own current slot) were all unreachable. Restored, including
     correct `slot_ref` minting for the `CHECK_OUT` selection_target case
     (each candidate is a distinct pickup time, not a drop-off time).*
  3. *`runtime.py`'s bound `preview_booking` tool exposed only 3 of
     `reference_tools.preview_booking`'s 5 real parameters — `add_on_ref`
     and `preferred_staff` were computed correctly one layer down but
     unreachable from the model. Restored.*
  4. *"the second one"/"the cheapest one" on a LATER turn than the one that
     listed options had no ref to resolve against (`state.history` only
     keeps final text, not tool results) — the model would have to call
     `get_service_options` again just to recover a ref it already had.
     Fixed with `ConversationState.active_options`, surfaced into
     `AgentContext.active_options` every turn, replaced wholesale (not
     merged) on each new `get_service_options` call.*
  5. *One claim in the same review was checked and found FALSE, not
     fixed: "this Monday" rolling forward to next Monday when the current
     week's Monday has already passed is not an unfixed bug — it's
     deliberate, already-shipped behavior from earlier this session, with
     its own regression test
     (`test_this_weekday_never_resolves_to_a_date_in_the_past`,
     `tests/test_uat_regressions.py:958`) confirming a future booking
     request should never resolve to an already-passed day.*
  All 241 pytest passing (8 new for this round, including 2 live
  end-to-end runs), `/chat` still fully untouched throughout.
- **Phase 7** — the remaining domain tools: policy, booking history/cancel/
  reschedule, loyalty, documents, staff handoff. 14 model-facing tools total
  now (within the proposed 10-14 range): `get_service_options`,
  `resolve_datetime`, `check_availability`, `preview_booking`,
  `confirm_booking`, `retrieve_policy`, `get_active_bookings`,
  `cancel_booking`, `reschedule_booking`, `get_loyalty`, `redeem_reward`,
  `register_loyalty_member`, `send_booking_confirmation`,
  `handoff_to_staff`. Same reuse principle as Phases 3/4: every one wraps
  existing, unmodified business logic (`app.tools.booking_tools`,
  `app.tools.loyalty_tools`, `app.db.escalations`, ...); only the
  model-facing interface changed (refs instead of raw IDs). *(done —
  tests/test_refactor_phase7_domain_tools.py. Two more real bugs caught
  live:*
  1. *Same "lost the specific target across a turn" bug as Phase 6's
     `pending_preview_ref`, this time for `cancel_booking`/
     `reschedule_booking`: their own two-step `confirm_pet_name` protection
     (a customer must type the pet's name after seeing a real preview —
     stricter than a plain "yes", since it undoes something) has no
     `booking_ref` memory across turns either. Fixed the same way:
     `ConversationState.pending_mutation_target`, surfaced as
     `AgentContext.pending_mutation`, set/cleared by a standalone, directly
     unit-tested `_update_pending_mutation_target()` (extracted out of the
     tool loop specifically so this could be verified deterministically,
     independent of LLM behavior).*
  2. *A second, more consequential gap in the SAME family:
     `AgentContext.pending_action`/`pending_mutation` existed since Phase 6
     but the model wasn't reliably reading it — confirmed live twice, the
     model re-ran the ENTIRE booking flow from scratch on a customer's
     "yes" instead of confirming the existing preview, at one point even
     fabricating plausible-looking-but-fake ref strings from memory of its
     own earlier reply (e.g. `datetime_ref: "2026-09-17T10:00:00"` — an
     ISO string dressed up as a ref, correctly rejected by the real
     `EvidenceStore`, but the model then re-did all four prior tool calls
     instead of using the real pending ref). Fixed by making this
     impossible to miss: a dedicated "CHECK THIS FIRST, EVERY TURN"
     section at the top of the prompt (above OPERATING PRINCIPLES) telling
     the model to check `pending_action`/`pending_mutation` before anything
     else, and switching `AgentContext`'s injection from a raw Python
     dict repr to indented JSON. Passed 3 real end-to-end runs in a row
     after the fix (was failing consistently before it).*
  *Also confirmed a genuinely dense real data artifact from today's own
  extensive testing (10 simultaneous active bookings for one seed
  customer) made an early version of the cancel test unrealistically hard
  for the model to disambiguate — not a code bug, just unusually noisy
  seed data for that one customer; later runs against it still passed once
  the two real bugs above were fixed.*
  All 250 pytest passing, `/chat` still fully untouched.
- **Phase 7.1 (2026-08-10)** — a follow-up architecture review gave 8
  prioritized items; #1/#2/#5 were already covered by Phases 1-7 and #4/#7
  were live-path-only fixes (see below), leaving #3/#6/#8 for the
  prototype, all now done:
  1. *Candidate vs verified availability* — `reference_tools.check_availability`
     now surfaces the live path's `preliminary`/`still_needs` fields as a
     `status: "candidate"|"verified"` on the result and on every minted
     `slot_ref`. `preview_booking` refuses a `"candidate"` slot_ref
     (`SLOT_NOT_YET_VERIFIED`) — the customer must supply the missing piece
     and get a re-checked `"verified"` slot_ref first. Confirmed live
     against real data: a DAYCARE check-in-only query correctly comes back
     `"candidate"` with real (not fabricated) times.
  2. *Greeting required elements, not fixed wording* — `AgentContext` gained
     `is_first_message`; `SYSTEM_PROMPT_V2` gained a GREETING section
     requiring name/company plus a relevant `recent_booking`/
     `upcoming_booking` mention on that one first turn, never later —
     wording is still free.
  3. *Membership via preview_ref→confirm_ref* — replaced the bare
     `register_loyalty_member(confirmed: bool)` passthrough (the model
     stating its own "confirmed" fact, the exact problem Phase 4 fixed for
     bookings) with `preview_membership`/`confirm_membership`, mirroring
     `preview_booking`/`confirm_booking` exactly: same mutation-authority
     gate (`_authorize_confirm_membership`, reusing `confirmation_intent`),
     same cross-turn `pending_member_ref` → `AgentContext.pending_membership`
     memory. An already-enrolled customer's real account still comes back
     immediately, no preview/confirm needed. Verified live end-to-end
     (real gpt-4o-mini, two turns, real Supabase write, confirmed in the DB
     and deleted again).
  Two more items from the same review were live-path-only (the user
  confirmed both explicitly rather than defaulting): DAYCARE now tracks
  *which* dimension the customer actually committed to
  (`ConversationState.daycare_duration_source`: `"explicit_duration"` keeps
  the length fixed and shifts pickup; `"explicit_range"` keeps the stated
  pickup time fixed and recomputes the length) so editing just the
  check-in time on a later turn no longer silently keeps a now-wrong
  duration; and loyalty flow inverted from "only when the customer asks"
  to a deterministic post-booking-success check — `_needs_tool_repair`'s
  soft prompt instruction wasn't reliable on its own (confirmed live twice:
  the model treated a successful `create_booking` as fully "done" and
  never made the bonus call even when explicitly told to), so it's now a
  forced, side-effect-free `check_coupon_eligibility` call
  (`_create_booking_just_succeeded` + `force_exact_tool_once`, same
  mechanism the availability-repair path already used) — the model still
  freely decides whether the real result is worth mentioning. 262 pytest
  passing.
- **Phase 8 (2026-08-10)** — `app/agent/policy.py`, the deterministic
  "Check" layer, organized into the 8 categories (identity scope, evidence
  scope, mutation authority, freshness, transaction integrity,
  idempotency, tenant isolation, human boundary). *(done —
  tests/test_refactor_phase8_policy.py.)* Confirmed this is NOT a port of
  `app/agent/guardrails.py`'s 1533 lines — 6 of the 8 categories were
  already fully satisfied structurally by Phases 3/4/6/7 (evidence refs,
  EvidenceStore's TTL/uniqueness/per-session scoping, the
  candidate/verified slot distinction) and needed no new code, only the
  audit trail written into `policy.py` itself so each category has one
  place to be checked from. Two real gaps closed:
  1. `_authorize_confirm_booking`/`_authorize_confirm_membership` were
     near-duplicate copies of the same gate living directly in
     `app/agent/runtime.py` — consolidated into one `authorize_confirm()`.
  2. A genuine, previously-unclosed mutation-authority gap: `cancel_booking`/
     `reschedule_booking`'s two-step `confirm_pet_name` protection never
     required the confirming call to be a LATER turn than its own preview —
     unlike `confirm_booking`/`confirm_membership`, which both already got
     that fix. The model already knows a pet's real name from context, so
     nothing structurally stopped it from self-supplying `confirm_pet_name`
     on the very same turn as the preview. Closed with
     `authorize_mutation_target_confirmation()`, reusing
     `pending_mutation_target`'s existing turn-tracking (now also stamped
     with `minted_turn`). 268 pytest passing, `/chat` still fully
     untouched — `app/agent/guardrails.py` itself is unmodified and keeps
     guarding the OLD raw-value tools `app/orchestrator.py` still calls.
- **Phase 8.1 (2026-08-10, second review)** — an independent review of the
  actual code (not just this plan) rated the prototype ~80-85% fit and
  found real gaps. Verified each claim against the code before acting
  (one, #5 below, turned out to already be fixed/incorrect as stated):
  1. **DAYCARE hourly pricing — confirmed real, fixed.** `_mint_option`
     dropped `pricing_unit`/duration-bound fields entirely, and
     `preview_booking` used `option["price"]` directly as the final price.
     For "Hourly Care" (RM20/hour, `pricing_unit: "hour"`) this meant a
     verified 5-hour visit would have been booked at RM20, not RM100 — a
     real, previously-unnoticed financial-correctness bug (GROOMING is
     always flat and BOARDING's `create_booking` already multiplies
     `price_per_night x nights` server-side, so neither was affected).
     Fixed: `_mint_option` now preserves `pricing_unit`; `check_availability`
     stores the slot's own real verified `duration_minutes` (reusing
     `service_duration_minutes` from the underlying availability result,
     or computing it directly for the CHECK_OUT-selection branch); `preview_booking`
     computes `rate x (duration_minutes / 60)` server-side for
     `pricing_unit == "hour"` only, and refuses to price one at all
     (`SLOT_MISSING_VERIFIED_DURATION_FOR_HOURLY_PRICING`) if a verified
     slot somehow lacks a duration rather than silently falling back to
     the bare rate.
  2. **DAYCARE duration authority never migrated to the new architecture —
     confirmed real, fixed.** The live orchestrator's
     `daycare_duration_source`/`daycare_range_end_time` capture (this
     session's earlier fix for "12pm to 5pm then just 1pm") lived only as
     a `PawfectOrchestrator` staticmethod — `app/agent/runtime.py`'s
     `check_availability` still let the model set `duration_minutes`
     itself. Extracted the capture into a shared
     `app.agent.tool_loop.capture_explicit_daycare_duration()` (the
     orchestrator's staticmethod now just delegates to it); `handle_turn()`
     calls it every turn and `check_availability`'s bound tool overrides
     the model's `duration_minutes` with `state.daycare_duration_minutes`
     whenever it's set for a DAYCARE option. Also tracks `state.service_type`
     from `get_service_options` calls (needed for the capture's own
     DAYCARE-context check) and resets the three duration fields after a
     successful `confirm_booking`, mirroring the live path.
  3. **A third, independently-discovered bug found while verifying #2 live:**
     `app.db.time_normalization.extract_time_range` split on only the
     FIRST "to"/"until"/etc. match in the whole message — "I want **to**
     book daycare ... 12pm **to** 5pm" landed on the *first* "to" (an
     unrelated "want to book"), so the real time range later in the same
     sentence was silently never reached, returning `None`. This affected
     BOTH architectures (`capture_explicit_daycare_duration`/the live
     orchestrator's version of it, and any other caller passing a full raw
     message) — confirmed live: today's earlier `daycare_duration_source`
     fix could silently fail to fire whenever the customer's message had
     any earlier "to" before the actual time range. Fixed by trying every
     separator occurrence in the message instead of only the first.
  4. **`slot_ref` doesn't carry `preferred_staff` — confirmed real, fixed.**
     `check_availability` now stores whatever `preferred_staff` value the
     slot was actually checked against directly on the minted slot;
     `preview_booking` no longer accepts `preferred_staff` as its own
     parameter at all — it reads it from the slot, so the two can no
     longer drift apart structurally (not just by convention). Lower
     severity than the pricing bug — the underlying `create_booking`
     already validates `preferred_staff` against real availability at
     write time, so the old gap could only ever produce a confusing
     "verified then rejected" mismatch, never a silently wrong booking —
     but removing the parameter is strictly better than relying on that
     recheck to catch it.
  5. **BOARDING check-in/check-out staff — claim checked, found incorrect
     as stated.** The review described a Python/SQL semantics mismatch
     (Python allegedly allowing different staff per event while the SQL
     RPC requires one). Read both: the SQL (`booking_conflict_prevention_migration.sql`)
     does require one `staff_id` free for both events, and
     `check_available_slots`'s BOARDING checkout branch already computes
     `checkin_staff_ids & checkout_staff_ids` (intersection) — deliberately
     aligned earlier this session, with an explicit comment explaining why.
     No live mismatch exists. Whether the business actually wants
     different staff per event (the review's proposed
     `booking_staff_assignment` table) is a separate, legitimate schema
     decision, not a current bug — not started, would need explicit
     confirmation given it touches the SQL RPC.
  6. **V2 post-booking loyalty still prompt-only — confirmed real, fixed.**
     Ported the live orchestrator's `_create_booking_just_succeeded()` +
     forced tool-call pattern: `handle_turn()`'s loop sets
     `force_loyalty_check` right after a real `confirm_booking` success
     (guarded on `get_loyalty` not already having been called this turn),
     and the NEXT iteration binds ONLY `get_loyalty` with
     `tool_choice="required"` before returning to normal tool choice — the
     model still freely decides whether the result is worth mentioning.
     Caught and fixed a real `NameError` in this same change (referenced
     an out-of-scope `customer_id` instead of `customer.get("customer_id")`)
     via the live E2E test, which also surfaced a PRE-EXISTING gap in that
     test's own cleanup (only deleted the payment row, never the
     `grooming_booking` row itself — every successful run left a real
     stray booking that then poisoned the next run's identical slot);
     fixed both.
  7. **New customer/pet creation — confirmed real gap, fixed.** Added
     `reference_tools.create_customer`/`create_pet`, thin wrappers over the
     existing, unmodified `app.tools.customer_tools` business logic (same
     reuse principle as every other reference tool). `phone_number` is
     server-injected from the session (never model-stated); `company_id`/
     `customer_id` likewise. No ref is minted for either — unlike
     option/slot/preview, there's nothing for the model to reference
     later; the new customer/pet becomes a real, resolvable `pet_ref` via
     the normal identity flow on a LATER turn, not the same one (documented
     in both tools' docstrings and `SYSTEM_PROMPT_V2`'s new NEW CUSTOMER /
     NEW PET section). `create_pet` guards `customer_id is None`
     (`CUSTOMER_NOT_YET_REGISTERED`) both in `app.agent.runtime._bind_tools`
     (before the call) and in `reference_tools.create_pet` itself (defense
     in depth — the underlying `@tool`'s pydantic schema would otherwise
     raise on `None` instead of returning a normal envelope). Verified live
     end-to-end (real gpt-4o-mini, 3 turns: register → register pet → get
     grooming options; real Supabase rows created and deleted). One
     reproduced-once, not-code-level flakiness noted: the model
     occasionally passed the pet's NAME as `pet_ref` instead of its real
     numeric ref on the very next turn after creating it (1 of 2 runs) —
     `AgentContext.customer.pets` had the correct ref both times
     (confirmed directly), so this is model inconsistency shortly after a
     registration, not a wiring bug; not chased further per this session's
     established flakiness-documentation precedent.
  8. **`BookingDraft`/`_synthetic_ref` — confirmed as described, fixed.**
     Removed `booking_draft` from `build_agent_context()`'s returned dict
     — it was a projection off the OLD orchestrator's raw state fields
     (`verified_service_options`/`verified_availability_slots`) producing
     non-resolvable display-only refs, injected into `AgentContext` every
     turn but never referenced by `SYSTEM_PROMPT_V2` at all. Left
     `derive_booking_draft()`/`BookingDraft` themselves in place (still
     exercised directly by `tests/test_refactor_phase1_context.py`) rather
     than deleting outright — only the model-facing injection is gone.
     `active_options`/`active_slots`/`pending_action`/`pending_mutation`/
     `pending_membership` are the real, resolvable cross-turn memory the
     model actually uses.
  9. **`active_slots` (fragmented slot-selection state) — confirmed real
     gap, fixed.** Added `ConversationState.active_slots`, mirroring
     `active_options`: replaced wholesale on every successful
     `check_availability` call, surfaced as `AgentContext.active_slots`,
     reset on a `confirm_booking` success (a finished booking's shown
     slots shouldn't bleed into the next request). Verified live
     end-to-end: a period query ("...on 2026-10-05 morning?") shows 5 real
     slots, a later turn ("Book the first one.") resolves the ordinal pick
     straight to `preview_booking` using the stored `slot_ref` — no second
     `check_availability` call needed. One flaky failure mode found and
     documented, not chased further: when the FIRST turn already reaches a
     single-slot preview (an exact time given, e.g. "around 10am"), a
     short second turn is genuinely ambiguous between "confirm the
     pending preview" (`AgentContext.pending_action`, checked first per
     the prompt) and "pick from the list" — the model sometimes takes the
     former, correctly gets rejected by `authorize_confirm` for not being
     a clean affirmative, and never reaches a fresh preview. Not a code
     defect (confirm-first is the deliberately correct priority); the test
     was redesigned to show a genuine multi-slot list first, avoiding the
     ambiguity, and passes reliably (3/3, 4/5 in later runs) with that
     design — the one remaining failure in a full-suite run reproduced
     this exact ambiguity, live-LLM flakiness in the literal test scenario
     chosen, not the `active_slots` mechanism itself (confirmed correct by
     direct inspection every run).
  278 pytest passing (was 268), `/chat` verified live end-to-end after
  restarting the service (items 1-3 land in shared code both
  architectures use). Items 4/6/7/8/9 all done as follow-ups in this same
  round, per explicit priority choices ("continue" after item 7).
- **Phase 9 (2026-08-10) — controlled cutover, not a hard one.** Explicit
  instruction: `/chat` should actually run v2, but v1
  (`PawfectOrchestrator`) stays fully intact as a one-click runtime
  fallback — not deleted, not disabled.
  - `main.py` now branches `_chat_locked` on a module-level `_AGENT_ENGINE`
    ("v1" | "v2"): v2 calls `app.agent.runtime.handle_turn()` (new
    `_evidence_registry = EvidenceStoreRegistry()` singleton + a
    lazily-built `ChatOpenAI` matching `PawfectOrchestrator`'s own
    model/temperature/timeout config exactly — architecture should be the
    only difference, not model behavior); v1 is the unchanged existing
    call. Both branches converge on the same `response_text`/`trace`
    locals so every downstream step (WhatsApp delivery, document
    extraction, staff escalation on delivery failure, `agent_state`
    summary, `_memory.save`) runs identically regardless of engine — v1
    and v2 share the same `ConversationMemory`/`ConversationState`
    instance per phone/company (same dict-backed store), so state carries
    over cleanly even if the engine is flipped mid-conversation.
  - Deploy-time default: `PAWFECT_AGENT_ENGINE` env var, unset → "v1" —
    an existing deployment's behavior does not change unless explicitly
    opted in.
  - Runtime one-click fallback: new debug-mode-gated `POST
    /debug/set-agent-engine {"engine": "v1"|"v2"}` and `GET
    /debug/agent-engine` — flips immediately, no restart, exactly the
    "one call back to the fully-proven old path" the instruction asked
    for.
  - `_documents_from_trace` updated to also recognize `confirm_booking`
    (v2's write tool) alongside v1's `create_booking` — otherwise v2
    bookings would never surface their real confirmation-PDF document in
    the response.
  - Verified live end-to-end against the actual running service (not just
    pytest): health check + `/debug/agent-engine` after restart (defaults
    "v1"); a v1 request (unchanged reply); flipped to v2 via the debug
    endpoint; a v2 upcoming-booking question answered correctly from
    context with zero tool calls; a full v2 GROOMING booking through real
    `/chat` (get_service_options → check_availability → preview_booking →
    confirm_booking → forced get_loyalty), producing a real booking row, a
    real signed confirmation-PDF URL correctly picked up in the response's
    `documents` field, and a real WhatsApp-simulated delivery payload — the
    real booking was deleted afterward, same as every other live-write
    test this session; a v2 DAYCARE hourly-pricing booking through real
    `/chat` also correctly quoted RM100 for a verified 5-hour visit (not
    reaching confirm, no cleanup needed). 279 pytest still passing.
  - Current live state on the running dev service (port 4000):
    `_AGENT_ENGINE` is "v2" (flipped via the debug endpoint during this
    verification and left there). This is the in-memory runtime state of
    THIS process only — it resets to the "v1" deploy-time default on the
    next restart/redeploy unless `PAWFECT_AGENT_ENGINE=v2` is also set in
    the actual deployment environment (Cloud Run, per README/
    DEVELOPER_GUIDE.md — not something this session touched; that's a
    separate, explicit deployment action for whoever manages that
    environment to take when actually ready to persist the switch there).
  - Explicitly NOT done (out of scope for "controlled", not "hard",
    cutover): v1's files/guardrails.py/system_prompt.py are NOT retired or
    deleted — both architectures remain fully present and importable.
- **Phase 9.1 (2026-08-10) — read-side/write-side consistency, the actual
  root cause of "verified slot then confirm overlaps".** A third review
  correctly diagnosed that clearing Supabase test data alone wouldn't fix
  the underlying "verified available, then the real write rejects it as a
  conflict" failure — the read-side (Python) and write-side (SQL) conflict
  checks disagreed. Verified every claim against the actual SQL
  (`backend/sql/booking_conflict_prevention_migration.sql`) and Python
  before changing anything — all 4 confirmed real:
  1. **GROOMING duration hardcoded to 90, ignoring the row's own
     `duration_minutes` column** — SQL's `pet_has_conflicting_booking`/
     `staff_has_conflicting_booking` use `coalesce(b.duration_minutes,
     90)`, the real stored value when one exists (schema allows 1-1440).
     Fixed: `_booking_interval` now reads it. Not observed to have fired
     with today's actual seed data (every real row happens to be 90), but
     confirmed latent and real given the schema explicitly supports other
     values.
  2. **BOARDING pet-occupancy semantics, confirmed genuinely live and
     two-directional** — traced the exact SQL calls: `create_booking_atomic`
     passes the FULL `[check_in, check_out)` span to
     `pet_has_conflicting_booking` unconditionally in both directions (a
     new boarding request's own span, and every existing booking's span
     including other boarding stays), while Python's
     `_pet_free_for_interval`/`_pet_boarding_conflict_row` (deliberately
     changed earlier this session — real, reasoned business logic:
     "a boarded pet can still be walked to a grooming appointment
     elsewhere during its stay") only treated the exact check-in/check-out
     INSTANTS as occupied. The SQL function was never updated to match, so
     that earlier fix created exactly this live "verified then overlap"
     failure mode in both directions. Per explicit instruction, matched
     Python to SQL's stricter full-stay-occupied semantics (the safe fix
     tonight — no SQL/schema change) rather than updating the SQL function
     — if grooming/daycare-during-boarding is genuinely wanted long-term,
     that needs a coordinated SQL + Python change, not a Python-only one.
     Verified live end-to-end: a real boarded pet (Buddy, mid-stay) now
     correctly shows zero available grooming slots instead of a false
     "available" that would fail at confirm.
  3. **V2 confirm_booking failure didn't invalidate stale evidence** —
     confirmed by reading the code (only the `result.get("ok")` branch
     cleared `pending_preview_ref`/`active_slots`). Fixed with a new
     standalone `_confirm_booking_hit_a_genuine_write_rejection()` helper
     (directly unit-tested) — critically, it only fires when the rejection
     came from the REAL write (`rejection is None`, i.e.
     `policy.authorize_confirm` didn't block the call first); a same-turn/
     non-affirmative block from the authority gate must NOT clear a still-
     perfectly-valid pending preview just because the customer asked a
     side question instead of confirming.
  4. **V2 never migrated `apply_datetime_resolution`'s merge fix** —
     confirmed: `handle_turn()` unconditionally overwrote
     `state.current_datetime_resolution` with each turn's raw
     `resolve_datetime` result, exactly the bug `apply_datetime_resolution`
     already fixed for the live orchestrator earlier this session (a bare
     "1pm" on a later turn silently dropped an already-established date).
     This very likely explains an earlier-session mystery: gpt-4o-mini
     inventing plausible-looking-but-fake `datetime_ref` strings and
     probing many individual half-hour times one at a time, previously
     shrugged off as unexplained flakiness — a `datetime_ref` with no date
     is unusable, so the model improvising around it fits exactly. Fixed
     by wiring `handle_turn()` to the existing shared function instead of
     reimplementing state management. Verified live: a date from turn 1
     survives a bare-time turn 2 correctly.
  5. Also implemented as explicitly requested, lower severity: elongated
     affirmative texting habits ("yessss", "confirmmmm", "okkkk") now
     match `confirmation_intent`/`AFFIRMATIVE_RE` (previously only the
     exact word); and `policy.authorize_confirm` gained an opt-in
     `current_ref` parameter (wired from `state.pending_preview_ref`/
     `pending_member_ref`) rejecting a confirm attempt against a ref that
     is no longer the actually-current pending one (`PREVIEW_NOT_CURRENT`)
     — closes the "customer changed their mind, old preview still
     technically valid but must never be confirmable" gap.
  Items 1-2 live in `app/db/relational_actions.py`, shared unmodified
  business logic — both v1 and v2 benefit automatically, no engine-specific
  duplication. Items 3-5 are v2 (`app/agent/runtime.py`/`policy.py`)
  specific; v1 already has its own, separately-matured equivalents (not
  re-audited here — this round's explicit framing was "v1 is correct, v2
  hasn't migrated it yet," and that held for every item checked). 287
  pytest passing (was 279). Live-verified: `/chat` on v2 now correctly
  shows zero available slots for a real mid-boarding-stay grooming request
  instead of a false "available" that would only fail later at confirm.
- **Phase 9.2 (2026-08-10) — GROOMING's default duration changed 90 -> 60
  minutes.** Explicit instruction. Updated every place that assumes a
  duration when none is otherwise given: `app/db/availability_service.py`'s
  `DEFAULT_SERVICE_DURATION_MINUTES["GROOMING"]` (the actual value
  `create_booking` writes to a new row when nothing overrides it — the
  real lever), `_booking_interval`'s and `_serialize_grooming_booking`'s
  own fallbacks in `relational_actions.py`, the live `system_prompt.py`'s
  matching mention, and — found while checking for consistency —
  `backend/src/lib/bookingService.js`'s identical `|| 90` fallback (the
  Node dashboard's own conflict check; left unfixed it would have
  reintroduced exactly this session's whole "read-side/write-side
  disagree" failure class from the other direction, a staff-created
  dashboard booking silently defaulting to 90 while the AI backend
  defaults to 60). Deliberately NOT changed: the SQL schema/RPC's own
  `duration_minutes int not null default 90` / `coalesce(b.duration_minutes,
  90)` (backend/sql/booking_conflict_prevention_migration.sql) — Python
  and Node's create/update paths always write an explicit duration_minutes
  value, so this SQL fallback is only reachable for a row that somehow has
  none at all (not a live path today); changing a schema/RPC default is a
  separate decision from an application default, not bundled in silently.
  Updated the one Node test that pinned the old `|| 90` literal via regex
  (`backend/test/uatHardening.test.js`) and the one Python test that pinned
  the old fallback value. 287 Python + 46 Node tests passing. Verified live
  end-to-end: a real `/chat` v2 grooming availability check with no
  explicit duration now mints a slot with `duration_minutes: 60`.
- **Phase 9.3 (2026-08-10) — verified ≠ selected: the Agent was silently
  choosing service options and time slots the customer never picked or
  delegated.** Reproduced live: "book grooming for Milo this Friday" (a
  pet and a date, nothing else) reached `check_availability`/
  `preview_booking` having silently picked a specific package (once
  "Luxury Bath - DAVIS", RM160, out of 9 real options) — a real Agent
  autonomy-boundary gap, not a data or availability bug. Two fixes,
  layered:
  1. **`SYSTEM_PROMPT_V2` gained a CUSTOMER CHOICE section** — never
     silently pick an option/slot/add-on/staff without the customer
     naming it or delegating via an explicit criterion ("cheapest",
     "same as last time", "you choose", etc.); may always recommend, but
     as a recommendation, not an already-made decision.
  2. **Prompt alone was confirmed NOT reliable enough** (same pattern as
     item #6 above) — reproduced live 2 of 4 runs after the prompt fix.
     Added deterministic code-level guards, mirroring the already-proven
     slot-verification pattern:
     - `check_availability` now marks each returned slot
       `selection_required: true` unless it matches an exact clock time
       the customer actually specified that call (never a period, never
       nothing at all) — `preview_booking` refuses a `selection_required`
       slot_ref (`CUSTOMER_SLOT_SELECTION_REQUIRED`). This one **is**
       fully deterministic (an exact-time match is checkable without
       NLU).
     - For the harder option-selection case (no clean equivalent of "an
       exact time" to check against), `handle_turn()` now blocks a
       same-turn `get_service_options` (>1 real option) ->
       `check_availability` chain (`CUSTOMER_OPTION_SELECTION_REQUIRED`)
       unless the CUSTOMER's own message shares a real, distinguishing
       word with the chosen option's name (a stoplist excludes generic
       words like "bath"/"fur"/"grooming" that appear in most option
       names, so "I want a bath" doesn't spuriously match any of them) or
       uses a delegation phrase. Explicitly a heuristic, not full NLU —
       documented as such; deliberately errs toward letting a plausible
       match through (a false negative costs one extra clarifying turn,
       never a wrong booking) rather than blocking a genuinely one-shot,
       fully-specified request ("Milo standard grooming this Friday at
       1pm", which must NOT be interrupted — confirmed still works via
       the existing `test_prompt_v2_reasons_cheapest_one_without_a_
       hardcoded_rule` smoke test and a new "same-turn full detail" case
       in the heuristic's own unit tests).
  Both existing tests whose OWN setup queried availability without an
  exact time (and so now correctly hit the new guard) were fixed to pass
  one, matching what a real customer selecting from a list actually does,
  not a workaround. 293 pytest passing (1 pre-existing, documented flaky
  live test unaffected). Verified live end-to-end, 3/3 clean runs: "book
  grooming this Friday" now always lists the real options and asks, never
  reaches availability/preview with a silently-chosen package.
- **Phase 9.4 (2026-08-10) — loyalty/member context hydrated once per
  session, not only after an explicit loyalty question or forced post-
  booking.** Real gap confirmed: a pre-booking recommendation/enquiry turn
  had zero member context even for a real existing member — only a
  loyalty keyword in the customer's own message, or a booking having just
  succeeded, ever triggered a lookup. `app.context.runtime_context.
  resolve_identity()` (shared by v1 and v2 — both benefit automatically,
  same file as the recent_booking/upcoming_booking prefetch already there)
  now also fetches `get_loyalty_account` in the same parallel batch,
  `getattr`-guarded exactly like recent/upcoming for test-double
  compatibility, gated on a new `state.loyalty_context_status` so it's
  fetched once per session (not every turn) — mirrors the ALREADY-real
  `app.db.relational_repository`/`supabase_relational_repository`
  `get_loyalty_account(company_id, customer_id)` method (no new repository
  method needed, it already existed). Surfaced as `customer["loyalty_
  account"]`/`customer["loyalty_context_status"]` in both
  `customer_context_for_state()` (v1's RUNTIME_CONTEXT) and
  `build_agent_context()` (v2's AgentContext); both `system_prompt.py` and
  `SYSTEM_PROMPT_V2` gained a short note that this is real, already-
  hydrated evidence, not something requiring its own tool call — and,
  deliberately, that it does NOT change WHEN loyalty gets proactively
  *mentioned* (still the post-booking-success/explicit-ask rule from
  earlier this session) — this is about the DATA being available for
  reasoning, not a new proactivity rule. Invalidated (forces a fresh
  hydration next turn) on a real `register_loyalty_member`/
  `confirm_membership` success in both v1 and v2, so a customer who joins
  mid-conversation doesn't keep showing as a cached non-member.
  `redeem_reward` deliberately does NOT invalidate it — real
  `points_balance` only changes after staff approval, not immediately on
  a submitted request. 296 pytest passing (was 293 — 2 new hydration
  tests plus the phase1 AgentContext key-set update). Verified against
  real Supabase data directly (not just a mock repo): a real member
  (Jason Lim, 606 points, Silver) now hydrates with zero tool calls.

- **Phase 9.5 (2026-08-10) — period-of-day ("morning"/"afternoon"/"evening")
  audited before touching anything, per explicit instruction ("先audit看对不
  对，才决定要不要改"). Confirmed correct, not touched: period parsing
  (`_PERIOD_TOKEN_ALIASES` in `app/db/time_normalization.py` — English +
  Chinese 早上/上午/清晨/中午/正午/下午/午后/晚上/傍晚/夜晚 + Malay
  pagi/tengah hari/petang/malam), backend-hardcoded period windows
  (`PERIOD_WINDOWS` in `app/tools/time_periods.py`: morning 00:00-11:59,
  afternoon 12:00-16:59, evening 17:00-20:59, night 21:00-23:59, noon
  11:30-13:30), and — the part actually worth checking, since parsing a
  word and filtering by it are two different things — `filter_slots_by_
  period()` genuinely called from both `check_availability` and
  `check_availability_range` (`app/tools/availability_tools.py`), so
  period really does constrain which slots come back, not just get
  echoed. "next Friday" -> "afternoon" and "next Friday afternoon" ->
  "2pm" (exact time supersedes period) both already merged correctly
  across turns. Also confirmed `slot_matches_preference()` in
  `app/db/time_normalization.py` is dead code — functionally equivalent
  to `filter_slots_by_period`, defined, never called anywhere; left alone
  since it's simply unused rather than wrongly used, not this fix's scope.

  Real gap confirmed by direct execution against `apply_datetime_
  resolution()` (`app/agent/tool_loop.py`, the shared v1/v2 per-turn
  merge function): only the reverse order was broken. A customer stating
  "afternoon" before any date, then a later turn naming the date without
  repeating "afternoon", silently dropped the period — same class of bug
  the date/date_range merge fix (Phase 6/earlier) already covers for
  date, just never extended to period. Fixed with a symmetric block: period
  carries forward from the previous turn unless the new turn states its
  own period OR an exact time (an exact time is more specific and must
  replace, not coexist with, a stale period — matches the already-correct
  "afternoon" -> "2pm" behavior). 297 pytest passing (was 296 — 1 new
  regression test, `test_period_preference_merges_forward_the_same_way_
  date_does`, mirroring the existing date-merge regression test's pattern
  and all 3 of the audited cases). Verified directly against the live
  merge function before AND after the fix, not just mentally traced.
  (Relocated to `tests/test_turn_state.py` in Phase 10 below, along with
  `apply_datetime_resolution` itself.)

- **Phase 9.6 (2026-08-10) — live /chat (v1) showed a real customer
  "book grooming...next monday afternoon" and got back the FULL day's
  slots (09:30-17:30), not just the afternoon window — a distinct bug
  from 9.5's cross-turn merge, found from a live screenshot, same turn.**
  Root cause: `filter_slots_by_period()` (already confirmed real and
  correctly wired in the 9.5 audit) only ever filters by whatever `time`
  string `check_availability`'s caller actually passes it — and nothing
  deterministically carried the same turn's own `resolve_datetime` period
  output ("afternoon") into the following `check_availability` call's
  `time` argument. It was 100% on the model to copy that value across two
  separate tool calls in the same turn, with nothing to catch a drop —
  exactly the class of gap `_inject_cached_daycare_duration`
  (`app/orchestrator.py`) already exists to solve for duration, just
  never extended to time/period. v2 (`app/agent/runtime.py`'s
  `check_availability` bound tool) turned out to already be immune —
  it resolves `time_preference` from the `datetime_ref` evidence itself
  (`time_preference or dt.get("time") or dt.get("period") or ""`) rather
  than trusting a second, separate model-supplied argument — so this was
  v1-only.

  Fixed with `PawfectOrchestrator._inject_resolved_time_preference()`,
  wired in next to `_inject_cached_daycare_duration` in the same tool-args
  pipeline: fills a genuinely blank `time` from `state.current_datetime_
  resolution` (exact time first, else period), never overrides a value
  the model actually supplied, and deliberately does NOT apply to a
  CHECK_OUT selection_target (a pickup-time preference is independently
  stated and must not silently inherit the check-in period). 298 pytest
  passing (was 297 — 1 new test, `test_dropped_period_is_injected_into_
  check_availabilitys_time_argument`, covering the drop case, the
  model-supplied-value-wins case, exact-time-over-period priority, the
  CHECK_OUT exclusion, and the no-resolution-at-all no-op). Verified live
  against the real reported scenario after restarting uvicorn: same
  customer, same phrasing, `check_availability`'s `data.available_slots`
  came back correctly bounded to the afternoon window (12:00-16:30) with
  `requested_time: "afternoon"` in the result; confirmed no stray booking
  was written to Supabase during the verification turns before clearing
  the test session. (Note: this fix, `PawfectOrchestrator._inject_
  resolved_time_preference`, and its test were V1-only and no longer
  exist — see Phase 10. V2 was already confirmed immune, since it
  resolves `time_preference` straight from `datetime_ref` evidence rather
  than trusting a second model-supplied argument.)

## Phase 10 (2026-08-10) — V1 decommission: `/chat` now runs only V2

Explicit instruction: not a brute-force delete — extract the handful of
genuinely architecture-independent helpers V2 still imported out of V1's
files first, repoint V2's own imports to the new homes, THEN delete V1.
Kept untouched throughout, per explicit instruction, because these are
business/domain tools V2 already reused unmodified, not V1 architecture:
`app/tools/customer_tools.py`, `availability_tools.py`, `booking_tools.py`,
`app/db/relational_actions.py`, RAG, Supabase logic, payment, loyalty,
documents.

**Extracted before deleting anything:**
- `app/agent/confirmation.py` (new) — `confirmation_intent`/
  `AFFIRMATIVE_RE`/`NEGATIVE_RE`, out of `app/agent/guardrails.py`. The
  only piece of guardrails.py (~1500 lines, otherwise pure V1 raw-value-
  tool enforcement) `app/agent/policy.py`'s `authorize_confirm` actually
  needed.
- `app/agent/turn_state.py` (new) — `compact_evidence_result`,
  `capture_explicit_daycare_duration`, `apply_datetime_resolution`, out of
  `app/agent/tool_loop.py` (~900 lines, otherwise pure V1 tool-loop
  plumbing: batch planning/execution, per-call dispatch, authoritative-
  scope replay). The only three functions `app/agent/runtime.py`'s
  `handle_turn()` actually needed — confirmed via grep before extracting,
  not assumed.
- `app/agent/policy.py`/`app/agent/runtime.py` repointed to the two new
  modules; docstrings updated to stop describing guardrails.py/
  tool_loop.py as "the OLD raw-value tools' file, unmodified" since that
  file no longer exists.

**Deleted:** `app/orchestrator.py`, `app/prompts/system_prompt.py`,
`app/agent/guardrails.py`, `app/agent/tool_loop.py`,
`app/agent/response_grounding.py` (only ever imported by tool_loop.py —
confirmed via grep, no other consumer), `app/tools/state_tools.py`.

**`main.py`:** `/chat` now calls `app.agent.runtime.handle_turn()`
unconditionally — removed `_AGENT_ENGINE`, `PAWFECT_AGENT_ENGINE`,
`_get_orchestrator()`/`_orchestrator`, `POST /debug/set-agent-engine`,
`GET /debug/agent-engine`, `SetAgentEngineRequest`, and the `agent_engine`
response field. `_get_v2_model` renamed `_get_agent_model` (only one
engine exists now). `_documents_from_trace`'s tool set trimmed to
`confirm_booking`/`reschedule_booking`/`send_booking_confirmation` (v1's
`create_booking` can never appear in a trace again). Explicit trade-off
acknowledged: no more one-click runtime fallback to V1 — a bad deploy now
rolls back via Cloud Run/Render's previous-revision mechanism instead,
which is the right call given V1's own booking behavior (Phase 9.1's
grooming/boarding read-write mismatches) was never actually fit to be the
safety net.

**Test suite triage** (298 -> 152 tests; the drop is almost entirely V1-
only coverage that has no V2 equivalent to preserve, not lost coverage of
anything V2 still does):
- Deleted wholesale (entirely V1-coupled, no salvageable generic tests):
  `tests/test_agentic_orchestration.py`, `tests/test_context_logic.py`,
  `tests/test_conversation_transcripts.py`, `tests/test_constrained_ai_
  backend.py`.
- Salvaged real, architecture-independent coverage out of the above
  before deleting, into three new files:
  - `tests/test_confirmation.py` — the 3 `confirmation_intent` tests.
  - `tests/test_turn_state.py` — the daycare-duration-capture tests and
    both `apply_datetime_resolution` merge tests (date/time survival,
    and Phase 9.5's period-merge fix), repointed at
    `app.agent.turn_state`.
  - `tests/test_backend_surface.py` — customer_tools/relational_actions/
    main.py-document-endpoint/ConversationMemory/RAG tests that never
    touched PawfectOrchestrator at all.
- `tests/test_uat_regressions.py` (54 tests, only 6 lines touched
  PawfectOrchestrator) — the 4 tests calling `PawfectOrchestrator.
  _resolve_identity` (a thin adapter over `app.context.runtime_context.
  resolve_identity`, confirmed by reading the deleted method before it
  was gone) retargeted to call `resolve_identity` directly with the SAME
  calling convention `handle_turn()` uses — better fidelity than before,
  since it now tests exactly what V2 actually calls. One test's
  `state.pet_id`/`state.pet_name` assertions removed (not retargeted):
  they tested `PawfectOrchestrator._cache_single_pet`'s state-side-channel
  convenience, which V2 has no equivalent of by design — V2 resolves pet
  identity fresh each call via `AgentContext.customer.pets[].ref`
  (`app.agent.runtime._resolve_pet_ref`), confirmed by reading
  `app/context/builder.py`, not assumed. One test (`ground_unavailable_
  profile_claims`, response_grounding.py's only other real consumer)
  deleted outright — V2 relies on structural reference-based constraints
  instead of a post-hoc grounding correction on the final text, so there
  is no equivalent to preserve.
- Full suite after triage: 151/152 passing, only the same pre-existing,
  already-documented flaky live-LLM test
  (`test_active_slots_lets_a_later_turn_pick_a_time_by_ordinal_without_
  recalling_check_availability`) — confirmed unchanged (same failure mode
  before touching anything tonight), not a regression from this phase.

**Live re-verification** (explicit instruction: clear session, retest
Grooming -> Daycare -> Boarding -> confirm -> payment -> loyalty fresh,
not continuing any V1 session) surfaced two real, previously-undetected V2
bugs — neither introduced by tonight's changes, both pre-existing gaps
this was the first live test thorough enough to hit:

1. **Stale `active_options` survived a finished booking.** A DAYCARE
   confirm that hit a genuine write-time rejection (real vaccination-
   eligibility check, `app.validation.validator.check_vaccination_
   eligibility` — unrelated business logic, working as designed) already
   cleared `pending_preview_ref`/`active_slots` but not `active_options`.
   Confirmed live: the customer then asked for BOARDING and the model
   reused the stale DAYCARE option_ref instead of calling
   `get_service_options` again, silently previewing "Daycare Above 3
   Hours" for what the customer explicitly called boarding. Fixed by
   clearing `active_options` in both the confirm-success AND confirm-
   rejection branches of `app/agent/runtime.py`'s `handle_turn()` — same
   staleness class `daycare_duration_minutes`/`active_slots` were already
   reset for. Regression test:
   `test_genuine_confirm_rejection_clears_the_shown_catalogue_not_just_
   the_preview` (forces a deterministic write rejection via monkeypatch
   rather than depending on real, mutable vaccination data staying
   expired).

2. **BOARDING's duration validation rejected a value it never used, and
   step-limit exhaustion discarded a real successful result.** Two
   compounding bugs found from one live BOARDING confirm attempt:
   - `app/db/relational_actions.py::check_available_slots` validated ANY
     `duration_minutes` against a 1-1440 (24h) range regardless of
     service_type, even though the very next line only ever *applies* it
     for GROOMING/DAYCARE — BOARDING's real length comes from check-in/
     check-out dates, so the parameter was never going to be used either
     way. The model reasonably computed a 3-night stay's length in
     minutes (4320) and passed it, got hard-rejected by a cap that was
     never actually about BOARDING, and burned two more retries before
     landing on 1440. Fixed by scoping the validation to
     `service_type in {"GROOMING", "DAYCARE"}`, matching the existing
     "does anything with it" branch exactly.
   - Separately, `app/agent/runtime.py`'s `handle_turn()` loop's `for...
     else` fallback (`MAX_AGENT_STEPS` exhausted without the model ever
     returning a tool-call-free response) unconditionally replaced
     whatever happened with a hardcoded "I'm having trouble... staff will
     help" message — confirmed live: the LAST tool call that same
     iteration had already been a genuine, successful `preview_booking`
     (a real preview_ref, real room, real price), but the customer was
     shown the generic escalation instead, discarding it. Fixed by giving
     the model one more no-tools-bound call against the same `messages`
     (which already holds the full real tool-call/result history) to
     summarize what was actually accomplished, before falling back to the
     generic message only if that recovery call also produces nothing
     usable.
   - Not unit-tested (the full non-preliminary BOARDING path needs
     substantial monkeypatch scaffolding disproportionate to a one-line
     conditional's scope); verified live instead, twice — once
     reproducing the original failure end to end, once confirming the fix
     (Mars Room, $78, correct Aug 21 -> Aug 24 dates, no escalation).

**Known, NOT fixed tonight — flagged, not silently left implicit:** the
same live BOARDING confirm sequence, on a later turn, hit a deeper,
un-investigated tangle: `confirm_booking` reported a rejection mentioning
a missing check-out time, and the model's recovery attempt incorrectly
invoked `reschedule_booking`/`cancel_booking` in the same turn — state
was left with `active_scenario` flipped to `CANCEL_BOOKING` and a
`booking_`-prefixed ref passed where `confirm_booking` expected a
`preview_` ref. Confirmed via Supabase inspection that this never reached
a real write (no `boarding_booking` row for those dates exists) and does
not appear to be caused by tonight's changes — but it was NOT
root-caused or fixed; it needs dedicated investigation before BOARDING
confirm can be trusted end to end. All test bookings created during
tonight's live verification (grooming 764/768, daycare 374, and their
payment rows) were deleted from Supabase afterward.

## Risk strategy (per explicit instruction)

Build in the new branch/new modules in parallel; the currently-running
service (`deploy` branch, port 4000, real Supabase data, live
adversarial testing happening today) is not touched until a phase's own
validation passes. Nothing here deletes or disables existing code before
its replacement is proven.
