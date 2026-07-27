"""Sanitize internal relational data before it reaches an LLM or API client."""

from __future__ import annotations

from typing import Any


def _is_staff_id_field(field_name: object) -> bool:
    """Match staff identifiers, including selected/reviewer/verifier variants."""
    normalized = str(field_name or "").strip().lower()
    return normalized == "staff_id" or normalized.endswith("_staff_id")


def sanitize_response_data(value: Any) -> Any:
    """
    Return a recursively sanitized copy of response-grounding data.

    Staff identifiers remain available inside repositories, availability
    calculations, booking drafts, and database writes. They are removed only
    at the trust boundary before data is provided to an LLM or API consumer.
    Staff-facing business fields such as ``staff_name`` and ``role`` remain.
    """
    if isinstance(value, dict):
        return {
            key: sanitize_response_data(item)
            for key, item in value.items()
            if not _is_staff_id_field(key)
        }
    if isinstance(value, list):
        return [sanitize_response_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_response_data(item) for item in value)
    return value
