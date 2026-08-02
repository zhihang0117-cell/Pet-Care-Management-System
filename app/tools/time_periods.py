"""
Period-of-day filtering for availability results.

The underlying relational_actions.check_available_slots only echoes back
whatever time/period text it was given — it never actually filters the
slot list. Left to the model alone, that meant a customer asking for
"morning" would still get shown the full day's slots. This filters
deterministically in the tool layer instead of relying on the model to do
it correctly every time.
"""

from __future__ import annotations

PERIOD_WINDOWS = {
    "morning": ("00:00", "11:59"),
    "afternoon": ("12:00", "16:59"),
    "evening": ("17:00", "20:59"),
    "night": ("21:00", "23:59"),
    "noon": ("11:30", "13:30"),
}


def filter_slots_by_period(slots: list[str], period_or_time: str | None) -> list[str] | None:
    """Return the subset of slots within the named period, or None if period_or_time isn't a period word."""
    key = str(period_or_time or "").strip().lower()
    window = PERIOD_WINDOWS.get(key)
    if not window:
        return None
    start, end = window
    return [s for s in slots if start <= str(s)[:5] <= end]
