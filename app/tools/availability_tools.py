from __future__ import annotations

import time as time_module
from datetime import date as date_cls, timedelta
from typing import Literal

from langchain_core.tools import tool

from app.db.relational_provider import get_relational_repository
from app.tools.booking_window import booking_window_error, max_bookable_date, resolve_date_string
from app.tools.time_periods import filter_slots_by_period


_TRANSIENT_DATABASE_MARKERS = (
    "errno 35",
    "resource temporarily unavailable",
    "timed out",
    "timeout",
    "connection reset",
    "connection aborted",
    "temporary failure",
)
ServiceType = Literal["GROOMING", "DAYCARE", "BOARDING"]


def _is_transient_database_result(result: dict) -> bool:
    if result.get("status") != "error":
        return False
    error = str(result.get("error") or "").lower()
    return any(marker in error for marker in _TRANSIENT_DATABASE_MARKERS)


def _check_with_transient_retry(repo, company_id: int, *, service: str, date: str, time: str, intent_json=None):
    """Retry only temporary transport/resource failures, never business errors."""
    attempts = 3
    result = None
    for attempt in range(attempts):
        result = repo.check_availability(
            company_id, service=service, date=date, time=time, intent_json=intent_json
        )
        if not _is_transient_database_result(result) or attempt == attempts - 1:
            return result
        time_module.sleep(0.15 * (2**attempt))
    return result


@tool
def check_availability(
    company_id: str | int,
    service_type: ServiceType,
    date: str,
    time: str = "",
    room_type: str = "",
    check_out_date: str = "",
    customer_id: str | int = "",
    exclude_booking_id: str | int = "",
    duration_minutes: int | None = None,
    check_out_time: str = "",
) -> dict:
    """
    Check real availability (staff + leave + existing bookings) for a
    service on ONE specific date. Call this before offering a slot and before
    create_booking. For rescheduling, use it to find/present candidate slots;
    reschedule_booking also revalidates the final chosen interval internally.
    Never offer a slot you have not verified here. If
    resolve_datetime gave you a date_range instead of a single date, use
    check_availability_range instead. Bookings default to a 14-day
    suggestion window but an explicit further-out date is still checked
    normally (see BOOKING WINDOW).

    Pass the customer's stated period (morning/afternoon/evening/night) or
    exact time as `time` — matching slots are pre-filtered into
    available_slots for you; do not present slots outside what the
    customer asked for. For DAYCARE, also pass check_out_time or
    duration_minutes so the entire visit (not only drop-off) is checked
    against staff conflicts and closing time.

    For BOARDING, once the customer has picked a room (room_type, matching
    a real room_type from get_booking_service_options) and you know
    check_out_date, pass both — the result includes room_capacity (real
    capacity vs already-overlapping bookings for those nights), which is a
    separate constraint from staff availability; a room can be full even
    when a staff slot is free.

    If this check is for RESCHEDULING an existing booking (not a fresh
    booking), pass exclude_booking_id set to that booking's own booking_id —
    otherwise a single-capacity room's own current stay counts against
    itself and always reports "fully booked" for its own dates.
    """
    resolved_date = resolve_date_string(date)
    if resolved_date is None:
        return {"status": "error", "data": {}, "error": f"Could not resolve a date from {date!r}."}
    date = resolved_date

    window_error = booking_window_error(date)
    if window_error:
        return window_error

    if check_out_date:
        resolved_check_out = resolve_date_string(check_out_date)
        if resolved_check_out is None:
            return {
                "status": "error",
                "data": {},
                "error": f"Could not resolve a date from check_out_date {check_out_date!r}.",
            }
        check_out_date = resolved_check_out
        if date_cls.fromisoformat(check_out_date) <= date_cls.fromisoformat(date):
            return {
                "status": "error",
                "data": {},
                "error": (
                    f"check_out_date ({check_out_date}) is not strictly after "
                    f"date/check-in ({date}). This almost always means "
                    "check_out_date was computed/guessed instead of resolved — "
                    "re-run resolve_datetime on the customer's ORIGINAL check-out "
                    "date expression (not the check-in one) and retry with its "
                    "exact date output."
                ),
            }

    intent_json = None
    if room_type or check_out_date or customer_id or exclude_booking_id or duration_minutes or check_out_time:
        intent_json = {
            "entities": {
                "room_type": room_type,
                "check_out_date": check_out_date,
                "customer_id": customer_id,
                "exclude_booking_id": exclude_booking_id,
                "duration_minutes": duration_minutes,
                "check_out_time": check_out_time,
            }
        }
    result = _check_with_transient_retry(
        get_relational_repository(), int(company_id),
        service=service_type, date=date, time=time, intent_json=intent_json
    )
    if result.get("status") == "success":
        data = dict(result.get("data") or {})
        all_slots = data.get("available_slots", [])
        filtered = filter_slots_by_period(all_slots, time)
        if filtered is not None:
            data["available_slots"] = filtered
            data["all_available_slots"] = all_slots
        result = {**result, "data": data}
    return result


@tool
def check_availability_range(
    company_id: str | int, service_type: ServiceType, start_date: str, end_date: str, time: str = "",
    duration_minutes: int | None = None,
) -> dict:
    """
    Check real availability across a range of days (e.g. "next week", "this
    week"). Use this instead of check_availability when resolve_datetime
    returned a date_range rather than a single date — do not ask the
    customer to narrow it down to one specific day first. The range is
    automatically capped at the 14-day booking window.

    Pass the customer's stated period (morning/afternoon/evening/night) or
    exact time as `time` — each day's available_slots is pre-filtered for
    you; do not present slots outside what the customer asked for.
    """
    resolved_start = resolve_date_string(start_date)
    resolved_end = resolve_date_string(end_date)
    if resolved_start is None or resolved_end is None:
        return {"status": "error", "data": {}, "error": "Could not resolve a start_date/end_date."}
    start_date, end_date = resolved_start, resolved_end

    window_error = booking_window_error(start_date)
    if window_error:
        return window_error

    repo = get_relational_repository()
    start = date_cls.fromisoformat(start_date)
    end = min(date_cls.fromisoformat(end_date), max_bookable_date())

    days = []
    current = start
    while current <= end:
        result = _check_with_transient_retry(
            repo, int(company_id), service=service_type, date=current.isoformat(), time=time,
            intent_json={"entities": {"duration_minutes": duration_minutes}} if duration_minutes else None,
        )
        data = (result.get("data") or {}) if result.get("status") == "success" else {}
        all_slots = data.get("available_slots", [])
        filtered = filter_slots_by_period(all_slots, time)
        day_entry = {
            "date": current.isoformat(),
            "weekday": current.strftime("%A"),
            "available_slots": filtered if filtered is not None else all_slots,
        }
        if filtered is not None:
            day_entry["all_available_slots"] = all_slots
        days.append(day_entry)
        current += timedelta(days=1)

    return {
        "service_type": service_type,
        "start_date": start_date,
        "end_date": end.isoformat(),
        "days": days,
    }
