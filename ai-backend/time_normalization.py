"""
Shared time normalization for booking and availability flows.

All internal preferred_time values use HH:MM (24-hour, business-local).
Slot matching uses HH:MM:SS.
"""

from __future__ import annotations

import re
from datetime import datetime

# Business operates in local wall-clock time (no UTC conversion in availability).
BUSINESS_TIMEZONE = "Asia/Kuala_Lumpur"

_PERIOD_TOKENS = frozenset({"morning", "afternoon", "evening", "night", "noon"})

_CLOCK_PATTERN = re.compile(
    r"\b("
    r"noon|midnight|"
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)|"
    r"\d{1,2}:\d{2}"
    r")\b",
    re.I,
)


def is_period_token(value: str) -> bool:
    return str(value or "").strip().lower() in _PERIOD_TOKENS


def normalize_time(value: str) -> str:
    """
    Normalize a time expression to canonical HH:MM (24-hour).

    Returns empty string when the value is not a concrete clock time.
    Period tokens (morning, afternoon, …) are not normalized here.
    """
    text = str(value or "").strip().lower()
    if text == "noon":
        return "12:00"
    if text == "midnight":
        return "00:00"
    if is_period_token(text):
        return text

    compact = re.sub(r"\s+", "", text)
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(compact, fmt)
            return parsed.strftime("%H:%M")
        except ValueError:
            continue

    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", compact, re.I)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3).lower()
        if meridiem == "am":
            if hour == 12:
                hour = 0
        elif hour != 12:
            hour += 12
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"

    match = re.match(r"^(\d{1,2})\s*(am|pm)$", compact, re.I)
    if match:
        return normalize_time(f"{match.group(1)}{match.group(2)}")

    return ""


def normalize_time_to_slot(value: str) -> str:
    """Normalize to HH:MM:SS for slot comparison."""
    if is_period_token(value):
        return str(value or "").strip().lower()
    normalized = normalize_time(value)
    if not normalized:
        return ""
    return f"{normalized}:00"


def extract_time_from_message(message: str) -> str:
    """Extract and normalize a clock time from a user message."""
    text = str(message or "").strip().lower()
    if not text:
        return ""
    for token in _PERIOD_TOKENS:
        if re.search(rf"\b{token}\b", text):
            return token
    match = _CLOCK_PATTERN.search(text)
    if not match:
        return ""
    raw = match.group(1).strip()
    if is_period_token(raw):
        return raw
    return normalize_time(raw)


def format_time_for_display(value: str) -> str:
    """Format HH:MM or HH:MM:SS as 12-hour display (e.g. 3:00 PM)."""
    slot = normalize_time_to_slot(value)
    if is_period_token(slot):
        return slot.title()
    if not slot:
        return str(value or "").strip() or "your preferred time"
    try:
        parsed = datetime.strptime(slot, "%H:%M:%S")
        hour = parsed.strftime("%I").lstrip("0") or "12"
        return f"{hour}:{parsed.strftime('%M %p')}"
    except ValueError:
        return str(value or "").strip()


def times_equal(left: str, right: str) -> bool:
    left_slot = normalize_time_to_slot(left)
    right_slot = normalize_time_to_slot(right)
    if not left_slot or not right_slot:
        return False
    if is_period_token(left_slot) or is_period_token(right_slot):
        return left_slot == right_slot
    return left_slot == right_slot


def slot_matches_preference(slot: str, preferred_time: str) -> bool:
    """True when a database slot matches the customer's preferred time or period."""
    pref = str(preferred_time or "").strip().lower()
    if not pref:
        return False
    slot_norm = normalize_time_to_slot(slot)
    if not slot_norm:
        return False

    if is_period_token(pref):
        try:
            hour = datetime.strptime(slot_norm, "%H:%M:%S").hour
        except ValueError:
            return False
        if pref == "morning":
            return hour < 12
        if pref == "afternoon":
            return 12 <= hour < 17
        if pref in {"evening", "night"}:
            return hour >= 17
        if pref == "noon":
            return 11 <= hour <= 13
        return False

    pref_norm = normalize_time_to_slot(pref)
    return pref_norm == slot_norm
