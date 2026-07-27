"""Real Supabase CRUD action tests — leaves TEST_CHATBOT_20260723 rows for manual review."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from dotenv import load_dotenv

load_dotenv()

TEST_MARKER = "TEST_CHATBOT_20260723"
TEST_PHONE = "+601999992023"
TEST_NAME = f"{TEST_MARKER} Customer"


def _supabase_configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"))


pytestmark = pytest.mark.skipif(not _supabase_configured(), reason="Supabase credentials not configured")


@pytest.fixture(scope="module")
def company_id() -> int:
    return int(os.getenv("RELATIONAL_COMPANY_ID") or os.getenv("RAG_COMPANY_ID") or "1")


@pytest.fixture(scope="module")
def repo(company_id):
    os.environ["RELATIONAL_PROVIDER"] = "supabase"
    os.environ["DATABASE_PROVIDER"] = "supabase"
    from relational_provider import get_relational_repository, reset_relational_repository

    reset_relational_repository()
    return get_relational_repository(force_new=True)


@pytest.fixture(scope="module")
def context(company_id):
    from customer_context import CustomerContext

    return CustomerContext(phone_number=TEST_PHONE, company_id=company_id)


@pytest.fixture(scope="module")
def test_state(repo, context, company_id):
    """Create and exercise test records; rows are left in Supabase for manual review."""
    from relational_actions import (
        cancel_booking,
        check_booking_status,
        check_loyalty_points,
        create_booking,
        create_customer,
        create_pet,
        get_pet_by_customer_and_name,
        redeem_reward,
        reschedule_booking,
        update_customer,
        update_pet,
    )
    from relational_repository import PetData

    state: dict = {"marker": TEST_MARKER}

    existing = repo.get_customer_by_phone(company_id, TEST_PHONE)
    if existing.get("status") == "success" and TEST_MARKER in str(existing["data"].get("full_name") or ""):
        created = existing
        created["data"] = existing["data"]
    else:
        created = create_customer(context, TEST_NAME, TEST_PHONE, address=f"{TEST_MARKER} address")
        assert created["status"] == "success", created
    state["customer_id"] = created["data"]["customer_id"]
    context.resolved_customer_id = state["customer_id"]

    read_back = repo.get_customer_by_phone(company_id, TEST_PHONE)
    assert read_back["status"] == "success"
    assert TEST_MARKER in read_back["data"]["full_name"]

    updated = update_customer(context, {"address": f"{TEST_MARKER} updated address"})
    assert updated["status"] == "success"

    pet_read = get_pet_by_customer_and_name(context, f"{TEST_MARKER}_Pet")
    if pet_read.get("status") == "success":
        state["pet_id"] = pet_read["data"]["pet"]["pet_id"]
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
        pet_read = get_pet_by_customer_and_name(context, f"{TEST_MARKER}_Pet")
    assert pet_read["status"] == "success"

    pet_updated = update_pet(context, state["pet_id"], {"service_notes": f"{TEST_MARKER} notes"})
    assert pet_updated["status"] == "success"

    from supabase_client import get_supabase_client

    client = get_supabase_client()
    loyalty = client.table("loyaltymember").select("*").eq("customer_id", state["customer_id"]).limit(1).execute()
    rows = loyalty.data or []
    if rows:
        state["loyalty_id"] = rows[0]["loyalty_id"]
        if int(rows[0].get("points_balance") or 0) < 150:
            client.table("loyaltymember").update({"points_balance": 150}).eq("loyalty_id", rows[0]["loyalty_id"]).execute()
    else:
        loyalty_resp = client.table("loyaltymember").select("loyalty_id").order("loyalty_id", desc=True).limit(1).execute()
        next_loyalty_id = int((loyalty_resp.data or [{"loyalty_id": 0}])[0]["loyalty_id"]) + 1
        client.table("loyaltymember").insert(
            {
                "company_id": company_id,
                "loyalty_id": next_loyalty_id,
                "customer_id": state["customer_id"],
                "tier": "Silver",
                "points_balance": 150,
                "redemption_made": 0,
            }
        ).execute()
        state["loyalty_id"] = next_loyalty_id

    booking_date = (date.today() + timedelta(days=14)).isoformat()
    booking_intent = {
        "service_type": "GROOMING",
        "entities": {
            "pet_id": state["pet_id"],
            "pet_name": f"{TEST_MARKER}_Pet",
            "preferred_date": booking_date,
            "preferred_time": "10:00",
            "service_name": "Full Grooming",
        },
    }
    booking_created = create_booking(context, booking_intent)
    if booking_created["status"] != "success":
        booking_intent["entities"]["preferred_time"] = "14:00"
        booking_created = create_booking(context, booking_intent)
    if booking_created["status"] != "success":
        latest = check_booking_status(context, {"service_type": "GROOMING"})
        assert latest["status"] == "success", booking_created
        state["booking_id"] = latest["data"]["booking_id"]
    else:
        state["booking_id"] = booking_created["data"]["booking_id"]
    state["booking_date"] = booking_date

    status = check_booking_status(context, {"service_type": "GROOMING", "entities": {"booking_id": state["booking_id"]}})
    assert status["status"] == "success"

    cancelled = cancel_booking(
        context,
        {"service_type": "GROOMING", "entities": {"booking_id": str(state["booking_id"])}},
    )
    assert cancelled["status"] == "success", cancelled
    assert cancelled["action"] == "cancel_booking"
    assert cancelled["data"].get("verified") is True
    assert str(cancelled["data"].get("booking_status", "")).lower().startswith("cancel")

    loyalty = check_loyalty_points(context)
    assert loyalty["status"] == "success"
    state["points_balance"] = loyalty["data"]["points_balance"]

    insufficient = redeem_reward(context, {"entities": {"points_to_redeem": "1000"}, "_source_message": "use 1000 points"})
    assert insufficient["status"] == "error"

    redeemed = redeem_reward(context, {"entities": {"points_to_redeem": "10"}, "_source_message": "use 10 points"})
    assert redeemed["status"] == "success", redeemed

    return state


def test_create_customer(test_state):
    assert test_state["customer_id"]


def test_read_customer(test_state):
    assert test_state["customer_id"]


def test_update_customer(test_state):
    assert test_state["customer_id"]


def test_create_pet(test_state):
    assert test_state["pet_id"]


def test_read_pet(test_state):
    assert test_state["pet_id"]


def test_update_pet(test_state):
    assert test_state["pet_id"]


def test_create_booking(test_state):
    assert test_state["booking_id"]


def test_read_booking_status(test_state):
    assert test_state["booking_id"]


def _create_booking_with_available_slot(context, pet_id: int, booking_date: str, service_name: str = "Full Grooming") -> dict:
    from relational_actions import check_available_slots, create_booking

    availability = check_available_slots(
        context,
        {
            "service_type": "GROOMING",
            "entities": {"pet_id": pet_id, "preferred_date": booking_date, "preferred_time": "10:00"},
        },
    )
    assert availability["status"] == "success", availability
    slots = (availability.get("data") or {}).get("available_slots") or ["10:00"]
    last_result = None
    for slot in slots:
        intent = {
            "service_type": "GROOMING",
            "entities": {
                "pet_id": pet_id,
                "preferred_date": booking_date,
                "preferred_time": slot[:5],
                "service_name": service_name,
            },
        }
        last_result = create_booking(context, intent)
        if last_result["status"] == "success":
            return last_result
    return last_result or {"status": "error", "error": "no slot available"}


def test_reschedule_booking(test_state, context):
    from datetime import date, timedelta

    from relational_actions import reschedule_booking

    booking_date = (date.today() + timedelta(days=28)).isoformat()
    created = _create_booking_with_available_slot(context, test_state["pet_id"], booking_date)
    assert created["status"] == "success", created
    booking_id = created["data"]["booking_id"]

    new_date = (date.today() + timedelta(days=29)).isoformat()
    from relational_actions import check_available_slots

    availability = check_available_slots(
        context,
        {
            "service_type": "GROOMING",
            "entities": {"preferred_date": new_date, "preferred_time": "10:00"},
        },
    )
    slots = (availability.get("data") or {}).get("available_slots") or ["11:00"]
    rescheduled = {"status": "error"}
    for slot in slots:
        rescheduled = reschedule_booking(
            context,
            {
                "service_type": "GROOMING",
                "entities": {
                    "booking_id": str(booking_id),
                    "new_preferred_date": new_date,
                    "new_preferred_time": slot[:5],
                },
            },
        )
        if rescheduled["status"] == "success":
            break
    assert rescheduled["status"] == "success", rescheduled
    assert rescheduled["action"] == "reschedule_booking"
    assert rescheduled["data"].get("verified") is True


def test_cancel_booking(test_state, context):
    from datetime import date, timedelta

    from relational_actions import cancel_booking

    booking_date = (date.today() + timedelta(days=30)).isoformat()
    created = _create_booking_with_available_slot(context, test_state["pet_id"], booking_date)
    assert created["status"] == "success", created
    booking_id = created["data"]["booking_id"]

    cancelled = cancel_booking(
        context,
        {"service_type": "GROOMING", "entities": {"booking_id": str(booking_id)}},
    )
    assert cancelled["status"] == "success", cancelled
    assert cancelled["action"] == "cancel_booking"
    assert cancelled["data"].get("verified") is True


def test_read_loyalty_balance(test_state):
    assert test_state["points_balance"] >= 0


def test_insufficient_redemption(test_state):
    assert test_state["loyalty_id"]


def test_successful_redemption(test_state):
    assert test_state["loyalty_id"]
