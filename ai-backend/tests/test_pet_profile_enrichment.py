"""Pytest conversion of run_pet_profile_enrichment_test.py with fixed patches."""

from __future__ import annotations

import os
from unittest.mock import patch

PHONE = "+60 12-345 6701"
MESSAGE = "Ya, it's Milo. I would like trimming. How much is it?"


def test_map_pet_row_to_profile():
    from pet_profile import map_pet_row_to_profile

    profile = map_pet_row_to_profile(
        {
            "pet_id": 101,
            "pet_name": "Milo",
            "pet_type": "DOG",
            "size": "MEDIUM",
            "height_cm": 45,
        }
    )
    assert profile["pet_name"] == "Milo"
    assert profile["pet_type"] == "DOG"
    assert profile["pet_height"] == "45cm"
    assert profile["pet_size"] == "medium"
    assert profile["pet_id"] == "101"


def test_mock_get_pets_by_customer_id():
    from mock_database import mock_get_customer_pets

    result = mock_get_customer_pets(customer_id=1)
    assert result["status"] == "success"
    pets = result["data"]["pets"]
    assert len(pets) >= 2
    milo = next(pet for pet in pets if pet["pet_name"] == "Milo")
    assert milo["height_cm"] == 45
    assert str(milo["size"]).upper() == "MEDIUM"


def test_enrich_session_matched_pet():
    from pet_profile import enrich_session_pet_profile
    from session_store import SessionContext, clear_session_for_phone

    clear_session_for_phone(PHONE)
    session = SessionContext(phone_number=PHONE)
    session.customer_id = 1
    session.existing_customer = True
    session.customer_name = "Alicia Lee"

    result = enrich_session_pet_profile(session, MESSAGE)
    assert result["status"] == "matched"
    assert session.pet_name == "Milo"
    assert session.pet_id == 101
    assert session.pet_type == "DOG"
    assert session.pet_height == "45cm"
    assert session.pet_size == "medium"


def test_price_enquiry_does_not_ask_pet_size_after_enrichment():
    from customer_identity import resolve_customer_at_request_start
    from booking_service_info import compute_grooming_price_missing_fields, handle_price_enquiry_turn
    from session_store import SessionContext, clear_session_for_phone

    clear_session_for_phone(PHONE)
    session = SessionContext(phone_number=PHONE)

    mock_customer = {
        "action": "check_customer_by_phone",
        "status": "success",
        "data": {"customer_id": 1, "full_name": "Alicia Lee", "phone_number": PHONE},
        "error": None,
    }
    with patch("database_service.lookup_customer_by_phone", return_value=mock_customer):
        resolve_customer_at_request_start(session, PHONE, user_message=MESSAGE)

    intent = {
        "main_intent": "POLICY_INTENT",
        "scenario_intent": "SERVICE_INFORMATION",
        "service_type": "GROOMING",
        "entities": {},
        "missing_information": [],
    }
    updated = handle_price_enquiry_turn(session, intent, MESSAGE)
    missing = list(updated.get("missing_information") or [])
    assert "pet_size_or_height" not in missing
    assert "pet_type" not in missing
    assert compute_grooming_price_missing_fields(session, exact_price=True) == [] or "service_package" in missing


def test_ambiguous_pet_choice_reply():
    os.environ["DATABASE_PROVIDER"] = "mock"

    from pet_profile import build_pet_choice_reply, enrich_session_pet_profile
    from booking_service_info import compute_grooming_price_missing_fields
    from session_store import SessionContext, clear_session_for_phone

    clear_session_for_phone(PHONE)
    session = SessionContext(phone_number=PHONE)
    session.customer_id = 1
    session.customer_pets = []

    result = enrich_session_pet_profile(session, "How much is grooming?")
    assert result["status"] == "ambiguous"
    reply = build_pet_choice_reply(session.customer_pets)
    assert "Milo" in reply and "Cleo" in reply
    missing = compute_grooming_price_missing_fields(session)
    assert missing == ["pet_name"]


def test_identity_resolution_loads_pets():
    os.environ["DATABASE_PROVIDER"] = "mock"

    from customer_identity import resolve_customer_at_request_start
    from session_store import SessionContext, clear_session_for_phone

    clear_session_for_phone(PHONE)
    session = SessionContext(phone_number=PHONE)

    mock_customer = {
        "action": "check_customer_by_phone",
        "status": "success",
        "data": {
            "customer_id": 1,
            "full_name": "Alicia Lee",
            "phone_number": PHONE,
            "address": "",
        },
        "error": None,
    }

    with patch("database_service.lookup_customer_by_phone", return_value=mock_customer):
        result = resolve_customer_at_request_start(session, PHONE, user_message=MESSAGE)

    assert result["status"] == "success"
    assert session.customer_id == 1
    assert session.pet_name == "Milo"
    assert session.pet_size == "medium"
    assert session.pet_id is not None
    assert session.pet_type in {"DOG", "CAT"}
    assert len(session.customer_pets) >= 1


def test_invalid_confirmation_token_pet_row_is_ignored_and_real_pet_autoselected():
    from pet_profile import enrich_session_pet_profile
    from session_store import SessionContext

    session = SessionContext(
        customer_id=1,
        existing_customer=True,
        customer_pets=[
            {
                "pet_id": 1,
                "pet_name": "Milo",
                "pet_type": "Cat",
                "size": "M",
                "height_cm": 37,
            },
            {
                "pet_id": 23,
                "pet_name": "correct",
                "pet_type": "Cat",
                "size": "M",
            },
        ],
    )

    result = enrich_session_pet_profile(
        session,
        "I want to book grooming for my pet.",
    )

    assert result["status"] == "matched"
    assert [pet["pet_name"] for pet in session.customer_pets] == ["Milo"]
    assert session.pet_name == "Milo"
    assert session.pet_id == 1
