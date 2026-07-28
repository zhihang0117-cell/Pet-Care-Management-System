# Pawfect LLM Backend Reasoning Code Audit

**Date:** 2026-07-28  
**Scope:** `ai-backend/*.py`  
**Goal:** Identify code that still performs semantic decisions through keyword/regex matching instead of a structured reasoning model.

## Executive finding

The backend is currently a hybrid:

```text
keyword/mock intent detection
+ optional LLM intent classification
+ regex overrides
+ deterministic flow rewrites
+ deterministic DecisionPlan/GroundedDecision
+ optional LLM response generation
```

It is not yet a reasoning-model-first agent.

The most important issue is that `SINGLE_LLM_PER_TURN` defaults to `true`.
For apparently high-confidence messages, `llm_service.py` runs
`mock_llm_intent_detection()` and `apply_message_pattern_overrides()` instead
of the real reasoning model. After any real LLM result, `main.py` applies the
same pattern overrides again. Keyword logic can therefore bypass or overwrite
model understanding.

## Severity classification

### P0 — Must replace with structured model reasoning

| File | Current behavior | Why it does not comply |
|---|---|---|
| `llm_service.py` | Uses keyword-based `mock_llm_intent_detection()` as a production “single LLM” optimization | Clear messages may never reach the reasoning model |
| `mock_llm.py` | Entire intent detector is ordered keyword matching | This is a rules engine, not an LLM or semantic reasoner |
| `intent_schema.py` | `apply_message_pattern_overrides()` rewrites greeting, account, booking, loyalty, availability, cancellation, and rescheduling intents | Regex can override a correct LLM decision and fails on unseen phrasing |
| `booking_service_info.py` | Determines price enquiry, service enquiry, package choice, booking signals, mixed intents, add-ons, and grooming subflows with many regex signals | Semantic conversation decisions are encoded as phrases |
| `session_continuation.py` | Determines topic shifts, interruptions, continuations, dates, services, and pending-flow restoration using regex hints | Multi-turn interpretation should use dialogue state reasoning |
| `booking_flow.py` | Detects generic booking requests, repeat/new choices, names, time-only replies, pet confirmation, and confirmation meaning using patterns | User dialogue meaning is being inferred by local text patterns |
| `retrieval_request.py` | Classifies information type and price/package/add-on retrieval intent through words and regex | Retrieval planning is keyword-driven rather than goal/evidence-driven |
| `package_selection.py` | Maps natural package/add-on choices using fixed phrases | Unseen aliases and conversational references are not understood |

### P1 — Replace semantic portions, retain deterministic validation

| File | Replace | Keep |
|---|---|---|
| `pet_extraction.py` | Inferring whether text refers to a pet, a new pet, or which pet the user means | Type validation, known-pet membership checks, size normalization |
| `rag_service.py` | Detecting service type, broad question, and price question from keywords | Tenant filters, embedding retrieval, chunk sanitation, ranking thresholds |
| `response_generator.py` | Detecting medical/price/service meaning from response-time regex | Evidence-only generation, false-confirmation prevention, formatting guard |
| `booking_draft.py` | Interpreting whether free text means confirmation or slot acceptance | Draft completeness checks and backend confirmation requirement |
| `relational_actions.py` | Extracting redemption points from raw message regex | Tenant-scoped SQL, capacity checks, transaction validation |
| `decision_support.py` | `build_decision_plan()` currently maps route/scenario to a plan deterministically; candidate selection uses fixed earliest/nearest rules only | Evidence validation, hard constraints, candidate ID enforcement, tenant rejection |

### P2 — Correctly deterministic; do not move into the LLM

| File/component | Reason to retain |
|---|---|
| `router.py` | Safe mapping from a validated structured plan to allow-listed tools |
| `relational_actions.py` dispatch | The model must not choose arbitrary tables or SQL |
| `availability_service.py` | Time normalization, capacity, duration, and valid-slot calculations must be deterministic |
| `date_normalization.py` / `time_normalization.py` | Parsing and canonical formatting are deterministic utilities |
| `response_data_sanitizer.py` | Security and privacy enforcement must not depend on model judgment |
| `customer_context.py` | Authenticated company/customer scope belongs to the backend |
| `evidence validation` | Cross-tenant evidence and false-success checks must remain hard rules |
| booking/payment writes | Must require validated arguments, confirmation, and backend success |
| medical and explicit-human-request guard | May remain as a conservative pre-model safety interrupt, with the model as a secondary classifier |

## Detailed findings

### 1. Production may bypass the real LLM

`llm_service._single_llm_intent_result()` calls:

```python
mock_llm_intent_detection(user_message)
apply_message_pattern_overrides(...)
```

when `SINGLE_LLM_PER_TURN=true`, which is the default. It returns a
`deterministic` provider result when confidence is at least `0.90`.

This optimization should be removed or limited to non-semantic commands such
as an exact protocol button payload. A natural-language customer message should
be interpreted by the reasoning model.

