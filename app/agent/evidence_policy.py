"""Scope cached tool evidence to the customer question it can support."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


def service_options_evidence_matches(
    state: Any,
    user_message: str,
    explicit_service_type: Callable[[str], str],
) -> bool:
    evidence = (state.verified_facts or {}).get("service_options")
    if not isinstance(evidence, dict) or evidence.get("status") != "success":
        return False
    args = evidence.get("args")
    if not isinstance(args, dict) or not args:
        return False
    requested_service = (
        explicit_service_type(user_message)
        or str(state.service_type or "").strip().upper()
    )
    evidence_service = str(args.get("service_type") or "").strip().upper()
    if requested_service and evidence_service != requested_service:
        return False
    if (
        state.pet_id is not None
        and requested_service == "GROOMING"
        and args.get("pet_id") in (None, "")
    ):
        return False
    if state.pet_id is not None and args.get("pet_id") not in (None, ""):
        try:
            if int(args["pet_id"]) != int(state.pet_id):
                return False
        except (TypeError, ValueError):
            return False
    return True


def policy_evidence_matches(state: Any, user_message: str) -> bool:
    evidence = (state.verified_facts or {}).get("policy_knowledge")
    if not isinstance(evidence, dict) or evidence.get("status") != "success":
        return False
    if evidence.get("turn") == state.turn_counter:
        return True
    args = evidence.get("args")
    query = str((args or {}).get("query") or "") if isinstance(args, dict) else ""
    if not query:
        return False

    def compact(value: str) -> str:
        return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", value.casefold())

    current = compact(str(user_message or ""))
    previous = compact(query)
    if previous in current or current in previous:
        return True

    stop_words = {
        "what", "whats", "your", "about", "policy", "policies",
        "company", "please", "rule", "rules", "allowed", "required",
        "polisi", "syarat",
    }
    current_terms = {
        term
        for term in re.findall(r"[a-z0-9]{4,}", str(user_message or "").casefold())
        if term not in stop_words
    }
    previous_terms = {
        term
        for term in re.findall(r"[a-z0-9]{4,}", query.casefold())
        if term not in stop_words
    }
    return bool(current_terms & previous_terms)
