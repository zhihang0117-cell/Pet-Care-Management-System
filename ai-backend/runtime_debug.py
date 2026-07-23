"""
Runtime verification helpers for live backend debugging.

Logs process/source identity at startup and per-request session transitions.
Does not log secrets or credentials.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("pawfect.runtime")


def _main_py_path() -> str:
    return str(Path(__file__).resolve().parent / "main.py")


def _environment_name() -> str:
    for key in ("ENVIRONMENT", "APP_ENV", "ENV", "DEPLOY_ENV"):
        value = str(os.getenv(key) or "").strip()
        if value:
            return value
    if os.getenv("_EVAL_OVERRIDE_ACTIVE"):
        return "eval_override"
    if os.getenv("TESTING", "").lower() in {"1", "true", "yes"}:
        return "testing"
    return "development"


def is_testing_mode_enabled() -> bool:
    from testing_mode import is_testing_mode

    return is_testing_mode() or bool(os.getenv("_EVAL_OVERRIDE_ACTIVE"))


def get_startup_info() -> dict[str, Any]:
    return {
        "main_py_path": _main_py_path(),
        "working_directory": str(Path.cwd().resolve()),
        "python_executable": sys.executable,
        "process_id": os.getpid(),
        "environment_name": _environment_name(),
        "testing_mode_enabled": is_testing_mode_enabled(),
        "database_provider": str(os.getenv("DATABASE_PROVIDER") or "unset"),
        "final_response_provider": str(os.getenv("FINAL_RESPONSE_PROVIDER") or "unset"),
    }


def configure_runtime_logging() -> None:
    """Ensure startup and runtime verification logs are visible under uvicorn."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name in ("pawfect.runtime", "pawfect.chat"):
        logging.getLogger(name).setLevel(logging.INFO)


def log_startup_info() -> None:
    configure_runtime_logging()
    info = get_startup_info()
    logger.info(
        "\n=== BACKEND STARTUP ===\n"
        "main_py_path: %s\n"
        "working_directory: %s\n"
        "python_executable: %s\n"
        "process_id: %s\n"
        "environment_name: %s\n"
        "testing_mode_enabled: %s\n"
        "database_provider: %s\n"
        "final_response_provider: %s\n"
        "=== BACKEND STARTUP END ===",
        info["main_py_path"],
        info["working_directory"],
        info["python_executable"],
        info["process_id"],
        info["environment_name"],
        info["testing_mode_enabled"],
        info["database_provider"],
        info["final_response_provider"],
    )


def _infer_next_question(intent_json: dict | None, reply: str = "") -> str:
    return infer_next_question(intent_json, reply)


def infer_next_question(intent_json: dict | None, reply: str = "") -> str:
    intent_json = dict(intent_json or {})
    for key in ("next_question",):
        value = str(intent_json.get(key) or "").strip()
        if value:
            return value
    missing = list(intent_json.get("missing_information") or [])
    if missing:
        return f"(missing: {missing[0]})"
    parts = [part.strip() for part in str(reply or "").split("\n\n") if part.strip()]
    return parts[-1] if parts else ""


def log_chat_runtime_state(
    *,
    raw_phone: str,
    normalized_phone: str,
    session_key: str,
    session_existed_before: bool,
    customer_name_before: str,
    customer_name_after: str,
    pet_type_before: str,
    pet_type_after: str,
    current_step: str,
    missing_fields: list[str],
    next_question: str,
    message: str = "",
) -> None:
    logger.info(
        "\n=== CHAT RUNTIME STATE ===\n"
        "process_id: %s\n"
        "raw_phone: %s\n"
        "normalized_phone: %s\n"
        "session_key: %s\n"
        "session_existed_before: %s\n"
        "customer_name_before: %s\n"
        "customer_name_after: %s\n"
        "pet_type_before: %s\n"
        "pet_type_after: %s\n"
        "current_step: %s\n"
        "missing_fields: %s\n"
        "next_question: %s\n"
        "message: %s\n"
        "=== CHAT RUNTIME STATE END ===",
        os.getpid(),
        raw_phone,
        normalized_phone,
        session_key,
        session_existed_before,
        customer_name_before or "(empty)",
        customer_name_after or "(empty)",
        pet_type_before or "(empty)",
        pet_type_after or "(empty)",
        current_step or "(none)",
        missing_fields,
        next_question or "(none)",
        message,
    )


def get_runtime_debug_payload(phone_number: str = "") -> dict[str, Any]:
    from session_store import get_sanitized_session_for_phone, list_session_keys

    payload: dict[str, Any] = {
        "startup": get_startup_info(),
        "active_process_id": os.getpid(),
        "active_source_path": _main_py_path(),
        "session_keys": list_session_keys(),
        "session_count": len(list_session_keys()),
    }
    phone = str(phone_number or "").strip()
    if phone:
        payload["requested_phone"] = phone
        payload["session"] = get_sanitized_session_for_phone(phone)
    return payload
