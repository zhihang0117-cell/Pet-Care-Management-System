"""
Mock database actions for Pawfect backend pipeline testing.

Simulates results from the Supabase relational database when DATABASE_PROVIDER=mock.
"""

from customer_context import missing_identity_result, phones_match

IDENTITY_SCENARIOS = {
    "VIEW_BOOKING_STATUS",
    "CHECK_LOYALTY_POINTS",
    "CHECK_MEMBERSHIP_STATUS",
    "LOYALTY_ACCOUNT_INQUIRY",
}

# Mirrors the Supabase test customer used in integration validation.
MOCK_CUSTOMERS = [
    {
        "customer_id": 1,
        "full_name": "Alicia Lee",
        "phone_number": "+60 12-345 6701",
        "address": "",
    },
    {
        "customer_id": 10,
        "full_name": "Sarah Tan",
        "phone_number": "+60 12-111 2221",
        "address": "",
    },
]

MOCK_LAST_BOOKING = {
    "+60 12-345 6701": {
        "booking_id": 101,
        "customer_id": 1,
        "pet_id": 101,
        "pet_name": "Milo",
        "last_service_type": "GROOMING",
        "service_name": "Grooming",
        "package_name": "Basic Grooming",
        "display_label": "Basic Grooming",
        "last_booking_date": "2026-07-24",
        "last_booking_time": "17:00:00",
        "booking_date": "2026-07-24",
        "booking_status": "confirmed",
        "source_table": "grooming_booking",
    },
    "+60 12-111 2221": {
        "booking_id": 5001,
        "customer_id": 10,
        "pet_id": 1001,
        "pet_name": "Milo",
        "last_service_type": "GROOMING",
        "service_name": "Grooming",
        "package_name": "Full Grooming",
        "display_label": "Full Grooming",
        "last_booking_date": "2026-07-20",
        "last_booking_time": "14:00:00",
        "booking_date": "2026-07-20",
        "booking_status": "confirmed",
        "source_table": "mock_booking",
    },
}

MOCK_CANCELLED_ONLY_PHONE = "+60 12-345 6702"

MOCK_CUSTOMERS.append(
    {
        "customer_id": 2,
        "full_name": "Angeline Tan",
        "phone_number": MOCK_CANCELLED_ONLY_PHONE,
        "address": "",
    },
)

MOCK_LAST_BOOKING[MOCK_CANCELLED_ONLY_PHONE] = {
    "booking_id": 201,
    "customer_id": 2,
    "pet_id": 201,
    "pet_name": "Cleo",
    "last_service_type": "GROOMING",
    "service_name": "Grooming",
    "package_name": "Basic Grooming",
    "display_label": "Basic Grooming",
    "last_booking_date": "2026-06-01",
    "booking_status": "cancelled",
    "source_table": "grooming_booking",
}

MOCK_PETS = {
    1: [
        {
            "pet_id": 101,
            "customer_id": 1,
            "pet_name": "Milo",
            "pet_type": "DOG",
            "breed": "Poodle",
            "size": "MEDIUM",
            "height_cm": 45,
        },
        {
            "pet_id": 102,
            "customer_id": 1,
            "pet_name": "Cleo",
            "pet_type": "CAT",
            "breed": "Persian",
            "size": "SMALL",
            "height_cm": 28,
        },
    ],
    10: [
        {
            "pet_id": 1001,
            "customer_id": 10,
            "pet_name": "Milo",
            "pet_type": "DOG",
            "breed": "Mixed",
            "size": "MEDIUM",
            "height_cm": 45,
        },
    ],
}


def mock_get_customer_pets(customer_id: int | None = None, phone_number: str = "") -> dict:
    """Mock pet lookup for local DATABASE_PROVIDER=mock runs."""
    resolved_id = customer_id
    if resolved_id is None and phone_number:
        matched = next(
            (row for row in MOCK_CUSTOMERS if phones_match(row.get("phone_number", ""), phone_number)),
            None,
        )
        if matched:
            resolved_id = matched.get("customer_id")

    if resolved_id is None:
        return _mock_result("get_pets_by_customer_id", "not_found", {})

    pets = list(MOCK_PETS.get(int(resolved_id), []))
    return _mock_result(
        "get_pets_by_customer_id",
        "success",
        {
            "customer_id": resolved_id,
            "pet_count": len(pets),
            "pets": pets,
        },
    )


