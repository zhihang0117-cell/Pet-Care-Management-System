"""Tests for the relational-data response trust boundary."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from response_data_sanitizer import sanitize_response_data


def test_sanitizer_recursively_removes_all_staff_identifier_variants():
    source = {
        "staff_id": 11,
        "staff_name": "Alice",
        "available_staff": [
            {
                "staff_id": 12,
                "staff_name": "Bob",
                "reviewed_by_staff_id": 13,
            }
        ],
        "draft": {
            "selected_staff_id": 14,
            "verified_by_staff_id": 15,
            "booking_status": "confirmed",
        },
    }

    sanitized = sanitize_response_data(source)

    assert sanitized == {
        "staff_name": "Alice",
        "available_staff": [{"staff_name": "Bob"}],
        "draft": {"booking_status": "confirmed"},
    }
    # Sanitization returns a copy and does not damage data needed internally.
    assert source["staff_id"] == 11
    assert source["draft"]["selected_staff_id"] == 14


def test_sanitizer_removes_internal_auth_identity_fields():
    sanitized = sanitize_response_data(
        {
            "account_status": "active",
            "auth_user_id": "private-auth-id",
            "company": {
                "company_name": "Pawfect",
                "manager_user_id": "private-manager-id",
            },
        }
    )

    assert sanitized == {
        "account_status": "active",
        "company": {"company_name": "Pawfect"},
    }


def test_chat_does_not_expose_staff_ids_in_database_or_session(app, monkeypatch):
    monkeypatch.setattr("main.update_session_after_turn", lambda session, *_args: session)
    monkeypatch.setattr("session_store.SessionContext.enable_runtime_guard", lambda self: None)

    database_result = {
        "action": "check_booking_status",
        "status": "success",
        "success": True,
        "data_found": True,
        "data": {
            "staff_id": 22,
            "staff_name": "Alice",
            "verified_by_staff_id": 23,
            "booking_status": "confirmed",
            "service_type": "GROOMING",
            "booking_date": "2026-07-30",
            "booking_time": "10:00:00",
        },
        "error": None,
    }

    with patch("main.execute_database_action", return_value=database_result):
        response = TestClient(app).post(
            "/chat",
            json={
                "message": "What is my booking status?",
                "phone_number": "+60 12-345 6701",
            },
        )

    body = response.json()
    body_text = response.text.lower()
    assert body["database_result"]["data"]["staff_name"] == "Alice"
    assert "staff_id" not in body_text
    assert "selected_staff_id" not in body["session_context"]
