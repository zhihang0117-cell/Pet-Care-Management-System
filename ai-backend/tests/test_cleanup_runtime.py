"""Runtime checks after overlap cleanup merges."""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from intent_schema import apply_message_pattern_overrides, normalize_intent_result
from router import route_intent

DELETED_MODULES = (
    "booking_entry",
    "booking_collection",
    "booking_safety",
    "session_continuation_legacy",
    "session_flow_reset",
    "response_composer",
    "booking_cta",
    "price_enquiry",
    "price_item_intent",
)

ROOT = Path(__file__).resolve().parents[1]


def test_intent_rag_route():
    intent = normalize_intent_result(
        apply_message_pattern_overrides(
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
        )
    )
    route = route_intent(intent)
    assert route["route"] == "CALL_KNOWLEDGE_RAG"


def test_booking_status_db_route():
    intent = normalize_intent_result(
        apply_message_pattern_overrides(
            "What is my booking status?",
            {
                "main_intent": "POLICY_INTENT",
                "scenario_intent": "SERVICE_INFORMATION",
                "service_type": "GENERAL",
                "retrieval_needed": True,
                "database_action_needed": False,
                "confidence": 0.9,
            },
        )
    )
    route = route_intent(intent)
    assert intent["scenario_intent"] == "VIEW_BOOKING_STATUS"
    assert route["route"] == "CALL_DATABASE"


def test_loyalty_db_route():
    intent = normalize_intent_result(
        apply_message_pattern_overrides(
            "How many points do I have?",
            {
                "main_intent": "POLICY_INTENT",
                "scenario_intent": "SERVICE_INFORMATION",
                "service_type": "GENERAL",
                "retrieval_needed": True,
                "database_action_needed": False,
                "confidence": 0.9,
            },
        )
    )
    route = route_intent(intent)
    assert intent["scenario_intent"] == "CHECK_LOYALTY_POINTS"
    assert route["route"] == "CALL_DATABASE"


def test_make_booking_collection():
    from booking_flow import apply_booking_collection_rules
    from session_store import SessionContext

    session = SessionContext(phone_number="+60123456789")
    session.existing_customer = True
    session.customer_id = 1
    session.last_service_type = "GROOMING"
    session.booking_creation_flow = True
    intent = {
        "main_intent": "BOOKING_INTENT",
        "scenario_intent": "MAKE_BOOKING",
        "service_type": "GROOMING",
        "entities": {"pet_name": "Milo", "preferred_date": "tomorrow"},
        "missing_information": [],
    }
    with patch("booking_flow.get_customer_pets_for_session", return_value=[{"pet_id": 1, "pet_name": "Milo"}]):
        with patch("booking_flow.resolve_pet_id", return_value=1):
            updated = apply_booking_collection_rules(session, intent, "tomorrow at 2pm")
    assert updated["scenario_intent"] in {
        "MAKE_BOOKING",
        "CHECK_AVAILABILITY",
        "GET_BOOKING_SERVICE_OPTIONS",
    }


def test_session_stores_field():
    from booking_flow import sync_session_booking_fields
    from session_store import SessionContext

    session = SessionContext(phone_number="+60123456789")
    sync_session_booking_fields(session, {"pet_name": "Milo", "service_type": "GROOMING"})
    assert session.pet_name == "Milo"
    assert session.last_service_type == "GROOMING"


def test_next_missing_field():
    from booking_flow import build_missing_field_reply

    reply = build_missing_field_reply(["preferred_date"], session=None, intent_json={})
    assert "date" in reply.lower()


def test_single_next_question_in_reply():
    from booking_flow import build_new_customer_booking_collection_reply
    from session_store import SessionContext

    session = SessionContext(phone_number="+60123456789")
    intent = {
        "missing_information": ["pet_type"],
        "entities": {},
        "new_customer_booking_collection": True,
    }
    reply = build_new_customer_booking_collection_reply(session, intent)
    assert reply.count("?") <= 1


def test_import_main():
    importlib.import_module("main")


def test_no_circular_imports():
    import booking_flow
    import booking_service_info
    import response_generator
    import session_continuation

    assert booking_flow.apply_booking_entry_rules
    assert booking_service_info.handle_price_enquiry_turn
    assert response_generator.validate_final_reply
    assert session_continuation.reset_flow_specific_state_on_intent_change


def test_no_deleted_module_references():
    pattern = re.compile(r"\b(" + "|".join(re.escape(name) for name in DELETED_MODULES) + r")\b")
    offenders: list[str] = []
    for module_info in pkgutil.iter_modules([str(ROOT)]):
        if module_info.name.startswith("test") or module_info.name in {"main", "tools"}:
            continue
        path = ROOT / f"{module_info.name}.py"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(module_info.name)
    assert offenders == []