def _mock_result(action: str, status: str, data: dict | None = None, error: str | None = None) -> dict:
    payload = {} if data is None else data
    if status == "success":
        data_found = bool(payload)
    elif status == "not_found":
        data_found = False
    else:
        data_found = False
    handoff_required = status == "error"
    handoff_reason = "DATABASE_ERROR" if status == "error" else None
    if status == "not_found":
        handoff_required = True
        if action in {"check_booking_status", "check_last_booking", "get_latest_booking_by_customer_id"}:
            handoff_reason = "BOOKING_NOT_FOUND"
        elif action in {"check_loyalty_points", "check_membership_status"}:
            handoff_reason = "LOYALTY_ACCOUNT_NOT_FOUND"
    return {
        "action": action,
        "status": status,
        "success": status in {"success", "not_found", "missing_information"},
        "data_found": data_found,
        "data": payload,
        "error": error,
        "handoff_required": handoff_required,
        "handoff_reason": handoff_reason,
    }


def mock_check_customer_by_phone(phone_number: str) -> dict:
    """Mock phone lookup for greeting / identity flows."""
    phone = str(phone_number or "").strip()
    if not phone:
        return missing_identity_result("check_customer_by_phone")

    matched = next(
        (row for row in MOCK_CUSTOMERS if phones_match(row.get("phone_number", ""), phone)),
        None,
    )
    if not matched:
        return _mock_result("check_customer_by_phone", "not_found", {"phone_number": phone})

    return _mock_result(
        "check_customer_by_phone",
        "success",
        {
            "customer_id": matched.get("customer_id"),
            "full_name": matched.get("full_name"),
            "phone_number": matched.get("phone_number"),
            "address": matched.get("address"),
        },
    )


def mock_get_latest_booking_by_customer_id(customer_id: int | None = None, phone_number: str = "") -> dict:
    phone = str(phone_number or "").strip()
    resolved_id = customer_id
    if resolved_id is None and phone:
        matched = next(
            (row for row in MOCK_CUSTOMERS if phones_match(row.get("phone_number", ""), phone)),
            None,
        )
        if matched:
            resolved_id = matched.get("customer_id")

    if resolved_id is None and not phone:
        return missing_identity_result("get_latest_booking_by_customer_id")

    if not phone and resolved_id is not None:
        matched = next((row for row in MOCK_CUSTOMERS if row.get("customer_id") == resolved_id), None)
        if matched:
            phone = str(matched.get("phone_number") or "")

    if not phone:
        return _mock_result(
            "get_latest_booking_by_customer_id",
            "not_found",
            {"customer_id": resolved_id},
        )

    last_booking = MOCK_LAST_BOOKING.get(phone)
    if not last_booking:
        return _mock_result(
            "get_latest_booking_by_customer_id",
            "not_found",
            {"customer_id": resolved_id},
        )

    from relational_actions import is_qualifying_previous_booking_status

    if not is_qualifying_previous_booking_status(last_booking.get("booking_status")):
        return _mock_result(
            "get_latest_booking_by_customer_id",
            "not_found",
            {"customer_id": resolved_id},
        )

    return _mock_result("get_latest_booking_by_customer_id", "success", dict(last_booking))


def mock_check_last_booking(phone_number: str) -> dict:
    result = mock_get_latest_booking_by_customer_id(phone_number=phone_number)
    if result.get("action") == "get_latest_booking_by_customer_id":
        result = {**result, "action": "check_last_booking"}
    return result


