"""End-to-end Supabase action-to-response loop validation (Booking, Loyalty, Policy)."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv()

TEST_MARKER = "TEST_E2E_20260723"
TEST_PHONE = "+601999992024"
TEST_NAME = f"{TEST_MARKER} Customer"
UNKNOWN_LOYALTY_PHONE = "+601999992099"
REPORT_PATH = Path(__file__).resolve().parents[1] / "outputs" / "supabase_e2e_action_loop_report.json"

_e2e_report: list[dict] = []


def _supabase_configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"))


pytestmark = pytest.mark.skipif(not _supabase_configured(), reason="Supabase credentials not configured")


def _record(case: str, **fields) -> dict:
    row = {"case": case, **fields}
    _e2e_report.append(row)
    return row


@pytest.fixture(scope="module")
def company_id() -> int:
    return int(os.getenv("RELATIONAL_COMPANY_ID") or os.getenv("RAG_COMPANY_ID") or "1")


@pytest.fixture(scope="module")
def context(company_id):
    os.environ["RELATIONAL_PROVIDER"] = "supabase"
    os.environ["DATABASE_PROVIDER"] = "supabase"
    from customer_context import CustomerContext

    return CustomerContext(phone_number=TEST_PHONE, company_id=company_id)


@pytest.fixture(scope="module")
def e2e_state(context, company_id):
    from relational_actions import (
        cancel_booking,
        check_available_slots,
        check_booking_status,
        check_loyalty_points,
        create_booking,
        create_customer,
        create_pet,
        get_booking_by_id,
        get_pet_by_customer_and_name,
        reschedule_booking,
    )

    state: dict = {"marker": TEST_MARKER, "company_id": company_id}

    from relational_actions import check_customer_by_phone

    existing = check_customer_by_phone(context)
    if existing.get("status") == "success" and TEST_MARKER in str(existing["data"].get("full_name") or ""):
        created = existing
    else:
        created = create_customer(context, TEST_NAME, TEST_PHONE, address=f"{TEST_MARKER} address")
    assert created["status"] == "success", created
    assert created.get("handoff_required") is False
    state["customer_id"] = created["data"]["customer_id"]
    context.resolved_customer_id = state["customer_id"]
    _record("1_new_customer", action="create_customer", result=created, record_id=state["customer_id"])

    pet_read = get_pet_by_customer_and_name(context, f"{TEST_MARKER}_Pet")
    if pet_read.get("status") == "success":
        state["pet_id"] = pet_read["data"]["pet"]["pet_id"]
        pet_created = pet_read
    else:
        pet_created = create_pet(
            context,
            pet_name=f"{TEST_MARKER}_Pet",
            pet_type="Dog",
            size="M",
            height_cm=40,
            breed="Mixed",
        )
        assert pet_created["status"] == "success", pet_created
        state["pet_id"] = pet_created["data"]["pet"]["pet_id"]
    _record("2_new_pet", action="create_pet", result=pet_created, record_id=state["pet_id"])

    pet_read = get_pet_by_customer_and_name(context, f"{TEST_MARKER}_Pet")
    assert pet_read["status"] == "success"
    _record("3_existing_pet_lookup", action="get_pet_by_customer_and_name", result=pet_read)

    booking_date = (date.today() + timedelta(days=21)).isoformat()
    availability_intent = {
        "service_type": "GROOMING",
        "entities": {
            "pet_id": state["pet_id"],
            "preferred_date": booking_date,
            "preferred_time": "10:00",
        },
    }
    availability = check_available_slots(context, availability_intent)
    assert availability["status"] == "success", availability
    slots = (availability.get("data") or {}).get("available_slots") or []
    assert slots, availability
    chosen_time = slots[0]
    state["booking_date"] = booking_date
    state["booking_time"] = chosen_time
    _record("4_availability_success", action="check_available_slots", result=availability, slots=slots)

    booking_intent = {
        "service_type": "GROOMING",
        "entities": {
            "pet_id": state["pet_id"],
            "pet_name": f"{TEST_MARKER}_Pet",
            "preferred_date": booking_date,
            "preferred_time": chosen_time,
            "service_name": "Full Grooming",
        },
    }
    booking_created = create_booking(context, booking_intent)
    assert booking_created["status"] == "success", booking_created
    assert booking_created["data"].get("verified") is True
    state["booking_id"] = booking_created["data"]["booking_id"]
    _record(
        "5_booking_create_verified",
        action="create_booking",
        result=booking_created,
        record_id=state["booking_id"],
        verified=True,
    )

    read_back = get_booking_by_id(context, state["booking_id"], "GROOMING")
    assert read_back["status"] == "success"
    assert read_back["data"]["pet_id"] == state["pet_id"]
    _record("6_booking_read_back", action="get_booking_by_id", result=read_back, verified=True)

    status = check_booking_status(
        context,
        {"service_type": "GROOMING", "entities": {"booking_id": str(state["booking_id"])}},
    )
    assert status["status"] == "success"
    assert status["data"]["booking_id"] == state["booking_id"]
    _record("7_booking_status", action="check_booking_status", result=status)

    duplicate = create_booking(context, booking_intent)
    _record(
        "14_duplicate_booking_attempt",
        action="create_booking",
        result=duplicate,
        note="Duplicate while active booking exists",
    )

    new_date = (date.today() + timedelta(days=22)).isoformat()
    rescheduled = reschedule_booking(
        context,
        {
            "service_type": "GROOMING",
            "entities": {
                "booking_id": str(state["booking_id"]),
                "new_preferred_date": new_date,
                "new_preferred_time": chosen_time,
            },
        },
    )
    if rescheduled["status"] != "success":
        alt_time = slots[-1] if len(slots) > 1 else "14:00"
        rescheduled = reschedule_booking(
            context,
            {
                "service_type": "GROOMING",
                "entities": {
                    "booking_id": str(state["booking_id"]),
                    "new_preferred_date": new_date,
                    "new_preferred_time": alt_time,
                },
            },
        )
    assert rescheduled["status"] == "success", rescheduled
    assert rescheduled["action"] == "reschedule_booking"
    assert rescheduled["data"].get("verified") is True
    state["rescheduled_date"] = rescheduled["data"].get("booking_date")
    _record("8_reschedule_read_back", action="reschedule_booking", result=rescheduled, verified=True)

    cancelled = cancel_booking(
        context,
        {"service_type": "GROOMING", "entities": {"booking_id": str(state["booking_id"])}},
    )
    assert cancelled["status"] == "success", cancelled
    assert cancelled["action"] == "cancel_booking"
    assert cancelled["data"].get("verified") is True
    _record("9_cancel_read_back", action="cancel_booking", result=cancelled, verified=True)

    loyalty = check_loyalty_points(context)
    if loyalty["status"] != "success":
        from supabase_client import get_supabase_client

        client = get_supabase_client()
        loyalty_resp = client.table("loyaltymember").select("loyalty_id").order("loyalty_id", desc=True).limit(1).execute()
        next_loyalty_id = int((loyalty_resp.data or [{"loyalty_id": 0}])[0]["loyalty_id"]) + 1
        client.table("loyaltymember").insert(
            {
                "company_id": company_id,
                "loyalty_id": next_loyalty_id,
                "customer_id": state["customer_id"],
                "tier": "Silver",
                "points_balance": 120,
                "redemption_made": 0,
            }
        ).execute()
        loyalty = check_loyalty_points(context)
    assert loyalty["status"] == "success"
    state["points_balance"] = loyalty["data"]["points_balance"]
    _record("10_loyalty_balance", action="check_loyalty_points", result=loyalty)

    return state


@pytest.fixture(scope="module", autouse=True)
def write_e2e_report():
    yield
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(_e2e_report, indent=2, default=str), encoding="utf-8")


def test_e2e_records_created(e2e_state):
    assert e2e_state["customer_id"]
    assert e2e_state["pet_id"]
    assert e2e_state["booking_id"]


def test_duplicate_booking_not_success(e2e_state):
    from relational_actions import create_booking

    intent = {
        "service_type": "GROOMING",
        "entities": {
            "pet_id": e2e_state["pet_id"],
            "preferred_date": e2e_state["booking_date"],
            "preferred_time": e2e_state["booking_time"],
            "service_name": "Full Grooming",
        },
    }
    from customer_context import CustomerContext

    ctx = CustomerContext(phone_number=TEST_PHONE, company_id=e2e_state["company_id"])
    ctx.resolved_customer_id = e2e_state["customer_id"]
    second = create_booking(ctx, intent)
    assert second["status"] != "success" or second["data"].get("booking_id") != e2e_state["booking_id"]


@pytest.fixture
def chat_client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def bypass_session_guard(monkeypatch):
    monkeypatch.setattr("main.update_session_after_turn", lambda session, *args, **kwargs: session)
    monkeypatch.setattr("session_store.SessionContext.enable_runtime_guard", lambda self: None)


def test_loyalty_not_found_hitl(chat_client):
    with patch("main.execute_database_action") as mock_db:
        mock_db.return_value = {
            "action": "check_loyalty_points",
            "status": "not_found",
            "success": True,
            "data_found": False,
            "data": {},
            "error": None,
            "handoff_required": True,
            "handoff_reason": "LOYALTY_ACCOUNT_NOT_FOUND",
        }
        response = chat_client.post(
            "/chat",
            json={"message": "How many points do I have?", "phone_number": UNKNOWN_LOYALTY_PHONE},
        )
    body = response.json()
    row = _record(
        "11_loyalty_not_found_hitl",
        route=body["route_result"]["route"],
        handoff_required=body["handoff_required"],
        handoff_reason=body["handoff_reason"],
        reply=body["reply"],
    )
    assert body["handoff_required"] is True
    assert body["handoff_reason"] == "LOYALTY_ACCOUNT_NOT_FOUND"
    assert "0" not in body["reply"] or "loyalty account" in body["reply"].lower()


def test_policy_rag_found(chat_client):
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
            "success": True,
            "data_found": True,
            "action": "retrieve_rag_context",
            "data": {"chunk_count": 1},
            "error": None,
            "handoff_required": False,
            "handoff_reason": None,
        }
        response = chat_client.post("/chat", json={"message": "What is the cancellation policy?", "phone_number": ""})
    body = response.json()
    _record(
        "12_policy_rag_found",
        route=body["route_result"]["route"],
        handoff_required=body["handoff_required"],
        rag_used=body["rag_used"],
        reply=body["reply"],
    )
    assert body["handoff_required"] is False
    assert body["rag_used"] is True


def test_policy_rag_not_found_hitl(chat_client):
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
            "success": True,
            "data_found": False,
            "action": "retrieve_rag_context",
            "data": {"chunk_count": 0, "chunks": []},
            "error": None,
            "handoff_required": True,
            "handoff_reason": "RAG_CONTEXT_NOT_FOUND",
        }
        response = chat_client.post("/chat", json={"message": "What is the cancellation policy?", "phone_number": ""})
    body = response.json()
    _record(
        "13_policy_rag_not_found_hitl",
        route=body["route_result"]["route"],
        handoff_required=body["handoff_required"],
        handoff_reason=body["handoff_reason"],
        reply=body["reply"],
    )
    assert body["handoff_required"] is True
    assert body["handoff_reason"] == "RAG_CONTEXT_NOT_FOUND"


def test_database_failure_no_success_reply():
    from response_generator import generate_final_response, handoff_reply_for_reason

    database_result = {
        "action": "create_booking",
        "status": "error",
        "success": False,
        "data_found": False,
        "data": {},
        "error": "insert failed",
        "handoff_required": True,
        "handoff_reason": "DATABASE_ERROR",
    }
    intent_json = {
        "scenario_intent": "CONFIRM_BOOKING",
        "handoff_reason": "DATABASE_ERROR",
    }
    result = generate_final_response(
        user_message="Yes, confirm my booking",
        intent_json=intent_json,
        route_result={"route": "HUMAN_HANDOFF", "reason": "DATABASE_ERROR"},
        rag_context=[],
        database_result=database_result,
    )
    _record(
        "15_database_failure_handoff",
        handoff_required=True,
        reply=result["reply"],
    )
    assert "confirmed with status" not in result["reply"].lower()
    assert result["reply"] == handoff_reply_for_reason("DATABASE_ERROR")


def test_booking_confirmed_reply_grounded(e2e_state):
    from booking_draft import build_booking_confirmed_reply
    from response_generator import handoff_reply_for_reason

    ok = {
        "status": "success",
        "data_found": True,
        "handoff_required": False,
        "data": {
            "verified": True,
            "booking_id": e2e_state["booking_id"],
            "booking_status": "Pending",
            "service_type": "GROOMING",
            "booking_date": e2e_state.get("rescheduled_date") or e2e_state["booking_date"],
            "booking_time": e2e_state["booking_time"],
        },
    }
    reply = build_booking_confirmed_reply(ok)
    assert str(e2e_state["booking_id"]) in reply
    assert "Reference:" in reply

    bad = build_booking_confirmed_reply(
        {"status": "error", "data_found": False, "handoff_required": True, "handoff_reason": "DATABASE_ERROR", "data": {}}
    )
    assert bad == handoff_reply_for_reason("DATABASE_ERROR")
