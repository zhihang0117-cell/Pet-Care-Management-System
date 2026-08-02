"""Normalize customer-friendly booking dates without asking them to use ISO."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_MONTH_NAMES = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)

# Chinese day-word and weekday aliases. This module previously had zero
# non-English support, so "明天"/"后天"/"星期一" etc. never resolved to a
# date at all — resolve_datetime returned ambiguous=True and the customer's
# actual date got left entirely to the LLM to translate/guess, turn to turn.
_CHINESE_RELATIVE_DAYS = {
    "今天": 0, "今日": 0,
    "明天": 1, "明日": 1,
    "后天": 2,
    "大后天": 3,
}
_CHINESE_WEEKDAY_NAMES = {
    1: ("星期一", "周一", "礼拜一"),
    2: ("星期二", "周二", "礼拜二"),
    3: ("星期三", "周三", "礼拜三"),
    4: ("星期四", "周四", "礼拜四"),
    5: ("星期五", "周五", "礼拜五"),
    6: ("星期六", "周六", "礼拜六"),
    7: ("星期日", "星期天", "周日", "周天", "礼拜日", "礼拜天"),
}
_CHINESE_WEEKDAY_TO_INDEX = {
    name: weekday_num - 1  # Monday=0 to match date.weekday()
    for weekday_num, names in _CHINESE_WEEKDAY_NAMES.items()
    for name in names
}
_CHINESE_NEXT_MODIFIERS = ("下个", "下")


def _parse_chinese_date(text: str, reference: date) -> date | None:
    """text: already stripped/lowercased (lower() is a no-op on CJK)."""
    if text in _CHINESE_RELATIVE_DAYS:
        return reference + timedelta(days=_CHINESE_RELATIVE_DAYS[text])
    calendar_match = re.fullmatch(
        r"(?:(明年|今年)\s*)?(?:(\d{4})年)?\s*(\d{1,2})月\s*(\d{1,2})(?:日|号)", text
    )
    if calendar_match:
        year_word, explicit_year, month_text, day_text = calendar_match.groups()
        year = int(explicit_year) if explicit_year else reference.year + (1 if year_word == "明年" else 0)
        try:
            parsed = date(year, int(month_text), int(day_text))
        except ValueError:
            return None
        if not explicit_year and not year_word and parsed < reference:
            try:
                parsed = parsed.replace(year=year + 1)
            except ValueError:
                return None
        return parsed
    for weekday_text, weekday_index in _CHINESE_WEEKDAY_TO_INDEX.items():
        is_next = False
        core = text
        for prefix in _CHINESE_NEXT_MODIFIERS:
            if core == f"{prefix}{weekday_text}":
                is_next = True
                core = weekday_text
                break
        if core != weekday_text:
            continue
        days_ahead = (weekday_index - reference.weekday()) % 7
        if is_next:
            days_ahead += 7
        elif days_ahead == 0:
            days_ahead = 7
        return reference + timedelta(days=days_ahead)
    return None


def parse_customer_date(value: str | None, *, today: date | None = None) -> date | None:
    if not value:
        return None
    reference = today or date.today()
    text = " ".join(str(value).strip().lower().replace(",", " ").split())
    if text == "today":
        return reference
    if text == "tomorrow":
        return reference + timedelta(days=1)
    chinese_result = _parse_chinese_date(str(value).strip(), reference)
    if chinese_result is not None:
        return chinese_result

    # "next year 30 august" / "30 august next year" — strip the modifier
    # before the month-day match below and force the year forward instead
    # of relying on that match's implicit "roll to next year only if the
    # bare date has already passed" behavior, which silently produces the
    # wrong year (e.g. returns this year's Aug 30 when the customer
    # explicitly said "next year" and today is already past Aug 1).
    next_year_modifier = bool(re.search(r"\bnext year\b", text))
    if next_year_modifier or re.search(r"\bthis year\b", text):
        text = re.sub(r"\b(?:next|this)\s+year\b", "", text)
        text = " ".join(text.split())

    weekday_match = re.fullmatch(
        r"(?:(next|this)\s+)?"
        r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)",
        text,
        re.I,
    )
    if weekday_match:
        modifier, weekday_text = weekday_match.groups()
        weekday_index = (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ).index(weekday_text.lower())
        days_ahead = (weekday_index - reference.weekday()) % 7
        if days_ahead == 0 or modifier == "next" and days_ahead == 0:
            days_ahead = 7
        return reference + timedelta(days=days_ahead)

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d/%m/%y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    # Support both "20 August[, 2026]" (day-then-month) and "August 20[, 2026]"
    # (month-then-day, the common US-style phrasing) — customers stating an
    # explicit calendar date (most often for BOARDING check-in/check-out,
    # where a relative phrase like "tomorrow" doesn't fit a date weeks out)
    # use either order, and only the first was recognized before, silently
    # forcing every "Aug 20"-style date into "ambiguous".
    day_month = re.fullmatch(
        rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_NAMES})(?:\s+(\d{{4}}))?",
        text,
        re.I,
    )
    month_day = re.fullmatch(
        rf"({_MONTH_NAMES})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+(\d{{4}}))?",
        text,
        re.I,
    )
    if day_month:
        day_number, month_text, explicit_year = day_month.groups()
    elif month_day:
        month_text, day_number, explicit_year = month_day.groups()
    else:
        return None
    month = datetime.strptime(month_text[:3], "%b").month
    year = int(explicit_year) if explicit_year else reference.year
    if next_year_modifier and not explicit_year:
        year += 1
    try:
        parsed = date(year, month, int(day_number))
    except ValueError:
        return None
    if not explicit_year and not next_year_modifier and parsed < reference:
        try:
            parsed = parsed.replace(year=year + 1)
        except ValueError:
            return None
    return parsed


def parse_week_range(value: str | None, *, today: date | None = None) -> tuple[date, date] | None:
    """
    Resolve vague week-level phrases ("next week", "this week") into a
    (start, end) date range. parse_customer_date/extract_customer_date only
    handle single specific days — this covers the range case they don't.
    """
    if not value:
        return None
    text = " ".join(str(value).strip().lower().replace(",", " ").split())
    reference = today or date.today()
    this_monday = reference - timedelta(days=reference.weekday())

    if re.search(r"\bnext week\b", text):
        start = this_monday + timedelta(days=7)
        return start, start + timedelta(days=6)
    if re.search(r"\bthis week\b", text):
        return reference, this_monday + timedelta(days=6)
    raw = str(value).strip()
    if any(token in raw for token in ("下周", "下星期", "下个星期", "下礼拜", "下个礼拜")):
        start = this_monday + timedelta(days=7)
        return start, start + timedelta(days=6)
    if any(token in raw for token in ("这周", "本周", "这星期", "这个星期", "这礼拜", "这个礼拜")):
        return reference, this_monday + timedelta(days=6)
    return None


def extract_customer_date(value: str | None, *, today: date | None = None) -> date | None:
    """Find one supported natural-language date inside a longer message."""
    text = " ".join(str(value or "").strip().lower().replace(",", " ").split())
    reference = today or date.today()
    # Chinese day-words/weekdays have no spaces to anchor a \b-based regex
    # search on, so check every known alias as a plain substring instead —
    # same reasoning as extract_time_from_message's period-word handling.
    for alias in sorted(
        list(_CHINESE_RELATIVE_DAYS) + list(_CHINESE_WEEKDAY_TO_INDEX), key=len, reverse=True
    ):
        for prefix in ("",) + _CHINESE_NEXT_MODIFIERS:
            candidate = f"{prefix}{alias}"
            if candidate in str(value or ""):
                parsed = _parse_chinese_date(candidate, reference)
                if parsed is not None:
                    return parsed
    chinese_calendar = re.search(
        r"(?:(?:明年|今年)\s*)?(?:(?:\d{4})年)?\s*\d{1,2}月\s*\d{1,2}(?:日|号)",
        str(value or ""),
    )
    if chinese_calendar:
        parsed = _parse_chinese_date(chinese_calendar.group(0).replace(" ", ""), reference)
        if parsed is not None:
            return parsed
    patterns = (
        r"\b(?:today|tomorrow)\b",
        r"\b(?:(?:next|this)\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        # day-then-month ("20 August[, 2026]"), with an optional "next/this
        # year" modifier either side ("next year 20 August", "20 August next
        # year") — kept in the matched substring so parse_customer_date
        # still sees it (see next_year_modifier there).
        rf"\b(?:next|this)\s+year\s+\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH_NAMES})(?:\s+\d{{4}})?\b",
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH_NAMES})(?:\s+\d{{4}})?(?:\s+(?:next|this)\s+year)?\b",
        # month-then-day ("August 20[, 2026]") — see parse_customer_date
        rf"\b(?:next|this)\s+year\s+(?:{_MONTH_NAMES})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:\s+\d{{4}})?\b",
        rf"\b(?:{_MONTH_NAMES})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:\s+\d{{4}})?(?:\s+(?:next|this)\s+year)?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            parsed = parse_customer_date(match.group(0), today=today)
            if parsed is not None:
                return parsed
    return None
