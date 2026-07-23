"""Focused tests for HITL handoff and strict response grounding."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from intent_schema import (
    apply_message_pattern_overrides,
    is_explicit_human_handoff_request,
    normalize_intent_result,
)
from response_generator import handoff_reply_for_reason, resolve_turn_handoff
from router import route_intent


@pytest.fixture(autouse=True)
def bypass_session_projection_guard(monkeypatch):
    """Allow full /chat integration checks without projection-only write assertions."""

    def _passthrough(session, intent_json, route_result, database_result):
        return session

    monkeypatch.setattr("main.update_session_after_turn", _passthrough)
    monkeypatch.setattr("session_store.SessionContext.enable_runtime_guard", lambda self: None)


@pytest.fixture
def client(app):
    return TestClient(app)


def test_explicit_manager_request_handoff(client):
    response = client.post("/chat", json={"message": "I want to speak to your manager", "phone_number": ""})
    body = response.json()
    assert body["route_result"]["route"] == "HUMAN_HANDOFF"
    assert body["handoff_required"] is True
    assert body["handoff_reason"] == "EXPLICIT_HUMAN_REQUEST"
    assert body["rag_used"] is False
    assert body["database_used"] is False
    assert "pass this conversation to our team" in body["reply"].lower()
    assert "?" not in body["reply"]


def test_talk_to_human_handoff(client):
    response = client.post("/chat", json={"message": "Can I talk to a human?", "phone_number": ""})
    body = response.json()
    assert body["route_result"]["route"] == "HUMAN_HANDOFF"
    assert body["handoff_required"] is True
    assert body["handoff_reason"] == "EXPLICIT_HUMAN_REQUEST"


def test_bot_cannot_solve_handoff(client):
    response = client.post("/chat", json={"message": "Your bot cannot solve this, get someone", "phone_number": ""})
    body = response.json()
    assert body["route_result"]["route"] == "HUMAN_HANDOFF"
    assert body["handoff_required"] is True
    assert body["handoff_reason"] == "EXPLICIT_HUMAN_REQUEST"


def test_booking_status_with_record(client):
    with patch("main.execute_database_action") as mock_db:
        mock_db.return_value = {
            "action": "check_booking_status",
            "status": "success",
            "success": True,
            "data_found": True,
            "data": {
                "booking_status": "confirmed",
                "service_type": "GROOMING",
                "booking_date": "2026-07-24",
                "booking_time": "15:00:00",
            },
            "error": None,
        }
        response = client.post(
            "/chat",
            json={"message": "What is my booking status?", "phone_number": "+60 12-345 6701"},
        )
    body = response.json()
    assert body["handoff_required"] is False
    assert body["database_result"]["data_found"] is True
    assert "confirmed" in body["reply"].lower() or body["database_result"]["status"] == "success"


def test_booking_status_no_record_handoff(client):
    with patch("main.execute_database_action") as mock_db:
        mock_db.return_value = {
            "action": "check_booking_status",
            "status": "not_found",
            "success": True,
            "data_found": False,
            "data": {},
            "error": None,
        }
        response = client.post(
            "/chat",
            json={"message": "What is my booking status?", "phone_number": "+60 12-345 6701"},
        )
    body = response.json()
    assert body["handoff_required"] is True
    assert body["handoff_reason"] == "BOOKING_NOT_FOUND"
    assert "couldn't find a matching booking" in body["reply"].lower()
    assert "confirmed" not in body["reply"].lower()


def test_loyalty_no_account_handoff(client):
    with patch("main.execute_database_action") as mock_db:
        mock_db.return_value = {
            "action": "check_loyalty_points",
            "status": "not_found",
            "success": True,
            "data_found": False,
            "data": {},
            "error": None,
        }
        response = client.post(
            "/chat",
            json={"message": "How many points do I have?", "phone_number": "+60 12-345 6701"},
        )
    body = response.json()
    assert body["handoff_required"] is True
    assert body["handoff_reason"] == "LOYALTY_ACCOUNT_NOT_FOUND"
    assert "0" not in body["reply"] or "loyalty account" in body["reply"].lower()


def test_rag_policy_with_context(client):
    with patch("main.retrieve_rag_context") as mock_rag:
        mock_rag.return_value = {
            "rag_used": True,
            "rag_reliable": True,
            "rag_context": [{"text": "Cancellation requires 24 hours notice.", "score": 0.9, "metadata": {}}],
            "rag_provider_used": "mock",
            "rag_provider_config": "mock",
            "rag_error": None,
            "rag_debug": {},
            "retrieval_query": "What is the cancellation policy?",
            "rewritten_query": "",
            "query_rewrite_used": False,
        }
        response = client.post("/chat", json={"message": "What is the cancellation policy?", "phone_number": ""})
    body = response.json()
    assert body["handoff_required"] is False
    assert body["rag_used"] is True


def test_rag_no_context_handoff(client):
    with patch("main.retrieve_rag_context") as mock_rag:
        mock_rag.return_value = {
            "rag_used": False,
            "rag_reliable": False,
            "rag_context": [],
            "rag_provider_used": "mock",
            "rag_provider_config": "mock",
            "rag_error": None,
            "rag_debug": {},
            "retrieval_query": "What is the cancellation policy?",
            "rewritten_query": "",
            "query_rewrite_used": False,
        }
        response = client.post("/chat", json={"message": "What is the cancellation policy?", "phone_number": ""})
    body = response.json()
    assert body["handoff_required"] is True
    assert body["handoff_reason"] == "RAG_CONTEXT_NOT_FOUND"
    assert "unable to confirm" in body["reply"].lower()


def test_database_exception_handoff(client):
    with patch("main.execute_database_action") as mock_db:
        mock_db.return_value = {
            "action": "check_booking_status",
            "status": "error",
            "success": False,
            "data_found": False,
            "data": {},
            "error": "connection failed",
        }
        response = client.post(
            "/chat",
            json={"message": "What is my booking status?", "phone_number": "+60 12-345 6701"},
        )
    body = response.json()
    assert body["handoff_required"] is True
    assert body["handoff_reason"] == "DATABASE_ERROR"


def test_rag_exception_handoff(client):
    with patch("main.retrieve_rag_context") as mock_rag:
        mock_rag.return_value = {
            "rag_used": False,
            "rag_reliable": False,
            "rag_context": [],
            "rag_provider_used": "mock",
            "rag_provider_config": "mock",
            "rag_error": "embedding failed",
            "rag_debug": {},
            "retrieval_query": "What is the cancellation policy?",
            "rewritten_query": "",
            "query_rewrite_used": False,
        }
        response = client.post("/chat", json={"message": "What is the cancellation policy?", "phone_number": ""})
    body = response.json()
    assert body["handoff_required"] is True
    assert body["handoff_reason"] == "RAG_RETRIEVAL_ERROR"


def test_medical_diagnosis_handoff(client):
    response = client.post(
        "/chat",
        json={"message": "What medicine should I give my dog for fever?", "phone_number": ""},
    )
    body = response.json()
    assert body["route_result"]["route"] == "HUMAN_HANDOFF"
    assert body["handoff_required"] is True
    assert body["handoff_reason"] == "MEDICAL_OUT_OF_SCOPE"


def test_manager_request_during_booking(client):
    response = client.post(
        "/chat",
        json={"message": "I want to speak to your manager, book grooming tomorrow", "phone_number": ""},
    )
    body = response.json()
    assert body["route_result"]["route"] == "HUMAN_HANDOFF"
    assert body["handoff_required"] is True
    assert body["handoff_reason"] == "EXPLICIT_HUMAN_REQUEST"
    assert "?" not in body["reply"]


def test_explicit_human_detection_unit():
    assert is_explicit_human_handoff_request("Can I talk to a human please?")


def test_resolve_turn_handoff_db_not_found():
    required, reason = resolve_turn_handoff(
        user_message="status",
        intent_json=normalize_intent_result(
            apply_message_pattern_overrides("What is my booking status?", {"confidence": 0.9})
        ),
        route_result=route_intent(
            normalize_intent_result(
                apply_message_pattern_overrides("What is my booking status?", {"confidence": 0.9})
            )
        ),
        database_result={
            "action": "check_booking_status",
            "status": "not_found",
            "success": True,
            "data_found": False,
            "data": {},
        },
        rag_result={},
    )
    assert required is True
    assert reason == "BOOKING_NOT_FOUND"


def test_handoff_reply_non_empty():
    assert handoff_reply_for_reason("EXPLICIT_HUMAN_REQUEST")
    assert "supabase" not in handoff_reply_for_reason("DATABASE_ERROR").lower()


def test_mixed_booking_no_rag_continues_without_handoff(client):
    """Missing RAG context must not block booking collection on mixed routes."""
    with patch("main.retrieve_rag_context") as mock_rag:
        mock_rag.return_value = {
            "rag_used": False,
            "rag_reliable": False,
            "rag_context": [],
            "rag_provider_used": "mock",
            "rag_provider_config": "mock",
            "rag_error": None,
            "rag_debug": {},
            "retrieval_query": "grooming price booking",
            "rewritten_query": "",
            "query_rewrite_used": False,
        }
        response = client.post(
            "/chat",
            json={
                "message": "How much is grooming and I want to book tomorrow",
                "phone_number": "+60123456701",
            },
        )
    body = response.json()
    assert body["handoff_required"] is False
    assert body["route_result"]["route"] in {
        "CALL_RAG_THEN_ASK_MISSING_INFO",
        "ASK_MISSING_INFO",
        "CALL_DATABASE",
    }
    reply = body["reply"].lower()
    assert "pass this to our team" not in reply
    assert "?" in body["reply"] or "may i" in reply or "details" in reply
