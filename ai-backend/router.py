"""
Router decision layer for Pawfect backend.

Uses intent_json from Query JSON Prompt output to decide the next backend action.
Does not call RAG or database — routing only.
"""

import os

from dotenv import load_dotenv

from intent_schema import (
    BOOKING_CHANGE_SCENARIOS,
    DATABASE_SCENARIOS,
    POLICY_RAG_SCENARIOS,
)

# Policy and service-info questions answered from the knowledge base via RAG
KNOWLEDGE_RAG_SCENARIOS = POLICY_RAG_SCENARIOS

# Read-only and write customer-specific DB scenarios (never RAG-only)
DATABASE_ROUTE_SCENARIOS = {
    "VIEW_BOOKING_STATUS",
    "CHECK_AVAILABILITY",
    "CHECK_LOYALTY_POINTS",
    "CHECK_MEMBERSHIP_STATUS",
    "LOYALTY_ACCOUNT_INQUIRY",
    "CUSTOMER_GREETING",
    "REPEAT_LAST_BOOKING",
    "CONFIRM_BOOKING",
    "CANCEL_BOOKING",
    "RESCHEDULE_BOOKING",
    "REDEEM_REWARD",
    "CREATE_CUSTOMER",
    "CREATE_PET",
}

READ_ONLY_DATABASE_SCENARIOS = DATABASE_ROUTE_SCENARIOS

RAG_AND_DATABASE_SCENARIOS = BOOKING_CHANGE_SCENARIOS

DEFAULT_CONFIDENCE_THRESHOLD = 0.80


def get_confidence_threshold() -> float:
    """Read router confidence threshold from environment."""
    load_dotenv(override=not os.getenv("_EVAL_OVERRIDE_ACTIVE"))
    raw_value = os.getenv("ROUTER_CONFIDENCE_THRESHOLD", str(DEFAULT_CONFIDENCE_THRESHOLD)).strip()
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_CONFIDENCE_THRESHOLD


def route_intent(intent_json: dict) -> dict:
    """
    Decide the next backend route based on detected intent.

    Routing rules:
    - UNKNOWN intent routes to HUMAN_HANDOFF without RAG or database
    - Low confidence routes to HUMAN_HANDOFF
    - POLICY_INTENT scenarios route to CALL_KNOWLEDGE_RAG unless database_action_needed
    """
    confidence = intent_json.get("confidence", 0.0)
    main_intent = intent_json.get("main_intent", "UNKNOWN")
    scenario_intent = intent_json.get("scenario_intent", "UNKNOWN")
    missing_information = intent_json.get("missing_information", [])
    retrieval_needed = bool(intent_json.get("retrieval_needed", False))
    database_action_needed = bool(intent_json.get("database_action_needed", False))
    confidence_threshold = get_confidence_threshold()

    if main_intent == "UNKNOWN" or scenario_intent == "UNKNOWN":
        return {
            "route": "HUMAN_HANDOFF",
            "reason": "Out-of-scope, unclear, or unsupported request",
        }

    if confidence < confidence_threshold:
        return {
            "route": "HUMAN_HANDOFF",
            "reason": "Low confidence",
        }

    if scenario_intent == "BOOKING_CONFIRMATION_ORPHAN":
        return {
            "route": "ASK_MISSING_INFO",
            "reason": "Confirmation received without an active booking draft",
        }

    if scenario_intent == "CONFIRM_BOOKING":
        return {
            "route": "CALL_DATABASE",
            "reason": "Confirmed booking draft; create booking in Supabase",
        }

    if scenario_intent in {"CANCEL_BOOKING", "RESCHEDULE_BOOKING", "REDEEM_REWARD", "CREATE_CUSTOMER", "CREATE_PET"}:
        if missing_information:
            return {
                "route": "ASK_MISSING_INFO",
                "reason": f"{scenario_intent} requires validated fields before database write",
            }
        return {
            "route": "CALL_DATABASE",
            "reason": "Customer action requires validated Supabase write",
        }

    if scenario_intent == "COLLECT_CUSTOMER_NAME":
        return {
            "route": "ASK_MISSING_INFO",
            "reason": "Customer name collection or acknowledgment",
        }

    if scenario_intent == "MAKE_BOOKING":
        if intent_json.get("booking_supporting_info_needed") and intent_json.get("retrieval_needed"):
            return {
                "route": "CALL_RAG_THEN_ASK_MISSING_INFO",
                "reason": "Booking with supporting package/price/service information question",
            }
        if missing_information:
            return {
                "route": "ASK_MISSING_INFO",
                "reason": "Make booking requires collecting booking details before checking availability",
            }
        return {
            "route": "CALL_DATABASE",
            "reason": "Read-only phase: check slot availability before booking creation",
        }

    if missing_information:
        if (
            scenario_intent == "SERVICE_INFORMATION"
            and intent_json.get("standalone_service_info")
            and retrieval_needed
            and intent_json.get("price_enquiry_incomplete")
        ):
            return {
                "route": "CALL_RAG_THEN_ASK_MISSING_INFO",
                "reason": "Price enquiry: retrieve pricing info then ask remaining fields",
            }
        return {
            "route": "ASK_MISSING_INFO",
            "reason": "Missing required customer information",
        }

    if scenario_intent in READ_ONLY_DATABASE_SCENARIOS:
        return {
            "route": "CALL_DATABASE",
            "reason": "Customer-specific live data requires database access",
        }

    if retrieval_needed and database_action_needed:
        return {
            "route": "CALL_RAG_AND_DATABASE",
            "reason": "Query requires both knowledge retrieval and database action",
        }

    if scenario_intent in KNOWLEDGE_RAG_SCENARIOS and not database_action_needed:
        return {
            "route": "CALL_KNOWLEDGE_RAG",
            "reason": "Policy or service information requires RAG retrieval",
        }

    if retrieval_needed and not database_action_needed:
        return {
            "route": "CALL_KNOWLEDGE_RAG",
            "reason": "Knowledge-based question requires RAG retrieval",
        }

    if scenario_intent in RAG_AND_DATABASE_SCENARIOS:
        return {
            "route": "CALL_RAG_AND_DATABASE",
            "reason": "Booking change requires policy check and database action",
        }

    if database_action_needed or scenario_intent in DATABASE_SCENARIOS:
        return {
            "route": "CALL_DATABASE",
            "reason": "Live business data or customer action requires database access",
        }

    return {
        "route": "HUMAN_HANDOFF",
        "reason": "Unknown or unsupported scenario",
    }
