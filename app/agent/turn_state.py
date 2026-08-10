"""Cross-turn state helpers: evidence compaction, DAYCARE duration capture,
and deterministic date/time resolution merging.

Extracted from app/agent/tool_loop.py during the V1 decommission
(2026-08-10) — tool_loop.py was mostly V1-only tool-loop plumbing (batch
planning/execution, per-call dispatch, authoritative-scope replay) built
around app/orchestrator.py's raw-value tool loop, which is gone. These
three functions are the genuinely architecture-independent pieces
app/agent/runtime.py (V2's handle_turn) actually needs, now living on
their own so V2 no longer has to import from a file built for V1.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from app.db.time_normalization import (
    extract_duration_minutes,
    extract_time_from_message,
    extract_time_range,
    is_period_token,
)


def compact_evidence_result(result, max_chars: int = 1800):
    """Bound cross-turn evidence size and remove internal delivery fields."""

    def sanitize(value, depth: int = 0):
        if depth > 3:
            return "[nested data omitted]"
        if isinstance(value, dict):
            return {
                key: sanitize(item, depth + 1)
                for key, item in value.items()
                if not str(key).startswith("_internal_")
            }
        if isinstance(value, list):
            return [sanitize(item, depth + 1) for item in value[:8]]
        if isinstance(value, str) and len(value) > 500:
            return value[:500] + "…"
        return value

    cleaned = sanitize(result)
    encoded = json.dumps(cleaned, ensure_ascii=False, default=str)
    if len(encoded) <= max_chars:
        return cleaned
    return {"preview": encoded[:max_chars] + "…", "truncated": True}


def capture_explicit_daycare_duration(state: Any, user_message: str) -> None:
    """Cache an exact customer-stated DAYCARE duration across side flows.

    Used by app/agent/runtime.py's handle_turn() — extracted so the
    architecture doesn't have to re-derive this from scratch or, worse,
    leave DAYCARE duration authority with the model entirely (the real
    gap a 2026-08-10 review found: app/agent/runtime.py's
    check_availability let the model set duration_minutes itself even
    though this exact deterministic capture already existed one file
    over).

    Also remembers *which* dimension the customer actually committed to
    (an explicit hour count vs. an explicit clock-time range), because
    those two cases must be revised differently when the customer later
    edits only the check-in/drop-off time on its own:
      - "5 hours" (explicit_duration) — the LENGTH is what they meant;
        keep it fixed and let the pickup time shift with check-in.
      - "12pm to 5pm" (explicit_range) — the PICKUP TIME is what they
        meant; keep 5pm fixed and recompute the length instead of
        silently carrying the old (now wrong) duration forward.
    """
    text = str(user_message or "").strip()
    if not text:
        return
    is_daycare_context = str(getattr(state, "service_type", None) or "").upper() == "DAYCARE" or bool(
        re.search(r"\bday\s*-?care\b|日托|日间托管|日間托管", text, re.IGNORECASE)
    )
    if not is_daycare_context:
        return

    time_range = extract_time_range(text)
    if time_range:
        _, end_time, duration = time_range
        state.daycare_duration_minutes = duration
        state.daycare_duration_source = "explicit_range"
        state.daycare_range_end_time = end_time
        state.verified_facts["daycare_duration_minutes"] = {
            "value": duration,
            "source": "customer_message_range",
            "turn": state.turn_counter,
        }
        return

    explicit_duration = extract_duration_minutes(text)
    if explicit_duration:
        state.daycare_duration_minutes = explicit_duration
        state.daycare_duration_source = "explicit_duration"
        state.daycare_range_end_time = None
        state.verified_facts["daycare_duration_minutes"] = {
            "value": explicit_duration,
            "source": "customer_message_duration",
            "turn": state.turn_counter,
        }
        return

    # No new duration/range stated this turn. If a range was already
    # pinned down and this message only revises the check-in time, hold
    # the stated pickup time fixed and recompute the length.
    if state.daycare_duration_source != "explicit_range" or not state.daycare_range_end_time:
        return
    new_start = extract_time_from_message(text)
    if not new_start or is_period_token(new_start):
        return
    try:
        start_hour, start_minute = (int(part) for part in new_start.split(":"))
        end_hour, end_minute = (int(part) for part in state.daycare_range_end_time.split(":"))
    except (TypeError, ValueError):
        return
    recomputed = (end_hour * 60 + end_minute) - (start_hour * 60 + start_minute)
    if recomputed <= 0:
        return
    state.daycare_duration_minutes = recomputed
    state.verified_facts["daycare_duration_minutes"] = {
        "value": recomputed,
        "source": "recomputed_from_pinned_pickup_time",
        "check_in_time": new_start,
        "pickup_time": state.daycare_range_end_time,
        "turn": state.turn_counter,
    }


def apply_datetime_resolution(
    state: Any,
    resolution: Any,
    *,
    cache_resolved_date: Callable[[Any, str, dict], None],
    compact_evidence_result: Callable[..., Any],
) -> None:
    """Deterministic per-turn date/time preprocessing (resolve_datetime run
    on the raw customer message, before the model ever sees it).

    Confirmed live: this used to unconditionally REPLACE (or, for an
    "ambiguous" result, wipe to None) state.current_datetime_resolution
    every single turn. A customer resolving "next Wednesday", then later
    just saying "10 AM" (resolves time only, date=None) or an unrelated
    aside/confirmation like "yes confirm"/"how much does boarding cost"
    (resolves NOTHING — this text alone is "ambiguous") silently lost the
    date that was already established, forcing the model to reconstruct it
    from raw conversation history instead of RUNTIME_CONTEXT — exactly the
    kind of "sometimes the model gets it, sometimes it doesn't" flakiness
    this was supposed to prevent in the first place.

    Fix: a turn that resolves nothing at all is a no-op (leave whatever was
    already established standing). A turn that resolves something but no
    date/date_range (a bare time/period/duration) merges onto the existing
    date/date_range instead of dropping it. A turn that DOES name a new
    date still fully overrides — unchanged from before.

    Real gap confirmed 2026-08-10 (audited, not assumed — "Friday" then
    "afternoon" and "Friday afternoon" then "2pm" were both already
    correct; only this direction was broken): the same merge only ever
    protected date/date_range, not period. "afternoon" (period only, no
    date yet) then "next Friday" (a new date, no period restated) silently
    dropped "afternoon" — the customer's stated time-of-day preference,
    not just the date, is real evidence that shouldn't vanish merely
    because the date arrived on a later turn. Symmetric fix: period
    carries forward the same way, UNLESS this turn gives a new period of
    its own or a new exact time (an exact time is more specific than a
    period and should replace it, not coexist with a stale one — matches
    "Friday afternoon" -> "2pm" clearing period today).
    """
    if not isinstance(resolution, dict):
        return
    if resolution.get("ambiguous"):
        return
    previous = state.current_datetime_resolution
    merged = dict(resolution)
    if (
        isinstance(previous, dict)
        and not merged.get("date")
        and not merged.get("date_range")
        and (previous.get("date") or previous.get("date_range"))
    ):
        merged["date"] = previous.get("date")
        merged["date_range"] = previous.get("date_range")
    if (
        isinstance(previous, dict)
        and not merged.get("period")
        and not merged.get("time")
        and previous.get("period")
    ):
        merged["period"] = previous.get("period")
    state.current_datetime_resolution = merged
    cache_resolved_date(state, "resolve_datetime", merged)
    state.verified_facts["current_datetime_resolution"] = {
        "source": "deterministic_preprocessing",
        "turn": state.turn_counter,
        "result": compact_evidence_result(merged),
    }
