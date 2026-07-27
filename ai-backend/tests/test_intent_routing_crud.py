"""Routing lock tests for RAG vs relational CRUD actions."""

import pytest

from intent_schema import apply_message_pattern_overrides, normalize_intent_result
from router import route_intent


def test_policy_rag_route():
    intent = normalize_intent_result(
        apply_message_pattern_overrides(
            "What is the cancellation policy?",
            {
                "main_intent": "POLICY_INTENT",
                "scenario_intent": "CANCELLATION_POLICY",
                "service_type": "GENERAL",
                "retrieval_needed": True,
                "database_action_needed": False,
                "confidence": 0.9,
            },
        )
    )
    route = route_intent(intent)
    assert route["route"] == "CALL_KNOWLEDGE_RAG"
    assert intent["database_action_needed"] is False
    assert intent["retrieval_needed"] is True


def test_service_information_rag_route():
    intent = normalize_intent_result(
        apply_message_pattern_overrides(
            "How much is full grooming?",
            {
                "main_intent": "POLICY_INTENT",
                "scenario_intent": "SERVICE_INFORMATION",
                "service_type": "GROOMING",
                "retrieval_needed": True,
                "database_action_needed": False,
                "confidence": 0.9,
            },
        )
    )
    route = route_intent(intent)
    assert route["route"] in {"CALL_KNOWLEDGE_RAG", "CALL_RAG_THEN_ASK_MISSING_INFO", "ASK_MISSING_INFO"}
    assert intent["database_action_needed"] is False


def test_booking_status_database_route():
    intent = normalize_intent_result(
        apply_message_pattern_overrides("What is my booking status?", {"confidence": 0.9})
    )
    route = route_intent(intent)
    assert intent["scenario_intent"] == "VIEW_BOOKING_STATUS"
    assert route["route"] == "CALL_DATABASE"
    assert intent["database_action"] == "check_booking_status"


def test_cancel_booking_database_route():
    intent = normalize_intent_result(
        apply_message_pattern_overrides("Cancel my booking", {"confidence": 0.9})
    )
    route = route_intent(intent)
    assert intent["scenario_intent"] == "CANCEL_BOOKING"
    assert route["route"] == "CALL_DATABASE"
    assert intent["database_action"] == "cancel_booking"


def test_redeem_reward_database_route():
    intent = normalize_intent_result(
        apply_message_pattern_overrides("Use 100 points for my booking", {"confidence": 0.9})
    )
    route = route_intent(intent)
    assert intent["scenario_intent"] == "REDEEM_REWARD"
    assert route["route"] == "CALL_DATABASE"
    assert intent["database_action"] == "redeem_reward"


def test_read_scenario_cannot_override_dispatch_with_create_booking(monkeypatch):
    from customer_context import CustomerContext
    import relational_actions

    monkeypatch.setattr(
        relational_actions,
        "check_booking_status",
        lambda *_args, **_kwargs: {"action": "check_booking_status", "status": "success"},
    )
    monkeypatch.setattr(
        relational_actions,
        "create_booking",
        lambda *_args, **_kwargs: pytest.fail("read scenario must not create a booking"),
    )

    result = relational_actions.relational_database_action(
        {
            "scenario_intent": "VIEW_BOOKING_STATUS",
            "database_action": "create_booking",
            "entities": {},
        },
        CustomerContext(company_id=1),
    )

    assert result["action"] == "check_booking_status"


def test_daycare_and_cancelled_booking_availability_fields():
    from relational_actions import _booking_blocks_availability, _booking_interval

    assert _booking_interval(
        {"check_in_time": "10:00:00", "check_out_time": "13:00:00"},
        "DAYCARE",
    ) == (600, 780)
    assert not _booking_blocks_availability({"booking_status": "Cancelled"})
