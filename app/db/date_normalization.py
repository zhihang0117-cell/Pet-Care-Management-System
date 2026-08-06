"""Normalize customer-friendly booking dates without asking them to use ISO."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from dateutil import parser as _dateutil_parser

_MONTH_NAMES = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)

# Common WhatsApp-texting abbreviations/misspellings — a customer typing
# "tmr" (as casually happens) previously matched nothing at all here.
_TODAY_WORDS = ("today", "tdy", "2day")
_TOMORROW_WORDS = (
    "tomorrow", "tmr", "tmrw", "tmrrw", "tmo", "tmro", "2mrw", "2moro", "2morrow",
    "tomorow", "tommorow", "tommorrow",
)
_TODAY_ALT = "|".join(_TODAY_WORDS)
_TOMORROW_ALT = "|".join(_TOMORROW_WORDS)


def _looks_like_abbreviation(word: str, target: str) -> bool:
    """True if word's letters appear in target in the same order (an
    ordered subsequence — "tmr" -> t.o.m.o.r.row, "tmo" -> t.omo.rrow),
    the general shape of a texting abbreviation. This is what lets
    parse_customer_date recognize a variant that isn't in the explicit
    _TODAY_WORDS/_TOMORROW_WORDS list above, instead of only ever
    understanding exactly the spellings someone thought to enumerate.

    Guarded by a minimum length and a matching first letter so short,
    unrelated real words ("to", "at", "or", "tow") can't accidentally
    qualify — without that guard, "to" would trivially subsequence-match
    "tomorrow". 4 chars, not 3: every 3-letter abbreviation actually in use
    ("tmr", "tdy", "tmo") is already caught by the exact-match word lists
    above before this fallback ever runs — this only has to catch novel
    4+ letter variants (typos like "tmrow", "tomoro") nobody enumerated.
    """
    word = word.strip()
    if len(word) < 4 or word[0] != target[0]:
        return False
    remaining = iter(target)
    return all(letter in remaining for letter in word)


def _normalize_texting_shorthand(word: str) -> str:
    """"2" as a stand-in for "to" ("2day", "2mrw", "2moro") — a narrow,
    specific substitution, not a general leetspeak decoder."""
    return re.sub(r"^2(?=[a-z])", "to", word)

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
    # Digit forms ("星期5") included alongside the character forms — a
    # customer texting on a phone commonly types the digit instead of 五.
    1: ("星期一", "周一", "礼拜一", "星期1", "周1", "礼拜1"),
    2: ("星期二", "周二", "礼拜二", "星期2", "周2", "礼拜2"),
    3: ("星期三", "周三", "礼拜三", "星期3", "周3", "礼拜3"),
    4: ("星期四", "周四", "礼拜四", "星期4", "周4", "礼拜4"),
    5: ("星期五", "周五", "礼拜五", "星期5", "周5", "礼拜5"),
    6: ("星期六", "周六", "礼拜六", "星期6", "周6", "礼拜6"),
    7: ("星期日", "星期天", "周日", "周天", "礼拜日", "礼拜天", "星期7", "周7", "礼拜7"),
}
_CHINESE_WEEKDAY_TO_INDEX = {
    name: weekday_num - 1  # Monday=0 to match date.weekday()
    for weekday_num, names in _CHINESE_WEEKDAY_NAMES.items()
    for name in names
}
_CHINESE_NEXT_MODIFIERS = ("下个", "下")

# Malay (Bahasa Malaysia) relative-day and weekday words — same reasoning as
# the Chinese block above: this is a Malaysia-based business, and a Malay
# customer saying "esok"/"isnin" deserves the same deterministic resolution
# English/Chinese speakers get, not a silent fall-through to the LLM guessing.
_MALAY_RELATIVE_DAYS = {"hari ini": 0, "esok": 1, "lusa": 2}
_MALAY_WEEKDAY_NAMES = {
    1: "isnin", 2: "selasa", 3: "rabu", 4: "khamis", 5: "jumaat", 6: "sabtu", 7: "ahad",
}
_MALAY_WEEKDAY_TO_INDEX = {name: num - 1 for num, name in _MALAY_WEEKDAY_NAMES.items()}
# Malay puts the "next" modifier after the noun ("isnin depan"), unlike
# Chinese's prefix ("下星期一") — handled with a trailing-word regex group
# below instead of the prefix-matching loop _parse_chinese_date uses.
_MALAY_NEXT_MODIFIER = "depan"


def _parse_malay_date(text: str, reference: date) -> date | None:
    """text: already stripped/lowercased/whitespace-collapsed."""
    if text in _MALAY_RELATIVE_DAYS:
        return reference + timedelta(days=_MALAY_RELATIVE_DAYS[text])
    match = re.fullmatch(
        r"(isnin|selasa|rabu|khamis|jumaat|sabtu|ahad)(?:\s+(depan|ini))?", text
    )
    if not match:
        return None
    weekday_text, modifier = match.groups()
    weekday_index = _MALAY_WEEKDAY_TO_INDEX[weekday_text]
    days_ahead = (weekday_index - reference.weekday()) % 7
    if modifier == _MALAY_NEXT_MODIFIER:
        days_ahead += 7
    elif days_ahead == 0:
        days_ahead = 7
    return reference + timedelta(days=days_ahead)


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


def _parse_with_dateutil(text: str, reference: date, next_year_modifier: bool) -> date | None:
    """Last-resort fallback for concrete-date spellings none of the rules
    above cover (dotted "2026.8.4", dashed "04-08-26", compact "20260804",
    alternate month spellings, ...) — everything above stays first since it
    encodes business-specific behavior (Chinese/Malay wording, the
    roll-to-next-year rule) dateutil doesn't know about.

    Requires at least two digit groups so a single bare number ("5") isn't
    silently read as "day 5 of the current month" — this only fires after
    every specific rule above has already failed to match, so the input is
    unstructured customer text, not a guaranteed date.

    dayfirst=True matches the d/m/y-before-m/d/y preference already used for
    slash dates above (this is a Malaysia-based business, a day-first
    locale).
    """
    # A bare 8-digit run ("20260807") is an unambiguous compact YYYYMMDD —
    # exempted from the "needs 2+ digit groups" guard below since it's not
    # a lone ambiguous number the way "5" or "2026" alone would be.
    if not re.fullmatch(r"\d{8}", text) and len(re.findall(r"\d+", text)) < 2:
        return None
    # A leading 4-digit year ("2026.8.4", "2026-8-4") is always followed by
    # month-then-day — nobody writes year-day-month. dayfirst=True is only
    # correct for the year-absent-or-last case; applying it here too makes
    # dateutil swap month and day (e.g. misreads "2026.8.4" as Apr 8).
    year_first = bool(re.match(r"^\d{4}", text))
    try:
        parsed = _dateutil_parser.parse(
            text,
            dayfirst=not year_first,
            yearfirst=year_first,
            default=datetime.combine(reference, datetime.min.time()),
        )
    except (ValueError, OverflowError, TypeError):
        return None
    result = parsed.date()
    has_explicit_year = bool(re.search(r"\d{4}", text))
    if not has_explicit_year:
        if next_year_modifier:
            result = result.replace(year=result.year + 1)
        elif result < reference:
            try:
                result = result.replace(year=result.year + 1)
            except ValueError:
                return None
    return result


def parse_customer_date(value: str | None, *, today: date | None = None) -> date | None:
    if not value:
        return None
    reference = today or date.today()
    text = " ".join(str(value).strip().lower().replace(",", " ").split())
    if text in _TODAY_WORDS:
        return reference
    # Checked before the bare "tomorrow" case below: extract_customer_date's
    # substring scan for "tomorrow" would otherwise match just the last word
    # of this phrase and silently return the wrong (one day short) date —
    # Chinese ("后天") and Malay ("lusa") already had their own +2-day word
    # for this; English never did.
    if any(text in (f"day after {w}", f"the day after {w}") for w in _TOMORROW_WORDS):
        return reference + timedelta(days=2)
    if text in _TOMORROW_WORDS:
        return reference + timedelta(days=1)
    # Generic fallback for a texting abbreviation nobody thought to list
    # above — only reached once the whole value has failed every exact
    # match, and only when it's a single bare word (never risked against a
    # longer phrase, where a stray short word coincidentally subsequencing
    # "tomorrow" would be a real false-positive risk).
    if " " not in text:
        shorthand = _normalize_texting_shorthand(text)
        if _looks_like_abbreviation(shorthand, "today") and not _looks_like_abbreviation(shorthand, "tomorrow"):
            return reference
        if _looks_like_abbreviation(shorthand, "tomorrow"):
            return reference + timedelta(days=1)
    chinese_result = _parse_chinese_date(str(value).strip(), reference)
    if chinese_result is not None:
        return chinese_result
    malay_result = _parse_malay_date(text, reference)
    if malay_result is not None:
        return malay_result

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
        # `days_ahead == 0 or modifier == "next" and days_ahead == 0` (the
        # previous condition here) is, by operator precedence, exactly
        # `days_ahead == 0` — the `modifier == "next"` clause never changes
        # the result, since it's ANDed with a condition already covered by
        # the first term. "next friday" said on any day but Friday itself
        # silently returned THIS Friday, ignoring "next" — same bug class
        # already fixed for Chinese/Malay (_parse_chinese_date,
        # _parse_malay_date both correctly do `if modifier == next: +=7;
        # elif days_ahead==0: =7`), just never applied to the English path.
        if modifier == "next":
            days_ahead += 7
        elif days_ahead == 0:
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
        return _parse_with_dateutil(text, reference, next_year_modifier)
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

    if re.search(r"\b(?:next week|minggu depan)\b", text):
        start = this_monday + timedelta(days=7)
        return start, start + timedelta(days=6)
    if re.search(r"\b(?:this week|minggu ini)\b", text):
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
        # Modifiers tried before the bare alias (and longest modifier
        # first): a bare weekday like "星期五" is always a substring of its
        # own "下个"-prefixed form, so checking it first would match "下个
        # 星期五大概中午这样" on the unprefixed candidate and silently drop
        # the "next" modifier, returning this week's Friday instead of next
        # week's.
        for prefix in _CHINESE_NEXT_MODIFIERS + ("",):
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
        # Tried before the bare today/tomorrow pattern below — otherwise
        # that pattern's substring match on "tomorrow" wins first and this
        # phrase's "day after" never gets seen at all.
        rf"\b(?:the\s+)?day\s+after\s+(?:{_TOMORROW_ALT})\b",
        rf"\b(?:{_TODAY_ALT}|{_TOMORROW_ALT})\b",
        r"\b(?:(?:next|this)\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b(?:hari\s+ini|esok|lusa)\b",
        r"\b(?:isnin|selasa|rabu|khamis|jumaat|sabtu|ahad)(?:\s+(?:depan|ini))?\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        # any other digit-separator date shape ("4/8/2026", "04-08-26",
        # "2026.8.4", ...) — parse_customer_date resolves the exact
        # day/month/year order (slash formats explicitly, everything else
        # via the dateutil fallback).
        r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b",
        r"\b\d{4}[./-]\d{1,2}[./-]\d{1,2}\b",
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
