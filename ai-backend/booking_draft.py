"""
Booking draft and confirmation gate (Step 4 — read-only, no DB writes).

Builds a session-held draft after check_available_slots succeeds and handles
YES/confirm replies without inserting payment or booking rows.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from customer_context import CustomerContext, get_relational_company_id

AWAIT_BOOKING_CONFIRMATION = "await_booking_confirmation"

_CONFIRMATION_PATTERN = re.compile(
    r"^(?:yes|yeah|yep|yup|confirm|confirmed|book\s+it|proceed|go\s+ahead|looks\s+good|correct|ok(?:ay)?)\.?!?$",
    re.I,
)

_SLOT_ACCEPTANCE_PATTERN = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|ok(?:ay)?|please|confirm|book\s+it|use\s+(?:this|that)\s+slot)\.?!?$|"
    r"\b(use this slot|book that|book this|that works|sounds good)\b",
    re.I,
)

_AFTERNOON_START = "13:00:00"
_MORNING_END = "12:00:00"
_EVENING_START = "17:00:00"


def is_explicit_booking_request(message: str) -> bool:
    text = " ".join(str(message or "").strip().split()).lower()
    if not text:
        return False
    if is_repeat_last_booking_message(text):
        return False
    if re.search(r"\bbooking\s+status\b", text):
        return False
    if re.search(r"\b(check|what\s+is|what'?s)\s+(my\s+)?(booking|appointment)\s+status\b", text):
        return False
    patterns = (
        r"\bbook(?:ing)?\b",
        r"\bmake\s+a\s+(?:booking|appointment)\b",
        r"\bschedule\s+(?:a|an)\b",
        r"\breserve\b",
    )
    return any(re.search(p, text) for p in patterns)


def is_repeat_last_booking_message(message: str) -> bool:
    text = " ".join(str(message or "").strip().split()).lower()
    if not text:
        return False
    patterns = (
        r"same\s+service\s+as\s+last\s+time",
        r"same\s+as\s+last\s+time",
        r"repeat\s+my\s+last\s+booking",
        r"book\s+the\s+same\s+service\s+again",
        r"can\s+do\s+like\s+last\s+time",
        r"same\s+one\s+as\s+before",
        r"same\s+(grooming|daycare|boarding)\s+as\s+before",
        r"like\s+last\s+time",
    )
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def is_booking_confirmation_message(message: str) -> bool:
    text = " ".join(str(message or "").strip().split())
    if not text:
        return False
    return bool(_CONFIRMATION_PATTERN.match(text))


def is_slot_acceptance_message(message: str) -> bool:
    text = " ".join(str(message or "").strip().split())
    if not text:
        return False
    return bool(_SLOT_ACCEPTANCE_PATTERN.search(text))


def _format_service_label(service_type: str) -> str:
    text = str(service_type or "").strip()
    if not text or text.upper() == "UNKNOWN":
        return "Your service"
    return text.replace("_", " ").title()


def _parse_iso_date(value: str | None) -> date | None:
    from date_normalization import parse_customer_date

    return parse_customer_date(value)


def format_date_for_display(value: str | None) -> str:
    parsed = _parse_iso_date(value)
    if parsed:
        return f"{parsed.day} {parsed.strftime('%b %Y')}"
    text = str(value or "").strip()
    if text.lower() == "tomorrow":
        d = date.today() + timedelta(days=1)
        return f"{d.day} {d.strftime('%b %Y')}"
    return text or "your preferred date"


def _format_time_for_display(slot: str, preferred_time: str) -> str:
    from time_normalization import format_time_for_display

    slot_text = str(slot or "").strip()
    if slot_text:
        return format_time_for_display(slot_text)
    return format_time_for_display(preferred_time)


def _slot_in_preferred_window(slot: str, preferred_time: str) -> bool:
    from time_normalization import slot_matches_preference

    return slot_matches_preference(slot, preferred_time)


def select_slot_from_preference(available_slots: list[str], preferred_time: str) -> str:
    slots = [str(s).strip() for s in (available_slots or []) if str(s).strip()]
    if not slots:
        return ""
    matched = [slot for slot in slots if _slot_in_preferred_window(slot, preferred_time)]
    if matched:
        return sorted(matched)[0]
    return ""


def select_staff_id(available_staff: list[dict]) -> int | None:
    staff_ids: list[int] = []
    for row in available_staff or []:
        raw = row.get("staff_id")
        if raw is None:
            continue
        try:
            staff_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return min(staff_ids) if staff_ids else None


def resolve_pet_id(
    customer_id: int | None,
    pet_name: str,
    phone_number: str = "",
) -> int | None:
    if customer_id is None:
        return None
    name = str(pet_name or "").strip()
    if not name:
        return None

    from customer_context import get_relational_company_id
    from relational_provider import get_relational_repository

    repo = get_relational_repository()
    company_id = int(get_relational_company_id())
    result = repo.find_pet_by_name(company_id, int(customer_id), name)
    if result.get("status") == "success":
        raw = (result.get("data") or {}).get("pet_id")
        return int(raw) if raw is not None else None
    return None


def resolve_price_quote(service_type: str, entities: dict, company_id: int | None = None) -> float | None:
    """
    Return a numeric price only from approved relational sources (no LLM/RAG).
    Currently: boarding room nightly rate from room table when room_type is known.
    """
    svc = str(service_type or "").strip().upper()
    if svc != "BOARDING":
        return None

    room_type = str(entities.get("room_type") or "").strip()
    if not room_type:
        return None

    try:
        from supabase_client import get_supabase_client

        cid = company_id if company_id is not None else get_relational_company_id()
        client = get_supabase_client()
        response = (
            client.table("room")
            .select("price, room_type")
            .eq("company_id", cid)
            .eq("room_type", room_type)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        price = rows[0].get("price")
        if price is None:
            return None
        return float(price)
    except (TypeError, ValueError, ImportError):
        return None


def build_draft_booking_payload(
    *,
    service_type: str,
    pet_id: int | None,
    pet_name: str,
    preferred_date: str,
    preferred_time: str,
    booking_date: str,
    selected_slot: str,
    selected_staff_id: int | None,
    price_quote: float | None,
    customer_id: int | None,
    customer_name: str = "",
    entities: dict | None = None,
) -> dict:
    return {
        "service_type": str(service_type or "").strip().upper(),
        "pet_id": pet_id,
        "pet_name": str(pet_name or "").strip(),
        "preferred_date": str(preferred_date or "").strip(),
        "preferred_time": str(preferred_time or "").strip(),
        "booking_date": str(booking_date or "").strip(),
        "selected_slot": str(selected_slot or "").strip(),
        "selected_staff_id": selected_staff_id,
        "price_quote": price_quote,
        "customer_id": customer_id,
        "customer_name": str(customer_name or "").strip(),
        "entities": dict(entities or {}),
        "status": "awaiting_confirmation",
    }


def build_draft_confirmation_reply(session) -> str:
    payload = getattr(session, "draft_booking_payload", None) or {}
    service_type = str(payload.get("service_type") or getattr(session, "last_service_type", "") or "").strip()
    pet_name = str(payload.get("pet_name") or getattr(session, "pet_name", "") or "").strip()
    booking_date = str(payload.get("booking_date") or payload.get("preferred_date") or "").strip()
    selected_slot = str(payload.get("selected_slot") or getattr(session, "selected_slot", "") or "").strip()
    preferred_time = str(payload.get("preferred_time") or getattr(session, "preferred_time", "") or "").strip()

    if not service_type or service_type.upper() == "UNKNOWN":
        return "Which service would you like to book — grooming, daycare, or boarding?"
    if not pet_name:
        return "Which pet would this booking be for?"
    if not booking_date or not selected_slot:
        return "What date and time would you prefer for the booking?"

    service = _format_service_label(service_type)
    date_text = format_date_for_display(booking_date)
    time_text = _format_time_for_display(selected_slot, preferred_time)

    price = payload.get("price_quote")
    if price is None:
        price = getattr(session, "price_quote", None)

    if price is not None:
        try:
            price_num = float(price)
            return (
                f"{service} for {pet_name} on {date_text}, {time_text} is available. "
                f"Estimated price: RM{price_num:g}.\n\n"
                "Please confirm whether these details are correct."
            )
        except (TypeError, ValueError):
            pass

    return (
        f"{service} for {pet_name} on {date_text}, {time_text} is available.\n\n"
        "Please confirm whether these details are correct."
    )


def build_confirm_not_implemented_reply() -> str:
    return (
        "Your booking details are ready to confirm, but online booking confirmation "
        "is not enabled in this test version yet. Our team will assist you with the "
        "final confirmation 😊"
    )


def build_booking_confirmed_reply(database_result: dict, session=None) -> str:
    """Reply only after a verified Supabase booking insert/read-back."""
    if database_result.get("handoff_required"):
        from response_generator import handoff_reply_for_reason

        return handoff_reply_for_reason(str(database_result.get("handoff_reason") or "DATABASE_ERROR"))
    if str(database_result.get("status") or "").strip() != "success" or not database_result.get("data_found"):
        from response_generator import handoff_reply_for_reason

        return handoff_reply_for_reason("DATABASE_ERROR")

    data = dict(database_result.get("data") or {})
    if not data.get("verified"):
        from response_generator import handoff_reply_for_reason

        return handoff_reply_for_reason("DATABASE_ERROR")

    booking_id = data.get("booking_id") or ""
    status = str(data.get("booking_status") or "pending").strip().title()
    draft = dict(getattr(session, "draft_booking_payload", {}) or {}) if session is not None else {}
    pet_name = str(draft.get("pet_name") or data.get("pet_name") or "").strip()
    service = _format_service_label(str(draft.get("service_type") or data.get("service_type") or ""))
    booking_date = str(data.get("booking_date") or draft.get("booking_date") or "").strip()
    booking_time = str(data.get("booking_time") or draft.get("selected_slot") or "").strip()

    if not booking_id:
        from response_generator import handoff_reply_for_reason

        return handoff_reply_for_reason("DATABASE_ERROR")

    parts = [
        f"Your booking is confirmed with status {status}.",
        f"Reference: {booking_id}.",
    ]
    if service and pet_name:
        parts.append(f"{service} for {pet_name} has been submitted for review.")
    if booking_date and booking_time:
        parts.append(f"Date: {booking_date}, Time: {booking_time}.")
    confirmed_reply = " ".join(parts).strip()
    if session is not None and bool(getattr(session, "new_customer_session", False)):
        confirmed_reply += (
            "\n\nWould you like to join Pawfect Membership to start collecting "
            "loyalty points for future visits? 😊"
        )
    return confirmed_reply


def build_orphan_confirmation_reply() -> str:
    return "Which service would you like to book, and what date and time work for you?"


def should_create_booking_draft(session, intent_json: dict) -> bool:
    from booking_flow import has_complete_booking_fields, is_booking_collection_active

    if not is_booking_collection_active(session, intent_json):
        return False
    if not has_complete_booking_fields(session, intent_json):
        return False
    if str(getattr(session, "pending_action", "") or "").strip() == "make_booking_pending_info":
        return True
    if str(getattr(session, "last_scenario_intent", "") or "").strip() == "REPEAT_LAST_BOOKING":
        return True
    if getattr(session, "booking_creation_flow", False):
        return True
    return False


def build_confirm_booking_intent(session, intent_json: dict) -> dict:
    import copy

    updated = copy.deepcopy(intent_json)
    updated["main_intent"] = "BOOKING_INTENT"
    updated["scenario_intent"] = "CONFIRM_BOOKING"
    updated["database_action_needed"] = True
    updated["retrieval_needed"] = False
    updated["retrieval_source"] = []
    updated["database_action"] = "confirm_booking"
    updated["next_action"] = "confirm_booking"
    updated["missing_information"] = []
    updated["confidence"] = max(float(updated.get("confidence") or 0.0), 0.95)
    updated["reason"] = "Customer confirmed booking draft awaiting insert"
    if session.last_service_type:
        updated["service_type"] = session.last_service_type
    return updated


def build_orphan_confirmation_intent(intent_json: dict) -> dict:
    import copy

    updated = copy.deepcopy(intent_json)
    updated["main_intent"] = "BOOKING_INTENT"
    updated["scenario_intent"] = "BOOKING_CONFIRMATION_ORPHAN"
    updated["database_action_needed"] = False
    updated["retrieval_needed"] = False
    updated["retrieval_source"] = []
    updated["database_action"] = ""
    updated["next_action"] = "ask_missing_information"
    updated["missing_information"] = ["service_type", "preferred_date", "preferred_time"]
    updated["confidence"] = max(float(updated.get("confidence") or 0.0), 0.92)
    updated["reason"] = "Confirmation received without an active booking draft"
    return updated