def mock_database_action(intent_json: dict, customer_id: str, phone_number: str) -> dict:
    """
    Simulate a database lookup or action based on scenario_intent.

    Args:
        intent_json: Output from intent detection.
        customer_id: Customer identifier from the chat request (optional for local testing).
        phone_number: WhatsApp sender ID / optional local test field.

    Returns:
        A mock database result dict with action, status, data, and error.
    """
    scenario_intent = intent_json.get("scenario_intent", "UNKNOWN")
    service_type = intent_json.get("service_type", "UNKNOWN")
    phone = str(phone_number or "").strip()
    has_identity = bool(phone or str(customer_id or "").strip())

    if scenario_intent == "CUSTOMER_GREETING":
        return mock_check_customer_by_phone(phone)

    if scenario_intent == "REPEAT_LAST_BOOKING":
        return mock_check_last_booking(phone)

    if scenario_intent in IDENTITY_SCENARIOS and not has_identity:
        action_map = {
            "VIEW_BOOKING_STATUS": "check_booking_status",
            "CHECK_LOYALTY_POINTS": "check_loyalty_points",
            "CHECK_MEMBERSHIP_STATUS": "check_loyalty_points",
            "LOYALTY_ACCOUNT_INQUIRY": "check_loyalty_account",
        }
        return missing_identity_result(action_map.get(scenario_intent, "unknown_database_action"))

    if scenario_intent == "VIEW_BOOKING_STATUS":
        return _mock_result(
            "check_booking_status",
            "success",
            {
                "booking_id": "B001",
                "service_type": "GROOMING",
                "booking_date": "2026-07-15",
                "booking_time": "10:00 AM",
                "booking_status": "confirmed",
            },
        )

    if scenario_intent == "CHECK_AVAILABILITY":
        entities = intent_json.get("entities") or {}
        from time_normalization import normalize_time

        requested_time = normalize_time(str(entities.get("preferred_time") or ""))
        booking_date = str(entities.get("preferred_date") or "2026-07-23")
        slots = [
            "09:00:00",
            "10:00:00",
            "14:00:00",
            "15:00:00",
            "16:30:00",
        ]
        occupied: set[str] = set()
        if requested_time == "15:00" and str(intent_json.get("_mock_unavailable_15") or "").strip():
            occupied = {"15:00:00"}
        available = [slot for slot in slots if slot not in occupied]
        return _mock_result(
            "check_available_slots",
            "success",
            {
                "service_type": service_type,
                "booking_date": booking_date,
                "available_slots": available,
                "requested_time": requested_time,
                "requested_date": booking_date,
                "service_duration_minutes": 90,
            },
        )

    if scenario_intent == "CHECK_LOYALTY_POINTS":
        return _mock_result(
            "check_loyalty_points",
            "success",
            {
                "customer_id": customer_id,
                "loyalty_points": 120,
            },
        )

    if scenario_intent == "REDEEM_REWARD":
        return _mock_result(
            "redeem_reward",
            "success",
            {
                "points_redeemed": 100,
                "points_balance": 20,
                "redemption_id": 999,
            },
        )

    if scenario_intent == "CHECK_MEMBERSHIP_STATUS":
        return _mock_result(
            "check_loyalty_points",
            "success",
            {
                "customer_id": customer_id,
                "membership_status": "active",
            },
        )

    if scenario_intent == "LOYALTY_ACCOUNT_INQUIRY":
        return _mock_result(
            "check_loyalty_account",
            "success",
            {
                "customer_id": customer_id,
                "loyalty_points": 120,
                "membership_status": "active",
            },
        )

    if scenario_intent == "CANCEL_BOOKING":
        return _mock_result(
            "cancel_booking",
            "success",
            {
                "booking_id": "B001",
                "booking_status": "Cancelled",
                "service_type": "GROOMING",
            },
        )

    if scenario_intent == "RESCHEDULE_BOOKING":
        entities = intent_json.get("entities") or {}
        return _mock_result(
            "reschedule_booking",
            "success",
            {
                "booking_id": "B001",
                "booking_status": "Pending",
                "booking_date": entities.get("new_preferred_date") or entities.get("preferred_date") or "2026-07-25",
                "booking_time": entities.get("new_preferred_time") or entities.get("preferred_time") or "14:00:00",
            },
        )

    if scenario_intent == "CONFIRM_BOOKING":
        entities = intent_json.get("entities") or {}
        return _mock_result(
            "create_booking",
            "success",
            {
                "booking_id": 9001,
                "booking_status": "Pending",
                "service_type": intent_json.get("service_type") or "GROOMING",
                "pet_id": entities.get("pet_id"),
                "pet_name": entities.get("pet_name"),
                "booking_date": entities.get("preferred_date"),
                "booking_time": entities.get("preferred_time"),
                "service_name": entities.get("service_package")
                or entities.get("service_name")
                or "Full Grooming",
                "verified": True,
            },
        )

    return _mock_result("unknown_database_action", "not_applicable")
