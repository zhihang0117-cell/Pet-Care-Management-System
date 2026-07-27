"""Routing lock tests for RAG vs relational CRUD actions."""

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
