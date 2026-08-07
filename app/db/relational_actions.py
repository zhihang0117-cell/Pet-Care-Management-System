"""
Read-only Supabase relational database actions for Pawfect AI backend.

Phone number identity comes from WhatsApp webhook metadata / request context,
not from customer free-text messages.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any

from .customer_context import (
    CustomerContext,
    missing_identity_result,
    normalize_phone_digits,
    phones_match,
)
from .supabase_client import get_supabase_client
from app.context.slot_holds import SLOT_HOLDS
from app.tools.booking_window import today_business

READ_ONLY_SCENARIOS = {
    "VIEW_BOOKING_STATUS",
    "CHECK_AVAILABILITY",
    "CHECK_LOYALTY_POINTS",
    "CHECK_MEMBERSHIP_STATUS",
    "LOYALTY_ACCOUNT_INQUIRY",
    "CHECK_COUPON_ELIGIBILITY",
    "GET_BOOKING_SERVICE_OPTIONS",
    "CUSTOMER_GREETING",
    "REPEAT_LAST_BOOKING",
    "VIEW_PAYMENT_HISTORY",
    "VIEW_REDEMPTION_HISTORY",
    "VIEW_MESSAGE_HISTORY",
    "VIEW_COMPANY_INFORMATION",
    "VIEW_STAFF_DIRECTORY",
    "VIEW_ACCOUNT_STATUS",
}

WRITE_SCENARIOS = {
    "CANCEL_BOOKING",
    "RESCHEDULE_BOOKING",
    "REDEEM_REWARD",
    "CONFIRM_BOOKING",
    "CREATE_CUSTOMER",
    "CREATE_PET",
}

SERVICE_TYPE_TO_BOOKING_TABLE = {
    "GROOMING": "grooming_booking",
    "DAYCARE": "daycare_booking",
    "BOARDING": "boarding_booking",
}

# Customer-facing choices use a half-hour grid.  Anchoring an hourly grid to
# the opening minute made every :30 choice disappear on days that opened on
# the hour, even when 17:30 (or another half-hour) was genuinely free.
SLOT_MINUTES = 30
BUSINESS_HOURS_CONFIG_ERROR = "BUSINESS_HOURS_NOT_CONFIGURED"

# Statuses that may be referenced as a customer's previous booking at entry.
QUALIFYING_PREVIOUS_BOOKING_STATUSES = frozenset(
    {
        "confirmed",
        "scheduled",
        "pending",
        "completed",
        "done",
        "in progress",
        "active",
    }
)

# Statuses that must never be presented as a previous completed service.
EXCLUDED_PREVIOUS_BOOKING_STATUSES = frozenset(
    {
        "cancelled",
        "canceled",
        "rejected",
        "deleted",
        "no show",
        "no-show",
        "failed",
    }
)


_BOOKING_LOOKUP_ACTIONS = frozenset(
    {
        "check_booking_status",
        "check_last_booking",
        "get_latest_booking_by_customer_id",
        "get_booking_by_id",
        "cancel_booking",
        "reschedule_booking",
        "update_booking",
    }
)
_LOYALTY_LOOKUP_ACTIONS = frozenset(
    {
        "check_loyalty_points",
        "check_membership_status",
        "check_loyalty_account",
        "get_loyalty_account",
        "check_coupon_eligibility",
        "redeem_reward",
    }
)
_PET_LOOKUP_ACTIONS = frozenset({"get_pet_by_customer_and_name", "get_pets_by_customer_id"})
_CUSTOMER_LOOKUP_ACTIONS = frozenset({"check_customer_by_phone", "create_customer", "update_customer"})
_WRITE_ACTIONS = frozenset(
    {
        "create_booking",
        "cancel_booking",
        "reschedule_booking",
        "update_booking",
        "create_customer",
        "create_pet",
        "update_customer",
        "update_pet",
        "redeem_reward",
    }
)


def _derive_handoff_meta(
    action: str, status: str, *, data_found: bool, error: str | None = None
) -> tuple[bool, str | None]:
    if status == "missing_information":
        return False, None
    if status == "error":
        message = str(error or "").lower()
        expected_customer_or_business_error = any(
            marker in message
            for marker in (
                "required",
                "does not belong",
                "not available",
                "overlaps this time",
                "fully booked",
                "outside business hours",
                "after closing time",
                "before opening time",
                "business is closed",
                "must be",
                "not enough points",
                "voucher has expired",
                "already has a",
                "confirmation",
                "cannot be requested",
            )
        )
        if expected_customer_or_business_error:
            return False, None
        return True, "DATABASE_ERROR"
    if status == "not_found" or (status == "success" and not data_found):
        # A phone number that is not registered is the normal entry point for
        # new-customer onboarding, not an operational failure or HITL case.
        if action == "check_customer_by_phone":
            return False, None
        if action in _BOOKING_LOOKUP_ACTIONS:
            return True, "BOOKING_NOT_FOUND"
        if action in _LOYALTY_LOOKUP_ACTIONS:
            return True, "LOYALTY_ACCOUNT_NOT_FOUND"
        if action in _PET_LOOKUP_ACTIONS:
            return True, "PET_NOT_FOUND"
        if action in _CUSTOMER_LOOKUP_ACTIONS:
            return True, "CUSTOMER_NOT_FOUND"
    return False, None


def _result(
    action: str,
    status: str,
    data: dict | None = None,
    error: str | None = None,
    *,
    handoff_required: bool | None = None,
    handoff_reason: str | None = None,
) -> dict:
    payload = {} if data is None else data
    operational_success = status in {"success", "not_found", "missing_information"}
    if status == "success":
        data_found = bool(payload)
    elif status == "not_found":
        data_found = False
    elif status == "missing_information":
        data_found = False
    else:
        data_found = False
    if handoff_required is None:
        handoff_required, handoff_reason = _derive_handoff_meta(
            action, status, data_found=data_found, error=error
        )
    return {
        "action": action,
        "status": status,
        "success": operational_success,
        "data_found": data_found,
        "data": payload,
        "error": error,
        "handoff_required": bool(handoff_required),
        "handoff_reason": handoff_reason,
    }


def _normalize_time_value(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 5 and text[2] == ":":
        return text[:5]
    return text


def _verify_persisted_booking(
    context: CustomerContext,
    booking_id: int,
    service_type: str,
    expected: dict,
) -> tuple[bool, dict, list[str]]:
    read_back = get_booking_by_id(context, booking_id, service_type)
    if read_back.get("status") != "success":
        return False, {}, ["read_back_not_found"]
    record = dict(read_back.get("data") or {})
    mismatches: list[str] = []
    if expected.get("pet_id") is not None:
        if int(record.get("pet_id") or 0) != int(expected["pet_id"]):
            mismatches.append("pet_id")
    if expected.get("staff_id") is not None:
        if int(record.get("staff_id") or 0) != int(expected["staff_id"]):
            mismatches.append("staff_id")
    expected_date = str(expected.get("booking_date") or expected.get("preferred_date") or "").strip()
    if expected_date and str(record.get("booking_date") or record.get("check_in_date") or "").strip() != expected_date:
        mismatches.append("booking_date")
    expected_check_out = str(expected.get("check_out_date") or "").strip()
    if expected_check_out and str(record.get("check_out_date") or "").strip() != expected_check_out:
        mismatches.append("check_out_date")
    expected_check_out_time = _normalize_time_value(str(expected.get("check_out_time") or ""))
    actual_check_out_time = _normalize_time_value(str(record.get("check_out_time") or ""))
    if expected_check_out_time and actual_check_out_time != expected_check_out_time:
        mismatches.append("check_out_time")
    expected_time = _normalize_time_value(
        str(expected.get("booking_time") or expected.get("preferred_time") or expected.get("check_in_time") or "")
    )
    actual_time = _normalize_time_value(
        str(record.get("booking_time") or record.get("check_in_time") or "")
    )
    if expected_time and actual_time and expected_time != actual_time:
        mismatches.append("booking_time")
    expected_service = str(expected.get("service_name") or expected.get("package_name") or "").strip().lower()
    if expected_service:
        actual_service = str(
            record.get("package_name") or record.get("service_name") or record.get("room_type") or ""
        ).strip().lower()
        if expected_service not in actual_service and actual_service not in expected_service:
            mismatches.append("service_name")
    expected_status = str(expected.get("booking_status") or "").strip().lower()
    if expected_status:
        actual_status = str(record.get("booking_status") or "").strip().lower()
        if not actual_status.startswith(expected_status[:6]):
            mismatches.append("booking_status")
    for amount_field in ("price", "price_per_night", "total_price", "add_on_price"):
        if expected.get(amount_field) is None:
            continue
        try:
            if round(float(record.get(amount_field)), 2) != round(
                float(expected[amount_field]), 2
            ):
                mismatches.append(amount_field)
        except (TypeError, ValueError):
            mismatches.append(amount_field)
    if expected.get("payment_id") is not None:
        if int(record.get("payment_id") or 0) != int(expected["payment_id"]):
            mismatches.append("payment_id")
    if not _verify_booking_belongs_to_customer(context, record):
        mismatches.append("customer_ownership")
    return not mismatches, record, mismatches


def _finalize_booking_write(
    action: str,
    context: CustomerContext,
    booking_id: int,
    service_type: str,
    write_result: dict,
    *,
    expected: dict | None = None,
) -> dict:
    if write_result.get("status") != "success":
        return _result(
            action,
            "error",
            dict(write_result.get("data") or {}),
            write_result.get("error") or "Booking update failed",
        )
    verified, record, mismatches = _verify_persisted_booking(
        context,
        booking_id,
        service_type,
        expected or {},
    )
    if not verified:
        return _result(
            action,
            "error",
            {"booking_id": booking_id, "mismatches": mismatches, "record": record},
            "Booking verification failed after write",
            handoff_required=True,
            handoff_reason="DATABASE_ERROR",
        )
    return _result(action, "success", {**record, "verified": True})


def _service_table(service_type: str) -> str:
    normalized = str(service_type or "UNKNOWN").strip().upper()
    return SERVICE_TYPE_TO_BOOKING_TABLE.get(normalized, "grooming_booking")


def _normalize_booking_status(status: str) -> str:
    return str(status or "").strip().lower()


def is_qualifying_previous_booking_status(status: str) -> bool:
    """True when a booking row may be shown as the customer's previous service."""
    normalized = _normalize_booking_status(status)
    if not normalized:
        return False
    if normalized in EXCLUDED_PREVIOUS_BOOKING_STATUSES:
        return False
    return normalized in QUALIFYING_PREVIOUS_BOOKING_STATUSES


def check_customer_by_phone(context: CustomerContext) -> dict:
    """Look up customer row by request/session phone_number."""
    if not context.has_phone:
        return missing_identity_result("check_customer_by_phone")

    try:
        client = get_supabase_client()
        # Keep phone matching inside Postgres instead of downloading every
        # customer in the company. The wildcard pattern tolerates legacy
        # formatting such as "+60 12-345 6701" while the final phones_match
        # check below prevents a loose SQL match from resolving the wrong row.
        from .customer_context import validate_phone_number

        validate_phone_number(context.phone_number)
        phone_digits = normalize_phone_digits(context.phone_number)
        phone_pattern = "%" + "%".join(phone_digits) + "%"
        response = (
            client.table("customer")
            .select("customer_id, company_id, full_name, phone_number, address")
            .eq("company_id", context.company_id)
            .ilike("phone_number", phone_pattern)
            .limit(10)
            .execute()
        )
        rows = response.data or []
        matched = next((row for row in rows if phones_match(row.get("phone_number", ""), context.phone_number)), None)
        if not matched:
            return _result("check_customer_by_phone", "not_found", {"phone_number": context.phone_number})

        context.resolved_customer_id = matched.get("customer_id")
        context.customer_record = matched
        return _result(
            "check_customer_by_phone",
            "success",
            {
                "customer_id": matched.get("customer_id"),
                "full_name": matched.get("full_name"),
                "phone_number": matched.get("phone_number"),
                "address": matched.get("address"),
            },
        )
    except Exception as exc:
        return _result("check_customer_by_phone", "error", {}, str(exc))


def resolve_customer_context(context: CustomerContext) -> CustomerContext:
    """Resolve customer_id from phone_number when available."""
    if context.resolved_customer_id is not None:
        return context

    if context.has_phone:
        lookup = check_customer_by_phone(context)
        if lookup["status"] == "success":
            context.resolved_customer_id = lookup["data"].get("customer_id")
            context.customer_record = lookup["data"]
    elif context.request_customer_id:
        try:
            context.resolved_customer_id = int(str(context.request_customer_id).strip())
        except (TypeError, ValueError):
            context.resolved_customer_id = None
    return context


def get_customer_pets(context: CustomerContext) -> dict:
    """Return pets registered to the resolved customer."""
    return get_pets_by_customer_id(context)


def get_booking_service_options(context: CustomerContext, service_type: str, pet_type: str = "") -> dict:
    """Return bookable main services from the company services catalogue."""
    category = str(service_type or "").strip().upper()
    if category not in {"GROOMING", "DAYCARE", "BOARDING"}:
        return _result(
            "get_booking_service_options",
            "missing_information",
            {"missing_fields": ["service_type"]},
        )
    try:
        if category == "BOARDING":
            room_rows = (
                get_supabase_client()
                .table("room")
                .select("room_id, room_type, capacity, price")
                .eq("company_id", context.company_id)
                .execute()
                .data
                or []
            )
            room_options = [
                {
                    "service_id": row.get("room_id"),
                    "service_name": row.get("room_type"),
                    "room_type": row.get("room_type"),
                    "capacity": row.get("capacity"),
                    "price": row.get("price"),
                    "price_display": (
                        f"RM{row.get('price')}/night"
                        if row.get("price") not in (None, "")
                        else None
                    ),
                }
                for row in room_rows
                if str(row.get("room_type") or "").strip()
            ]
            return _result(
                "get_booking_service_options",
                "success",
                {"service_type": category, "service_options": room_options},
            )

        # There is no relational "services" table backing GROOMING/DAYCARE
        # pricing in this deployment — those catalogues live only in RAG
        # (company_documents/chunks_bge_large), unlike BOARDING's real
        # "room" table above. Return an empty relational catalogue rather
        # than querying a table that doesn't exist; the caller
        # (app/tools/customer_tools.py get_booking_service_options) already
        # bundles the RAG detailed_pricing_by_size lookup for these two
        # categories, so that remains the real source of truth.
        return _result(
            "get_booking_service_options",
            "success",
            {"service_type": category, "service_options": []},
        )
    except Exception as exc:
        return _result("get_booking_service_options", "error", {}, str(exc))


def get_pets_by_customer_id(context: CustomerContext) -> dict:
    """Return all pet records for the resolved customer_id."""
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        if not context.has_phone:
            return missing_identity_result("get_pets_by_customer_id")
        return _result("get_pets_by_customer_id", "not_found", {"phone_number": context.phone_number})

    try:
        client = get_supabase_client()
        response = (
            client.table("pet")
            .select(
                "pet_id, customer_id, pet_name, pet_type, breed, size, height_cm, gender, "
                "vaccination_status, vaccination_expired_date, health_notes, service_notes"
            )
            .eq("company_id", context.company_id)
            .eq("customer_id", context.resolved_customer_id)
            .execute()
        )
        pets = response.data or []
        return _result(
            "get_pets_by_customer_id",
            "success",
            {
                "customer_id": context.resolved_customer_id,
                "pet_count": len(pets),
                "pets": pets,
            },
        )
    except Exception as exc:
        return _result("get_pets_by_customer_id", "error", {}, str(exc))


def get_pet_by_customer_and_name(context: CustomerContext, pet_name: str) -> dict:
    """Return one pet row for the customer matched by case-insensitive pet name."""
    pets_result = get_pets_by_customer_id(context)
    if pets_result.get("status") != "success":
        return _result("get_pet_by_customer_and_name", pets_result.get("status", "error"), {}, pets_result.get("error"))

    target = str(pet_name or "").strip().lower()
    if not target:
        return _result("get_pet_by_customer_and_name", "not_found", {"pet_name": pet_name})

    for pet in pets_result.get("data", {}).get("pets") or []:
        if str(pet.get("pet_name") or "").strip().lower() == target:
            return _result(
                "get_pet_by_customer_and_name",
                "success",
                {"pet": pet, "customer_id": pets_result.get("data", {}).get("customer_id")},
            )
    return _result("get_pet_by_customer_and_name", "not_found", {"pet_name": pet_name})


