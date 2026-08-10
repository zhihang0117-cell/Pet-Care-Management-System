"""
Service duration lookup used by relational_actions.check_available_slots.

Trimmed from the original ai-backend availability_service.py: the
session-shaped reply-building helpers (build_availability_reply, etc.)
belonged to the retired intent/router pipeline and are not needed here —
the LLM builds the customer-facing reply itself from raw tool results.
"""

from __future__ import annotations

DEFAULT_SERVICE_DURATION_MINUTES = {
    "GROOMING": 60,  # full appointment: one continuous slot, start to finish
    "DAYCARE": 180,  # conservative fallback; use the customer's real pickup/duration when known
    "BOARDING": 30,  # same — check-in/check-out is a short event, not an all-day block
}


def service_duration_minutes(service_type: str) -> int:
    return DEFAULT_SERVICE_DURATION_MINUTES.get(str(service_type or "").upper(), 60)
