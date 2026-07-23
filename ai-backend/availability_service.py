"""
Availability check result building and customer-facing replies.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from booking_draft import format_date_for_display, resolve_price_quote
from time_normalization import format_time_for_display, normalize_time, normalize_time_to_slot, slot_matches_preference

DEFAULT_SERVICE_DURATION_MINUTES = {
    "GROOMING": 90,
    "DAYCARE": 180,
    "BOARDING": 60,
}


def _slot_to_minutes(slot: str) -> int | None:
    normalized = normalize_time_to_slot(slot)
    if not normalized or ":" not in normalized:
        return None
    try:
        parsed = datetime.strptime(normalized, "%H:%M:%S")
        return parsed.hour * 60 + parsed.minute
    except ValueError:
        return None


def _minutes_to_slot(minutes: int) -> str:
    hours, mins = divmod(max(0, minutes), 60)
    return f"{hours:02d}:{mins:02d}:00"


def service_duration_minutes(service_type: str) -> int:
    return DEFAULT_SERVICE_DURATION_MINUTES.get(str(service_type or "").upper(), 60)


def find_nearest_slots(available_slots: list[str], requested_time: str, *, limit: int = 2) -> list[str]:
    requested_minutes = _slot_to_minutes(requested_time)
    if requested_minutes is None:
        return []
    scored: list[tuple[int, str]] = []
    for slot in available_slots:
        slot_minutes = _slot_to_minutes(slot)
        if slot_minutes is None:
            continue
        scored.append((abs(slot_minutes - requested_minutes), slot))
    scored.sort(key=lambda item: (item[0], item[1]))
    seen: set[str] = set()
    nearest: list[str] = []
    for _, slot in scored:
        if slot in seen:
            continue
        seen.add(slot)
        nearest.append(slot)
        if len(nearest) >= limit:
            break
    return nearest


def build_availability_result(
    *,
    requested_date: str,
    requested_time: str,
    available_slots: list[str],
    service_type: str = "GROOMING",
) -> dict:
    """Structured availability result — single source of truth for the reply."""
    normalized_time = normalize_time(requested_time) or str(requested_time or "").strip().lower()
    slots = [str(s).strip() for s in (available_slots or []) if str(s).strip()]
    duration = service_duration_minutes(service_type)

    matched_slot: dict | None = None
    available = False
    for slot in slots:
        if slot_matches_preference(slot, normalized_time):
            start = normalize_time_to_slot(slot)
            try:
                start_dt = datetime.strptime(start, "%H:%M:%S")
                end_dt = start_dt + timedelta(minutes=duration)
                matched_slot = {
                    "start_time": normalize_time(start),
                    "end_time": end_dt.strftime("%H:%M"),
                }
                available = True
                break
            except ValueError:
                continue

    alternatives: list[str] = []
    if not available and normalized_time and not normalized_time in {"morning", "afternoon", "evening", "night", "noon"}:
        alternatives = [
            normalize_time(slot) for slot in find_nearest_slots(slots, normalized_time, limit=2)
        ]

    return {
        "requested_date": str(requested_date or "").strip(),
        "requested_time": normalized_time,
        "available": available,
        "matched_slot": matched_slot,
        "alternative_slots": alternatives,
        "service_duration_minutes": duration,
        "all_available_slots": [normalize_time(s) or s for s in slots],
    }


def build_availability_reply(session, availability_result: dict, intent_json: dict | None = None) -> str:
    """Customer-facing reply from structured availability result only."""
    intent_json = intent_json or {}
    result = availability_result or {}
    entities = dict(intent_json.get("entities") or {})

    service_type = str(
        intent_json.get("service_type")
        or entities.get("service_type")
        or getattr(session, "last_service_type", "")
        or "GROOMING"
    ).strip().upper()
    pet_name = str(
        entities.get("pet_name") or getattr(session, "pet_name", "") or ""
    ).strip()

    requested_date = str(result.get("requested_date") or "").strip()
    requested_time = str(result.get("requested_time") or "").strip()
    if not requested_date:
        time_text = format_time_for_display(requested_time)
        return f"What date would you like me to check for {time_text}?"

    date_text = format_date_for_display(requested_date)
    time_text = format_time_for_display(requested_time)
    service_label = service_type.replace("_", " ").title().lower()
    pet_phrase = f" for {pet_name}'s {service_label}" if pet_name else f" for {service_label}"

    if result.get("available") and result.get("matched_slot"):
        start = (result.get("matched_slot") or {}).get("start_time") or requested_time
        display_time = format_time_for_display(start)
        return (
            f"Yes, {display_time} on {date_text} is available{pet_phrase}.\n\n"
            "Would you like me to use this slot?"
        )

    alternatives = [t for t in (result.get("alternative_slots") or []) if t]
    if alternatives:
        alt_text = " and ".join(format_time_for_display(t) for t in alternatives)
        return (
            f"{time_text} is not available on {date_text}.\n\n"
            f"The nearest available times are {alt_text}. Which one would you prefer?"
        )

    if not result.get("all_available_slots"):
        return f"{time_text} is not available on {date_text}. Would you like to try another time?"

    return f"{time_text} is not available on {date_text}. Would you like to try another time?"


def enrich_database_result_with_availability(
    database_result: dict,
    *,
    requested_date: str,
    requested_time: str,
    service_type: str,
) -> dict:
    """Attach structured availability_result to a check_available_slots database result."""
    if str(database_result.get("action") or "") != "check_available_slots":
        return database_result
    data = dict(database_result.get("data") or {})
    availability = build_availability_result(
        requested_date=str(requested_date or data.get("booking_date") or "").strip(),
        requested_time=requested_time,
        available_slots=list(data.get("available_slots") or []),
        service_type=service_type,
    )
    data["availability_result"] = availability
    data["requested_time"] = availability.get("requested_time")
    data["requested_date"] = availability.get("requested_date")
    return {**database_result, "data": data}
