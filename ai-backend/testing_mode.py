"""
Testing-mode helpers for Pawfect backend.

When TESTING=true (or pytest is running), unexpected errors propagate instead of
being converted into customer-facing fallback replies.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from typing import Any

logger = logging.getLogger("pawfect.testing")


def is_testing_mode() -> bool:
    """True when automated tests should surface real failures."""
    if str(os.getenv("TESTING", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if "pytest" in sys.modules:
        return True
    return False


def should_reraise_on_error() -> bool:
    """True when service-level fallbacks must not mask unexpected errors."""
    if is_testing_mode():
        return True
    from llm_api_error import is_eval_strict_mode

    return is_eval_strict_mode()


def new_error_id() -> str:
    return str(uuid.uuid4())


def log_unhandled_exception(
    log: logging.Logger,
    exc: Exception,
    *,
    error_id: str,
    **context: Any,
) -> None:
    """Log full exception details with a unique error_id for support correlation."""
    context_bits = " ".join(f"{key}={value!r}" for key, value in context.items() if value is not None)
    log.exception(
        "unhandled_error error_id=%s type=%s %s",
        error_id,
        type(exc).__name__,
        context_bits,
    )
