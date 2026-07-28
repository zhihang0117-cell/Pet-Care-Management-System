# Pawfect Internal Agent Test Output

**Date:** 2026-07-28  
**Mode:** Offline internal integration test  
**Providers:** Mock LLM + Mock RAG + Mock relational database  
**Endpoint:** `POST /chat`

## Summary

| Case | Route | Evidence valid | Recommendation | Next action | Result |
|---|---|---:|---|---|---|
| Existing-customer greeting | `CALL_DATABASE` | Yes | Repeat verified latest booking | `REQUEST_CONFIRMATION` | Pass |
| Boarding policy | `CALL_KNOWLEDGE_RAG` | Yes | None required | `ANSWER_DIRECTLY` | Pass |
| Grooming availability | `CALL_DATABASE` | Yes | `09:00:00` | `REQUEST_CONFIRMATION` | Pass |
| Incomplete grooming price | `CALL_RAG_THEN_ASK_MISSING_INFO` | Yes | Correctly suppressed | `ASK_CRITICAL_CLARIFICATION` | Pass |
| Medical diagnosis request | `HUMAN_HANDOFF` | N/A | None | Staff handoff | Pass |

## Case 1 — Greeting with a verified proactive recommendation

**Customer**

> Hi

**Customer-facing reply**

> Hi Alicia Lee, welcome back to Pawfect! 😊 Your last booking was grooming for Milo; you can repeat it for the quickest option, or choose something different. What would you like to do today?

**Decision output**

```json
{
  "evidence_validation": {
    "valid": true,
    "evidence_ids": ["db:check_customer_by_phone:success"],
    "issues": []
  },
  "grounded_decision": {
    "goal_status": "awaiting_confirmation",
    "valid_candidate_ids": ["repeat_booking:101"],
    "recommendation": {
      "trigger": true,
      "selected_candidate_id": "repeat_booking:101",
      "decisive_factors": [
        "most recent verified booking was grooming for Milo"
      ]
    },
    "next_action": "REQUEST_CONFIRMATION"
  }
}
```

The greeting recommendation comes from the customer's verified latest booking. It is not a generic upsell.

## Case 2 — RAG answer without unnecessary recommendation

**Customer**

> What is your boarding policy?

**Customer-facing reply**

> Hi, welcome to Pawfect! 😊
>
> Boarding rules and requirements may vary by pet type and stay duration. Customers should follow the relevant boarding guidelines before the appointment.

**Decision output**

```json
{
  "evidence_need": {
    "rag": true,
    "relational": false
  },
  "evidence_validation": {
    "valid": true,
    "evidence_ids": ["mock_boarding_policy_001"],
    "issues": []
  },
  "grounded_decision": {
    "goal_status": "resolved",
    "recommendation": {
      "trigger": false
    },
    "next_action": "ANSWER_DIRECTLY"
  }
}
```

## Case 3 — Verified database candidates and proactive recommendation

**Customer**

> Is grooming available on 30 July 2026?

**Customer-facing reply**

> Hi, welcome to Pawfect! 😊
>
> For 30 Jul 2026, the available times for grooming are 9:00 AM, 10:00 AM, 2:00 PM, 3:00 PM, or 4:30 PM.
>
> 9:00 AM is the earliest option. Reply with that time, or choose another slot above, and I'll prepare the booking.

**Decision output**

```json
{
  "evidence_validation": {
    "valid": true,
    "evidence_ids": ["db:check_available_slots:success"],
    "issues": []
  },
  "grounded_decision": {
    "goal_status": "awaiting_confirmation",
    "valid_candidate_ids": [
      "09:00:00",
      "10:00:00",
      "14:00:00",
      "15:00:00",
      "16:30:00"
    ],
    "recommendation": {
      "trigger": true,
      "selected_candidate_id": "09:00:00",
      "decisive_factors": ["earliest verified available slot"]
    },
    "next_action": "REQUEST_CONFIRMATION"
  }
}
```

The recommended slot is present in `valid_candidate_ids`; it was not generated from model memory.

## Case 4 — Ask only decision-changing information

**Customer**

> How much is grooming?

**Customer-facing reply**

> Hi, welcome to Pawfect! 😊
>
> Service prices depend on the selected service type, pet type, and pet size.
>
> What is your pet's size or height, and which grooming package are you considering?

**Decision output**

```json
{
  "critical_unknowns": [
    "pet_size_or_height",
    "service_package"
  ],
  "evidence_validation": {
    "valid": true,
    "evidence_ids": ["mock_service_information_001"],
    "issues": []
  },
  "grounded_decision": {
    "goal_status": "awaiting_user",
    "recommendation": {
      "trigger": false
    },
    "next_action": "ASK_CRITICAL_CLARIFICATION"
  }
}
```

The agent does not guess an exact price or force a booking recommendation.

## Case 5 — Safe escalation

**Customer**

> Can you diagnose why my dog is sick?

**Customer-facing reply**

> I'll pass this conversation to our team so someone can assist you.

**Result**

```json
{
  "route": "HUMAN_HANDOFF",
  "rag_used": false,
  "database_used": false,
  "recommendation_generated": false
}
```

## Regression validation

```text
186 passed, 3 warnings
```

The warnings are existing Supabase client deprecations and are unrelated to the decision-support changes.

## Issue found and fixed during this test

The first internal run showed that `GroundedDecision` correctly recommended the verified `09:00` slot, but the legacy rule-based response could still say it could not confirm availability when `availability_result` had not been pre-attached.

The response layer now reconstructs the structured availability result from the verified database payload before generating the customer reply. A second issue—missing clarification wording in the rule-based RAG fallback—was also corrected so the visible response follows `ASK_CRITICAL_CLARIFICATION`.
