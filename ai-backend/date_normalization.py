"""Normalize customer-friendly booking dates without asking them to use ISO."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta


def parse_customer_date(value: str | None, *, today: date | None = None) -> date | None:
    if not value:
        return None
    reference = today or date.today()
    text = " ".join(str(value).strip().lower().replace(",", " ").split())
    if text == "today":
        return reference
    if text == "tomorrow":
        return reference + timedelta(days=1)

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

    month_date = re.fullmatch(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"(?:\s+(\d{4}))?",
        text,
        re.I,
    )
    if not month_date:
        return None

    day_number, month_text, explicit_year = month_date.groups()
    month = datetime.strptime(month_text[:3], "%b").month
    year = int(explicit_year) if explicit_year else reference.year
    try:
        parsed = date(year, month, int(day_number))
    except ValueError:
        return None
    if not explicit_year and parsed < reference:
        try:
            parsed = parsed.replace(year=year + 1)
        except ValueError:
            return None
    return parsed


def extract_customer_date(value: str | None, *, today: date | None = None) -> date | None:
    """Find one supported natural-language date inside a longer message."""
    text = " ".join(str(value or "").strip().lower().replace(",", " ").split())
    patterns = (
        r"\b(?:today|tomorrow)\b",
        r"\b(?:(?:next|this)\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+"
        r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"(?:\s+\d{4})?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            parsed = parse_customer_date(match.group(0), today=today)
            if parsed is not None:
                return parsed
    return None