def check_loyalty_points(context: CustomerContext) -> dict:
    """Return loyalty member balance/tier for the resolved customer."""
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        if not context.has_phone:
            return missing_identity_result("check_loyalty_points")
        return _result("check_loyalty_points", "not_found", {"phone_number": context.phone_number})

    try:
        client = get_supabase_client()
        response = (
            client.table("loyaltymember")
            .select("loyalty_id, customer_id, points_balance, tier, redemption_made")
            .eq("company_id", context.company_id)
            .eq("customer_id", context.resolved_customer_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return _result(
                "check_loyalty_points",
                "not_found",
                {"customer_id": context.resolved_customer_id},
            )

        row = rows[0]
        return _result(
            "check_loyalty_points",
            "success",
            {
                "customer_id": context.resolved_customer_id,
                "loyalty_id": row.get("loyalty_id"),
                "loyalty_points": row.get("points_balance"),
                "points_balance": row.get("points_balance"),
                "membership_status": row.get("tier"),
                "tier": row.get("tier"),
                "redemption_made": row.get("redemption_made"),
            },
        )
    except Exception as exc:
        return _result("check_loyalty_points", "error", {}, str(exc))


def _serialize_grooming_booking(row: dict) -> dict:
    package_name = str(row.get("service_name") or "").strip()
    return {
        "booking_id": row.get("grooming_booking_id"),
        "service_type": "GROOMING",
        "booking_date": row.get("booking_date"),
        "booking_time": row.get("booking_time"),
        "booking_status": row.get("booking_status"),
        "service_name": "Grooming",
        "package_name": package_name,
        "pet_id": row.get("pet_id"),
        "staff_id": row.get("staff_id"),
        "price": row.get("price"),
        "add_on": row.get("add_on"),
        "add_on_price": row.get("add_on_price"),
        "payment_id": row.get("payment_id"),
    }


def _serialize_daycare_booking(row: dict) -> dict:
    package_name = str(row.get("package_type") or "").strip()
    return {
        "booking_id": row.get("daycare_booking_id"),
        "service_type": "DAYCARE",
        "booking_date": row.get("booking_date"),
        "booking_time": row.get("check_in_time"),
        "check_in_time": row.get("check_in_time"),
        "check_out_time": row.get("check_out_time"),
        "booking_status": row.get("booking_status"),
        "service_name": "Daycare",
        "package_name": package_name,
        "pet_id": row.get("pet_id"),
        "staff_id": row.get("staff_id"),
        "price": row.get("price"),
        "add_on": row.get("add_on"),
        "add_on_price": row.get("add_on_price"),
        "payment_id": row.get("payment_id"),
    }


def _serialize_boarding_booking(row: dict) -> dict:
    package_name = str(row.get("room_type") or "").strip()
    return {
        "booking_id": row.get("boarding_booking_id"),
        "service_type": "BOARDING",
        "booking_date": row.get("check_in_date"),
        "check_in_date": row.get("check_in_date"),
        "check_in_time": row.get("check_in_time"),
        "check_out_date": row.get("check_out_date"),
        "check_out_time": row.get("check_out_time"),
        "booking_status": row.get("booking_status"),
        "service_name": "Boarding",
        "package_name": package_name,
        "pet_id": row.get("pet_id"),
        "staff_id": row.get("staff_id"),
        "room_type": row.get("room_type"),
        "price_per_night": row.get("price_per_night"),
        "total_price": row.get("total_price"),
        "payment_id": row.get("payment_id"),
    }


def _display_label_for_booking(booking: dict) -> str:
    package_name = str(booking.get("package_name") or "").strip()
    service_name = str(booking.get("service_name") or "").strip()
    service_type = str(booking.get("service_type") or "").strip().upper()
    if package_name:
        return package_name
    if service_type == "DAYCARE":
        return "daycare"
    if service_type == "BOARDING":
        return "boarding"
    if service_name:
        return service_name
    if service_type and service_type != "UNKNOWN":
        return service_type.replace("_", " ").title()
    return "your previous service"


def _fetch_bookings_for_customer(client, company_id: int, pet_ids: list[int], service_type: str) -> list[dict]:
    if not pet_ids:
        return []

    table = _service_table(service_type)
    response = (
        client.table(table)
        .select("*")
        .eq("company_id", company_id)
        .in_("pet_id", pet_ids)
        .execute()
    )
    return response.data or []


def _pet_name_map(client, company_id: int, pet_ids: list[int]) -> dict[int, str]:
    if not pet_ids:
        return {}
    response = (
        client.table("pet")
        .select("pet_id, pet_name")
        .eq("company_id", company_id)
        .in_("pet_id", pet_ids)
        .execute()
    )
    return {
        row.get("pet_id"): str(row.get("pet_name") or "").strip()
        for row in (response.data or [])
        if row.get("pet_id") is not None
    }


def _serialize_booking_row(table: str, row: dict) -> dict:
    if table == "grooming_booking":
        return {**_serialize_grooming_booking(row), "source_table": table}
    if table == "daycare_booking":
        return {**_serialize_daycare_booking(row), "source_table": table}
    return {**_serialize_boarding_booking(row), "source_table": table}


def _booking_sort_key(booking: dict) -> str:
    return f"{booking.get('booking_date') or ''} {booking.get('booking_time') or booking.get('check_in_time') or ''}"


def _collect_latest_customer_booking(context: CustomerContext, client) -> dict | None:
    pets_result = get_customer_pets(context)
    pet_ids = [pet["pet_id"] for pet in pets_result.get("data", {}).get("pets", [])]
    if not pet_ids:
        return None

    pet_names = _pet_name_map(client, context.company_id, pet_ids)
    bookings: list[dict] = []
    for service_type in SERVICE_TYPE_TO_BOOKING_TABLE:
        table = _service_table(service_type)
        for row in _fetch_bookings_for_customer(client, context.company_id, pet_ids, service_type):
            if not is_qualifying_previous_booking_status(row.get("booking_status")):
                continue
            booking = _serialize_booking_row(table, row)
            booking["pet_name"] = pet_names.get(booking.get("pet_id"), "")
            bookings.append(booking)

    if not bookings:
        return None

    bookings.sort(key=_booking_sort_key, reverse=True)
    latest = bookings[0]
    latest["display_label"] = _display_label_for_booking(latest)
    return latest


def _collect_last_completed_customer_booking(context: CustomerContext, client) -> dict | None:
    """Most recent real past visit, independent of any newer future booking."""
    pets_result = get_customer_pets(context)
    pet_ids = [pet["pet_id"] for pet in pets_result.get("data", {}).get("pets", [])]
    if not pet_ids:
        return None

    pet_names = _pet_name_map(client, context.company_id, pet_ids)
    today = today_business()
    completed: list[dict] = []
    for service_type in SERVICE_TYPE_TO_BOOKING_TABLE:
        table = _service_table(service_type)
        for row in _fetch_bookings_for_customer(client, context.company_id, pet_ids, service_type):
            if not is_qualifying_previous_booking_status(row.get("booking_status")):
                continue
            booking = _serialize_booking_row(table, row)
            raw_date = booking.get("booking_date")
            try:
                booking_date = date.fromisoformat(str(raw_date))
            except (TypeError, ValueError):
                continue
            if booking_date >= today:
                continue
            booking["pet_name"] = pet_names.get(booking.get("pet_id"), "")
            completed.append(booking)

    if not completed:
        return None
    completed.sort(key=_booking_sort_key, reverse=True)
    latest = completed[0]
    latest["display_label"] = _display_label_for_booking(latest)
    return latest


def _active_bookings_for_customer(context: CustomerContext) -> list[dict]:
    """
    All of a customer's currently Scheduled/Pending bookings across every
    service type — used to detect when "cancel my booking"/"reschedule my
    booking" (no booking_id given) is genuinely ambiguous. Without this,
    cancel_booking/reschedule_booking silently fall back to whichever
    booking happens to be "latest" (get_latest_booking_by_customer_id),
    even when the customer actually has several active bookings and never
    said which one they meant.
    """
    pets_result = get_customer_pets(context)
    pet_ids = [pet["pet_id"] for pet in pets_result.get("data", {}).get("pets", [])]
    if not pet_ids:
        return []

    client = get_supabase_client()
    pet_names = _pet_name_map(client, context.company_id, pet_ids)
    bookings: list[dict] = []
    for service_type in SERVICE_TYPE_TO_BOOKING_TABLE:
        table = _service_table(service_type)
        for row in _fetch_bookings_for_customer(client, context.company_id, pet_ids, service_type):
            if _normalize_booking_status(row.get("booking_status")) not in {"scheduled", "pending"}:
                continue
            booking = _serialize_booking_row(table, row)
            booking["pet_name"] = pet_names.get(booking.get("pet_id"), "")
            bookings.append(booking)
    bookings.sort(key=_booking_sort_key)
    return bookings


def _serialize_latest_booking_payload(latest: dict, customer_id: int | None) -> dict:
    service_type = str(latest.get("service_type") or "UNKNOWN")
    return {
        "booking_id": latest.get("booking_id"),
        "customer_id": customer_id,
        "pet_id": latest.get("pet_id"),
        "pet_name": latest.get("pet_name") or "",
        "service_id": None,
        "service_name": latest.get("service_name") or service_type.replace("_", " ").title(),
        "package_id": None,
        "package_name": latest.get("package_name") or latest.get("display_label") or "",
        "selected_variant": latest.get("package_name") or latest.get("room_type") or "",
        "last_service_type": service_type,
        "last_booking_date": latest.get("booking_date"),
        "last_booking_time": latest.get("booking_time") or latest.get("check_in_time"),
        "booking_date": latest.get("booking_date"),
        "booking_status": latest.get("booking_status"),
        "source_table": latest.get("source_table"),
        "display_label": latest.get("display_label") or _display_label_for_booking(latest),
    }


def get_latest_booking_by_customer_id(context: CustomerContext) -> dict:
    """Read-only lookup of the most recent qualifying booking for a customer."""
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        if not context.has_phone:
            return missing_identity_result("get_latest_booking_by_customer_id")
        return _result(
            "get_latest_booking_by_customer_id",
            "not_found",
            {"phone_number": context.phone_number},
        )

    try:
        client = get_supabase_client()
        latest = _collect_latest_customer_booking(context, client)
        if not latest:
            return _result(
                "get_latest_booking_by_customer_id",
                "not_found",
                {"customer_id": context.resolved_customer_id},
            )
        return _result(
            "get_latest_booking_by_customer_id",
            "success",
            _serialize_latest_booking_payload(latest, context.resolved_customer_id),
        )
    except Exception as exc:
        return _result("get_latest_booking_by_customer_id", "error", {}, str(exc))


def get_last_completed_booking_by_customer_id(context: CustomerContext) -> dict:
    """Return the latest past visit even when a newer upcoming booking exists."""
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return _result("get_last_completed_booking_by_customer_id", "not_found", {})
    try:
        latest = _collect_last_completed_customer_booking(context, get_supabase_client())
        if not latest:
            return _result(
                "get_last_completed_booking_by_customer_id",
                "not_found",
                {"customer_id": context.resolved_customer_id},
            )
        return _result(
            "get_last_completed_booking_by_customer_id",
            "success",
            _serialize_latest_booking_payload(latest, context.resolved_customer_id),
        )
    except Exception as exc:
        return _result("get_last_completed_booking_by_customer_id", "error", {}, str(exc))


def check_last_booking(context: CustomerContext) -> dict:
    """Return the customer's most recent booking across grooming, daycare, and boarding."""
    result = get_latest_booking_by_customer_id(context)
    if result.get("action") == "get_latest_booking_by_customer_id":
        result = {**result, "action": "check_last_booking"}
    return result


def check_booking_status(context: CustomerContext, intent_json: dict) -> dict:
    """Return booking status by booking_id or latest bookings for the customer."""
    resolve_customer_context(context)
    entities = intent_json.get("entities") or {}
    service_type = str(intent_json.get("service_type") or "UNKNOWN").strip().upper()
    booking_id_raw = str(entities.get("booking_id") or "").strip()

    try:
        client = get_supabase_client()

        if booking_id_raw:
            booking_id = int(booking_id_raw)
            table = _service_table(service_type if service_type in SERVICE_TYPE_TO_BOOKING_TABLE else "GROOMING")
            id_column = {
                "grooming_booking": "grooming_booking_id",
                "daycare_booking": "daycare_booking_id",
                "boarding_booking": "boarding_booking_id",
            }[table]
            response = (
                client.table(table)
                .select("*")
                .eq("company_id", context.company_id)
                .eq(id_column, booking_id)
                .limit(1)
                .execute()
            )
            rows = response.data or []
            if not rows:
                return _result("check_booking_status", "not_found", {"booking_id": booking_id})

            row = rows[0]
            serializer = {
                "grooming_booking": _serialize_grooming_booking,
                "daycare_booking": _serialize_daycare_booking,
                "boarding_booking": _serialize_boarding_booking,
            }[table]
            booking = serializer(row)
            if context.resolved_customer_id is None:
                return missing_identity_result("check_booking_status")
            if not _verify_booking_belongs_to_customer(context, booking):
                return _result(
                    "check_booking_status",
                    "not_found",
                    {"booking_id": booking_id},
                    "Booking does not belong to this customer",
                )
            return _result("check_booking_status", "success", booking)

        if context.resolved_customer_id is None:
            if not context.has_phone:
                return missing_identity_result("check_booking_status")
            return _result("check_booking_status", "not_found", {"phone_number": context.phone_number})

        pets_result = get_customer_pets(context)
        pet_ids = [pet["pet_id"] for pet in pets_result.get("data", {}).get("pets", [])]
        service_types = (
            [service_type]
            if service_type in SERVICE_TYPE_TO_BOOKING_TABLE
            else list(SERVICE_TYPE_TO_BOOKING_TABLE.keys())
        )

        bookings: list[dict] = []
        for svc in service_types:
            table = _service_table(svc)
            for row in _fetch_bookings_for_customer(client, context.company_id, pet_ids, svc):
                if table == "grooming_booking":
                    bookings.append(_serialize_grooming_booking(row))
                elif table == "daycare_booking":
                    bookings.append(_serialize_daycare_booking(row))
                else:
                    bookings.append(_serialize_boarding_booking(row))

        if not bookings:
            return _result(
                "check_booking_status",
                "not_found",
                {"customer_id": context.resolved_customer_id},
            )

        bookings.sort(key=lambda item: str(item.get("booking_date") or ""), reverse=True)
        latest = bookings[0]
        return _result(
            "check_booking_status",
            "success",
            {
                **latest,
                "recent_bookings": bookings[:5],
            },
        )
    except Exception as exc:
        return _result("check_booking_status", "error", {}, str(exc))


def _parse_date(value: str | None) -> date | None:
    from .date_normalization import parse_customer_date

    return parse_customer_date(value)


def _business_hours_for_date(company_id: int, target_date: date) -> tuple[str | None, str | None, str | None]:
    """
    Real per-day operating hours for target_date, from company_business_hours
    and company_closed_dates.

    Returns (open_time, close_time, closed_reason). closed_reason is set
    (open/close then None) when the business is shut all day — an explicit
    closed_dates row takes priority over the day-of-week row, since it's the
    more specific override (e.g. a public holiday on an otherwise-open day).
    Missing or invalid hours are a configuration error. Booking decisions
    must never silently fall back to invented hours.
    """
    client = get_supabase_client()

    closed_rows = (
        client.table("company_closed_dates")
        .select("reason")
        .eq("company_id", company_id)
        .eq("closed_date", target_date.isoformat())
        .execute()
        .data
        or []
    )
    if closed_rows:
        return None, None, str(closed_rows[0].get("reason") or "Closed")

    # Postgres-style day_of_week convention used by this table: 0=Sunday..6=Saturday.
    db_day_of_week = (target_date.weekday() + 1) % 7
    hours_rows = (
        client.table("company_business_hours")
        .select("open_time, close_time, is_closed")
        .eq("company_id", company_id)
        .eq("day_of_week", db_day_of_week)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not hours_rows:
        return (
            None,
            None,
            f"{BUSINESS_HOURS_CONFIG_ERROR}: no operating-hours row for "
            f"{target_date.isoformat()} ({target_date.strftime('%A')})",
        )

    row = hours_rows[0]
    if row.get("is_closed"):
        return None, None, "Closed"
    open_time = _normalize_time_value(str(row.get("open_time") or ""))
    close_time = _normalize_time_value(str(row.get("close_time") or ""))
    open_minutes = _time_to_minutes(open_time)
    close_minutes = _time_to_minutes(close_time)
    if open_minutes is None or close_minutes is None or close_minutes <= open_minutes:
        return (
            None,
            None,
            f"{BUSINESS_HOURS_CONFIG_ERROR}: invalid operating hours for "
            f"{target_date.isoformat()} ({open_time or 'missing'}-{close_time or 'missing'})",
        )
    return open_time, close_time, None


def _time_slots_for_day(open_time: str, close_time: str) -> list[str]:
    start_minutes = _time_to_minutes(open_time)
    end_minutes = _time_to_minutes(close_time)
    if start_minutes is None or end_minutes is None or end_minutes <= start_minutes:
        return []
    slots: list[str] = []
    cursor = start_minutes
    while cursor < end_minutes:
        slots.append(f"{cursor // 60:02d}:{cursor % 60:02d}:00")
        cursor += SLOT_MINUTES
    return slots


def _staff_available_on_date(staff_row: dict, target_date: date, leave_rows: list[dict]) -> bool:
    if str(staff_row.get("status", "")).strip().lower() != "active":
        return False

    weekday = target_date.strftime("%A")
    off_days = staff_row.get("off_days_json") or []
    if isinstance(off_days, list) and weekday in off_days:
        return False

    staff_id = staff_row.get("staff_id")
    for leave in leave_rows:
        if leave.get("staff_id") != staff_id:
            continue
        if str(leave.get("status", "")).strip().lower() != "approved":
            continue
        start = _parse_date(leave.get("start_date"))
        end = _parse_date(leave.get("end_date"))
        if start and end and start <= target_date <= end:
            return False
    return True


def _time_to_minutes(value: object) -> int | None:
    text = _normalize_time_value(str(value or ""))
    try:
        parsed = datetime.strptime(text, "%H:%M")
    except ValueError:
        return None
    return parsed.hour * 60 + parsed.minute


def _booking_interval(row: dict, service_type: str, date_str: str | None = None) -> list[tuple[int, int]]:
    """
    Return the occupied [start, end) interval(s) for one booking row.

    BOARDING is the one service whose two staff-occupying events (check-in,
    check-out) can land on different calendar dates — everything else here
    is a single same-day event. date_str (the day currently being checked)
    gates which of those events actually apply: a boarding booking that
    checks in on Monday and checks out on Wednesday must occupy staff time
    on BOTH days individually, never on Tuesday, and never both events at
    once when only one of them falls on date_str. Every real caller must
    pass date_str for BOARDING rows to be handled correctly; a caller that
    doesn't (date_str=None) gets both events unconditionally, which is only
    correct when the check-in/check-out rows it was given are already
    known to belong to that one date (kept as a narrow default rather than
    a required argument only because a couple of internal helpers below
    build single-day interval lists where that's already guaranteed).
    """
    service = str(service_type or "").strip().upper()
    if service == "BOARDING":
        from .availability_service import service_duration_minutes

        width = service_duration_minutes("BOARDING")
        intervals: list[tuple[int, int]] = []
        if date_str is None or str(row.get("check_in_date") or "") == date_str:
            start = _time_to_minutes(row.get("check_in_time"))
            if start is not None:
                intervals.append((start, start + width))
        if date_str is None or str(row.get("check_out_date") or "") == date_str:
            start = _time_to_minutes(row.get("check_out_time"))
            if start is not None:
                intervals.append((start, start + width))
        return intervals
    start = _time_to_minutes(
        row.get("booking_time") if service == "GROOMING" else row.get("check_in_time")
    )
    if start is None:
        return []
    if service == "DAYCARE":
        end = _time_to_minutes(row.get("check_out_time"))
        if end is not None and end > start:
            return [(start, end)]
        return [(start, start + 180)]
    if service == "GROOMING":
        return [(start, start + 90)]
    return [(start, start + 60)]


def _booking_blocks_availability(row: dict) -> bool:
    status = _normalize_booking_status(row.get("booking_status"))
    return status not in {
        "cancelled",
        "canceled",
        "done",
        "completed",
        "no show",
        "no-show",
        "rejected",
        "deleted",
        "failed",
    }


def _cross_service_staff_bookings(client, company_id: int, staff_ids: list[int], date_str: str) -> list[dict]:
    """
    A staff member's real occupied intervals on date_str, ACROSS all three
    service tables — not just the one currently being checked. Availability
    used to be checked against only the current service's own table (e.g. a
    DAYCARE check never looked at that same staff member's GROOMING
    bookings that day), so the same staff could be shown as "available" for
    two different services at an overlapping time. Each row is tagged with
    its real service type so _booking_interval computes the right duration
    for it regardless of which service is being checked right now.
    """
    if not staff_ids:
        return []

    def _fetch_one(service_type: str, table: str, date_column: str) -> list[dict]:
        rows = (
            client.table(table)
            .select("*")
            .eq("company_id", company_id)
            .eq(date_column, date_str)
            .in_("staff_id", staff_ids)
            .execute()
            .data
            or []
        )
        return [{**row, "_service_type": service_type} for row in rows]

    def _fetch_boarding() -> list[dict]:
        # A boarding booking's check-in and check-out events can land on
        # different calendar dates — fetching by check_in_date alone (the
        # old behavior) made an existing stay's checkout commitment
        # completely invisible to any availability check run for that
        # later checkout date. Fetch both and dedup by real row id (a stay
        # that both checks in AND out on date_str, or one whose window
        # happens to satisfy both queries, must not be double-counted).
        checkin_rows = _fetch_one("BOARDING", "boarding_booking", "check_in_date")
        checkout_rows = (
            client.table("boarding_booking")
            .select("*")
            .eq("company_id", company_id)
            .eq("check_out_date", date_str)
            .in_("staff_id", staff_ids)
            .execute()
            .data
            or []
        )
        by_id = {row["boarding_booking_id"]: row for row in checkin_rows}
        for row in checkout_rows:
            by_id.setdefault(row["boarding_booking_id"], {**row, "_service_type": "BOARDING"})
        return list(by_id.values())

    combined: list[dict] = []
    # Keep these small Supabase reads sequential. They all share the cached
    # synchronous client; running them in nested thread pools intermittently
    # exhausted resolver/socket resources (Errno 35 / EAGAIN) and turned a
    # healthy availability request into a false staff handoff.
    combined.extend(_fetch_one("GROOMING", "grooming_booking", "booking_date"))
    combined.extend(_fetch_one("DAYCARE", "daycare_booking", "booking_date"))
    combined.extend(_fetch_boarding())
    return combined


def _cross_service_pet_bookings(client, company_id: int, pet_id: int | None) -> list[dict]:
    """Active-candidate rows for one pet across every booking table.

    Staff availability alone cannot prevent the same pet from being booked
    with two different staff members.  The final SQL RPC enforces this too;
    this read-side copy prevents the LLM from offering such a slot first.
    """
    if pet_id is None:
        return []
    combined: list[dict] = []
    for service_type, table in (
        ("GROOMING", "grooming_booking"),
        ("DAYCARE", "daycare_booking"),
        ("BOARDING", "boarding_booking"),
    ):
        rows = (
            client.table(table)
            .select("*")
            .eq("company_id", company_id)
            .eq("pet_id", pet_id)
            .execute()
            .data
            or []
        )
        combined.extend({**row, "_service_type": service_type} for row in rows)
    return combined


def _pet_free_for_interval(
    pet_bookings: list[dict], date_str: str, start: int, end: int,
    *, exclude_booking_id: int | None = None,
) -> bool:
    for row in pet_bookings:
        if not _booking_blocks_availability(row):
            continue
        service_type = str(row.get("_service_type") or "").upper()
        row_id = row.get(
            {
                "GROOMING": "grooming_booking_id",
                "DAYCARE": "daycare_booking_id",
                "BOARDING": "boarding_booking_id",
            }.get(service_type, "")
        )
        if exclude_booking_id is not None and row_id == exclude_booking_id:
            continue
        if service_type == "BOARDING":
            check_in = str(row.get("check_in_date") or "")
            check_out = str(row.get("check_out_date") or "")
            # A boarded pet is occupied for its full stay, not only during the
            # short staff check-in/check-out events.
            if check_in and check_out and check_in <= date_str < check_out:
                return False
        elif str(row.get("booking_date") or "") == date_str:
            for booked_start, booked_end in _booking_interval(row, service_type, date_str):
                if start < booked_end and booked_start < end:
                    return False
    return True


def _pet_free_for_boarding_stay(
    pet_bookings: list[dict], check_in: date, check_out: date,
    *, exclude_booking_id: int | None = None,
) -> bool:
    for row in pet_bookings:
        if not _booking_blocks_availability(row):
            continue
        service_type = str(row.get("_service_type") or "").upper()
        row_id = row.get(
            {
                "GROOMING": "grooming_booking_id",
                "DAYCARE": "daycare_booking_id",
                "BOARDING": "boarding_booking_id",
            }.get(service_type, "")
        )
        if exclude_booking_id is not None and row_id == exclude_booking_id:
            continue
        if service_type == "BOARDING":
            existing_in = _parse_date(row.get("check_in_date"))
            existing_out = _parse_date(row.get("check_out_date"))
            if existing_in and existing_out and existing_in < check_out and check_in < existing_out:
                return False
        else:
            booked_date = _parse_date(row.get("booking_date"))
            if booked_date and check_in <= booked_date <= check_out:
                return False
    return True


def _staff_free_for_interval(
    start: int, end: int, available_staff: list[dict], bookings: list[dict], date_str: str | None = None
) -> list[dict]:
    """available_staff rows with no booking (of ANY service type) overlapping [start, end)
    on date_str (see _booking_interval for why date_str matters — it's what
    correctly scopes a BOARDING row's check-in/check-out events to the
    right individual day instead of always applying both)."""
    bookings_by_staff: dict[int, list[tuple[int, int]]] = {}
    for row in bookings:
        if not _booking_blocks_availability(row):
            continue
        raw_staff_id = row.get("staff_id")
        if raw_staff_id is None:
            continue
        intervals = _booking_interval(row, row.get("_service_type") or row.get("service_type") or "", date_str)
        if not intervals:
            continue
        bookings_by_staff.setdefault(int(raw_staff_id), []).extend(intervals)
    free: list[dict] = []
    for staff in available_staff:
        raw_staff_id = staff.get("staff_id")
        if raw_staff_id is None:
            continue
        intervals = bookings_by_staff.get(int(raw_staff_id), [])
        if all(end <= booked_start or start >= booked_end for booked_start, booked_end in intervals):
            free.append(staff)
    return free


def _slot_has_available_staff(
    slot: str,
    *,
    duration_minutes: int,
    available_staff: list[dict],
    bookings: list[dict],
    service_type: str,
    date_str: str | None = None,
) -> bool:
    del service_type  # bookings now carry their own _service_type per row
    start = _time_to_minutes(slot)
    if start is None:
        return False
    end = start + duration_minutes
    return bool(_staff_free_for_interval(start, end, available_staff, bookings, date_str))


def _staff_day_roster(
    client, company_id: int, target_date: date, service_type: str | None = None
) -> list[dict]:
    """Active staff not on leave/off-day for target_date, optionally narrowed
    to staff actually qualified for service_type — the same day-level
    roster check_available_slots and create_booking's staff assignment must
    agree on, factored out once instead of duplicated.

    provides_service/service_types_json (see backend/sql/
    staff_service_capability_migration.sql) default true / all three
    services for any row that predates that migration, so this stays a
    no-op filter until a company actually narrows a staff member's real
    capabilities."""
    staff_rows = (
        client.table("staff")
        .select("staff_id, staff_name, status, off_days_json, role, provides_service, service_types_json")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    leave_rows = (
        client.table("leave")
        .select("staff_id, start_date, end_date, status")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    roster = [row for row in staff_rows if _staff_available_on_date(row, target_date, leave_rows)]
    if service_type:
        svc = str(service_type).strip().upper()
        roster = [
            row
            for row in roster
            if row.get("provides_service", True) and svc in (row.get("service_types_json") or [])
        ]
    return roster


def _match_preferred_staff(preferred: str, staff_list: list[dict]) -> dict | None:
    """Match a customer's stated staff preference (name or numeric staff_id)
    against a list of real staff rows — exact id, exact name, then loose
    substring name match, in that order."""
    preferred = str(preferred or "").strip()
    if not preferred:
        return None
    if preferred.isdigit():
        for row in staff_list:
            if str(row.get("staff_id")) == preferred:
                return row
        return None
    lowered = preferred.lower()
    for row in staff_list:
        if str(row.get("staff_name") or "").strip().lower() == lowered:
            return row
    for row in staff_list:
        name = str(row.get("staff_name") or "").strip().lower()
        if name and (name in lowered or lowered in name):
            return row
    return None


def _shared_booking_holds(client, company_id: int) -> list[dict] | None:
    """Active DB-backed holds, or None when the migration is not installed."""
    try:
        return (
            client.table("booking_slot_hold")
            .select(
                "resource_kind,service_type,holder_key,slot_date,slot_time,"
                "room_type,check_in_date,check_out_date,expires_at"
            )
            .eq("company_id", company_id)
            .gt("expires_at", datetime.now(timezone.utc).isoformat())
            .execute()
            .data
            or []
        )
    except Exception as exc:
        text = str(exc)
        if "PGRST205" in text or "booking_slot_hold" in text:
            return None
        raise


def _shared_slot_held_by_other(
    holds: list[dict], service_type: str, date_str: str, slot: str, holder: str | None
) -> bool:
    normalized_slot = _normalize_time_value(slot)
    return any(
        str(row.get("resource_kind") or "").upper() == "SLOT"
        and str(row.get("service_type") or "").upper() == service_type
        and str(row.get("slot_date") or "") == date_str
        and _normalize_time_value(str(row.get("slot_time") or "")) == normalized_slot
        and str(row.get("holder_key") or "") != str(holder or "")
        for row in holds
    )


def _acquire_shared_hold(client, payload: dict) -> bool | None:
    try:
        data = client.rpc("acquire_booking_hold", payload).execute().data
        return bool(data)
    except Exception as exc:
        text = str(exc)
        if "PGRST202" in text or "acquire_booking_hold" in text or "booking_slot_hold" in text:
            return None
        raise


def _release_shared_holds(client, company_id: int, holder: str) -> None:
    try:
        (
            client.table("booking_slot_hold")
            .delete()
            .eq("company_id", company_id)
            .eq("holder_key", holder)
            .execute()
        )
    except Exception:
        # Compatibility only: create/update themselves never fall back from
        # their required atomic RPCs. An older deployment merely lacks the
        # optional shared-hold table and still releases its in-process hold.
        pass


def check_available_slots(context: CustomerContext, intent_json: dict) -> dict:
    """
    Estimate available slots from active staff, approved leave, and existing
    bookings, within the company's real operating hours for that specific
    date (company_business_hours + company_closed_dates — see
    _business_hours_for_date).
    """
    entities = intent_json.get("entities") or {}
    service_type = str(
        intent_json.get("service_type") or entities.get("service_type") or "GROOMING"
    ).strip().upper()
    if service_type not in SERVICE_TYPE_TO_BOOKING_TABLE:
        service_type = "GROOMING"
    if context.resolved_customer_id is None and entities.get("customer_id"):
        try:
            context.resolved_customer_id = int(entities["customer_id"])
        except (TypeError, ValueError):
            context.resolved_customer_id = None

    preferred_date = _parse_date(entities.get("preferred_date"))
    if preferred_date is None:
        return _result(
            "check_available_slots",
            "missing_information",
            {"missing_fields": ["preferred_date"]},
        )
    date_str = preferred_date.isoformat()

    room_type = str(entities.get("room_type") or entities.get("package_name") or "").strip()
    check_out_date_text = str(entities.get("check_out_date") or "").strip()
    requested_duration = entities.get("duration_minutes")
    requested_check_out_time = _normalize_time_value(str(entities.get("check_out_time") or ""))
    selection_target = str(entities.get("selection_target") or "CHECK_IN").strip().upper()
    fixed_check_in_time = _normalize_time_value(str(entities.get("check_in_time") or ""))
    choosing_check_out = selection_target == "CHECK_OUT"

    # A partial check must never be presented as a final choice. Boarding
    # availability depends on room capacity across the whole stay, while
    # daycare depends on the complete visit duration through pickup.
    missing_constraints: list[str] = []
    if choosing_check_out and service_type not in {"DAYCARE", "BOARDING"}:
        return _result(
            "check_available_slots",
            "error",
            {
                "service_type": service_type,
                "selection_target": selection_target,
                "available_check_out_times": [],
            },
            "CHECK_OUT choices are only supported for DAYCARE or BOARDING.",
        )
    if choosing_check_out and not fixed_check_in_time:
        missing_constraints.append("check_in_time")
    if service_type == "BOARDING":
        if not room_type:
            missing_constraints.append("room_type")
        if not check_out_date_text:
            missing_constraints.append("check_out_date")
    elif (
        service_type == "DAYCARE"
        and not choosing_check_out
        and requested_duration in (None, "")
        and not requested_check_out_time
    ):
        missing_constraints.append("check_out_time_or_duration_minutes")
    if missing_constraints:
        return _result(
            "check_available_slots",
            "missing_information",
            {
                "service_type": service_type,
                "booking_date": date_str,
                "missing_fields": missing_constraints,
                "available_slots": [],
                "available_check_out_times": [],
            },
            (
                "Complete the service details before offering times; no slot has been "
                "validated against all booking constraints yet."
            ),
        )

    parsed_check_out_date = _parse_date(check_out_date_text) if check_out_date_text else None
    if service_type == "BOARDING" and (
        parsed_check_out_date is None or parsed_check_out_date <= preferred_date
    ):
        return _result(
            "check_available_slots",
            "error",
            {
                "service_type": service_type,
                "booking_date": date_str,
                "check_out_date": check_out_date_text,
                "available_slots": [],
            },
            "Boarding check_out_date must be a valid date after check-in.",
        )

    try:
        open_time, close_time, closed_reason = _business_hours_for_date(context.company_id, preferred_date)
        if closed_reason:
            if closed_reason.startswith(BUSINESS_HOURS_CONFIG_ERROR):
                return _result(
                    "check_available_slots",
                    "error",
                    {"service_type": service_type, "booking_date": date_str},
                    closed_reason,
                    handoff_required=True,
                    handoff_reason=BUSINESS_HOURS_CONFIG_ERROR,
                )
            return _result(
                "check_available_slots",
                "success",
                {
                    "service_type": service_type,
                    "booking_date": date_str,
                    "available_slots": [],
                    "available_staff": [],
                    "closed_reason": closed_reason,
                },
            )

        client = get_supabase_client()
        shared_holds = _shared_booking_holds(client, context.company_id)
        available_staff = _staff_day_roster(client, context.company_id, preferred_date, service_type=service_type)
        preferred_staff = str(entities.get("preferred_staff") or "").strip()
        if preferred_staff:
            matched_staff = _match_preferred_staff(preferred_staff, available_staff)
            if matched_staff is None:
                return _result(
                    "check_available_slots",
                    "error",
                    {
                        "service_type": service_type,
                        "booking_date": date_str,
                        "preferred_staff": preferred_staff,
                        "available_slots": [],
                        "available_check_out_times": [],
                    },
                    (
                        "The requested staff member does not match an available, qualified "
                        "staff member on this date. Ask the customer whether they prefer a "
                        "different date or any available staff; do not silently substitute."
                    ),
                )
            available_staff = [matched_staff]
        if not available_staff:
            return _result(
                "check_available_slots",
                "success",
                {
                    "service_type": service_type,
                    "booking_date": date_str,
                    "available_slots": [],
                    "available_staff": [],
                },
            )

        staff_ids = [row["staff_id"] for row in available_staff]
        bookings = _cross_service_staff_bookings(client, context.company_id, staff_ids, date_str)
        pet_id_raw = str(entities.get("pet_id") or "").strip()
        pet_id = int(pet_id_raw) if pet_id_raw.isdigit() and int(pet_id_raw) > 0 else None
        pet_bookings = _cross_service_pet_bookings(
            client, context.company_id, pet_id
        )
        exclude_booking_id_raw = str(entities.get("exclude_booking_id") or "").strip()
        exclude_booking_id = (
            int(exclude_booking_id_raw)
            if exclude_booking_id_raw.isdigit() and int(exclude_booking_id_raw) > 0
            else None
        )

        from .availability_service import service_duration_minutes

        if choosing_check_out:
            checkin_minutes = _time_to_minutes(fixed_check_in_time)
            open_minutes = _time_to_minutes(open_time)
            close_minutes = _time_to_minutes(close_time)
            if (
                checkin_minutes is None
                or open_minutes is None
                or close_minutes is None
                or checkin_minutes < open_minutes
                or checkin_minutes >= close_minutes
            ):
                return _result(
                    "check_available_slots",
                    "error",
                    {
                        "service_type": service_type,
                        "selection_target": selection_target,
                        "check_in_time": fixed_check_in_time or None,
                        "available_check_out_times": [],
                    },
                    "check_in_time must be a valid time within operating hours.",
                )

            capacity_status = None
            if service_type == "DAYCARE":
                # Each endpoint is safe only if at least one qualified staff
                # member is free continuously from the fixed drop-off through
                # that pickup. A 17:30 pickup can therefore be offered even
                # when 17:30 would be too late to START a multi-hour visit.
                check_out_choices = [
                    candidate
                    for candidate in _time_slots_for_day(open_time, close_time)
                    if (
                        (_time_to_minutes(candidate) or 0) > checkin_minutes
                        and _staff_free_for_interval(
                            checkin_minutes,
                            _time_to_minutes(candidate) or 0,
                            available_staff,
                            bookings,
                            date_str=date_str,
                        )
                        and _pet_free_for_interval(
                            pet_bookings,
                            date_str,
                            checkin_minutes,
                            _time_to_minutes(candidate) or 0,
                            exclude_booking_id=exclude_booking_id,
                        )
                    )
                ]
                checkout_date_value = date_str
            else:
                checkout_date_value = parsed_check_out_date.isoformat()
                checkout_open, checkout_close, checkout_closed_reason = _business_hours_for_date(
                    context.company_id, parsed_check_out_date
                )
                checkout_open_minutes = _time_to_minutes(checkout_open)
                checkout_close_minutes = _time_to_minutes(checkout_close)
                if (
                    checkout_closed_reason
                    or checkout_open_minutes is None
                    or checkout_close_minutes is None
                ):
                    check_out_choices = []
                else:
                    checkout_roster = _staff_day_roster(
                        client,
                        context.company_id,
                        parsed_check_out_date,
                        service_type=service_type,
                    )
                    if preferred_staff:
                        matched_checkout_staff = _match_preferred_staff(
                            preferred_staff, checkout_roster
                        )
                        checkout_roster = (
                            [matched_checkout_staff]
                            if matched_checkout_staff is not None
                            else []
                        )
                    checkout_staff_ids = [
                        row["staff_id"]
                        for row in checkout_roster
                        if row.get("staff_id") is not None
                    ]
                    checkout_bookings = _cross_service_staff_bookings(
                        client,
                        context.company_id,
                        checkout_staff_ids,
                        checkout_date_value,
                    )
                    width = service_duration_minutes("BOARDING")
                    checkin_staff_ids = {
                        row.get("staff_id")
                        for row in _staff_free_for_interval(
                            checkin_minutes,
                            checkin_minutes + width,
                            available_staff,
                            bookings,
                            date_str=date_str,
                        )
                    }
                    check_out_choices = []
                    for candidate in _time_slots_for_day(checkout_open, checkout_close):
                        checkout_minutes = _time_to_minutes(candidate)
                        if (
                            checkout_minutes is None
                            or checkout_minutes + width > checkout_close_minutes
                        ):
                            continue
                        checkout_staff_ids_free = {
                            row.get("staff_id")
                            for row in _staff_free_for_interval(
                                checkout_minutes,
                                checkout_minutes + width,
                                checkout_roster,
                                checkout_bookings,
                                date_str=checkout_date_value,
                            )
                        }
                        if checkin_staff_ids & checkout_staff_ids_free:
                            check_out_choices.append(candidate)

                exclude_booking_id_raw = str(
                    entities.get("exclude_booking_id") or ""
                ).strip()
                exclude_booking_id = (
                    int(exclude_booking_id_raw)
                    if exclude_booking_id_raw.isdigit()
                    else None
                )
                if (
                    exclude_booking_id is None
                    and context.resolved_customer_id is not None
                ):
                    own_active = [
                        booking
                        for booking in _active_bookings_for_customer(context)
                        if booking.get("service_type") == "BOARDING"
                        and str(
                            booking.get("room_type")
                            or booking.get("package_name")
                            or ""
                        ).strip().lower() == room_type.lower()
                    ]
                    if len(own_active) == 1:
                        exclude_booking_id = own_active[0].get("booking_id")
                capacity_status = _room_capacity_status(
                    context,
                    room_type,
                    date_str,
                    checkout_date_value,
                    exclude_booking_id=exclude_booking_id,
                )
                if capacity_status is None:
                    return _result(
                        "check_available_slots",
                        "error",
                        {
                            "service_type": service_type,
                            "room_type": room_type,
                            "available_check_out_times": [],
                        },
                        (
                            f"room_type {room_type!r} does not exactly match a configured room. "
                            "Use the exact room_type returned by get_booking_service_options."
                        ),
                    )
                if not capacity_status["available"]:
                    check_out_choices = []
                if not _pet_free_for_boarding_stay(
                    pet_bookings,
                    preferred_date,
                    parsed_check_out_date,
                    exclude_booking_id=exclude_booking_id,
                ):
                    check_out_choices = []

            holder = (
                str(context.resolved_customer_id)
                if context.resolved_customer_id is not None
                else None
            )
            if holder and SLOT_HOLDS.held_by_other(
                (context.company_id, service_type, date_str, fixed_check_in_time), holder
            ):
                check_out_choices = []
            if shared_holds is not None and _shared_slot_held_by_other(
                shared_holds, service_type, date_str, fixed_check_in_time, holder
            ):
                check_out_choices = []

            # A CHECK_OUT request may be the first availability call for this
            # interval.  Hold the already-selected start as soon as at least one
            # valid endpoint is returned, just as the CHECK_IN path does.
            if holder and check_out_choices:
                key = (context.company_id, service_type, date_str, fixed_check_in_time)
                SLOT_HOLDS.release_all_for(
                    holder, prefix=(context.company_id, service_type)
                )
                shared_acquired = _acquire_shared_hold(
                    client,
                    {
                        "p_company_id": context.company_id,
                        "p_resource_kind": "SLOT",
                        "p_service_type": service_type,
                        "p_holder_key": holder,
                        "p_slot_date": date_str,
                        "p_slot_time": fixed_check_in_time,
                        "p_room_type": None,
                        "p_check_in_date": None,
                        "p_check_out_date": None,
                        "p_capacity": 1,
                        "p_exclude_booking_id": exclude_booking_id,
                    },
                )
                if shared_acquired is False or not SLOT_HOLDS.acquire(key, holder):
                    check_out_choices = []

            result_data = {
                "service_type": service_type,
                "selection_target": selection_target,
                "booking_date": date_str,
                "check_in_time": fixed_check_in_time,
                "check_out_date": checkout_date_value,
                "available_slots": [],
                "available_check_out_times": check_out_choices,
                "available_staff": [
                    {"staff_id": row.get("staff_id"), "staff_name": row.get("staff_name")}
                    for row in available_staff
                ],
                "room_type": room_type or None,
                "preferred_staff": preferred_staff or None,
            }
            if capacity_status is not None:
                result_data["room_capacity"] = capacity_status
            return _result("check_available_slots", "success", result_data)

        duration_minutes = service_duration_minutes(service_type)
        if service_type == "DAYCARE":
            requested_start = _time_to_minutes(entities.get("preferred_time"))
            requested_end = _time_to_minutes(entities.get("check_out_time"))
            if requested_start is not None and requested_end is not None and requested_end > requested_start:
                duration_minutes = requested_end - requested_start
            elif requested_duration is not None:
                try:
                    parsed_duration = int(requested_duration)
                except (TypeError, ValueError):
                    return _result(
                        "check_available_slots",
                        "error",
                        {"duration_minutes": requested_duration},
                        "duration_minutes must be a positive number of minutes",
                    )
                if not 0 < parsed_duration <= 24 * 60:
                    return _result(
                        "check_available_slots",
                        "error",
                        {"duration_minutes": requested_duration},
                        "duration_minutes must be between 1 and 1440 minutes",
                    )
                duration_minutes = parsed_duration
        close_minutes = _time_to_minutes(close_time)
        if close_minutes is None or not open_time or not close_time:
            return _result(
                "check_available_slots",
                "error",
                {"service_type": service_type, "booking_date": date_str},
                f"{BUSINESS_HOURS_CONFIG_ERROR}: invalid hours returned for {date_str}",
                handoff_required=True,
                handoff_reason=BUSINESS_HOURS_CONFIG_ERROR,
            )
        all_slots = [
            slot
            for slot in _time_slots_for_day(open_time, close_time)
            if (_time_to_minutes(slot) or 0) + duration_minutes <= close_minutes
        ]
        free_slots = [
            slot
            for slot in all_slots
            if _slot_has_available_staff(
                slot,
                duration_minutes=duration_minutes,
                available_staff=available_staff,
                bookings=bookings,
                service_type=service_type,
                date_str=date_str,
            )
            and _pet_free_for_interval(
                pet_bookings,
                date_str,
                _time_to_minutes(slot) or 0,
                (_time_to_minutes(slot) or 0) + duration_minutes,
                exclude_booking_id=exclude_booking_id,
            )
        ]

        # A boarding slot is valid only when the SAME qualified staff member
        # is free for both check-in and check-out, and the checkout event also
        # fits that day's operating hours. Previously only check-in was tested;
        # the create path could therefore reject a slot just shown as free.
        if service_type == "BOARDING" and parsed_check_out_date is not None:
            if not _pet_free_for_boarding_stay(
                pet_bookings,
                preferred_date,
                parsed_check_out_date,
                exclude_booking_id=exclude_booking_id,
            ):
                free_slots = []
            checkout_open, checkout_close, checkout_closed_reason = _business_hours_for_date(
                context.company_id, parsed_check_out_date
            )
            checkout_open_minutes = _time_to_minutes(checkout_open)
            checkout_close_minutes = _time_to_minutes(checkout_close)
            if checkout_closed_reason or checkout_open_minutes is None or checkout_close_minutes is None:
                free_slots = []
            else:
                if parsed_check_out_date == preferred_date:
                    checkout_roster = available_staff
                    checkout_bookings = bookings
                else:
                    checkout_roster = _staff_day_roster(
                        client,
                        context.company_id,
                        parsed_check_out_date,
                        service_type=service_type,
                    )
                    if preferred_staff:
                        matched_checkout_staff = _match_preferred_staff(preferred_staff, checkout_roster)
                        checkout_roster = [matched_checkout_staff] if matched_checkout_staff is not None else []
                    checkout_staff_ids = [
                        row["staff_id"] for row in checkout_roster if row.get("staff_id") is not None
                    ]
                    checkout_bookings = _cross_service_staff_bookings(
                        client,
                        context.company_id,
                        checkout_staff_ids,
                        parsed_check_out_date.isoformat(),
                    )

                boarding_width = service_duration_minutes("BOARDING")
                jointly_available_slots: list[str] = []
                for slot in free_slots:
                    checkin_start = _time_to_minutes(slot)
                    checkout_start = _time_to_minutes(requested_check_out_time or slot)
                    if checkin_start is None or checkout_start is None:
                        continue
                    if (
                        checkout_start < checkout_open_minutes
                        or checkout_start + boarding_width > checkout_close_minutes
                    ):
                        continue
                    checkin_ids = {
                        row.get("staff_id")
                        for row in _staff_free_for_interval(
                            checkin_start,
                            checkin_start + boarding_width,
                            available_staff,
                            bookings,
                            date_str=date_str,
                        )
                    }
                    checkout_ids = {
                        row.get("staff_id")
                        for row in _staff_free_for_interval(
                            checkout_start,
                            checkout_start + boarding_width,
                            checkout_roster,
                            checkout_bookings,
                            date_str=parsed_check_out_date.isoformat(),
                        )
                    }
                    if checkin_ids & checkout_ids:
                        jointly_available_slots.append(slot)
                free_slots = jointly_available_slots

        capacity_status = None
        if service_type == "BOARDING":
            # Validate room capacity before filtering/holding a staff slot.
            # Otherwise a full room can take a provisional staff hold even
            # though no time will ultimately be offered to that customer.
            if exclude_booking_id is None and context.resolved_customer_id is not None:
                # During rescheduling the model normally checks availability
                # before it knows to pass exclude_booking_id. Exclude the
                # customer's own stay only when exactly one active booking
                # matches the selected room, which keeps this inference
                # narrow and deterministic.
                own_active = [
                    booking
                    for booking in _active_bookings_for_customer(context)
                    if booking.get("service_type") == "BOARDING"
                    and str(
                        booking.get("room_type") or booking.get("package_name") or ""
                    ).strip().lower() == room_type.lower()
                ]
                if len(own_active) == 1:
                    exclude_booking_id = own_active[0].get("booking_id")
            capacity_status = _room_capacity_status(
                context,
                room_type,
                date_str,
                check_out_date_text,
                exclude_booking_id=exclude_booking_id,
            )
            if capacity_status is None:
                return _result(
                    "check_available_slots",
                    "error",
                    {
                        "service_type": service_type,
                        "room_type": room_type,
                        "available_slots": [],
                    },
                    (
                        f"room_type {room_type!r} does not exactly match a configured room. "
                        "Use the exact room_type returned by get_booking_service_options."
                    ),
                )
            elif not capacity_status["available"]:
                free_slots = []

        # A slot another customer is actively being asked to confirm right
        # now is provisionally unavailable to everyone else (see
        # app.context.slot_holds) — filter those out same as a real booking.
        # Keyed by resolved customer_id, not phone_number — this repo path
        # never resolves a phone (check_availability's CustomerContext is
        # built without one); entities.customer_id is threaded in from
        # state.customer_id by the orchestrator instead (see _run_tool).
        holder = str(context.resolved_customer_id) if context.resolved_customer_id is not None else None
        if holder:
            free_slots = [
                slot
                for slot in free_slots
                if not SLOT_HOLDS.held_by_other((context.company_id, service_type, date_str, slot), holder)
                and not (
                    shared_holds is not None
                    and _shared_slot_held_by_other(
                        shared_holds, service_type, date_str, slot, holder
                    )
                )
            ]

        entities = intent_json.get("entities") or {}
        from .time_normalization import normalize_time

        requested_time = normalize_time(
            str(entities.get("preferred_time") or intent_json.get("preferred_time") or "")
        )

        hold_info = None
        if holder:
            requested_minutes = _time_to_minutes(requested_time) if requested_time else None
            if requested_minutes is not None:
                for slot in free_slots:
                    if _time_to_minutes(slot) == requested_minutes:
                        key = (context.company_id, service_type, date_str, slot)
                        # A customer can only be deciding on one slot at a time —
                        # drop any earlier hold of theirs for this service before
                        # taking a new one, so holds don't pile up indefinitely.
                        SLOT_HOLDS.release_all_for(holder, prefix=(context.company_id, service_type))
                        shared_acquired = _acquire_shared_hold(
                            client,
                            {
                                "p_company_id": context.company_id,
                                "p_resource_kind": "SLOT",
                                "p_service_type": service_type,
                                "p_holder_key": holder,
                                "p_slot_date": date_str,
                                "p_slot_time": slot,
                                "p_room_type": None,
                                "p_check_in_date": None,
                                "p_check_out_date": None,
                                "p_capacity": 1,
                                "p_exclude_booking_id": exclude_booking_id,
                            },
                        )
                        if shared_acquired is not False and SLOT_HOLDS.acquire(key, holder):
                            hold_info = {"slot": slot, "hold_minutes": 15}
                        elif shared_acquired is False:
                            free_slots = [candidate for candidate in free_slots if candidate != slot]
                        break

        result_data = {
            "service_type": service_type,
            "booking_date": date_str,
            "available_slots": free_slots,
            "available_staff": [
                {"staff_id": row.get("staff_id"), "staff_name": row.get("staff_name")}
                for row in available_staff
            ],
            "requested_time": requested_time,
            "requested_date": date_str,
            "service_duration_minutes": duration_minutes,
            "room_type": room_type or None,
            "check_out_date": parsed_check_out_date.isoformat() if parsed_check_out_date else None,
            "check_out_time": requested_check_out_time or None,
            "preferred_staff": preferred_staff or None,
        }
        if hold_info:
            result_data["slot_held"] = hold_info
        if capacity_status is not None:
            result_data["room_capacity"] = capacity_status

        return _result("check_available_slots", "success", result_data)
    except Exception as exc:
        return _result("check_available_slots", "error", {}, str(exc))


def _room_hold_key(context: CustomerContext, room_type: str, check_in_date: str, check_out_date: str) -> tuple:
    return (context.company_id, "BOARDING_ROOM", room_type, check_in_date, check_out_date)


def _room_capacity_status(
    context: CustomerContext,
    room_type: str,
    check_in_date: str,
    check_out_date: str,
    exclude_booking_id: int | None = None,
) -> dict | None:
    """
    Real BOARDING capacity for one room_type across [check_in_date,
    check_out_date) — a room can be double-booked past its capacity and
    still show a free staff slot, because staff-slot logic never looks at
    the room table. Returns None when room_type/dates aren't resolvable
    yet (caller should not block on an incomplete check), otherwise
    {"capacity", "booked_count", "available"}.
    """
    room_type = str(room_type or "").strip()
    check_in = _parse_date(check_in_date)
    check_out = _parse_date(check_out_date)
    if not room_type or check_in is None or check_out is None:
        return None
    try:
        client = get_supabase_client()
        room_rows = (
            client.table("room")
            .select("capacity")
            .eq("company_id", context.company_id)
            .eq("room_type", room_type)
            .execute()
            .data
            or []
        )
        if not room_rows:
            return None
        capacity = int(room_rows[0].get("capacity") or 0)

        existing = (
            client.table("boarding_booking")
            .select("boarding_booking_id, check_in_date, check_out_date, booking_status")
            .eq("company_id", context.company_id)
            .eq("room_type", room_type)
            .execute()
            .data
            or []
        )
        overlapping = 0
        for row in existing:
            if exclude_booking_id is not None and row.get("boarding_booking_id") == int(exclude_booking_id):
                # A reschedule checking the new dates against this SAME
                # booking's own current (pre-change) dates would otherwise
                # always collide with itself in a capacity-1 room — confirmed
                # live: rescheduling Mars Room (capacity 1) was reported
                # "fully booked" purely because of the booking being moved.
                continue
            if not _booking_blocks_availability(row):
                continue
            existing_in = _parse_date(row.get("check_in_date"))
            existing_out = _parse_date(row.get("check_out_date"))
            if existing_in is None or existing_out is None:
                continue
            # Half-open interval overlap: [check_in, check_out) vs [existing_in, existing_out)
            if existing_in < check_out and check_in < existing_out:
                overlapping += 1

        holder = str(context.resolved_customer_id) if context.resolved_customer_id is not None else None
        shared_holds = _shared_booking_holds(client, context.company_id)
        if shared_holds is None:
            overlapping += SLOT_HOLDS.count_overlapping_room_holds(
                context.company_id,
                room_type,
                check_in_date,
                check_out_date,
                holder,
            )
        else:
            overlapping += sum(
                1
                for row in shared_holds
                if str(row.get("resource_kind") or "").upper() == "ROOM"
                and str(row.get("room_type") or "").casefold() == room_type.casefold()
                and str(row.get("holder_key") or "") != str(holder or "")
                and str(row.get("check_in_date") or "") < check_out_date
                and check_in_date < str(row.get("check_out_date") or "")
            )

        available = overlapping < capacity
        held_now = False
        if available and holder:
            # Same reasoning as the staff-slot hold above: once a specific
            # room+date-range has actually been shown to this customer as
            # available, hold it for the 15-minute confirmation window
            # instead of letting a second customer be told the same thing.
            SLOT_HOLDS.release_all_for(holder, prefix=(context.company_id, "BOARDING_ROOM"))
            key = _room_hold_key(context, room_type, check_in_date, check_out_date)
            shared_acquired = _acquire_shared_hold(
                client,
                {
                    "p_company_id": context.company_id,
                    "p_resource_kind": "ROOM",
                    "p_service_type": "BOARDING",
                    "p_holder_key": holder,
                    "p_slot_date": None,
                    "p_slot_time": None,
                    "p_room_type": room_type,
                    "p_check_in_date": check_in_date,
                    "p_check_out_date": check_out_date,
                    "p_capacity": capacity,
                    "p_exclude_booking_id": exclude_booking_id,
                },
            )
            if shared_acquired is False:
                available = False
            else:
                held_now = SLOT_HOLDS.acquire(key, holder)

        return {
            "capacity": capacity,
            "booked_count": overlapping,
            "available": available,
            # Only claim a hold when one was actually acquired (requires a
            # resolved customer_id) — reporting this unconditionally would
            # tell the customer their slot is reserved when no hold object
            # exists at all.
            "held_for_minutes": 15 if held_now else None,
        }
    except Exception as exc:
        raise RuntimeError(f"Room capacity could not be verified: {exc}") from exc


def _today_parts() -> tuple[str, str]:
    """A booking row's created_date/created_time — must be business-local
    (Asia/Kuala_Lumpur), not naive server time. The container runs in UTC
    (no TZ set), so a bare datetime.now() silently recorded a created_time
    up to 8 hours off real local time (and, near midnight either side, the
    wrong calendar date) — inconsistent with booking_date/booking_time,
    which are always real business-local values via resolve_datetime."""
    from zoneinfo import ZoneInfo

    from .time_normalization import BUSINESS_TIMEZONE

    now = datetime.now(ZoneInfo(BUSINESS_TIMEZONE))
    return now.date().isoformat(), now.strftime("%H:%M:%S")


def _booking_table_meta(service_type: str) -> tuple[str, str]:
    table = _service_table(service_type)
    id_column = {
        "grooming_booking": "grooming_booking_id",
        "daycare_booking": "daycare_booking_id",
        "boarding_booking": "boarding_booking_id",
    }[table]
    return table, id_column


def _resolve_booking_slot(entities: dict, draft: dict | None = None) -> str:
    from .time_normalization import normalize_time_to_slot

    draft = draft or {}
    for source in (draft.get("selected_slot"), entities.get("preferred_time"), draft.get("preferred_time")):
        slot = normalize_time_to_slot(str(source or ""))
        if slot and ":" in slot:
            return slot
    return ""


def _resolve_booking_date(entities: dict, draft: dict | None = None) -> str:
    draft = draft or {}
    for source in (draft.get("booking_date"), entities.get("preferred_date"), draft.get("preferred_date")):
        parsed = _parse_date(str(source or ""))
        if parsed:
            return parsed.isoformat()
        text = str(source or "").strip()
        if text:
            return text
    return ""


def _slot_is_available(context: CustomerContext, intent_json: dict, *, booking_date: str, booking_time: str) -> bool:
    probe = dict(intent_json)
    entities = dict(probe.get("entities") or {})
    entities["preferred_date"] = booking_date
    entities["preferred_time"] = booking_time
    probe["entities"] = entities
    availability = check_available_slots(context, probe)
    if availability.get("status") != "success":
        return False
    from .time_normalization import normalize_time_to_slot

    target = normalize_time_to_slot(booking_time)
    for slot in availability.get("data", {}).get("available_slots") or []:
        if normalize_time_to_slot(str(slot)) == target:
            return True
    return False


def create_customer(context: CustomerContext, full_name: str, phone_number: str, address: str = "") -> dict:
    if not str(full_name or "").strip():
        return _result("create_customer", "error", {}, "Customer name is required")
    if not str(phone_number or "").strip():
        return _result("create_customer", "error", {}, "Phone number is required")
    try:
        from .customer_context import validate_phone_number

        validate_phone_number(phone_number)
    except ValueError as exc:
        return _result("create_customer", "error", {}, str(exc))
    try:
        client = get_supabase_client()
        existing = check_customer_by_phone(
            CustomerContext(phone_number=phone_number, company_id=context.company_id)
        )
        if existing.get("status") == "success":
            return _result(
                "create_customer",
                "error",
                existing.get("data") or {},
                "Customer already exists for this phone number",
            )
        row = {
            "company_id": context.company_id,
            "full_name": str(full_name).strip(),
            "phone_number": str(phone_number).strip(),
            "address": str(address or "-").strip() or "-",
        }
        inserted = client.table("customer").insert(row).execute().data or []
        record = inserted[0] if inserted else row
        context.resolved_customer_id = record.get("customer_id")
        context.customer_record = record
        return _result("create_customer", "success", record)
    except Exception as exc:
        return _result("create_customer", "error", {}, str(exc))


def update_customer(context: CustomerContext, updates: dict) -> dict:
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("update_customer")
    allowed = {"full_name", "phone_number", "address"}
    payload = {key: value for key, value in (updates or {}).items() if key in allowed and value is not None}
    if not payload:
        return _result("update_customer", "error", {}, "No valid customer fields to update")
    try:
        client = get_supabase_client()
        response = (
            client.table("customer")
            .update(payload)
            .eq("company_id", context.company_id)
            .eq("customer_id", context.resolved_customer_id)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return _result("update_customer", "not_found", {"customer_id": context.resolved_customer_id})
        return _result("update_customer", "success", rows[0])
    except Exception as exc:
        return _result("update_customer", "error", {}, str(exc))


def create_pet(
    context: CustomerContext,
    *,
    pet_name: str,
    pet_type: str = "",
    size: str = "",
    height_cm: int | None = None,
    breed: str = "",
) -> dict:
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("create_pet")
    if not str(pet_name or "").strip():
        return _result("create_pet", "error", {}, "Pet name is required")
    normalized_breed = str(breed or "").strip()
    if not normalized_breed or normalized_breed == "-":
        return _result(
            "create_pet",
            "missing_information",
            {"missing_fields": ["breed"]},
            "Pet breed is required; use 'mixed' or 'unknown' only when that is the customer's answer",
        )
    if normalized_breed.casefold() in {
        "dog", "cat", "canine", "feline", "犬", "狗", "猫", "貓", "anjing", "kucing",
    }:
        return _result(
            "create_pet",
            "missing_information",
            {"missing_fields": ["breed"]},
            "Pet species is not a breed; ask for the breed, or record 'unknown' if the customer does not know",
        )
    try:
        client = get_supabase_client()
        existing = get_pet_by_customer_and_name(context, pet_name)
        if existing.get("status") == "success":
            return _result(
                "create_pet",
                "error",
                existing.get("data") or {},
                "Pet with this name already exists for the customer",
            )
        row: dict[str, Any] = {
            "company_id": context.company_id,
            "customer_id": context.resolved_customer_id,
            "pet_name": str(pet_name).strip(),
            "pet_type": str(pet_type or "Dog").strip(),
            # No invented default here — an unverified size would silently
            # feed the wrong grooming price later (see
            # customer_tools.compute_verified_pet_size, the caller that
            # computes this from height_cm against real breakpoints).
            "size": str(size).strip() if size else None,
            "breed": normalized_breed,
            "vaccination_status": "Unknown",
            "health_notes": "-",
            "service_notes": "-",
        }
        if height_cm is not None:
            row["height_cm"] = int(height_cm)
        inserted = client.table("pet").insert(row).execute().data or []
        record = inserted[0] if inserted else row
        return _result("create_pet", "success", {"pet": record, "customer_id": context.resolved_customer_id})
    except Exception as exc:
        return _result("create_pet", "error", {}, str(exc))


def update_pet(context: CustomerContext, pet_id: int, updates: dict) -> dict:
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("update_pet")
    allowed = {
        "pet_name",
        "pet_type",
        "breed",
        "gender",
        "size",
        "height_cm",
        "vaccination_status",
        "vaccination_expired_date",
        "health_notes",
        "service_notes",
    }
    payload = {key: value for key, value in (updates or {}).items() if key in allowed and value is not None}
    if not payload:
        return _result("update_pet", "error", {}, "No valid pet fields to update")
    try:
        client = get_supabase_client()
        response = (
            client.table("pet")
            .update(payload)
            .eq("company_id", context.company_id)
            .eq("customer_id", context.resolved_customer_id)
            .eq("pet_id", int(pet_id))
            .execute()
        )
        rows = response.data or []
        if not rows:
            return _result("update_pet", "not_found", {"pet_id": pet_id})
        return _result("update_pet", "success", {"pet": rows[0]})
    except Exception as exc:
        return _result("update_pet", "error", {}, str(exc))


def get_booking_by_id(context: CustomerContext, booking_id: int, service_type: str = "GROOMING") -> dict:
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("get_booking_by_id")
    try:
        client = get_supabase_client()
        table, id_column = _booking_table_meta(service_type)
        response = (
            client.table(table)
            .select("*")
            .eq("company_id", context.company_id)
            .eq(id_column, int(booking_id))
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return _result("get_booking_by_id", "not_found", {"booking_id": booking_id})
        booking = _serialize_booking_row(table, rows[0])
        if not _verify_booking_belongs_to_customer(context, booking):
            return _result(
                "get_booking_by_id",
                "not_found",
                {"booking_id": booking_id},
                "Booking does not belong to this customer",
            )
        return _result("get_booking_by_id", "success", booking)
    except Exception as exc:
        return _result("get_booking_by_id", "error", {}, str(exc))


def _locate_customer_booking(context: CustomerContext, intent_json: dict) -> dict | None:
    entities = intent_json.get("entities") or {}
    service_type = str(intent_json.get("service_type") or "GROOMING").strip().upper()
    booking_id_raw = str(entities.get("booking_id") or "").strip()
    if booking_id_raw:
        result = get_booking_by_id(context, int(booking_id_raw), service_type)
        if result.get("status") == "success":
            return result.get("data")
        return None

    latest = get_latest_booking_by_customer_id(context)
    if latest.get("status") == "success":
        return latest.get("data")
    return None


def _verify_booking_belongs_to_customer(context: CustomerContext, booking: dict) -> bool:
    pet_id = booking.get("pet_id")
    if pet_id is None:
        return False
    pets_result = get_pets_by_customer_id(context)
    if pets_result.get("status") != "success":
        return False
    pet_ids = {pet.get("pet_id") for pet in pets_result.get("data", {}).get("pets") or []}
    return int(pet_id) in pet_ids


def create_booking(context: CustomerContext, intent_json: dict) -> dict:
    resolve_customer_context(context)
    entities = dict(intent_json.get("entities") or {})
    draft = dict(intent_json.get("_draft_booking") or {})
    service_type = str(
        intent_json.get("service_type") or entities.get("service_type") or draft.get("service_type") or "GROOMING"
    ).strip().upper()
    customer_id = context.resolved_customer_id
    if customer_id is None:
        return missing_identity_result("create_booking")

    pet_id_raw = entities.get("pet_id") or draft.get("pet_id")
    pet_name = str(entities.get("pet_name") or draft.get("pet_name") or "").strip()
    if pet_id_raw is None and pet_name:
        pet_lookup = get_pet_by_customer_and_name(context, pet_name)
        if pet_lookup.get("status") == "success":
            pet_id_raw = (pet_lookup.get("data") or {}).get("pet", {}).get("pet_id")
    try:
        pet_id = int(pet_id_raw) if pet_id_raw is not None else None
    except (TypeError, ValueError):
        pet_id = None

    booking_date = _resolve_booking_date(entities, draft)
    booking_time = _resolve_booking_slot(entities, draft)
    service_name = str(
        entities.get("package_name")
        or entities.get("service_name")
        or entities.get("service_package")
        or draft.get("package_name")
        or (draft.get("entities") or {}).get("service_package")
        or service_type.title()
    ).strip()

    missing: list[str] = []
    if not context.company_id:
        missing.append("company_id")
    if customer_id is None:
        missing.append("customer_id")
    if pet_id is None:
        missing.append("pet_id")
    if service_type not in SERVICE_TYPE_TO_BOOKING_TABLE:
        missing.append("service_type")
    if not booking_date:
        missing.append("booking_date")
    if not booking_time:
        missing.append("booking_time")
    if missing:
        return _result("create_booking", "error", {"missing_fields": missing}, "Missing required booking fields")

    if service_type == "DAYCARE" and not _normalize_time_value(
        str(entities.get("check_out_time") or "")
    ):
        return _result(
            "create_booking",
            "error",
            {"missing_fields": ["check_out_time"]},
            "DAYCARE check_out_time is required; the data layer will not invent a visit duration.",
        )
    if service_type == "BOARDING":
        boarding_missing = [
            field
            for field in ("check_out_date", "check_out_time")
            if not str(entities.get(field) or "").strip()
        ]
        if boarding_missing:
            return _result(
                "create_booking",
                "error",
                {"missing_fields": boarding_missing},
                "BOARDING check-out date and time are required; the data layer will not invent them.",
            )

    add_on_name = str(entities.get("add_on") or "").strip()
    has_add_on_name = add_on_name not in {"", "-"}
    add_on_price_supplied = entities.get("add_on_price") is not None
    if service_type == "BOARDING" and (has_add_on_name or add_on_price_supplied):
        return _result("create_booking", "error", {}, "BOARDING does not support add-ons")
    if service_type in {"GROOMING", "DAYCARE"} and has_add_on_name != add_on_price_supplied:
        return _result(
            "create_booking",
            "error",
            {},
            "add_on and add_on_price must be supplied together as separate fields",
        )

    owned_pets = get_pets_by_customer_id(context)
    owned_pet_ids = {
        int(pet.get("pet_id"))
        for pet in (owned_pets.get("data") or {}).get("pets", [])
        if pet.get("pet_id") is not None
    }
    if pet_id not in owned_pet_ids:
        return _result(
            "create_booking",
            "error",
            {"pet_id": pet_id},
            "Pet does not belong to this customer",
        )

    from app.validation.validator import check_vaccination_eligibility

    vaccination = check_vaccination_eligibility(context.company_id, pet_id, service_type, booking_date)
    if not vaccination.ok:
        return _result(
            "create_booking",
            "error",
            {"pet_id": pet_id, "service_type": service_type},
            "; ".join(vaccination.errors),
        )

    # check_available_slots (called inside _slot_is_available) has no draft
    # fallback of its own — passing the raw intent_json here would silently
    # skip the BOARDING room-capacity gate whenever room_type/service_type
    # were only resolved from the draft above (a normal case: service_name
    # falls back to draft.get("package_name"), service_type to
    # draft.get("service_type")), not because the customer typed neither.
    # Mirror reschedule_booking's own probe-enrichment for the same reason.
    probe = dict(intent_json)
    probe_entities = dict(entities)
    probe_entities["service_type"] = service_type
    if service_type == "BOARDING":
        probe_entities["room_type"] = service_name
        probe_check_out_date = _resolve_booking_date({"preferred_date": entities.get("check_out_date")})
        probe_entities["check_out_date"] = probe_check_out_date
    probe["service_type"] = service_type
    probe["entities"] = probe_entities
    if not _slot_is_available(context, probe, booking_date=booking_date, booking_time=booking_time):
        return _result(
            "create_booking",
            "error",
            {"booking_date": booking_date, "booking_time": booking_time},
            "Requested slot is not available",
        )

    # _slot_is_available above can only use the duration supplied to the
    # availability call. Re-check the persisted start/end values here so a
    # customer's actual pickup/check-out
    # time can be well past closing and still pass that check (e.g. a 5:30pm
    # DAYCARE drop-off for 3 hours, ending 8:30pm, when the business closes
    # at 7pm). Validate the real start AND end time against business hours
    # here instead of trusting the placeholder-duration gate for this.
    if service_type in ("DAYCARE", "BOARDING"):
        parsed_hours_date = _parse_date(booking_date)
        if parsed_hours_date is not None:
            open_time, close_time, closed_reason = _business_hours_for_date(context.company_id, parsed_hours_date)
            if closed_reason:
                if closed_reason.startswith(BUSINESS_HOURS_CONFIG_ERROR):
                    return _result(
                        "create_booking",
                        "error",
                        {"booking_date": booking_date},
                        closed_reason,
                        handoff_required=True,
                        handoff_reason=BUSINESS_HOURS_CONFIG_ERROR,
                    )
                return _result(
                    "create_booking",
                    "error",
                    {"booking_date": booking_date},
                    f"The business is closed on {booking_date}: {closed_reason}",
                )
            open_minutes = _time_to_minutes(open_time)
            close_minutes = _time_to_minutes(close_time)
            if open_minutes is None or close_minutes is None:
                return _result(
                    "create_booking",
                    "error",
                    {"booking_date": booking_date},
                    f"{BUSINESS_HOURS_CONFIG_ERROR}: invalid hours returned for {booking_date}",
                    handoff_required=True,
                    handoff_reason=BUSINESS_HOURS_CONFIG_ERROR,
                )
            start_minutes_check = _time_to_minutes(booking_time)
            if start_minutes_check is not None and (
                start_minutes_check < open_minutes or start_minutes_check >= close_minutes
            ):
                return _result(
                    "create_booking",
                    "error",
                    {"booking_time": booking_time, "open_time": open_time, "close_time": close_time},
                    (
                        f"Requested time {booking_time} is outside business hours "
                        f"({open_time}-{close_time}) on {booking_date}. Ask the customer "
                        "for a time within business hours."
                    ),
                )
            check_out_time_raw = _normalize_time_value(
                str(entities.get("check_out_time") or (booking_time if service_type == "BOARDING" else ""))
            )
            end_minutes = _time_to_minutes(check_out_time_raw) if check_out_time_raw else None
            checkout_date = parsed_hours_date
            checkout_open_time = open_time
            checkout_close_time = close_time
            checkout_closed_reason = None
            if service_type == "BOARDING":
                checkout_date = _parse_date(str(entities.get("check_out_date") or ""))
                if checkout_date is not None:
                    checkout_open_time, checkout_close_time, checkout_closed_reason = _business_hours_for_date(
                        context.company_id, checkout_date
                    )
            if checkout_closed_reason:
                if checkout_closed_reason.startswith(BUSINESS_HOURS_CONFIG_ERROR):
                    return _result(
                        "create_booking",
                        "error",
                        {"check_out_date": checkout_date.isoformat() if checkout_date else None},
                        checkout_closed_reason,
                        handoff_required=True,
                        handoff_reason=BUSINESS_HOURS_CONFIG_ERROR,
                    )
                return _result(
                    "create_booking",
                    "error",
                    {"check_out_date": checkout_date.isoformat() if checkout_date else None},
                    f"The business is closed on the requested check-out date: {checkout_closed_reason}",
                )
            checkout_open_minutes = _time_to_minutes(checkout_open_time)
            checkout_close_minutes = _time_to_minutes(checkout_close_time)
            if checkout_open_minutes is None or checkout_close_minutes is None:
                return _result(
                    "create_booking",
                    "error",
                    {"check_out_date": checkout_date.isoformat() if checkout_date else None},
                    f"{BUSINESS_HOURS_CONFIG_ERROR}: invalid check-out-day hours",
                    handoff_required=True,
                    handoff_reason=BUSINESS_HOURS_CONFIG_ERROR,
                )
            if (
                service_type == "DAYCARE"
                and start_minutes_check is not None
                and end_minutes is not None
                and end_minutes <= start_minutes_check
            ):
                return _result(
                    "create_booking",
                    "error",
                    {"check_in_time": booking_time, "check_out_time": check_out_time_raw},
                    "DAYCARE check_out_time must be after check_in_time on the same day",
                )
            if end_minutes is not None and end_minutes < checkout_open_minutes:
                return _result(
                    "create_booking",
                    "error",
                    {"check_out_time": check_out_time_raw, "open_time": checkout_open_time},
                    (
                        f"Requested pickup/check-out time {check_out_time_raw} is before "
                        f"opening time ({checkout_open_time}) on "
                        f"{checkout_date.isoformat() if checkout_date else booking_date}."
                    ),
                )
            if end_minutes is not None and end_minutes > checkout_close_minutes:
                return _result(
                    "create_booking",
                    "error",
                    {"check_out_time": check_out_time_raw, "close_time": checkout_close_time},
                    (
                        f"Requested pickup/check-out time {check_out_time_raw} is after "
                        f"closing time ({checkout_close_time}) on "
                        f"{checkout_date.isoformat() if checkout_date else booking_date}. Ask the customer "
                        "for an earlier pickup time, or a shorter visit, so it ends before "
                        "closing."
                    ),
                )

    from .booking_draft import select_staff_id
    from .availability_service import service_duration_minutes

    # Recompute the real per-slot-free staff list ourselves rather than
    # reusing check_available_slots's own "available_staff" (that's just
    # the day-level roster, not filtered to this exact time — select_staff_id
    # used to pick blindly from it, which could assign a staff member who's
    # actually busy at this specific time with a DIFFERENT booking).
    client = get_supabase_client()
    staff_id = None
    parsed_booking_date = _parse_date(booking_date)
    start_minutes = _time_to_minutes(booking_time)
    preferred_staff_raw = str(entities.get("preferred_staff") or draft.get("preferred_staff") or "").strip()
    if parsed_booking_date is not None and start_minutes is not None:
        roster = _staff_day_roster(client, context.company_id, parsed_booking_date, service_type=service_type)
        roster_ids = [row["staff_id"] for row in roster if row.get("staff_id") is not None]
        bookings_today = _cross_service_staff_bookings(client, context.company_id, roster_ids, booking_date)
        duration_minutes = service_duration_minutes(service_type)
        if service_type == "DAYCARE":
            checkout_minutes = _time_to_minutes(entities.get("check_out_time"))
            if checkout_minutes is not None and checkout_minutes > start_minutes:
                duration_minutes = checkout_minutes - start_minutes
        free_staff = _staff_free_for_interval(
            start_minutes, start_minutes + duration_minutes, roster, bookings_today, date_str=booking_date
        )
        if service_type == "BOARDING":
            # Both values were required above; use the exact customer-approved
            # checkout rather than manufacturing a stay length here.
            resolved_checkout_date_str = _resolve_booking_date({"preferred_date": entities.get("check_out_date")})
            resolved_checkout_date = _parse_date(resolved_checkout_date_str)
            checkout_time_raw = _normalize_time_value(str(entities.get("check_out_time") or ""))
            checkout_minutes_boarding = _time_to_minutes(checkout_time_raw)
            if resolved_checkout_date is not None and checkout_minutes_boarding is not None:
                boarding_width = service_duration_minutes("BOARDING")
                if resolved_checkout_date == parsed_booking_date:
                    co_roster, co_bookings, co_date_str = roster, bookings_today, booking_date
                else:
                    co_date_str = resolved_checkout_date.isoformat()
                    co_roster = _staff_day_roster(
                        client, context.company_id, resolved_checkout_date, service_type=service_type
                    )
                    co_roster_ids = [r["staff_id"] for r in co_roster if r.get("staff_id") is not None]
                    co_bookings = _cross_service_staff_bookings(
                        client, context.company_id, co_roster_ids, co_date_str
                    )
                free_at_checkout_ids = {
                    r.get("staff_id")
                    for r in _staff_free_for_interval(
                        checkout_minutes_boarding,
                        checkout_minutes_boarding + boarding_width,
                        co_roster,
                        co_bookings,
                        date_str=co_date_str,
                    )
                }
                free_staff = [r for r in free_staff if r.get("staff_id") in free_at_checkout_ids]
        if preferred_staff_raw:
            chosen = _match_preferred_staff(preferred_staff_raw, free_staff)
            if chosen is None:
                exists = _match_preferred_staff(preferred_staff_raw, roster)
                if exists is not None:
                    return _result(
                        "create_booking",
                        "error",
                        {
                            "preferred_staff": preferred_staff_raw,
                            "booking_date": booking_date,
                            "booking_time": booking_time,
                        },
                        (
                            f"{exists.get('staff_name')} is not available at "
                            f"{booking_date} {booking_time} (already booked elsewhere, on "
                            "leave, or off that day). Ask the customer to pick a different "
                            "time, or confirm booking with any available staff instead."
                        ),
                    )
                return _result(
                    "create_booking",
                    "error",
                    {"preferred_staff": preferred_staff_raw},
                    (
                        "The requested preferred_staff does not match a real, qualified "
                        "staff member. Ask the customer to choose a listed staff member or "
                        "explicitly agree to any available staff."
                    ),
                )
            else:
                staff_id = int(chosen["staff_id"])
        if staff_id is None:
            staff_id = select_staff_id(free_staff)

    try:
        client = get_supabase_client()
        table, _ = _booking_table_meta(service_type)
        created_date, created_time = _today_parts()
        status = "Pending"
        price = draft.get("price_quote")
        if price is None:
            price = entities.get("price")
        numeric_price = round(float(price or 0), 2)

        if table == "grooming_booking":
            row = {
                "pet_id": pet_id,
                "staff_id": staff_id,
                "service_name": service_name,
                "booking_date": booking_date,
                "booking_time": booking_time,
                "booking_status": status,
                "price": numeric_price,
                "add_on": str(entities.get("add_on") or "-"),
                "add_on_price": round(float(entities.get("add_on_price") or 0), 2),
                "notes": str(entities.get("notes") or "-"),
                "created_date": created_date,
                "created_time": created_time,
            }
        elif table == "daycare_booking":
            check_out_time = _normalize_time_value(str(entities.get("check_out_time") or ""))
            if not check_out_time:
                return _result(
                    "create_booking",
                    "error",
                    {"missing_fields": ["check_out_time"]},
                    "DAYCARE check_out_time is required; the data layer will not invent a visit duration.",
                )
            row = {
                "pet_id": pet_id,
                "staff_id": staff_id,
                "booking_date": booking_date,
                "check_in_time": booking_time,
                "check_out_time": check_out_time,
                "package_type": service_name,
                "booking_status": status,
                "price": numeric_price,
                "add_on": str(entities.get("add_on") or "-"),
                "add_on_price": round(float(entities.get("add_on_price") or 0), 2),
                "special_instruction": str(entities.get("special_instruction") or "-"),
                "created_date": created_date,
                "created_time": created_time,
            }
        else:
            parsed_check_in = _parse_date(booking_date)
            check_out_date = _resolve_booking_date(
                {"preferred_date": entities.get("check_out_date")}
            )
            if not check_out_date:
                return _result(
                    "create_booking",
                    "error",
                    {"missing_fields": ["check_out_date"]},
                    "BOARDING check_out_date is required; the data layer will not invent a stay length.",
                )
            check_out_time = _normalize_time_value(
                str(entities.get("check_out_time") or "")
            )
            if not check_out_time:
                return _result(
                    "create_booking",
                    "error",
                    {"missing_fields": ["check_out_time"]},
                    "BOARDING check_out_time is required; the data layer will not invent it.",
                )
            price_per_night = round(
                float(entities.get("price_per_night") or numeric_price or 0), 2
            )
            nights = max(
                1,
                (
                    (_parse_date(check_out_date) or parsed_check_in)
                    - parsed_check_in
                ).days
                if parsed_check_in
                else 1,
            )
            row = {
                "pet_id": pet_id,
                "staff_id": staff_id,
                "check_in_date": booking_date,
                "check_in_time": booking_time,
                "check_out_date": check_out_date,
                "check_out_time": check_out_time,
                "room_type": service_name,
                "booking_status": status,
                "price_per_night": price_per_night,
                # Never accept a caller-supplied total that can diverge from
                # the verified room rate and stay length.
                "total_price": round(price_per_night * nights, 2),
                "feeding_instruction": str(entities.get("feeding_instruction") or "-"),
                "medical_instruction": str(entities.get("medical_instruction") or "-"),
                "notes": str(entities.get("notes") or "-"),
                "created_date": created_date,
                "created_time": created_time,
            }

        payment = {
            "service": service_name,
            "base_price": (
                row.get("total_price")
                if table == "boarding_booking"
                else row.get("price")
            ) or 0,
            "add_ons": row.get("add_on") if table in ("grooming_booking", "daycare_booking") else "",
            "final_amount": (
                (row.get("price") or 0) + (row.get("add_on_price") or 0)
                if table in ("grooming_booking", "daycare_booking")
                else (row.get("total_price") if table == "boarding_booking" else row.get("price"))
            ) or 0,
            "payment_method": "",
            "date": created_date,
            "status": "Pending",
        }
        booking_type = service_type.lower()
        atomic = client.rpc(
            "create_booking_atomic",
            {
                "p_company_id": context.company_id,
                "p_booking_type": booking_type,
                "p_booking": row,
                "p_payment": payment,
            },
        ).execute().data or {}
        if not atomic.get("booking"):
            raise RuntimeError(
                "create_booking_atomic returned no booking; apply the required booking migrations"
            )
        record = dict(atomic["booking"])
        serialized = _serialize_booking_row(table, record)
        booking_id = int(serialized.get("booking_id"))
        expected = {
            "pet_id": pet_id,
            "booking_date": booking_date,
            "booking_time": booking_time,
            "service_name": service_name,
            "booking_status": status,
            "staff_id": staff_id,
            "price": row.get("price"),
            "price_per_night": row.get("price_per_night"),
            "total_price": row.get("total_price"),
            "add_on_price": row.get("add_on_price"),
            "payment_id": record.get("payment_id"),
        }
        verified, persisted, mismatches = _verify_persisted_booking(
            context,
            booking_id,
            service_type,
            expected,
        )
        persisted_payment = dict(atomic.get("payment") or {})
        if int(persisted_payment.get("payment_id") or 0) != int(record.get("payment_id") or 0):
            mismatches.append("payment_id")
        for amount_field in ("base_price", "final_amount"):
            try:
                if round(float(persisted_payment.get(amount_field)), 2) != round(
                    float(payment[amount_field]), 2
                ):
                    mismatches.append(f"payment.{amount_field}")
            except (TypeError, ValueError):
                mismatches.append(f"payment.{amount_field}")
        verified = not mismatches
        if verified:
            # booking_status is always "Pending" at this exact point — every
            # new booking starts there regardless of path — so it tells the
            # customer nothing. payment_status ("Pending" until staff verify
            # it, matching the local `payment` dict just inserted in both the
            # RPC and compensating-fallback paths above) is what's actually
            # still outstanding and worth surfacing in the confirmation.
            # persisted (not serialized above) is what the final result
            # below actually spreads into the tool response.
            persisted["payment_status"] = payment["status"]
        if not verified:
            return _result(
                "create_booking",
                "error",
                {
                    "booking_id": booking_id,
                    "mismatches": mismatches,
                    "inserted": serialized,
                    "record": persisted,
                },
                "Booking verification failed after insert",
                handoff_required=True,
                handoff_reason="DATABASE_ERROR",
            )
        # The slot is now a real booking (which itself blocks availability
        # going forward) — the temporary hold has done its job.
        holder = (
            str(context.resolved_customer_id)
            if context.resolved_customer_id is not None
            else None
        )
        if holder:
            _release_shared_holds(client, context.company_id, holder)
            SLOT_HOLDS.release_all_for(holder, prefix=(context.company_id, service_type))
            if service_type == "BOARDING":
                SLOT_HOLDS.release_all_for(holder, prefix=(context.company_id, "BOARDING_ROOM"))

        from app.documents.service import generate_and_send_booking_confirmation

        confirmation_booking = {**persisted, "pet_id": pet_id, "pet_name": persisted.get("pet_name") or pet_name}
        confirmation_result = generate_and_send_booking_confirmation(context.company_id, confirmation_booking)
        confirmation_send_status = str(
            (confirmation_result.get("send_result") or {}).get("status") or ""
        )
        confirmation_delivered = (
            confirmation_result.get("status") == "success"
            and confirmation_send_status in {"sent", "sent_console"}
        )

        return _result(
            "create_booking",
            "success",
            {
                **persisted,
                "customer_id": customer_id,
                "pet_id": pet_id,
                "_internal_payment_id": record.get("payment_id"),
                "_internal_confirmation_url": confirmation_result.get("document_url"),
                "confirmation_delivery_status": confirmation_send_status or confirmation_result.get("status"),
                "verified": True,
            },
            handoff_required=not confirmation_delivered,
            handoff_reason=None if confirmation_delivered else "DOCUMENT_DELIVERY_ERROR",
        )
    except Exception as exc:
        return _result(
            "create_booking",
            "error",
            {},
            str(exc),
            handoff_required=True,
            handoff_reason="DATABASE_ERROR",
        )


def update_booking(
    context: CustomerContext,
    booking_id: int,
    service_type: str,
    updates: dict,
    payment_updates: dict | None = None,
) -> dict:
    try:
        client = get_supabase_client()
        table, id_column = _booking_table_meta(service_type)
        existing = get_booking_by_id(context, booking_id, service_type)
        if existing.get("status") != "success":
            return existing
        booking = existing.get("data") or {}
        if not _verify_booking_belongs_to_customer(context, booking):
            return _result("update_booking", "error", {}, "Booking does not belong to this customer")

        allowed = {
            "booking_date",
            "booking_time",
            "check_in_date",
            "check_in_time",
            "check_out_date",
            "check_out_time",
            "booking_status",
            "service_name",
            "package_type",
            "room_type",
            "staff_id",
            "price",
            "price_per_night",
            "total_price",
            "add_on",
            "add_on_price",
            "special_instruction",
            "notes",
        }
        payload = {key: value for key, value in (updates or {}).items() if key in allowed and value is not None}
        if not payload:
            return _result("update_booking", "error", {}, "No valid booking fields to update")

        atomic = (
            client.rpc(
                "update_booking_atomic",
                {
                    "p_company_id": context.company_id,
                    "p_booking_type": str(service_type).strip().lower(),
                    "p_booking_id": int(booking_id),
                    "p_booking_patch": payload,
                    "p_payment_patch": payment_updates or {},
                },
            )
            .execute()
            .data
        )
        if not atomic:
            return _result("update_booking", "not_found", {"booking_id": booking_id})
        return _result("update_booking", "success", _serialize_booking_row(table, dict(atomic)))
    except Exception as exc:
        return _result("update_booking", "error", {}, str(exc))


def _locate_active_customer_booking(context: CustomerContext, intent_json: dict) -> tuple[dict | None, dict | None]:
    """
    Locate the customer's booking to cancel/reschedule — unlike
    _locate_customer_booking (used for "repeat my last booking", which
    intentionally matches completed/historical bookings too),
    cancel/reschedule must NEVER resolve to anything except a currently
    Scheduled/Pending booking. Falling back to "latest booking" (any
    qualifying status, including Done/Completed) let a real, already-
    finished historical booking get silently cancelled when the customer
    had zero actually-active bookings at the time (confirmed live: a
    completed daycare booking from days earlier was cancelled for real when
    the customer meant an entirely different, never-actually-confirmed
    booking). Returns (booking, error_result) — exactly one is non-None.
    """
    entities = intent_json.get("entities") or {}
    booking_id_raw = str(entities.get("booking_id") or "").strip()
    if booking_id_raw:
        if not booking_id_raw.isdigit() or int(booking_id_raw) <= 0:
            return None, _result(
                "cancel_booking",
                "error",
                {"booking_id": booking_id_raw},
                "booking_id must be a positive integer",
            )
        service_type = str(intent_json.get("service_type") or "GROOMING").strip().upper()
        result = get_booking_by_id(context, int(booking_id_raw), service_type)
        if result.get("status") != "success":
            return None, _result(
                "cancel_booking", "not_found", {"booking_id": booking_id_raw}, "Booking not found"
            )
        booking = dict(result.get("data") or {})
        if not booking.get("pet_name") and booking.get("pet_id") is not None:
            # get_booking_by_id's serializers don't carry pet_name — without
            # it, the confirm_pet_name gate below silently has nothing to
            # check against and lets a plain "yes" straight through
            # (confirmed live: this bypassed the gate entirely whenever a
            # booking_id was given directly, the most common real path once
            # a booking_id has already been told to the customer).
            names = _pet_name_map(get_supabase_client(), context.company_id, [int(booking["pet_id"])])
            booking["pet_name"] = names.get(int(booking["pet_id"]), "")
        status = _normalize_booking_status(booking.get("booking_status"))
        if status not in {"scheduled", "pending"}:
            return None, _result(
                "cancel_booking",
                "error",
                {"booking_id": booking_id_raw, "booking_status": booking.get("booking_status")},
                (
                    f"This booking is already '{booking.get('booking_status')}', not an "
                    "active upcoming booking — nothing to cancel/reschedule. If the "
                    "customer meant a different, currently active booking, ask which one."
                ),
            )
        return booking, None

    active = _active_bookings_for_customer(context)
    if len(active) > 1:
        return None, _ambiguous_active_bookings_result("cancel_booking", context)
    if len(active) == 1:
        return active[0], None
    return None, _result(
        "cancel_booking",
        "not_found",
        {},
        "This customer has no currently active (Scheduled/Pending) booking. Do not "
        "cancel/reschedule a completed or already-cancelled historical booking instead "
        "— tell the customer there's nothing active to change.",
    )


def _ambiguous_active_bookings_result(action: str, context: CustomerContext) -> dict | None:
    """None if the target booking is unambiguous; otherwise a result listing
    every active candidate, for the caller to ask the customer to pick one."""
    active = _active_bookings_for_customer(context)
    if len(active) <= 1:
        return None
    return _result(
        action,
        "ambiguous",
        {
            "candidates": [
                {
                    "booking_id": b.get("booking_id"),
                    "service_type": b.get("service_type"),
                    "pet_name": b.get("pet_name"),
                    "package_name": b.get("package_name") or b.get("service_name") or b.get("room_type"),
                    "date": b.get("booking_date") or b.get("check_in_date"),
                }
                for b in active
            ]
        },
        "Customer has multiple active bookings — ask which one (by pet/service/date) before proceeding; do not guess or default to the most recent.",
    )


def cancel_booking(context: CustomerContext, intent_json: dict) -> dict:
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("cancel_booking")

    booking, error = _locate_active_customer_booking(context, intent_json)
    if error:
        return error
    if not _verify_booking_belongs_to_customer(context, booking):
        return _result("cancel_booking", "error", {}, "Booking does not belong to this customer")

    # SAFETY CONFIRMATION: cancelling is irreversible-in-effect (it frees the
    # slot for someone else) and a wrong target here has been observed to be
    # dangerous (see _locate_active_customer_booking) — require the customer
    # to state the pet's real name before the write actually happens, not
    # just a generic "yes". confirm_pet_name is only checked, never trusted
    # blindly: it must match this SPECIFIC booking's own real pet_name.
    entities = intent_json.get("entities") or {}
    confirm_pet_name = str(entities.get("confirm_pet_name") or "").strip().lower()
    real_pet_name = str(booking.get("pet_name") or "").strip().lower()
    if real_pet_name and confirm_pet_name != real_pet_name:
        return _result(
            "cancel_booking",
            "confirmation_required",
            {
                "booking_id": booking.get("booking_id"),
                "service_type": booking.get("service_type"),
                "pet_name": booking.get("pet_name"),
                "package_name": booking.get("package_name") or booking.get("service_name") or booking.get("room_type"),
                "date": booking.get("booking_date") or booking.get("check_in_date"),
            },
            (
                "Show the customer these exact booking details and ask them to type "
                f"the pet's name ({booking.get('pet_name')!r}) to confirm cancellation. "
                "Do not proceed until they reply with it — then call cancel_booking "
                "again with confirm_pet_name set to what they typed."
            ),
        )

    service_type = str(booking.get("service_type") or intent_json.get("service_type") or "GROOMING")
    booking_id = int(booking.get("booking_id"))
    try:
        table, _ = _booking_table_meta(service_type)
        cancelled = (
            get_supabase_client()
            .rpc(
                "cancel_booking_atomic",
                {
                    "p_company_id": int(context.company_id),
                    "p_booking_type": service_type.strip().lower(),
                    "p_booking_id": booking_id,
                },
            )
            .execute()
            .data
        )
        if not cancelled:
            return _result("cancel_booking", "not_found", {"booking_id": booking_id})
        update_result = _result(
            "cancel_booking",
            "success",
            _serialize_booking_row(table, dict(cancelled)),
        )
    except Exception as exc:
        return _result(
            "cancel_booking",
            "error",
            {"booking_id": booking_id, "service_type": service_type},
            f"Atomic booking cancellation failed: {exc}",
            handoff_required=True,
            handoff_reason="DATABASE_ERROR",
        )
    final = _finalize_booking_write(
        "cancel_booking",
        context,
        booking_id,
        service_type,
        update_result,
        expected={"booking_status": "cancelled"},
    )
    if final.get("status") == "success" and context.resolved_customer_id is not None:
        holder = str(context.resolved_customer_id)
        client = get_supabase_client()
        _release_shared_holds(client, context.company_id, holder)
        SLOT_HOLDS.release_all_for(holder)
    return final


def reschedule_booking(context: CustomerContext, intent_json: dict) -> dict:
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("reschedule_booking")

    booking, error = _locate_active_customer_booking(context, intent_json)
    if error:
        return {**error, "action": "reschedule_booking"}
    if not _verify_booking_belongs_to_customer(context, booking):
        return _result("reschedule_booking", "error", {}, "Booking does not belong to this customer")

    entities = intent_json.get("entities") or {}
    confirm_pet_name = str(entities.get("confirm_pet_name") or "").strip().lower()
    real_pet_name = str(booking.get("pet_name") or "").strip().lower()
    if real_pet_name and confirm_pet_name != real_pet_name:
        return _result(
            "reschedule_booking",
            "confirmation_required",
            {
                "booking_id": booking.get("booking_id"),
                "service_type": booking.get("service_type"),
                "pet_name": booking.get("pet_name"),
                "package_name": booking.get("package_name") or booking.get("service_name") or booking.get("room_type"),
                "date": booking.get("booking_date") or booking.get("check_in_date"),
                "new_date": entities.get("new_preferred_date") or entities.get("preferred_date"),
                "new_time": entities.get("new_preferred_time") or entities.get("preferred_time"),
                "new_check_out_date": entities.get("new_check_out_date") or None,
                "new_check_out_time": entities.get("new_check_out_time") or None,
            },
            (
                "Show the customer the target booking AND exact new date/time details, then ask them to type "
                f"the pet's name ({booking.get('pet_name')!r}) to confirm rescheduling "
                "THIS booking. Do not proceed until they reply with it — then call "
                "reschedule_booking again with confirm_pet_name set to what they typed "
                "(plus the new date/time)."
            ),
        )
    new_date = _resolve_booking_date(
        {
            "preferred_date": entities.get("new_preferred_date") or entities.get("preferred_date"),
            "preferred_time": entities.get("new_preferred_time") or entities.get("preferred_time"),
        }
    )
    new_time = _resolve_booking_slot(
        {
            "preferred_time": entities.get("new_preferred_time") or entities.get("preferred_time"),
        }
    )
    if not new_date or not new_time:
        return _result(
            "reschedule_booking",
            "error",
            {"missing_fields": ["new_preferred_date", "new_preferred_time"]},
            "New date and time are required to reschedule",
        )

    service_type_hint = str(booking.get("service_type") or intent_json.get("service_type") or "GROOMING").upper()
    new_check_out_date = str(entities.get("new_check_out_date") or "").strip()
    new_check_out_time = ""
    if service_type_hint == "BOARDING":
        if not new_check_out_date:
            return _result(
                "reschedule_booking",
                "error",
                {"missing_fields": ["new_check_out_date"]},
                "new_check_out_date is required to reschedule a BOARDING booking",
            )
        try:
            if date.fromisoformat(new_check_out_date) <= date.fromisoformat(new_date):
                return _result(
                    "reschedule_booking",
                    "error",
                    {"new_date": new_date, "new_check_out_date": new_check_out_date},
                    (
                        f"new_check_out_date ({new_check_out_date}) is not strictly after "
                        f"new_date ({new_date}). This almost always means new_check_out_date "
                        "was computed/guessed instead of resolved — re-run resolve_datetime "
                        "on the customer's ORIGINAL new check-out date expression and retry "
                        "with its exact date output."
                    ),
                )
        except ValueError:
            return _result(
                "reschedule_booking",
                "error",
                {"new_check_out_date": new_check_out_date},
                "new_check_out_date must be a resolved YYYY-MM-DD date",
            )

    start_minutes = _time_to_minutes(new_time)
    if start_minutes is None:
        return _result(
            "reschedule_booking",
            "error",
            {"new_preferred_time": new_time},
            "new_preferred_time must be a resolved HH:MM time",
        )

    requested_check_out = _normalize_time_value(str(entities.get("new_check_out_time") or ""))
    if service_type_hint == "DAYCARE":
        end_minutes = _time_to_minutes(requested_check_out) if requested_check_out else None
        if requested_check_out and end_minutes is None:
            return _result(
                "reschedule_booking",
                "error",
                {"new_check_out_time": requested_check_out},
                "new_check_out_time must be a resolved HH:MM time",
            )
        if end_minutes is None and entities.get("duration_minutes") is not None:
            try:
                duration = int(entities["duration_minutes"])
            except (TypeError, ValueError):
                duration = 0
            end_minutes = start_minutes + duration if 0 < duration <= 24 * 60 else None
        if end_minutes is None:
            old_start = _time_to_minutes(booking.get("check_in_time") or booking.get("booking_time"))
            old_end = _time_to_minutes(booking.get("check_out_time"))
            if old_start is not None and old_end is not None and old_end > old_start:
                end_minutes = start_minutes + (old_end - old_start)
        if end_minutes is None:
            return _result(
                "reschedule_booking",
                "error",
                {"missing_fields": ["new_check_out_time_or_duration_minutes"]},
                "A DAYCARE pickup time or duration is required because the original visit duration is unavailable",
            )
        if end_minutes <= start_minutes or end_minutes >= 24 * 60:
            return _result(
                "reschedule_booking",
                "error",
                {"new_preferred_time": new_time, "new_check_out_time": requested_check_out},
                "DAYCARE check_out_time must be after check_in_time on the same day",
            )
        new_check_out_time = f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"
    elif service_type_hint == "BOARDING":
        new_check_out_time = requested_check_out or _normalize_time_value(
            str(booking.get("check_out_time") or new_time)
        )
        if _time_to_minutes(new_check_out_time) is None:
            return _result(
                "reschedule_booking",
                "error",
                {"new_check_out_time": new_check_out_time},
                "new_check_out_time must be a resolved HH:MM time",
            )

    if service_type_hint in {"DAYCARE", "BOARDING"}:
        new_date_value = _parse_date(new_date)
        if new_date_value is None:
            return _result("reschedule_booking", "error", {"new_date": new_date}, "New date is invalid")
        open_time, close_time, closed_reason = _business_hours_for_date(context.company_id, new_date_value)
        if closed_reason:
            if closed_reason.startswith(BUSINESS_HOURS_CONFIG_ERROR):
                return _result(
                    "reschedule_booking",
                    "error",
                    {"new_date": new_date},
                    closed_reason,
                    handoff_required=True,
                    handoff_reason=BUSINESS_HOURS_CONFIG_ERROR,
                )
            return _result(
                "reschedule_booking",
                "error",
                {"new_date": new_date},
                f"The business is closed on the requested date: {closed_reason}",
            )
        open_minutes = _time_to_minutes(open_time)
        close_minutes = _time_to_minutes(close_time)
        if open_minutes is None or close_minutes is None:
            return _result(
                "reschedule_booking",
                "error",
                {"new_date": new_date},
                f"{BUSINESS_HOURS_CONFIG_ERROR}: invalid hours returned for {new_date}",
                handoff_required=True,
                handoff_reason=BUSINESS_HOURS_CONFIG_ERROR,
            )
        if start_minutes < open_minutes or start_minutes >= close_minutes:
            return _result(
                "reschedule_booking",
                "error",
                {"new_time": new_time, "open_time": open_time, "close_time": close_time},
                f"Requested time {new_time} is outside business hours ({open_time}-{close_time}) on {new_date}",
            )

        checkout_date_value = new_date_value
        checkout_open_time, checkout_close_time = open_time, close_time
        if service_type_hint == "BOARDING":
            checkout_date_value = _parse_date(new_check_out_date)
            if checkout_date_value is None:
                return _result(
                    "reschedule_booking", "error", {"new_check_out_date": new_check_out_date}, "New check-out date is invalid"
                )
            checkout_open_time, checkout_close_time, checkout_closed_reason = _business_hours_for_date(
                context.company_id, checkout_date_value
            )
            if checkout_closed_reason:
                if checkout_closed_reason.startswith(BUSINESS_HOURS_CONFIG_ERROR):
                    return _result(
                        "reschedule_booking",
                        "error",
                        {"new_check_out_date": new_check_out_date},
                        checkout_closed_reason,
                        handoff_required=True,
                        handoff_reason=BUSINESS_HOURS_CONFIG_ERROR,
                    )
                return _result(
                    "reschedule_booking",
                    "error",
                    {"new_check_out_date": new_check_out_date},
                    f"The business is closed on the requested check-out date: {checkout_closed_reason}",
                )
        checkout_minutes = _time_to_minutes(new_check_out_time)
        checkout_open_minutes = _time_to_minutes(checkout_open_time)
        checkout_close_minutes = _time_to_minutes(checkout_close_time)
        if checkout_open_minutes is None or checkout_close_minutes is None:
            return _result(
                "reschedule_booking",
                "error",
                {"new_check_out_date": checkout_date_value.isoformat()},
                f"{BUSINESS_HOURS_CONFIG_ERROR}: invalid check-out-day hours",
                handoff_required=True,
                handoff_reason=BUSINESS_HOURS_CONFIG_ERROR,
            )
        if checkout_minutes is None or checkout_minutes < checkout_open_minutes or checkout_minutes > checkout_close_minutes:
            return _result(
                "reschedule_booking",
                "error",
                {
                    "new_check_out_time": new_check_out_time,
                    "open_time": checkout_open_time,
                    "close_time": checkout_close_time,
                },
                (
                    f"Requested pickup/check-out time {new_check_out_time} is outside business hours "
                    f"({checkout_open_time}-{checkout_close_time}) on {checkout_date_value.isoformat()}"
                ),
            )

    from app.validation.validator import check_vaccination_eligibility

    vaccination = check_vaccination_eligibility(
        context.company_id, booking.get("pet_id"), service_type_hint, new_date
    )
    if not vaccination.ok:
        return _result(
            "reschedule_booking",
            "error",
            {"pet_id": booking.get("pet_id"), "service_type": service_type_hint},
            "; ".join(vaccination.errors),
        )

    probe = dict(intent_json)
    probe_entities = dict(entities)
    probe_entities["preferred_date"] = new_date
    probe_entities["preferred_time"] = new_time
    # A reschedule preserves the assigned staff unless the write explicitly
    # changes staff_id.  Validate the exact original staff member, not merely
    # whether somebody else happens to be free.
    if booking.get("staff_id") is not None:
        probe_entities["preferred_staff"] = str(booking["staff_id"])
    if new_check_out_time:
        probe_entities["check_out_time"] = new_check_out_time
    if service_type_hint == "BOARDING":
        # Otherwise a single-capacity room's own current (pre-reschedule)
        # stay counts against itself in the overlap check.
        probe_entities["room_type"] = booking.get("room_type") or booking.get("package_name")
        probe_entities["check_out_date"] = new_check_out_date
        probe_entities["exclude_booking_id"] = booking.get("booking_id")
    probe["entities"] = probe_entities
    if not _slot_is_available(context, probe, booking_date=new_date, booking_time=new_time):
        return _result(
            "reschedule_booking",
            "error",
            {"booking_date": new_date, "booking_time": new_time},
            "Requested new slot is not available",
        )

    service_type = str(booking.get("service_type") or intent_json.get("service_type") or "GROOMING")
    booking_id = int(booking.get("booking_id"))
    table = _service_table(service_type)
    updates = {"booking_date": new_date, "booking_time": new_time}
    new_total_price = None
    if table == "daycare_booking":
        updates = {
            "booking_date": new_date,
            "check_in_time": new_time,
            "check_out_time": new_check_out_time,
        }
        # Unlike BOARDING (a real per-night rate from the `room` table, so a
        # changed stay length can be safely recalculated below), DAYCARE
        # pricing only ever exists as unstructured RAG text — there is no
        # verified per-hour rate to recompute from here, and some DAYCARE
        # packages are flat-per-day rather than hourly, so naively scaling
        # the old price by the new/old duration ratio would be actively
        # WRONG for those. Silently keeping the stale price was the
        # previous behavior (a 3-hour visit rescheduled to 6 hours kept its
        # original 3-hour total with no correction and no flag for staff).
        # Flag it for a human to actually price instead of guessing either
        # way — the date/time change itself is still valid and proceeds.
        old_start = _time_to_minutes(booking.get("check_in_time") or booking.get("booking_time"))
        old_end = _time_to_minutes(booking.get("check_out_time"))
        if (
            old_start is not None and old_end is not None
            and start_minutes is not None and end_minutes is not None
            and (end_minutes - start_minutes) != (old_end - old_start)
        ):
            return _result(
                "reschedule_booking",
                "error",
                {
                    "booking_id": booking.get("booking_id"),
                    "price_review_required": True,
                    "old_duration_minutes": old_end - old_start,
                    "new_duration_minutes": end_minutes - start_minutes,
                },
                (
                    "The DAYCARE visit duration changed and no structured pricing rule is "
                    "available to recalculate it safely. No booking change was written; "
                    "staff must confirm the new price first."
                ),
                handoff_required=True,
                handoff_reason="DAYCARE_DURATION_PRICE_REVIEW",
            )
    elif table == "boarding_booking":
        updates = {
            "check_in_date": new_date,
            "check_in_time": new_time,
            "check_out_date": new_check_out_date,
            "check_out_time": new_check_out_time,
        }
        # The stay length can change on reschedule (different check-in and/or
        # check-out) — total_price was computed once at creation time from
        # the ORIGINAL number of nights and never revisited, so a reschedule
        # that adds/removes nights silently kept the old, now-wrong total.
        price_per_night = booking.get("price_per_night")
        try:
            nights = max(1, (date.fromisoformat(new_check_out_date) - date.fromisoformat(new_date)).days)
            if price_per_night is not None:
                new_total_price = round(float(price_per_night) * nights, 2)
                updates["total_price"] = new_total_price
        except (TypeError, ValueError) as exc:
            return _result(
                "reschedule_booking",
                "error",
                {"booking_id": booking_id, "price_per_night": price_per_night},
                f"Could not recalculate the rescheduled boarding total: {exc}",
                handoff_required=True,
                handoff_reason="DATABASE_ERROR",
            )

    payment_updates: dict = {}
    if new_total_price is not None:
        payment_id = booking.get("payment_id")
        if payment_id is None:
            return _result(
                "reschedule_booking",
                "error",
                {"booking_id": booking_id, "new_total_price": new_total_price},
                "The boarding booking has no linked payment to synchronize",
                handoff_required=True,
                handoff_reason="DATABASE_ERROR",
            )
        try:
            payment_rows = (
                get_supabase_client()
                .table("payment")
                .select("base_price, final_amount")
                .eq("company_id", context.company_id)
                .eq("payment_id", int(payment_id))
                .limit(1)
                .execute()
                .data
                or []
            )
            if not payment_rows:
                raise RuntimeError("Linked payment was not found")
            old_base = float(payment_rows[0].get("base_price") or 0)
            old_final = float(payment_rows[0].get("final_amount") or 0)
            existing_discount = max(0.0, old_base - old_final)
            payment_updates = {
                "base_price": new_total_price,
                "final_amount": max(0, round(new_total_price - existing_discount, 2)),
            }
        except Exception as exc:
            return _result(
                "reschedule_booking",
                "error",
                {"booking_id": booking_id, "payment_id": payment_id},
                f"Could not prepare the linked payment synchronization: {exc}",
                handoff_required=True,
                handoff_reason="DATABASE_ERROR",
            )

    update_result = update_booking(
        context,
        booking_id,
        service_type,
        updates,
        payment_updates=payment_updates,
    )
    final = _finalize_booking_write(
        "reschedule_booking",
        context,
        booking_id,
        service_type,
        update_result,
        expected={
            "booking_date": new_date,
            "booking_time": new_time,
            "check_out_date": new_check_out_date or None,
            "check_out_time": new_check_out_time or None,
        },
    )
    if final.get("status") == "success" and payment_updates:
        try:
            payment_rows = (
                get_supabase_client()
                .table("payment")
                .select("base_price, final_amount")
                .eq("company_id", context.company_id)
                .eq("payment_id", int(booking.get("payment_id")))
                .limit(1)
                .execute()
                .data
                or []
            )
            if not payment_rows:
                raise RuntimeError("Linked payment read-back returned no row")
            for field, expected_amount in payment_updates.items():
                actual_amount = payment_rows[0].get(field)
                if round(float(actual_amount), 2) != round(float(expected_amount), 2):
                    raise RuntimeError(f"Payment {field} read-back did not match the rescheduled total")
        except Exception as exc:
            return _result(
                "reschedule_booking",
                "error",
                dict(final.get("data") or {}),
                f"Booking/payment verification failed after rescheduling: {exc}",
                handoff_required=True,
                handoff_reason="DATABASE_ERROR",
            )
    if final.get("status") == "success" and context.resolved_customer_id is not None:
        holder = str(context.resolved_customer_id)
        client = get_supabase_client()
        _release_shared_holds(client, context.company_id, holder)
        SLOT_HOLDS.release_all_for(holder, prefix=(context.company_id, service_type.upper()))
        if service_type.upper() == "BOARDING":
            SLOT_HOLDS.release_all_for(holder, prefix=(context.company_id, "BOARDING_ROOM"))
    if final.get("status") == "success":
        from app.documents.service import generate_and_send_booking_confirmation

        confirmation_result = generate_and_send_booking_confirmation(context.company_id, dict(final.get("data") or {}))
        final["data"]["_internal_confirmation_url"] = confirmation_result.get("document_url")
        confirmation_send_status = str(
            (confirmation_result.get("send_result") or {}).get("status") or ""
        )
        final["data"]["confirmation_delivery_status"] = (
            confirmation_send_status or confirmation_result.get("status")
        )
        if (
            confirmation_result.get("status") != "success"
            or confirmation_send_status not in {"sent", "sent_console"}
        ):
            final["handoff_required"] = True
            final["handoff_reason"] = "DOCUMENT_DELIVERY_ERROR"
    return final


def register_loyalty_member(context: CustomerContext, confirmed: bool = False) -> dict:
    """
    Enrol the resolved customer in the loyalty program (idempotent — an
    existing member is returned as-is rather than duplicated). Requires
    confirmed=True — without it, returns confirmation_required instead of
    writing anything, so a customer is never silently signed up without
    having actually agreed (confirmed live: the model called this
    unprompted, with no offer ever reaching the customer, the moment it was
    reachable at all).
    """
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("register_loyalty_member")
    existing = check_loyalty_points(context)
    if existing.get("status") == "success":
        return _result(
            "register_loyalty_member",
            "success",
            {**(existing.get("data") or {}), "already_member": True},
        )
    if not confirmed:
        return _result(
            "register_loyalty_member",
            "confirmation_required",
            {},
            (
                "Ask the customer if they'd like to join the loyalty program first "
                "(mention: earn points on bookings, redeemable later for coupons/"
                "discounts). Only call register_loyalty_member again with "
                "confirmed=true if they actually say yes — do not enrol them "
                "without an explicit yes."
            ),
        )
    try:
        client = get_supabase_client()
        row = {
            "company_id": context.company_id,
            "customer_id": context.resolved_customer_id,
            "tier": "Bronze",
            "points_balance": 0,
            "redemption_made": 0,
        }
        inserted = client.table("loyaltymember").insert(row).execute().data or []
        record = inserted[0] if inserted else row
        return _result(
            "register_loyalty_member",
            "success",
            {
                "loyalty_id": record.get("loyalty_id"),
                "points_balance": record.get("points_balance"),
                "tier": record.get("tier"),
                "already_member": False,
            },
        )
    except Exception as exc:
        return _result("register_loyalty_member", "error", {}, str(exc))


def check_membership_status(context: CustomerContext) -> dict:
    loyalty = check_loyalty_points(context)
    if loyalty.get("status") != "success":
        return _result("check_membership_status", loyalty.get("status", "error"), loyalty.get("data") or {}, loyalty.get("error"))
    data = dict(loyalty.get("data") or {})
    return _result(
        "check_membership_status",
        "success",
        {
            "customer_id": data.get("customer_id"),
            "membership_status": data.get("tier") or data.get("membership_status"),
            "tier": data.get("tier"),
            "points_balance": data.get("points_balance"),
        },
    )


def get_loyalty_account(context: CustomerContext) -> dict:
    customer_result = check_customer_by_phone(context) if context.has_phone else None
    loyalty_result = check_loyalty_points(context)
    if loyalty_result.get("status") == "success":
        data = dict(loyalty_result.get("data") or {})
        if customer_result and customer_result.get("status") == "success":
            data.update(
                {
                    "full_name": customer_result["data"].get("full_name"),
                    "phone_number": customer_result["data"].get("phone_number"),
                }
            )
        return _result("check_loyalty_account", "success", data)
    return loyalty_result


def check_coupon_eligibility(context: CustomerContext) -> dict:
    """Return live loyalty balance plus currently redeemable coupon choices."""
    loyalty = check_loyalty_points(context)
    if loyalty.get("status") != "success":
        return _result(
            "check_coupon_eligibility",
            loyalty.get("status", "error"),
            loyalty.get("data") or {},
            loyalty.get("error"),
        )

    loyalty_data = dict(loyalty.get("data") or {})
    balance = int(loyalty_data.get("points_balance") or 0)
    try:
        response = (
            get_supabase_client()
            .table("coupon")
            .select("*")
            .eq("company_id", context.company_id)
            .execute()
        )
        today = today_business()
        available: list[dict] = []
        for row in response.data or []:
            required = int(row.get("points_required") or 0)
            expiry = _parse_date(row.get("expiry_date"))
            if required <= 0 or (expiry is not None and expiry < today):
                continue
            available.append(
                {
                    "coupon_id": row.get("coupon_id"),
                    "reward_name": row.get("reward_name"),
                    "reward_type": row.get("reward_type"),
                    "points_required": required,
                    "discount_value": row.get("discount_value")
                    or row.get("discount_value (RM)")
                    or row.get("discount_value_rm"),
                    "expiry_date": row.get("expiry_date"),
                    "eligible": required <= balance,
                    "points_short": max(required - balance, 0),
                }
            )
        available.sort(key=lambda item: item["points_required"])
        eligible = [item for item in available if item["eligible"]]
        next_coupon = next((item for item in available if not item["eligible"]), None)
        return _result(
            "check_coupon_eligibility",
            "success",
            {
                "customer_id": loyalty_data.get("customer_id"),
                "loyalty_id": loyalty_data.get("loyalty_id"),
                "points_balance": balance,
                "tier": loyalty_data.get("tier"),
                "eligible_coupons": eligible,
                "next_coupon": next_coupon,
            },
        )
    except Exception as exc:
        return _result("check_coupon_eligibility", "error", {}, str(exc))




def redeem_reward(context: CustomerContext, intent_json: dict) -> dict:
    """
    Submit a loyalty coupon redemption REQUEST against a specific unpaid
    payment — does NOT deduct any points itself. Calls the real
    request_redemption() Postgres function (backend/sql/
    verify_payment_function.sql, confirmed present in the live database),
    which only validates the member has enough points and creates a Pending
    redemption row + links it to the payment; the actual deduction happens
    only when staff approves it from the dashboard (decide_redemption) —
    per an explicit decision that redemption points must only ever be
    deducted at staff approval, never at the customer's request time. An
    earlier version of this function deducted immediately; do not revert to
    that.
    """
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("redeem_reward")

    entities = intent_json.get("entities") or {}
    try:
        payment_id = int(entities.get("payment_id"))
        coupon_id = int(entities.get("coupon_id"))
    except (TypeError, ValueError):
        return _result(
            "redeem_reward",
            "error",
            {},
            "payment_id and coupon_id are required — payment_id must be the real payment_id "
            "from a just-created booking (create_booking's result), and coupon_id from "
            "check_coupon_eligibility's eligible_coupons.",
            handoff_required=False,
        )

    # Ownership check: payment_id must belong to a booking for one of this
    # customer's own pets — otherwise any customer could redeem against any
    # other customer's payment just by guessing/quoting a payment_id.
    owned_pet_ids = {
        int(pet.get("pet_id"))
        for pet in (get_pets_by_customer_id(context).get("data") or {}).get("pets", [])
        if pet.get("pet_id") is not None
    }
    client = get_supabase_client()
    payment_pet_id = None
    for table in SERVICE_TYPE_TO_BOOKING_TABLE.values():
        rows = (
            client.table(table)
            .select("pet_id")
            .eq("company_id", context.company_id)
            .eq("payment_id", payment_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if rows:
            payment_pet_id = rows[0].get("pet_id")
            break
    if payment_pet_id is None or int(payment_pet_id) not in owned_pet_ids:
        return _result(
            "redeem_reward", "error", {}, "This payment does not belong to this customer",
            handoff_required=False,
        )

    try:
        result = client.rpc(
            "request_redemption",
            {"p_company_id": context.company_id, "p_payment_id": payment_id, "p_coupon_id": coupon_id},
        ).execute()
        data = result.data or {}
        redemption_id = data.get("redemption_id")
        if redemption_id is None or int(data.get("payment_id") or 0) != payment_id:
            raise RuntimeError("request_redemption returned an incomplete or mismatched result")
        payment_rows = (
            client.table("payment")
            .select("payment_id, redemption_id")
            .eq("company_id", context.company_id)
            .eq("payment_id", payment_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        redemption_rows = (
            client.table("redemption")
            .select("redemption_id, coupon_id, loyalty_spend, status")
            .eq("company_id", context.company_id)
            .eq("redemption_id", int(redemption_id))
            .limit(1)
            .execute()
            .data
            or []
        )
        if (
            not payment_rows
            or int(payment_rows[0].get("redemption_id") or 0) != int(redemption_id)
            or not redemption_rows
            or int(redemption_rows[0].get("coupon_id") or 0) != coupon_id
        ):
            raise RuntimeError("Redemption write verification failed after request")

        coupon_rows = (
            client.table("coupon")
            .select('reward_name, reward_type, "discount_value (RM)", points_required')
            .eq("company_id", context.company_id)
            .eq("coupon_id", coupon_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        coupon = coupon_rows[0] if coupon_rows else {}
        from app.documents.service import regenerate_booking_confirmation_for_payment

        confirmation = regenerate_booking_confirmation_for_payment(context.company_id, payment_id)
        confirmation_send_status = str(
            (confirmation.get("send_result") or {}).get("status") or ""
        )
        confirmation_delivered = (
            confirmation.get("status") == "success"
            and confirmation_send_status in {"sent", "sent_console"}
        )
        return _result(
            "redeem_reward",
            "success",
            {
                "redemption_id": redemption_id,
                "payment_id": data.get("payment_id"),
                "redemption_status": data.get("status"),
                "points_requested": data.get("points_requested"),
                "reward_name": coupon.get("reward_name"),
                "reward_type": coupon.get("reward_type"),
                "discount_value": coupon.get("discount_value (RM)"),
                "confirmation_document_status": confirmation.get("status"),
                "confirmation_delivery_status": confirmation_send_status or confirmation.get("status"),
                "_internal_confirmation_url": confirmation.get("document_url"),
            },
            handoff_required=not confirmation_delivered,
            handoff_reason=None if confirmation_delivered else "DOCUMENT_DELIVERY_ERROR",
        )
    except Exception as exc:
        # request_redemption raises a real Postgres exception with the
        # actual reason (e.g. "Not enough points...", "This payment already
        # has a pending point redemption", "This voucher has expired") —
        # relay it plainly rather than a generic failure.
        error_text = str(exc)
        expected_business_error = any(
            marker in error_text.lower()
            for marker in (
                "not enough points",
                "already has a pending point redemption",
                "already has a approved point redemption",
                "voucher has expired",
                "can only be requested for an unpaid payment",
                "does not belong to this customer",
            )
        )
        return _result(
            "redeem_reward",
            "error",
            {},
            str(exc),
            handoff_required=not expected_business_error,
            handoff_reason=None if expected_business_error else "DATABASE_ERROR",
        )


def merge_session_booking_context(intent_json: dict, session) -> dict:
    """Attach session draft booking fields for database writes."""
    if session is None:
        return intent_json
    import copy

    updated = copy.deepcopy(intent_json)
    draft = dict(getattr(session, "draft_booking_payload", {}) or {})
    entities = dict(updated.get("entities") or {})
    if getattr(session, "customer_id", None) is not None:
        entities.setdefault("customer_id", session.customer_id)
    for key in ("pet_id", "pet_name", "preferred_date", "preferred_time", "service_type", "package_name"):
        if draft.get(key) and not str(entities.get(key) or "").strip():
            entities[key] = draft[key]
    if draft.get("booking_date"):
        entities.setdefault("preferred_date", draft["booking_date"])
    if draft.get("selected_slot"):
        entities.setdefault("preferred_time", draft["selected_slot"])
    updated["entities"] = entities
    if draft.get("service_type") and not str(updated.get("service_type") or "").strip():
        updated["service_type"] = draft["service_type"]
    if draft:
        updated["_draft_booking"] = draft
    return updated


def _resolved_customer_pet_ids(context: CustomerContext) -> list[int]:
    pets_result = get_pets_by_customer_id(context)
    if pets_result.get("status") != "success":
        return []
    return [
        int(pet["pet_id"])
        for pet in (pets_result.get("data", {}).get("pets") or [])
        if pet.get("pet_id") is not None
    ]


def get_payment_history(context: CustomerContext) -> dict:
    """Retrieve payments referenced by this customer's own bookings."""
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("get_payment_history")
    try:
        client = get_supabase_client()
        pet_ids = _resolved_customer_pet_ids(context)
        payment_ids: set[int] = set()
        for table in SERVICE_TYPE_TO_BOOKING_TABLE.values():
            if not pet_ids:
                break
            rows = (
                client.table(table)
                .select("payment_id")
                .eq("company_id", context.company_id)
                .in_("pet_id", pet_ids)
                .execute()
                .data
                or []
            )
            payment_ids.update(
                int(row["payment_id"])
                for row in rows
                if row.get("payment_id") is not None
            )
        if not payment_ids:
            return _result("get_payment_history", "success", {"payments": [], "count": 0})
        payments = (
            client.table("payment")
            .select(
                "payment_id, service, base_price, add_ons, final_amount, "
                "payment_method, date, status, paid_at"
            )
            .eq("company_id", context.company_id)
            .in_("payment_id", sorted(payment_ids))
            .order("date", desc=True)
            .execute()
            .data
            or []
        )
        return _result(
            "get_payment_history",
            "success",
            {"payments": payments[:10], "count": len(payments)},
        )
    except Exception as exc:
        return _result("get_payment_history", "error", {}, str(exc))


def get_redemption_history(context: CustomerContext) -> dict:
    """Retrieve redemptions through the authenticated customer's loyalty row."""
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("get_redemption_history")
    try:
        client = get_supabase_client()
        members = (
            client.table("loyaltymember")
            .select("loyalty_id")
            .eq("company_id", context.company_id)
            .eq("customer_id", context.resolved_customer_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not members:
            return _result("get_redemption_history", "success", {"redemptions": [], "count": 0})
        rows = (
            client.table("redemption")
            .select(
                "redemption_id, coupon_id, loyalty_earn, loyalty_spend, "
                "create_date, create_time, status, approved_date, approved_time"
            )
            .eq("company_id", context.company_id)
            .eq("loyalty_id", members[0].get("loyalty_id"))
            .order("create_date", desc=True)
            .execute()
            .data
            or []
        )
        coupon_ids = sorted(
            {int(row["coupon_id"]) for row in rows if row.get("coupon_id") is not None}
        )
        coupon_names: dict[int, str] = {}
        if coupon_ids:
            coupons = (
                client.table("coupon")
                .select("coupon_id, reward_name")
                .eq("company_id", context.company_id)
                .in_("coupon_id", coupon_ids)
                .execute()
                .data
                or []
            )
            coupon_names = {
                int(row["coupon_id"]): str(row.get("reward_name") or "")
                for row in coupons
                if row.get("coupon_id") is not None
            }
        redemptions = [
            {
                **row,
                "reward_name": coupon_names.get(int(row["coupon_id"]), "")
                if row.get("coupon_id") is not None
                else "",
            }
            for row in rows[:10]
        ]
        return _result(
            "get_redemption_history",
            "success",
            {"redemptions": redemptions, "count": len(rows)},
        )
    except Exception as exc:
        return _result("get_redemption_history", "error", {}, str(exc))


def get_message_history(context: CustomerContext) -> dict:
    """Retrieve only messages sent by the authenticated customer."""
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("get_message_history")
    try:
        rows = (
            get_supabase_client()
            .table("messages")
            .select(
                "message_id, sender_type, message_text, intent_label, "
                "receive_date, receive_time, reply_date, reply_time"
            )
            .eq("company_id", context.company_id)
            .eq("sender_type", "customer")
            .eq("sender_id", context.resolved_customer_id)
            .order("receive_date", desc=True)
            .order("receive_time", desc=True)
            .limit(20)
            .execute()
            .data
            or []
        )
        return _result(
            "get_message_history",
            "success",
            {"messages": rows, "count": len(rows)},
        )
    except Exception as exc:
        return _result("get_message_history", "error", {}, str(exc))


def get_company_information(context: CustomerContext) -> dict:
    """Return safe public business fields from companies."""
    try:
        rows = (
            get_supabase_client()
            .table("companies")
            .select(
                "company_name, country, street_address, city, state, postcode, "
                "business_description"
            )
            .eq("company_id", context.company_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        return _result(
            "get_company_information",
            "success" if rows else "not_found",
            {"company": rows[0] if rows else None},
        )
    except Exception as exc:
        return _result("get_company_information", "error", {}, str(exc))


def get_staff_directory(context: CustomerContext) -> dict:
    """Return staff-facing business fields while excluding every identifier."""
    try:
        rows = (
            get_supabase_client()
            .table("staff")
            .select("staff_name, role, status")
            .eq("company_id", context.company_id)
            .execute()
            .data
            or []
        )
        return _result(
            "get_staff_directory",
            "success",
            {"staff": rows, "count": len(rows)},
        )
    except Exception as exc:
        return _result("get_staff_directory", "error", {}, str(exc))


def relational_database_action(intent_json: dict, context: CustomerContext) -> dict:
    """Dispatch from the normalized scenario; never trust an LLM write-action override."""
    scenario_intent = str(intent_json.get("scenario_intent") or "UNKNOWN").strip().upper()

    if scenario_intent == "CONFIRM_BOOKING":
        return create_booking(context, intent_json)

    if scenario_intent == "CREATE_CUSTOMER":
        entities = intent_json.get("entities") or {}
        return create_customer(
            context,
            str(entities.get("customer_name") or entities.get("full_name") or "").strip(),
            str(context.phone_number or entities.get("phone_number") or "").strip(),
            str(entities.get("address") or "-").strip(),
        )

    if scenario_intent == "CREATE_PET":
        entities = intent_json.get("entities") or {}
        height_raw = entities.get("pet_height") or entities.get("height_cm")
        height_cm = None
        if str(height_raw or "").strip().isdigit():
            height_cm = int(str(height_raw).strip())
        return create_pet(
            context,
            pet_name=str(entities.get("pet_name") or "").strip(),
            pet_type=str(entities.get("pet_type") or "").strip(),
            size=str(entities.get("pet_size") or entities.get("size") or "").strip(),
            height_cm=height_cm,
            breed=str(entities.get("breed") or "").strip(),
        )

    if scenario_intent == "CANCEL_BOOKING":
        return cancel_booking(context, intent_json)

    if scenario_intent == "RESCHEDULE_BOOKING":
        return reschedule_booking(context, intent_json)

    if scenario_intent == "REDEEM_REWARD":
        return redeem_reward(context, intent_json)

    if scenario_intent == "MAKE_BOOKING":
        return check_available_slots(context, intent_json)

    if scenario_intent == "VIEW_BOOKING_STATUS":
        return check_booking_status(context, intent_json)
    if scenario_intent == "CHECK_AVAILABILITY":
        return check_available_slots(context, intent_json)
    if scenario_intent == "CHECK_MEMBERSHIP_STATUS":
        return check_membership_status(context)
    if scenario_intent == "CHECK_LOYALTY_POINTS":
        return check_loyalty_points(context)
    if scenario_intent == "LOYALTY_ACCOUNT_INQUIRY":
        return get_loyalty_account(context)
    if scenario_intent == "CHECK_COUPON_ELIGIBILITY":
        return check_coupon_eligibility(context)
    if scenario_intent == "GET_BOOKING_SERVICE_OPTIONS":
        entities = intent_json.get("entities") or {}
        return get_booking_service_options(
            context,
            str(intent_json.get("service_type") or entities.get("service_type") or ""),
            str(entities.get("pet_type") or ""),
        )
    if scenario_intent == "GET_PET_PROFILES":
        entities = intent_json.get("entities") or {}
        pet_name = str(entities.get("pet_name") or "").strip()
        if pet_name:
            return get_pet_by_customer_and_name(context, pet_name)
        return get_pets_by_customer_id(context)

    if scenario_intent == "VIEW_PAYMENT_HISTORY":
        return get_payment_history(context)
    if scenario_intent == "VIEW_REDEMPTION_HISTORY":
        return get_redemption_history(context)
    if scenario_intent == "VIEW_MESSAGE_HISTORY":
        return get_message_history(context)
    if scenario_intent == "VIEW_COMPANY_INFORMATION":
        return get_company_information(context)
    if scenario_intent == "VIEW_STAFF_DIRECTORY":
        return get_staff_directory(context)
    if scenario_intent == "VIEW_ACCOUNT_STATUS":
        return check_customer_by_phone(context)

    if scenario_intent == "CUSTOMER_GREETING":
        return check_customer_by_phone(context)

    if scenario_intent == "REPEAT_LAST_BOOKING":
        return check_last_booking(context)

    return _result("unknown_database_action", "not_applicable", {}, None)
