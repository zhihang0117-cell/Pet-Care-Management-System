"""
Shared "how far ahead can a customer book" constraint, used by both the
availability-checking and booking-writing tools so the limit can't be
bypassed by going through one path but not the other.
"""

from __future__ import annotations

from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = "Asia/Kuala_Lumpur"  # matches time_normalization.BUSINESS_TIMEZONE

# No company policy document actually specifies a hard advance-booking
# ceiling (checked RAG for "advance booking"/"reservation limit" — nothing
# names a day count). DEFAULT_SUGGESTION_WINDOW_DAYS is only the range the
# assistant should default to suggesting/checking when the customer hasn't
# named a specific date. It is NOT a hard cap: a customer who explicitly
# names a real future date (e.g. "15 October") must still be bookable past
# it — see booking_window_error, which only rejects past dates and an
# absurd-lookahead sanity ceiling, not this window.
DEFAULT_SUGGESTION_WINDOW_DAYS = 14

# Generous outer sanity ceiling — not a business rule, just a guard against
# a resolve_datetime/model date so far out it's almost certainly a wrong-year
# hallucination (e.g. "2029") rather than a real customer request beyond the
# suggestion window above.
MAX_BOOKING_ADVANCE_DAYS = 180


def today_business() -> date:
    return datetime.now(ZoneInfo(BUSINESS_TIMEZONE)).date()


def max_bookable_date() -> date:
    return today_business() + timedelta(days=MAX_BOOKING_ADVANCE_DAYS)


def default_suggestion_window_end() -> date:
    return today_business() + timedelta(days=DEFAULT_SUGGESTION_WINDOW_DAYS)


def resolve_date_string(value: str) -> str | None:
    """
    Re-resolve a date argument that SHOULD already be a resolved YYYY-MM-DD
    string (per each tool's docstring) but, in practice, gpt-4o-mini has
    been repeatedly observed passing an unresolved/self-guessed value
    instead of actually calling resolve_datetime first — confirmed live for
    both check-in and check-out dates, in both wildly-wrong (years off) and
    subtly-wrong (a few days off, inside the normal booking window, so
    booking_window_error's own sanity check never catches it) forms.
    check_out_date is especially exposed since nothing else validates it
    against a real calendar the way check-in dates are.

    Rather than keep patching individual symptoms of the same root cause,
    every tool that receives a date string re-resolves it here — accepts
    an already-correct ISO date as a no-op, but also recovers a raw
    natural-language phrase the model forwarded (or guessed) instead.
    Returns None if nothing resolvable was found.
    """
    if not value:
        return None
    from app.db.date_normalization import extract_customer_date

    parsed = extract_customer_date(value, today=today_business())
    return parsed.isoformat() if parsed else None


def booking_window_error(date_str: str) -> dict | None:
    """None if date_str is bookable now; otherwise a result-shaped error dict."""
    target = date.fromisoformat(date_str)
    today = today_business()
    limit = max_bookable_date()
    if today <= target <= limit:
        return None
    if target < today:
        days_ago = (today - target).days
        if days_ago > 30:
            # A gap this large almost always means the model guessed/computed
            # a date itself instead of calling resolve_datetime (e.g. falling
            # back to training-data date assumptions) — spell out the fix
            # rather than a plain "already passed" the model might repeat
            # back to the customer as if the real calendar date had passed.
            reason = (
                f"'{date_str}' is not a real date for this conversation — it is "
                f"{days_ago} days before today ({today.isoformat()}). This means "
                "you computed or guessed a date instead of using the resolver. "
                "Call resolve_datetime on the customer's original date expression "
                "and use its exact date output, then retry — do not tell the "
                "customer any date has passed based on this error."
            )
        else:
            reason = "That date has already passed."
    else:
        days_ahead = (target - today).days
        reason = (
            f"'{date_str}' is {days_ahead} days from today ({today.isoformat()}) — "
            f"further out than this ever gets requested for real (limit checked: "
            f"{limit.isoformat()}). This almost always means the date was computed/"
            "guessed instead of resolved. Call resolve_datetime on the customer's "
            "original date expression and use its exact output, then retry — do "
            "not tell the customer this date is unavailable or too far ahead "
            "unless resolve_datetime genuinely produces a date this far out."
        )
    return {"status": "error", "data": {}, "error": reason}
