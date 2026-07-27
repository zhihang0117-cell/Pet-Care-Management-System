# Pawfect AI Backend

WhatsApp-style pet care chat backend: intent detection → routing → RAG (policy/service info) → relational DB (live customer data) → final response.

**Milestone (read-only integration):** RAG Supabase + Supabase relational DB + greeting/identity + in-memory session + multi-turn read-only continuation. Validated **10/10** via `run_readonly_integration_test.py`.

---

## Architecture

```
POST /chat
  → detect_intent (Query JSON Prompt)
  → apply_message_pattern_overrides
  → apply_request_context_to_intent (phone from metadata)
  → apply_read_only_entity_rules + session continuation
  → route_intent
  → retrieve_rag_context (if RAG route)
  → execute_database_action (if DB route)
  → generate_final_response
```

| Layer | Provider env | Purpose |
|-------|--------------|---------|
| Intent | `LLM_PROVIDER` | Structured JSON routing |
| RAG | `RAG_PROVIDER=mock\|supabase` | Policy / service information |
| Relational DB | `DATABASE_PROVIDER=mock\|supabase` | Customer-specific live data |
| Final reply | `FINAL_RESPONSE_PROVIDER` | WhatsApp customer message |

---

## RAG — Supabase vector DB

When `RAG_PROVIDER=supabase`:

- Embeddings via `EMBEDDING_MODEL` (e.g. `BAAI/bge-large-en-v1.5`)
- Vector search through Supabase RPC (`RAG_RPC`, e.g. `match_chunks_bge_large`)
- Chunks table: `RAG_TABLE` (e.g. `chunks_bge_large`)
- Scoped by `RAG_COMPANY_ID`

Used for: cancellation/grooming/boarding/daycare policies, service information, loyalty **rules** (not personal account data).

Set `RAG_PROVIDER=mock` for offline pipeline testing without Supabase.

---

## Relational DB — Supabase read-only actions

When `DATABASE_PROVIDER=supabase`:

- Shared client: `supabase_client.py`
- Actions: `relational_actions.py` via `database_service.py`
- Company scope: `RELATIONAL_COMPANY_ID` (defaults to `RAG_COMPANY_ID`)

### Implemented read-only actions

| Action | Scenario | Description |
|--------|----------|-------------|
| `check_customer_by_phone` | `CUSTOMER_GREETING` | Identity lookup by WhatsApp sender |
| `check_loyalty_points` | `CHECK_LOYALTY_POINTS`, `CHECK_MEMBERSHIP_STATUS` | Points, tier, redemptions |
| `check_booking_status` | `VIEW_BOOKING_STATUS` | Latest / next booking for customer |
| `check_available_slots` | `CHECK_AVAILABILITY` | Staff + leave + booking heuristic |

### Not implemented (TODO)

- `create_booking` / `MAKE_BOOKING` write path
- `cancel_booking`, `reschedule_booking`
- `redeem_reward` / redemption
- Payment update
- Message logging
- `check_last_booking` (explicit last-booking helper)

Write scenarios return an error or redirect to read-only availability check where applicable.

---

## `DATABASE_PROVIDER=mock|supabase`

| Value | Behavior |
|-------|----------|
| `mock` | `mock_database.py` — local fallback, no Supabase calls |
| `supabase` | Live Supabase relational tables |

Copy `.env.example` → `.env` and set credentials locally. **Never commit `.env`.**

---

## Identity — WhatsApp `phone_number`

- **Production:** `phone_number` comes from WhatsApp webhook sender metadata (not parsed from message text).
- **Local testing:** pass `phone_number` in `POST /chat` JSON body.
- **`customer_id` is internal only** — resolved from phone via `check_customer_by_phone`; not requested from the customer.
- Do not send `customer_id` in the request body for normal customer flows.

Example:

```json
{"message": "points?", "phone_number": "+60 12-345 6701"}
```

Known test customer: `+60 12-345 6701` → Alicia Lee, 407 pts, Silver tier.

---

