"""Database action dispatcher: Supabase relational reads and writes via relational_actions."""

from __future__ import annotations

import copy
import os
import re

from dotenv import load_dotenv

from customer_context import CustomerContext
from relational_actions import get_latest_booking_by_customer_id, merge_session_booking_context, relational_database_action


def get_database_provider() -> str:
    load_dotenv(override=not os.getenv("_EVAL_OVERRIDE_ACTIVE"))
    provider = os.getenv("RELATIONAL_PROVIDER", "").strip().lower()
    if provider:
        return provider
    return os.getenv("DATABASE_PROVIDER", "supabase").strip().lower() or "supabase"


def lookup_customer_by_phone(phone_number: str, customer_id: str = "") -> dict:
    if get_database_provider() == "mock":
        from mock_database import mock_check_customer_by_phone

        return mock_check_customer_by_phone(str(phone_number or "").strip())

    from relational_actions import check_customer_by_phone

    context = CustomerContext(
        phone_number=str(phone_number or "").strip(),
        request_customer_id=str(customer_id or "").strip(),
    )
    return check_customer_by_phone(context)


def execute_database_action(
    intent_json: dict,
    customer_id: str = "",
    phone_number: str = "",
    session=None,
    user_message: str = "",
) -> dict:
    payload = copy.deepcopy(intent_json)
    if session is not None:
        payload = merge_session_booking_context(payload, session)
    if user_message:
        payload["_source_message"] = user_message
    context = CustomerContext(
        phone_number=str(phone_number or "").strip(),
        request_customer_id=str(customer_id or "").strip(),
    )
    if customer_id:
        try:
            context.resolved_customer_id = int(customer_id)
        except (TypeError, ValueError):
            pass
    if get_database_provider() == "mock":
        from mock_database import mock_database_action

        return mock_database_action(
            payload,
            str(customer_id or "").strip(),
            str(phone_number or "").strip(),
        )
    if (
        str(payload.get("scenario_intent") or "").strip().upper() == "CONFIRM_BOOKING"
        and session is not None
        and bool(getattr(session, "new_customer_session", False))
    ):
        onboarding_error = _provision_new_booking_customer(context, payload, session)
        if onboarding_error is not None:
            return onboarding_error
    return relational_database_action(payload, context)


def _provision_new_booking_customer(
    context: CustomerContext,
    payload: dict,
    session,
) -> dict | None:
    """
    Create the minimum customer and pet records only after the customer has
    explicitly confirmed a complete booking draft.

    Returns an error result when onboarding cannot complete; otherwise mutates
    the scoped context/session/payload so the normal create_booking action can
    run immediately afterwards.
    """
    from relational_actions import (
        check_customer_by_phone,
        create_customer,
        create_pet,
        get_pet_by_customer_and_name,
    )

    customer_lookup = check_customer_by_phone(context)
    if customer_lookup.get("status") == "success":
        context.resolved_customer_id = customer_lookup.get("data", {}).get("customer_id")
    else:
        customer_name = str(getattr(session, "customer_name", "") or "").strip()
        created_customer = create_customer(
            context,
            customer_name,
            str(context.phone_number or "").strip(),
            address="-",
        )
        if created_customer.get("status") != "success":
            return created_customer
        context.resolved_customer_id = created_customer.get("data", {}).get("customer_id")

    session.customer_id = context.resolved_customer_id
    session.existing_customer = True

    entities = dict(payload.get("entities") or {})
    draft = dict(payload.get("_draft_booking") or getattr(session, "draft_booking_payload", {}) or {})
    pet_name = str(
        entities.get("pet_name")
        or draft.get("pet_name")
        or getattr(session, "pet_name", "")
        or ""
    ).strip()
    pet_lookup = get_pet_by_customer_and_name(context, pet_name)
    if pet_lookup.get("status") == "success":
        pet_record = dict((pet_lookup.get("data") or {}).get("pet") or {})
    else:
        height_text = str(
            entities.get("pet_height")
            or draft.get("pet_height")
            or getattr(session, "pet_height", "")
            or ""
        )
        height_match = re.search(r"\d+", height_text)
        created_pet = create_pet(
            context,
            pet_name=pet_name,
            pet_type=str(
                entities.get("pet_type")
                or draft.get("pet_type")
                or getattr(session, "pet_type", "")
                or ""
            ),
            size=str(
                entities.get("pet_size")
                or draft.get("pet_size")
                or getattr(session, "pet_size", "")
                or ""
            ),
            height_cm=int(height_match.group()) if height_match else None,
        )
        if created_pet.get("status") != "success":
            return created_pet
        pet_record = dict((created_pet.get("data") or {}).get("pet") or {})

    pet_id = pet_record.get("pet_id")
    entities["customer_id"] = str(context.resolved_customer_id)
    entities["pet_id"] = str(pet_id)
    entities["pet_name"] = pet_name
    payload["entities"] = entities
    draft["pet_id"] = pet_id
    draft["pet_name"] = pet_name
    payload["_draft_booking"] = draft
    session.pet_id = pet_id
    session.draft_booking_payload = draft
    return None


def fetch_latest_booking_for_entry(session) -> dict:
    customer_id = getattr(session, "customer_id", None)
    phone = str(getattr(session, "phone_number", "") or "").strip()
    if get_database_provider() == "mock":
        from mock_database import mock_get_latest_booking_by_customer_id

        return mock_get_latest_booking_by_customer_id(customer_id, phone)
    context = CustomerContext(phone_number=phone)
    if customer_id is not None:
        context.resolved_customer_id = int(customer_id)
    return get_latest_booking_by_customer_id(context)
