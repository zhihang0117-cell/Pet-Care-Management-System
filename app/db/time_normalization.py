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

# Chinese and Malay (Bahasa Malaysia) period-of-day words, mapped to the SAME
# canonical English tokens used everywhere else in this module
# (slot_matches_preference's hour-range logic below is keyed to these exact
# strings) — this module previously had zero non-English support at all, so
# a customer saying "明天早上"/"晚上" never resolved to a time or period;
# resolve_datetime/check_availability just silently got nothing from it
# (ambiguous=True), leaving the whole thing to the LLM to guess/translate
# inconsistently turn to turn. Malay entries added the same way: this is a
# Malaysia-based business (BUSINESS_TIMEZONE=Asia/Kuala_Lumpur) where a
# customer messaging in Malay is a normal, expected case, not an edge case —
# every OTHER deterministic guardrail in this codebase (breed/height
# confirmation, ordinal "the second one" detection, loyalty consent capture)
# only ever had English+Chinese coverage, silently falling back to trusting
# the LLM alone for Malay wording instead of the same deterministic check
# English/Chinese speakers get.
_PERIOD_TOKEN_ALIASES = {
    "早上": "morning", "上午": "morning", "清晨": "morning", "早": "morning",
    "中午": "noon", "正午": "noon",
    "下午": "afternoon", "午后": "afternoon",
    "晚上": "evening", "傍晚": "evening", "夜晚": "evening", "晚": "evening",
    "pagi": "morning",
    "tengah hari": "noon", "tengahari": "noon",
    "petang": "afternoon",
    "malam": "evening",
}


def _canonicalize_period(value: str) -> str | None:
    text = str(value or "").strip().lower()
    if text in _PERIOD_TOKENS:
        return text
    # .lower() here too: Chinese entries are case-insensitive by nature so
    # this never mattered for them, but the Malay/Latin-script entries above
    # need it — a bare .strip() previously missed "Pagi"/"PAGI" against the
    # lowercase dict keys.
    return _PERIOD_TOKEN_ALIASES.get(text)


_CLOCK_PATTERN = re.compile(
    r"\b("
    r"noon|midnight|"
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)|"
    r"\d{1,2}:\d{2}"
    r")\b",
    re.I,
)

_CHINESE_CLOCK_PATTERN = re.compile(
    r"(?:(早上|上午|清晨|中午|下午|午后|傍晚|晚上|夜晚)\s*)?"
    r"([零〇一二两三四五六七八九十\d]{1,3})"
    r"(?:点|時|时)"
    r"(?:(半)|([零〇一二两三四五六七八九十\d]{1,3})\s*分?)?"
)

_CHINESE_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                   "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _parse_chinese_number(value: str) -> int | None:
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        left, right = text.split("十", 1)
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        ones = _CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(text) == 1:
        return _CHINESE_DIGITS.get(text)
    return None


def extract_duration_minutes(message: str) -> int | None:
    """Extract a stated visit duration such as ``3 hours``, ``三个小时``, or ``3 jam``."""
    text = str(message or "").strip().lower()
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", text)
    if match:
        minutes = round(float(match.group(1)) * 60)
        return minutes if 0 < minutes <= 24 * 60 else None
    match = re.search(r"([零〇一二两三四五六七八九十\d]{1,3})(?:个)?(?:小时|鐘頭|钟头)", text)
    if match:
        hours = _parse_chinese_number(match.group(1))
        return hours * 60 if hours and hours <= 24 else None
    # Malay (Bahasa Malaysia) — "jam" (hours). Digit-only, same as the
    # English branch above; Malay number WORDS (satu, dua, tiga...) aren't
    # parsed here since a duration is overwhelmingly typed as a digit even
    # in an otherwise-Malay message (e.g. "3 jam", not "tiga jam").
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*jam\b", text)
    if match:
        minutes = round(float(match.group(1)) * 60)
        return minutes if 0 < minutes <= 24 * 60 else None
    match = re.search(r"\b(\d+)\s*(?:minutes?|mins?)\b", text)
    if match:
        minutes = int(match.group(1))
        return minutes if 0 < minutes <= 24 * 60 else None
    match = re.search(r"([零〇一二两三四五六七八九十\d]{1,3})\s*分钟", text)
    if match:
        minutes = _parse_chinese_number(match.group(1))
        return minutes if minutes and minutes <= 24 * 60 else None
    match = re.search(r"\b(\d+)\s*minit\b", text)
    if match:
        minutes = int(match.group(1))
        return minutes if 0 < minutes <= 24 * 60 else None
    return None


