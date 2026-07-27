"""
Read-only Supabase relational database actions for Pawfect AI backend.

Phone number identity comes from WhatsApp webhook metadata / request context,
not from customer free-text messages.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from customer_context import (
    CustomerContext,
    missing_identity_result,
    normalize_phone_digits,
    phones_match,
)
from supabase_client import get_supabase_client

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

DEFAULT_OPEN_TIME = "09:00:00"
DEFAULT_CLOSE_TIME = "18:00:00"
SLOT_MINUTES = 60

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
        "update_loyalty_points",
        "create_loyalty_transaction",
    }
)


def _derive_handoff_meta(action: str, status: str, *, data_found: bool) -> tuple[bool, str | None]:
    if status == "missing_information":
        return False, None
    if status == "error":
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
        handoff_required, handoff_reason = _derive_handoff_meta(action, status, data_found=data_found)
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
    expected_date = str(expected.get("booking_date") or expected.get("preferred_date") or "").strip()
    if expected_date and str(record.get("booking_date") or record.get("check_in_date") or "").strip() != expected_date:
        mismatches.append("booking_date")
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
            handoff_required=True,
            handoff_reason="DATABASE_ERROR",
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
    if normalized in QUALIFYING_PREVIOUS_BOOKING_STATUSES:
        return True
    return normalized not in EXCLUDED_PREVIOUS_BOOKING_STATUSES


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
    pet_kind = str(pet_type or "").strip().upper()
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

        rows = (
            get_supabase_client()
            .table("services")
            .select("service_id, service_name, price_display")
            .eq("company_id", context.company_id)
            .execute()
            .data
            or []
        )
        options: list[dict] = []
        for row in rows:
            name = str(row.get("service_name") or "").strip()
            lowered = name.lower()
            if category == "DAYCARE":
                include = any(
                    token in lowered
                    for token in ("daycare", "splash pool", "enrichment class")
                )
            else:
                include = (
                    lowered.startswith(("standard bath", "premium bath", "luxury bath"))
                    or lowered in {
                        "cat trimming packages",
                        "dog bathing packages",
                        "dog trimming packages",
                    }
                )
                if pet_kind == "CAT" and lowered.startswith("dog "):
                    include = False
                if pet_kind == "DOG" and lowered.startswith("cat "):
                    include = False
            if include:
                options.append(
                    {
                        "service_id": row.get("service_id"),
                        "service_name": name,
                        "price_display": row.get("price_display"),
                    }
                )
        return _result(
            "get_booking_service_options",
            "success",
            {"service_type": category, "service_options": options},
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
        "total_price": row.get("total_price"),
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
    from date_normalization import parse_customer_date

    return parse_customer_date(value)


def _time_slots_for_day(open_time: str = DEFAULT_OPEN_TIME, close_time: str = DEFAULT_CLOSE_TIME) -> list[str]:
    start = datetime.strptime(open_time, "%H:%M:%S")
    end = datetime.strptime(close_time, "%H:%M:%S")
    slots: list[str] = []
    cursor = start
    while cursor < end:
        slots.append(cursor.strftime("%H:%M:%S"))
        cursor += timedelta(minutes=SLOT_MINUTES)
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


def _booking_interval(row: dict, service_type: str) -> tuple[int, int] | None:
    """Return the occupied [start, end) interval for one booking row."""
    service = str(service_type or "").strip().upper()
    start = _time_to_minutes(
        row.get("booking_time") if service == "GROOMING" else row.get("check_in_time")
    )
    if start is None:
        return None
    if service == "DAYCARE":
        end = _time_to_minutes(row.get("check_out_time"))
        if end is not None and end > start:
            return start, end
        return start, start + 180
    if service == "GROOMING":
        return start, start + 90
    return start, start + 60


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


def _slot_has_available_staff(
    slot: str,
    *,
    duration_minutes: int,
    available_staff: list[dict],
    bookings: list[dict],
    service_type: str,
) -> bool:
    start = _time_to_minutes(slot)
    if start is None:
        return False
    end = start + duration_minutes
    bookings_by_staff: dict[int, list[tuple[int, int]]] = {}
    for row in bookings:
        if not _booking_blocks_availability(row):
            continue
        raw_staff_id = row.get("staff_id")
        interval = _booking_interval(row, service_type)
        if raw_staff_id is None or interval is None:
            continue
        bookings_by_staff.setdefault(int(raw_staff_id), []).append(interval)
    for staff in available_staff:
        raw_staff_id = staff.get("staff_id")
        if raw_staff_id is None:
            continue
        intervals = bookings_by_staff.get(int(raw_staff_id), [])
        if all(end <= booked_start or start >= booked_end for booked_start, booked_end in intervals):
            return True
    return False


def check_available_slots(context: CustomerContext, intent_json: dict) -> dict:
    """
    Estimate available slots from active staff, approved leave, and existing bookings.

    Uses default business hours (09:00-18:00) because operating hours are not stored
    on the companies table.
    """
    entities = intent_json.get("entities") or {}
    service_type = str(
        intent_json.get("service_type") or entities.get("service_type") or "GROOMING"
    ).strip().upper()
    if service_type not in SERVICE_TYPE_TO_BOOKING_TABLE:
        service_type = "GROOMING"

    preferred_date = _parse_date(entities.get("preferred_date"))
    if preferred_date is None:
        return _result(
            "check_available_slots",
            "missing_information",
            {"missing_fields": ["preferred_date"]},
        )
    date_str = preferred_date.isoformat()

    try:
        client = get_supabase_client()
        staff_response = (
            client.table("staff")
            .select("staff_id, staff_name, status, off_days_json, role")
            .eq("company_id", context.company_id)
            .execute()
        )
        leave_response = (
            client.table("leave")
            .select("staff_id, start_date, end_date, status")
            .eq("company_id", context.company_id)
            .execute()
        )
        staff_rows = staff_response.data or []
        leave_rows = leave_response.data or []

        available_staff = [
            row for row in staff_rows if _staff_available_on_date(row, preferred_date, leave_rows)
        ]
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
        table = _service_table(service_type)
        booking_query = (
            client.table(table)
            .select("*")
            .eq("company_id", context.company_id)
            .in_("staff_id", staff_ids)
        )
        if table == "boarding_booking":
            booking_query = booking_query.eq("check_in_date", date_str)
        else:
            booking_query = booking_query.eq("booking_date", date_str)
        bookings = (booking_query.execute().data) or []

        from availability_service import service_duration_minutes

        duration_minutes = service_duration_minutes(service_type)
        close_minutes = _time_to_minutes(DEFAULT_CLOSE_TIME) or 18 * 60
        all_slots = [
            slot
            for slot in _time_slots_for_day()
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
            )
        ]

        entities = intent_json.get("entities") or {}
        from time_normalization import normalize_time

        requested_time = normalize_time(
            str(entities.get("preferred_time") or intent_json.get("preferred_time") or "")
        )

        return _result(
            "check_available_slots",
            "success",
            {
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
            },
        )
    except Exception as exc:
        return _result("check_available_slots", "error", {}, str(exc))


def _next_table_id(client, table: str, id_column: str) -> int:
    response = client.table(table).select(id_column).order(id_column, desc=True).limit(1).execute()
    rows = response.data or []
    if not rows:
        return 1
    try:
        return int(rows[0].get(id_column) or 0) + 1
    except (TypeError, ValueError):
        return 1


def _today_parts() -> tuple[str, str]:
    now = datetime.now()
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
    from time_normalization import normalize_time_to_slot

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
    from time_normalization import normalize_time_to_slot

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
        customer_id = _next_table_id(client, "customer", "customer_id")
        row = {
            "company_id": context.company_id,
            "customer_id": customer_id,
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
        pet_id = _next_table_id(client, "pet", "pet_id")
        row: dict[str, Any] = {
            "company_id": context.company_id,
            "pet_id": pet_id,
            "customer_id": context.resolved_customer_id,
            "pet_name": str(pet_name).strip(),
            "pet_type": str(pet_type or "Dog").strip(),
            "size": str(size or "M").strip(),
            "breed": str(breed or "-").strip() or "-",
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

    if not _slot_is_available(context, intent_json, booking_date=booking_date, booking_time=booking_time):
        return _result(
            "create_booking",
            "error",
            {"booking_date": booking_date, "booking_time": booking_time},
            "Requested slot is not available",
        )

    from booking_draft import select_staff_id

    availability = check_available_slots(context, intent_json)
    staff_id = select_staff_id((availability.get("data") or {}).get("available_staff") or [])

    try:
        client = get_supabase_client()
        table, _ = _booking_table_meta(service_type)
        created_date, created_time = _today_parts()
        status = "Pending"
        price = draft.get("price_quote")
        if price is None:
            price = entities.get("price")
        # Current Supabase monetary columns are int8, so keep the Python
        # payload integral until the schema is migrated to numeric/decimal.
        numeric_price = int(round(float(price or 0)))

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
                "add_on_price": int(round(float(entities.get("add_on_price") or 0))),
                "notes": str(entities.get("notes") or "-"),
                "created_date": created_date,
                "created_time": created_time,
            }
        elif table == "daycare_booking":
            check_out_time = _normalize_time_value(str(entities.get("check_out_time") or ""))
            if not check_out_time:
                start_minutes = _time_to_minutes(booking_time) or 0
                check_out_time = f"{(start_minutes + 180) // 60:02d}:{(start_minutes + 180) % 60:02d}:00"
            row = {
                "pet_id": pet_id,
                "staff_id": staff_id,
                "booking_date": booking_date,
                "check_in_time": booking_time,
                "check_out_time": check_out_time,
                "package_type": service_name,
                "booking_status": status,
                "price": numeric_price,
                "special_instruction": str(entities.get("special_instruction") or "-"),
                "created_date": created_date,
                "created_time": created_time,
            }
        else:
            parsed_check_in = _parse_date(booking_date)
            check_out_date = _resolve_booking_date(
                {"preferred_date": entities.get("check_out_date")}
            )
            if not check_out_date and parsed_check_in:
                check_out_date = (parsed_check_in + timedelta(days=1)).isoformat()
            check_out_time = _normalize_time_value(
                str(entities.get("check_out_time") or booking_time)
            )
            price_per_night = int(
                round(float(entities.get("price_per_night") or numeric_price or 0))
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
                "total_price": int(
                    round(float(entities.get("total_price") or price_per_night * nights))
                ),
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
            "add_ons": row.get("add_on") if table == "grooming_booking" else "",
            "final_amount": (
                (row.get("price") or 0) + (row.get("add_on_price") or 0)
                if table == "grooming_booking"
                else (row.get("total_price") if table == "boarding_booking" else row.get("price"))
            ) or 0,
            "payment_method": "",
            "date": created_date,
            "status": "Pending",
        }
        booking_type = service_type.lower()
        used_compensating_fallback = False
        try:
            atomic = client.rpc(
                "create_booking_atomic",
                {
                    "p_company_id": context.company_id,
                    "p_booking_type": booking_type,
                    "p_booking": row,
                    "p_payment": payment,
                },
            ).execute().data or {}
            record = dict(atomic.get("booking") or {})
        except Exception as rpc_exc:
            # Some deployed projects have not applied crud_consistency_functions.sql
            # yet. Preserve the booking/payment relationship with compensating
            # cleanup instead of silently reverting to an unlinked booking insert.
            if "PGRST202" not in str(rpc_exc) and "create_booking_atomic" not in str(rpc_exc):
                raise
            used_compensating_fallback = True
            table, id_column = _booking_table_meta(service_type)
            fallback_row = {
                "company_id": context.company_id,
                id_column: _next_table_id(client, table, id_column),
                **row,
            }
            inserted = client.table(table).insert(fallback_row).execute().data or []
            if not inserted:
                raise RuntimeError("Booking insert returned no row")
            record = dict(inserted[0])
            booking_id = record.get(id_column)
            payment_id = None
            try:
                payment_rows = (
                    client.table("payment")
                    .insert({"company_id": context.company_id, **payment})
                    .execute()
                    .data
                    or []
                )
                if not payment_rows:
                    raise RuntimeError("Payment insert returned no row")
                payment_id = payment_rows[0].get("payment_id")
                linked = (
                    client.table(table)
                    .update({"payment_id": payment_id})
                    .eq("company_id", context.company_id)
                    .eq(id_column, booking_id)
                    .execute()
                    .data
                    or []
                )
                if not linked:
                    raise RuntimeError("Booking payment link update returned no row")
                record = dict(linked[0])
            except Exception:
                if payment_id is not None:
                    (
                        client.table("payment")
                        .delete()
                        .eq("company_id", context.company_id)
                        .eq("payment_id", payment_id)
                        .execute()
                    )
                (
                    client.table(table)
                    .delete()
                    .eq("company_id", context.company_id)
                    .eq(id_column, booking_id)
                    .execute()
                )
                raise
        serialized = _serialize_booking_row(table, record)
        booking_id = int(serialized.get("booking_id"))
        expected = {
            "pet_id": pet_id,
            "booking_date": booking_date,
            "booking_time": booking_time,
            "service_name": service_name,
            "booking_status": status,
        }
        verified, persisted, mismatches = _verify_persisted_booking(
            context,
            booking_id,
            service_type,
            expected,
        )
        if not verified:
            if used_compensating_fallback:
                (
                    client.table(table)
                    .delete()
                    .eq("company_id", context.company_id)
                    .eq(_booking_table_meta(service_type)[1], booking_id)
                    .execute()
                )
                payment_id = record.get("payment_id")
                if payment_id is not None:
                    (
                        client.table("payment")
                        .delete()
                        .eq("company_id", context.company_id)
                        .eq("payment_id", payment_id)
                        .execute()
                    )
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
        return _result(
            "create_booking",
            "success",
            {
                **persisted,
                "customer_id": customer_id,
                "pet_id": pet_id,
                "payment_id": record.get("payment_id"),
                "verified": True,
            },
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
            "notes",
        }
        payload = {key: value for key, value in (updates or {}).items() if key in allowed and value is not None}
        if not payload:
            return _result("update_booking", "error", {}, "No valid booking fields to update")

        response = (
            client.table(table)
            .update(payload)
            .eq("company_id", context.company_id)
            .eq(id_column, int(booking_id))
            .execute()
        )
        rows = response.data or []
        if not rows:
            return _result("update_booking", "not_found", {"booking_id": booking_id})
        return _result("update_booking", "success", _serialize_booking_row(table, rows[0]))
    except Exception as exc:
        return _result("update_booking", "error", {}, str(exc))


def cancel_booking(context: CustomerContext, intent_json: dict) -> dict:
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("cancel_booking")

    booking = _locate_customer_booking(context, intent_json)
    if not booking:
        return _result("cancel_booking", "not_found", {}, "No booking found for this customer")

    if not _verify_booking_belongs_to_customer(context, booking):
        return _result("cancel_booking", "error", {}, "Booking does not belong to this customer")

    current_status = _normalize_booking_status(booking.get("booking_status"))
    if current_status in {"cancelled", "canceled"}:
        return _finalize_booking_write(
            "cancel_booking",
            context,
            int(booking.get("booking_id")),
            str(booking.get("service_type") or intent_json.get("service_type") or "GROOMING"),
            _result("cancel_booking", "success", booking),
            expected={"booking_status": "cancelled"},
        )

    service_type = str(booking.get("service_type") or intent_json.get("service_type") or "GROOMING")
    booking_id = int(booking.get("booking_id"))
    update_result = update_booking(context, booking_id, service_type, {"booking_status": "Cancelled"})
    return _finalize_booking_write(
        "cancel_booking",
        context,
        booking_id,
        service_type,
        update_result,
        expected={"booking_status": "cancelled"},
    )


def reschedule_booking(context: CustomerContext, intent_json: dict) -> dict:
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("reschedule_booking")

    booking = _locate_customer_booking(context, intent_json)
    if not booking:
        return _result("reschedule_booking", "not_found", {}, "No booking found for this customer")
    if not _verify_booking_belongs_to_customer(context, booking):
        return _result("reschedule_booking", "error", {}, "Booking does not belong to this customer")

    entities = intent_json.get("entities") or {}
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

    probe = dict(intent_json)
    probe_entities = dict(entities)
    probe_entities["preferred_date"] = new_date
    probe_entities["preferred_time"] = new_time
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
    if table == "daycare_booking":
        updates = {"booking_date": new_date, "check_in_time": new_time}
    elif table == "boarding_booking":
        updates = {"check_in_date": new_date, "check_in_time": new_time}
    update_result = update_booking(context, booking_id, service_type, updates)
    return _finalize_booking_write(
        "reschedule_booking",
        context,
        booking_id,
        service_type,
        update_result,
        expected={
            "booking_date": new_date,
            "booking_time": new_time,
        },
    )


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
        today = date.today()
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


def update_loyalty_points(context: CustomerContext, new_balance: int) -> dict:
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("update_loyalty_points")
    try:
        client = get_supabase_client()
        response = (
            client.table("loyaltymember")
            .update({"points_balance": int(new_balance)})
            .eq("company_id", context.company_id)
            .eq("customer_id", context.resolved_customer_id)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return _result("update_loyalty_points", "not_found", {"customer_id": context.resolved_customer_id})
        row = rows[0]
        return _result(
            "update_loyalty_points",
            "success",
            {
                "loyalty_id": row.get("loyalty_id"),
                "points_balance": row.get("points_balance"),
                "tier": row.get("tier"),
            },
        )
    except Exception as exc:
        return _result("update_loyalty_points", "error", {}, str(exc))


def create_loyalty_transaction(
    context: CustomerContext,
    *,
    loyalty_id: int,
    loyalty_spend: int = 0,
    loyalty_earn: int = 0,
    coupon_id: int | None = None,
) -> dict:
    try:
        client = get_supabase_client()
        redemption_id = _next_table_id(client, "redemption", "redemption_id")
        created_date, created_time = _today_parts()
        row = {
            "company_id": context.company_id,
            "redemption_id": redemption_id,
            "loyalty_id": int(loyalty_id),
            "loyalty_spend": int(loyalty_spend),
            "loyalty_earn": int(loyalty_earn),
            "coupon_id": coupon_id,
            "create_date": created_date,
            "create_time": created_time,
            "status": "Approved",
        }
        inserted = client.table("redemption").insert(row).execute().data or []
        record = inserted[0] if inserted else row
        return _result("create_loyalty_transaction", "success", record)
    except Exception as exc:
        return _result("create_loyalty_transaction", "error", {}, str(exc))


def redeem_reward(context: CustomerContext, intent_json: dict) -> dict:
    resolve_customer_context(context)
    if context.resolved_customer_id is None:
        return missing_identity_result("redeem_reward")

    entities = intent_json.get("entities") or {}
    points_raw = str(entities.get("reward_type") or entities.get("points_to_redeem") or "").strip()
    digits = "".join(ch for ch in points_raw if ch.isdigit())
    try:
        points_to_redeem = int(digits) if digits else 0
    except ValueError:
        points_to_redeem = 0
    if points_to_redeem <= 0:
        import re

        match = re.search(r"(\d+)\s*points?", str(intent_json.get("_source_message") or ""), re.I)
        if match:
            points_to_redeem = int(match.group(1))
    if points_to_redeem <= 0:
        return _result("redeem_reward", "error", {}, "Points amount to redeem was not provided")

    loyalty = check_loyalty_points(context)
    if loyalty.get("status") != "success":
        return _result("redeem_reward", loyalty.get("status", "error"), loyalty.get("data") or {}, loyalty.get("error"))

    data = loyalty.get("data") or {}
    balance = int(data.get("points_balance") or 0)
    loyalty_id = data.get("loyalty_id")
    if balance < points_to_redeem:
        return _result(
            "redeem_reward",
            "error",
            {"points_balance": balance, "points_requested": points_to_redeem},
            "Insufficient loyalty points for redemption",
        )
    if loyalty_id is None:
        return _result("redeem_reward", "error", {}, "Loyalty account not found")

    new_balance = balance - points_to_redeem
    updated = update_loyalty_points(context, new_balance)
    if updated.get("status") != "success":
        return _result("redeem_reward", "error", {}, updated.get("error") or "Could not update loyalty balance")

    transaction = create_loyalty_transaction(
        context,
        loyalty_id=int(loyalty_id),
        loyalty_spend=points_to_redeem,
    )
    if transaction.get("status") != "success":
        update_loyalty_points(context, balance)
        return _result(
            "redeem_reward",
            "error",
            {},
            transaction.get("error") or "Redemption record could not be created; balance restored",
            handoff_required=True,
            handoff_reason="DATABASE_ERROR",
        )

    try:
        client = get_supabase_client()
        client.table("loyaltymember").update(
            {"redemption_made": int(data.get("redemption_made") or 0) + 1}
        ).eq("loyalty_id", loyalty_id).execute()
    except Exception:
        pass

    verified = check_loyalty_points(context)
    if verified.get("status") != "success":
        return _result(
            "redeem_reward",
            "error",
            {},
            "Could not verify loyalty balance after redemption",
            handoff_required=True,
            handoff_reason="DATABASE_ERROR",
        )
    verified_balance = int((verified.get("data") or {}).get("points_balance") or -1)
    if verified_balance != new_balance:
        return _result(
            "redeem_reward",
            "error",
            {"expected_balance": new_balance, "verified_balance": verified_balance},
            "Loyalty balance verification failed after redemption",
            handoff_required=True,
            handoff_reason="DATABASE_ERROR",
        )

    return _result(
        "redeem_reward",
        "success",
        {
            "points_redeemed": points_to_redeem,
            "points_balance": verified_balance,
            "redemption_id": (transaction.get("data") or {}).get("redemption_id"),
            "verified": True,
            "rollback_note": (
                "Non-atomic redemption: balance update and transaction insert are separate "
                "operations with explicit rollback on transaction failure."
            ),
        },
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
