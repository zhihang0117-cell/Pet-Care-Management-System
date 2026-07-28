"""Development-only intent catalogue for the chat process inspector."""

from __future__ import annotations

from intent_schema import (
    ALLOWED_MAIN_INTENTS,
    ALLOWED_SCENARIO_INTENTS,
    ALLOWED_SERVICE_TYPES,
    BOOKING_CHANGE_SCENARIOS,
    DATABASE_SCENARIOS,
    POLICY_RAG_SCENARIOS,
    RETRIEVAL_SOURCE_BY_SCENARIO,
)


_EXTRA_RUNTIME_SCENARIOS = {
    "BOOKING_CONFIRMATION_ORPHAN",
    "GET_BOOKING_SERVICE_OPTIONS",
}

_TOOL_BY_SCENARIO = {
    "CUSTOMER_GREETING": ["get_customer_profile", "get_pet_profiles", "get_latest_booking"],
    "MAKE_BOOKING": ["get_customer_profile", "get_pet_profiles"],
    "CHECK_AVAILABILITY": ["get_customer_profile", "get_pet_profiles", "check_available_slots"],
    "GET_BOOKING_SERVICE_OPTIONS": ["get_booking_service_options"],
    "REPEAT_LAST_BOOKING": ["get_latest_booking"],
    "VIEW_BOOKING_STATUS": ["get_booking_status"],
    "CONFIRM_BOOKING": ["create_booking"],
    "CANCEL_BOOKING": ["get_booking_status", "cancel_booking"],
    "RESCHEDULE_BOOKING": ["get_booking_status", "check_available_slots", "reschedule_booking"],
    "CREATE_CUSTOMER": ["create_customer"],
    "CREATE_PET": ["create_pet"],
    "CHECK_LOYALTY_POINTS": ["get_loyalty_points"],
    "CHECK_MEMBERSHIP_STATUS": ["get_membership_status"],
    "LOYALTY_ACCOUNT_INQUIRY": ["get_loyalty_account"],
    "CHECK_COUPON_ELIGIBILITY": ["check_coupon_eligibility"],
    "REDEEM_REWARD": ["get_loyalty_points", "redeem_reward"],
    "VIEW_PAYMENT_HISTORY": ["get_payment_history"],
    "VIEW_REDEMPTION_HISTORY": ["get_redemption_history"],
    "VIEW_MESSAGE_HISTORY": ["get_message_history"],
    "VIEW_COMPANY_INFORMATION": ["get_company_information"],
    "VIEW_STAFF_DIRECTORY": ["get_staff_directory"],
    "VIEW_ACCOUNT_STATUS": ["get_account_status"],
}

_WRITE_SCENARIOS = {
    "CONFIRM_BOOKING",
    "CANCEL_BOOKING",
    "RESCHEDULE_BOOKING",
    "CREATE_CUSTOMER",
    "CREATE_PET",
    "REDEEM_REWARD",
}

_DESCRIPTIONS = {
    "CUSTOMER_GREETING": "Open the conversation with customer, pet, and latest-booking context.",
    "MAKE_BOOKING": "Collect only decision-critical booking details.",
    "CHECK_AVAILABILITY": "Query live slots once category, pet, and date are known.",
    "GET_BOOKING_SERVICE_OPTIONS": "Compare verified selectable services from RAG and catalogue data.",
    "REPEAT_LAST_BOOKING": "Offer the latest verified booking as a fast path.",
    "CONFIRM_BOOKING": "Create a booking only from an explicitly confirmed draft.",
    "SERVICE_INFORMATION": "Answer tenant-specific service/package questions from service-info chunks.",
    "UNKNOWN": "Escalate unclear or unsupported requests safely.",
}


def _default_route(scenario: str) -> str:
    if scenario in BOOKING_CHANGE_SCENARIOS or scenario == "GET_BOOKING_SERVICE_OPTIONS":
        return "CALL_RAG_AND_DATABASE"
    if scenario in POLICY_RAG_SCENARIOS:
        return "CALL_KNOWLEDGE_RAG"
    if scenario in DATABASE_SCENARIOS:
        return "CALL_DATABASE"
    if scenario in {"MAKE_BOOKING", "COLLECT_CUSTOMER_NAME"}:
        return "DYNAMIC"
    if scenario in {"BOOKING_CONFIRMATION_ORPHAN"}:
        return "ASK_MISSING_INFO"
    return "HUMAN_HANDOFF"


def _flow_for(scenario: str, route: str) -> list[str]:
    steps = ["Understand turn with conversation memory"]
    if scenario not in {"UNKNOWN", "COLLECT_CUSTOMER_NAME"}:
        steps.append("Load customer and pet profile when identity is available")
    if scenario == "MAKE_BOOKING":
        steps.extend(
            [
                "Confirm service category before specific service",
                "Collect pet and preferred date from message/profile",
                "Preview verified service options proactively",
                "Query live category-level availability",
                "Collect service option before final confirmation",
            ]
        )
    elif route == "CALL_RAG_AND_DATABASE":
        steps.extend(["Retrieve tenant evidence", "Query relational evidence"])
    elif route == "CALL_KNOWLEDGE_RAG":
        steps.append("Retrieve tenant evidence")
    elif route == "CALL_DATABASE":
        steps.append("Query relational evidence")
    elif route == "ASK_MISSING_INFO":
        steps.append("Ask one decision-critical clarification")
    elif route == "HUMAN_HANDOFF":
        steps.append("Escalate safely")
    steps.extend(["Validate evidence", "Resolve next-best action", "Generate grounded reply"])
    return steps


def build_intent_debug_catalog() -> dict:
    scenarios = []
    for scenario in sorted(set(ALLOWED_SCENARIO_INTENTS) | _EXTRA_RUNTIME_SCENARIOS):
        route = _default_route(scenario)
        sources = list(RETRIEVAL_SOURCE_BY_SCENARIO.get(scenario, []))
        if scenario == "GET_BOOKING_SERVICE_OPTIONS":
            sources = ["service_information"]
        scenarios.append(
            {
                "scenario_intent": scenario,
                "description": _DESCRIPTIONS.get(
                    scenario,
                    scenario.replace("_", " ").title(),
                ),
                "default_route": route,
                "rag": route in {"CALL_KNOWLEDGE_RAG", "CALL_RAG_AND_DATABASE"} or scenario == "MAKE_BOOKING",
                "rag_sources": sources,
                "relational": route in {"CALL_DATABASE", "CALL_RAG_AND_DATABASE"} or scenario == "MAKE_BOOKING",
                "tools": list(_TOOL_BY_SCENARIO.get(scenario, [])),
                "write_action": scenario in _WRITE_SCENARIOS,
                "process_flow": _flow_for(scenario, route),
            }
        )
    return {
        "main_intents": sorted(ALLOWED_MAIN_INTENTS),
        "service_types": sorted(ALLOWED_SERVICE_TYPES),
        "scenarios": scenarios,
        "shared_pre_turn_tools": [
            "get_customer_profile",
            "get_pet_profiles",
            "get_latest_booking (first relevant turn)",
        ],
        "notes": [
            "RAG is tenant-scoped and filtered by company/service metadata.",
            "Relational tools provide live profile, booking, catalogue, loyalty, and availability evidence.",
            "Write tools require validated fields and explicit customer confirmation.",
        ],
    }