### 2. Real LLM output is not authoritative

After `detect_intent()`, `main._process_chat()` calls:

```python
intent_json = apply_message_pattern_overrides(request.message, intent_json)
```

This means a correct model decision can be overwritten by regex. A future
reasoning architecture should normalize and validate the model output, not
reclassify its semantics with keyword rules.

### 3. Current model call is only an intent classifier

`real_llm.py` asks for a Query JSON intent record. It does not receive:

- full conversation history;
- complete session decision state;
- verified customer/pet context;
- tool catalogue with evidence contracts;
- candidate requirements;
- previous rejected options;
- the current decision to resolve.

It therefore cannot perform the target agent loop. It only classifies the
latest message.

### 4. Current DecisionPlan is not model reasoning

`decision_support.build_decision_plan()` is auditable and useful, but it is
constructed by mapping `scenario_intent` and `route` values. It does not reason
over the conversation.

The future model should output a strict pre-tool `DecisionPlan` containing:

```json
{
  "user_goal": "...",
  "current_decision": "...",
  "known_facts": {},
  "supported_inferences": [],
  "critical_unknowns": [],
  "evidence_need": {},
  "candidate_requirements": {},
  "next_action": "..."
}
```

The backend should validate this plan and convert it to allow-listed tool calls.

### 5. Multi-turn understanding is mostly state-machine plus regex

`session_continuation.py`, `booking_flow.py`, and `booking_service_info.py`
contain useful state preservation, but semantic decisions such as:

- whether the user changed topic;
- whether a short reply answers the previous question;
- whether “same as before” refers to a service, pet, time, or package;
- whether the user is comparing, asking, selecting, or confirming;
- whether a price question interrupts or replaces a booking;

are still determined mainly by patterns.

State storage should remain deterministic, while a dialogue-state reasoning
model should produce a structured state patch with evidence for each change.

### 6. Retrieval planning is keyword-oriented

`retrieval_request.py`, `rag_service.py`, and parts of
`booking_service_info.py` build retrieval scopes using words such as price,
cost, fee, package, grooming, trimming, and add-on.

The reasoning model should instead output:

```json
{
  "source": "policy_rag",
  "information_need": "published grooming price for a medium dog",
  "service_type": "GROOMING",
  "document_types": ["service_information"],
  "must_contain": ["package", "size band", "price"],
  "reason": "exact price depends on pet size and package"
}
```

The backend should still enforce tenant filters and allowed document types.

### 7. Entity extraction is fragmented

Pet, package, add-on, date, time, service, name, and redemption points are
extracted in different modules. This causes one user message to be interpreted
multiple times by independent regex systems.

The reasoning model should return one structured `DialogueStatePatch`.
Deterministic parsers may then verify dates, times, IDs, enumerations, and
catalogue membership.

## Target architecture

```text
User message
+ conversation summary/history
+ current session state
+ authenticated customer/pet context
+ allow-listed tool catalogue
        ↓
Reasoning model: DecisionPlan JSON
        ↓
Schema and policy validator
        ↓
Allow-listed RAG / relational tools
        ↓
Evidence and candidate validator
        ↓
Reasoning model: GroundedDecision JSON
        ↓
Backend action/confirmation guard
        ↓
Response model or deterministic response renderer
```

## What the reasoning model should own

- goal and multi-intent understanding;
- dialogue-state interpretation;
- reference resolution;
- deciding whether evidence is needed;
- selecting RAG, relational data, both, or neither;
- identifying decision-changing unknowns;
- producing retrieval questions;
- comparing validated candidates against user preferences;
- choosing the next conversational action;
- preparing a grounded response plan.

## What the backend must continue to own

- authenticated tenant and customer scope;
- tool allow-list;
- schema validation;
- SQL and vector filters;
- hard constraints;
- candidate existence and availability;
- transaction confirmation;
- action execution;
- false-success prevention;
- security, privacy, logging, and rate limits.

## Migration priority

1. Disable keyword-based production intent shortcut in `llm_service.py`.
2. Stop applying semantic pattern overrides after real LLM output.
3. Replace Query JSON intent classification with a conversation-aware
   `DecisionPlan` model call.
4. Add a model-generated `DialogueStatePatch`; keep backend field validators.
5. Replace retrieval keyword classification with model-generated evidence needs.
6. Add post-tool `GroundedDecision` model reasoning over validated evidence.
7. Remove duplicated semantic regex from booking, service info, session
   continuation, package selection, and response generation.
8. Retain deterministic safety, tenant, hard-filter, and transaction layers.

## Conclusion

The codebase already has the correct safety skeleton—separate tools, tenant
filtering, evidence validation, session state, candidate IDs, and action
confirmation. The part that does not meet the requested design is the semantic
control plane. It remains distributed across keyword detectors and regex
overrides instead of being handled by one structured, conversation-aware
reasoning model.