def extract_time_range(message: str) -> tuple[str, str, int] | None:
    """Extract an explicit same-day start/end pair and its duration."""
    text = str(message or "").strip()
    parts = re.split(r"\s+(?:to|until|till)\s+|\s*[-–—]\s*|\s*(?:到|至)\s*", text, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return None
    start = extract_time_from_message(parts[0])
    end = extract_time_from_message(parts[1])
    if not start or not end or is_period_token(start) or is_period_token(end):
        return None
    try:
        start_hour, start_minute = (int(part) for part in start.split(":"))
        end_hour, end_minute = (int(part) for part in end.split(":"))
    except (TypeError, ValueError):
        return None
    duration = (end_hour * 60 + end_minute) - (start_hour * 60 + start_minute)
    if duration <= 0:
        return None
    return start, end, duration


def is_period_token(value: str) -> bool:
    return _canonicalize_period(value) is not None


def normalize_time(value: str) -> str:
    """
    Normalize a time expression to canonical HH:MM (24-hour).

    Returns empty string when the value is not a concrete clock time.
    Period tokens (morning, afternoon, …, and their Chinese equivalents —
    see _PERIOD_TOKEN_ALIASES) are canonicalized to English here, not
    converted to a clock time.
    """
    text = str(value or "").strip().lower()
    if text == "noon":
        return "12:00"
    if text == "midnight":
        return "00:00"
    canonical_period = _canonicalize_period(value)
    if canonical_period:
        return canonical_period

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
    canonical_period = _canonicalize_period(value)
    if canonical_period:
        return canonical_period
    normalized = normalize_time(value)
    if not normalized:
        return ""
    return f"{normalized}:00"


def extract_time_from_message(message: str) -> str:
    """Extract and normalize a clock time from a user message."""
    text = str(message or "").strip().lower()
    if not text:
        return ""
    # Concrete clock expressions take priority over broad period words. The
    # old order returned just "evening" for "晚上7点", losing the exact time.
    chinese_clock = _CHINESE_CLOCK_PATTERN.search(text)
    if chinese_clock:
        period, hour_text, half, minute_text = chinese_clock.groups()
        hour = _parse_chinese_number(hour_text)
        minute = 30 if half else (_parse_chinese_number(minute_text) if minute_text else 0)
        if hour is not None and minute is not None and 0 <= minute <= 59:
            if period in {"下午", "午后", "傍晚", "晚上", "夜晚"} and hour < 12:
                hour += 12
            elif period in {"中午"} and hour < 11:
                hour += 12
            elif period in {"早上", "上午", "清晨"} and hour == 12:
                hour = 0
            if 0 <= hour <= 23:
                return f"{hour:02d}:{minute:02d}"

    match = _CLOCK_PATTERN.search(text)
    if match:
        raw = match.group(1).strip()
        if is_period_token(raw):
            return _canonicalize_period(raw)
        normalized = normalize_time(raw)
        if normalized:
            return normalized

    for token in _PERIOD_TOKENS:
        if re.search(rf"\b{token}\b", text):
            return token
    # Chinese period words have no spaces between characters, so a `\b`
    # word-boundary regex (meant for space-separated English words) isn't
    # the right tool here — a plain substring check is enough since these
    # are 1-3 character tokens unlikely to appear as an accidental
    # substring of something else in this domain.
    for alias, canonical in _PERIOD_TOKEN_ALIASES.items():
        if alias in text:
            return canonical
    return ""


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

    canonical_pref_period = _canonicalize_period(pref)
    if canonical_pref_period:
        try:
            hour = datetime.strptime(slot_norm, "%H:%M:%S").hour
        except ValueError:
            return False
        if canonical_pref_period == "morning":
            return hour < 12
        if canonical_pref_period == "afternoon":
            return 12 <= hour < 17
        if canonical_pref_period in {"evening", "night"}:
            return hour >= 17
        if canonical_pref_period == "noon":
            return 11 <= hour <= 13
        return False

    pref_norm = normalize_time_to_slot(pref)
    return pref_norm == slot_norm