## Greeting flow

Pure greetings (`Hi`, `Hello`, `Hey`, …) → `CUSTOMER_GREETING` → `check_customer_by_phone`.

| Result | Customer reply (fixed template) |
|--------|----------------------------------|
| Found | `Hi {name}, welcome back to Pawfect! 😊 How can I help you today?` |
| Not found | Asks for **name** to create profile (not phone/customer_id) |
| No phone (local fallback) | Asks for phone to check existing account |

Greetings do **not** trigger RAG.

---

## Session store (in-memory)

`session_store.py` — keyed by phone digits. **Local dev only**; production should use Redis / DB / webhook provider cache.

Stored fields:

- `phone_number`, `customer_id`, `customer_name`, `existing_customer`
- `last_intent`, `last_scenario_intent`, `last_service_type`
- `pending_action`, `missing_fields`, `collected_entities`

Returned in `/chat` response as `session_context` (debug).

---

## Multi-turn read-only continuation

`session_continuation.py` — when the bot asks for missing info, the next short reply continues the pending action:

| Pending action | Example |
|----------------|---------|
| `check_available_slots` | Turn 1: slots tomorrow → ask time; Turn 2: `afternoon` → DB slots |
| `check_booking_status` | After greeting, booking status without re-asking identity |
| `check_loyalty_points` | Identity fallback when phone present |

**Topic change reset:** e.g. pending availability + `"Actually what is your cancellation policy?"` clears pending and routes to RAG.

---

## Running the server

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env: OPENAI_API_KEY, SUPABASE_*, DATABASE_PROVIDER=supabase, RAG_PROVIDER=supabase

uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Local tester UI: `frontend-test/index.html` (Phone + Message only).

---

## Tests

### Read-only milestone suite (recommended)

```powershell
$env:DATABASE_PROVIDER="supabase"
.\.venv\Scripts\python.exe run_readonly_integration_test.py
```

**Latest result:** **10/10 PASS** (2026-07-20)

| ID | Test |
|----|------|
| T01 | Greeting existing customer |
| T02 | Greeting new customer |
| T03 | Loyalty after greeting |
| T04 | Booking status after greeting |
| T05 | Availability multi-turn |
| T06 | Topic change reset |
| T07 | RAG policy |
| T08 | Service information |
| T09 | Short WhatsApp DB queries |
| T10 | Out-of-scope handoff |

Artifact: `outputs/readonly_integration_test_20260720_223538.json`

### Other suites

- `run_manual_integration_test.py` — 40-row plan (`data/manual_full_integration_test_plan.csv`)
- `run_unseen_integration_test.py` — 50-row unseen validation (`data/unseen_integration_validation_50.csv`)

---

## Current limitations

1. Session state is **in-memory** — lost on server restart.
2. Availability uses staff/leave/booking heuristic — no dedicated capacity table.
3. `preferred_date` natural language (`tomorrow`) may not parse to ISO in DB layer (defaults to next day).
4. Final Response LLM may ask extra fields during `ASK_MISSING_INFO`; session tracks schema `missing_fields` only.
5. Write actions deferred (see TODO list above).
6. Large `outputs/` eval artifacts are local-only; commit milestone JSON if desired, not entire folder.

---

## Key files

| File | Role |
|------|------|
| `main.py` | `/chat` pipeline |
| `query_json_prompt.py` | Intent classification prompt |
| `intent_schema.py` | Schema + pattern overrides |
| `router.py` | Route decisions |
| `rag_service.py` | RAG retrieval |
| `relational_actions.py` | Supabase read-only actions |
| `database_service.py` | mock/supabase dispatch |
| `customer_context.py` | Phone identity context |
| `session_store.py` | In-memory session |
| `session_continuation.py` | Multi-turn continuation |
| `response_generator.py` | Final WhatsApp reply |
| `run_readonly_integration_test.py` | Milestone test suite |

---

## Environment variables

See `.env.example`. Secrets (`OPENAI_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY`) belong in `.env` only.
