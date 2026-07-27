"""Focused tests for booking-status and loyalty-balance intent routing."""

from __future__ import annotations

import pytest

from intent_schema import apply_message_pattern_overrides, normalize_intent_result, normalize_scenario_intent
from router import route_intent


def _misclassified_service_information(message: str) -> dict:
    """Simulate live LLM mislabelling customer-specific queries as SERVICE_INFORMATION."""
    return normalize_intent_result(
        {
            "main_intent": "POLICY_INTENT",
            "scenario_intent": "SERVICE_INFORMATION",
            "service_type": "GENERAL",
            "pet_type": "UNKNOWN",
            "pet_size": "UNKNOWN",
            "pet_height": "",
            "customer_status": "UNKNOWN",
            "entities": {},
            "missing_information": [],
            "retrieval_needed": True,
            "retrieval_source": ["service_information"],
            "database_action_needed": False,
            "database_action": "",
            "next_action": "retrieve_service_info",
            "confidence": 0.88,
            "reason": "misclassified service information",
        }
    )


def _classify(message: str, *, raw_intent: dict | None = None) -> tuple[dict, dict]:
    intent = raw_intent if raw_intent is not None else _misclassified_service_information(message)
    intent = normalize_intent_result(apply_message_pattern_overrides(message, intent))
    return intent, route_intent(intent)


def _assert_database_route(intent: dict, route: dict, *, main_intent: str, scenario_intent: str) -> None:
    assert intent["main_intent"] == main_intent
    assert intent["scenario_intent"] == scenario_intent
    assert intent["database_action_needed"] is True
    assert intent["retrieval_needed"] is False
    assert route["route"] == "CALL_DATABASE"


@pytest.mark.parametrize(
    "message",
    [
        "What is my booking status?",
        "Can I check my booking?",
        "Is my appointment confirmed?",
        "What time is my booking?",
        "Do I have any upcoming bookings?",
        "Can you check Milo's grooming appointment?",
    ],
)
def test_booking_status_routes_to_database(message: str):
    intent, route = _classify(message)
    _assert_database_route(
        intent,
        route,
        main_intent="BOOKING_INTENT",
        scenario_intent="VIEW_BOOKING_STATUS",
    )


@pytest.mark.parametrize(
    "message",
    [
        "How many points do I have?",
        "Check my loyalty points",
        "What is my points balance?",
        "Do I have enough points?",
        "Can you check my rewards balance?",
    ],
)
def test_loyalty_balance_routes_to_database(message: str):
    intent, route = _classify(message)
    _assert_database_route(
        intent,
        route,
        main_intent="LOYALTY_INTENT",
        scenario_intent="CHECK_LOYALTY_POINTS",
    )


@pytest.mark.parametrize(
    ("message", "raw_intent", "scenario_intent", "route"),
    [
        (
            "What grooming services do you offer?",
            {
                "main_intent": "POLICY_INTENT",
                "scenario_intent": "SERVICE_INFORMATION",
                "service_type": "GROOMING",
                "retrieval_needed": True,
                "database_action_needed": False,
                "next_action": "retrieve_service_info",
                "confidence": 0.9,
            },
            "SERVICE_INFORMATION",
            "CALL_KNOWLEDGE_RAG",
        ),
        (
            "What is your cancellation policy?",
            {
                "main_intent": "POLICY_INTENT",
                "scenario_intent": "CANCELLATION_POLICY",
                "service_type": "GENERAL",
                "retrieval_needed": True,
                "database_action_needed": False,
                "next_action": "retrieve_policy",
                "confidence": 0.9,
            },
            "CANCELLATION_POLICY",
            "CALL_KNOWLEDGE_RAG",
        ),
        (
            "Do you have grooming slots tomorrow?",
            {
                "main_intent": "BOOKING_INTENT",
                "scenario_intent": "CHECK_AVAILABILITY",
                "service_type": "GROOMING",
                "retrieval_needed": False,
                "database_action_needed": True,
                "database_action": "check_availability",
                "next_action": "check_availability",
                "confidence": 0.9,
            },
            "CHECK_AVAILABILITY",
            "CALL_DATABASE",
        ),
        (
            "How does the loyalty programme work?",
            {
                "main_intent": "POLICY_INTENT",
                "scenario_intent": "LOYALTY_POLICY",
                "service_type": "GENERAL",
                "retrieval_needed": True,
                "database_action_needed": False,
                "next_action": "retrieve_policy",
                "confidence": 0.9,
            },
            "LOYALTY_POLICY",
            "CALL_KNOWLEDGE_RAG",
        ),
    ],
)
def test_negative_contrast_queries(message: str, raw_intent: dict, scenario_intent: str, route: str):
    base = _misclassified_service_information(message)
    base.update(raw_intent)
    intent = normalize_intent_result(apply_message_pattern_overrides(message, base))
    result = route_intent(intent)
    assert intent["scenario_intent"] == scenario_intent
    assert result["route"] == route


@pytest.mark.parametrize(
    ("raw_label", "expected"),
    [
        ("CHECK_POINTS_BALANCE", "CHECK_LOYALTY_POINTS"),
        ("POINTS_BALANCE", "CHECK_LOYALTY_POINTS"),
        ("BOOKING_STATUS", "VIEW_BOOKING_STATUS"),
        ("CHECK_BOOKING_STATUS", "VIEW_BOOKING_STATUS"),
    ],
)
def test_scenario_label_normalization(raw_label: str, expected: str):
    assert normalize_scenario_intent(raw_label) == expected
