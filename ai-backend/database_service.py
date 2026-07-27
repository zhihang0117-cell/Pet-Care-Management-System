"""Database action dispatcher: Supabase relational reads and writes via relational_actions."""

from __future__ import annotations

import copy
import os

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
    return relational_database_action(payload, context)


def fetch_latest_booking_for_entry(session) -> dict:
    customer_id = getattr(session, "customer_id", None)
    phone = str(getattr(session, "phone_number", "") or "").strip()
    context = CustomerContext(phone_number=phone)
    if customer_id is not None:
        context.resolved_customer_id = int(customer_id)
    return get_latest_booking_by_customer_id(context)
