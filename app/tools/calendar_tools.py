from __future__ import annotations

from langchain_core.tools import tool

from app.db.date_normalization import extract_customer_date, parse_week_range
from app.db.time_normalization import (
    extract_duration_minutes,
    extract_time_from_message,
    extract_time_range,
    is_period_token,
)


@tool
def resolve_datetime(text: str) -> dict:
    """
    Resolve natural-language date/time text (e.g. "tomorrow", "this Saturday",
    "3pm", "afternoon", "next week") using the business's own date/time
    normalizers (business-local time, Asia/Kuala_Lumpur).

    Returns date (YYYY-MM-DD or None) for a single specific day, date_range
    ({"start":..., "end":...} or None) for vague week-level phrases like
    "next week"/"this week", time (HH:MM or None), period
    (morning/afternoon/evening/night/noon or None), end_time/duration_minutes
    for a stated range or duration, and ambiguous (true only when none of the
    above could be resolved).

    If date_range comes back instead of date, use check_availability_range
    (not check_availability) — do not ask the customer to narrow a range
    down to one specific day first.
    """
    parsed_date = extract_customer_date(text)
    date_range = None
    if parsed_date is None:
        range_result = parse_week_range(text)
        if range_result:
            date_range = {"start": range_result[0].isoformat(), "end": range_result[1].isoformat()}

    time_range = extract_time_range(text)
    extracted_time = time_range[0] if time_range else extract_time_from_message(text)
    period = extracted_time if extracted_time and is_period_token(extracted_time) else None
    clock_time = extracted_time if extracted_time and not period else None
    duration_minutes = extract_duration_minutes(text) or (time_range[2] if time_range else None)
    return {
        "date": parsed_date.isoformat() if parsed_date else None,
        "date_range": date_range,
        "time": clock_time,
        "end_time": time_range[1] if time_range else None,
        "period": period,
        "duration_minutes": duration_minutes,
        "ambiguous": parsed_date is None and date_range is None and not clock_time and not period and duration_minutes is None,
        "raw_text": text,
    }
