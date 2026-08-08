"""Deterministic preparation and execution helpers for one model tool call."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any


def redirect_existing_customer_membership(
    tool_call: dict,
    args: dict,
    state: Any,
    *,
    mutation_signature: Callable[[str, dict], str],
) -> dict:
    """Redirect an invalid duplicate customer create into member enrollment."""
    if not (
        tool_call.get("name") == "create_customer"
        and state.customer_id is not None
        and (
            state.active_scenario == "MEMBER"
            or state.loyalty_decision == "accepted"
        )
    ):
        return args

    accepted_prior_offer = bool(
        state.loyalty_decision == "accepted"
        and state.loyalty_offer_shown_turn is not None
        and state.loyalty_offer_shown_turn < state.turn_counter
    )
    tool_call["name"] = "register_loyalty_member"
    redirected_args = {
        "company_id": state.company_id,
        "customer_id": state.customer_id,
        "confirmed": accepted_prior_offer,
    }
    if accepted_prior_offer:
        state.pending_actions["register_loyalty_member"] = {
            "signature": mutation_signature(
                "register_loyalty_member", redirected_args
            ),
            "args": dict(redirected_args),
            "preview_turn": state.loyalty_offer_shown_turn,
            "scenario": state.active_scenario,
        }
    return redirected_args


def apply_authoritative_scope(
    tool_name: str,
    args: dict,
    state: Any,
    *,
    company_scoped_tools: set[str],
    customer_scoped_tools: set[str],
) -> tuple[dict, dict | None]:
    """Overwrite model-supplied tenant/customer IDs with session authority."""
    if tool_name in company_scoped_tools or "company_id" in args:
        args = {**args, "company_id": state.company_id}

    if tool_name not in customer_scoped_tools:
        return args, None
    if state.customer_id is not None:
        return {**args, "customer_id": state.customer_id}, None
    if tool_name in {
        "check_availability",
        "check_availability_range",
        "get_booking_service_options",
    }:
        return args, None
    return args, {
        "error": "CUSTOMER_NOT_RESOLVED",
        "message": (
            "No customer_id has been established for this phone number yet "
            "— call create_customer with the customer's name first (their "
            "phone number is already known from RUNTIME_CONTEXT, never "
            "invent or reuse someone else's customer_id), then retry this "
            "call with the real customer_id it returns."
        ),
    }


def restore_confirmed_pending_args(
    tool_name: str,
    args: dict,
    state: Any,
    user_message: str,
    *,
    confirmation_intent: Callable[[str], str | None],
    pending_scenario_matches: Callable[[Any, str, dict], bool],
) -> tuple[dict, int | None]:
    """Use the exact server-cached payload after a standalone confirmation."""
    if tool_name not in {
        "create_booking",
        "redeem_reward",
        "register_loyalty_member",
    }:
        return args, None

    pending_action = state.pending_actions.get(tool_name)
    pending_args = (
        pending_action.get("args")
        if isinstance(pending_action, dict)
        else None
    )
    if not (
        confirmation_intent(user_message) == "affirmative"
        and isinstance(pending_args, dict)
        and not pending_args.get("truncated")
        and int(pending_action.get("preview_turn") or 0) == state.turn_counter - 1
        and pending_scenario_matches(state, tool_name, pending_action)
    ):
        return args, None

    confirmed_preview_turn = (
        int(pending_action["preview_turn"])
        if tool_name == "create_booking"
        else None
    )
    restored = {
        **pending_args,
        "company_id": state.company_id,
        "customer_id": state.customer_id,
    }
    if tool_name == "create_booking" and pending_action.get("idempotency_key"):
        restored["idempotency_key"] = pending_action["idempotency_key"]
    return restored, confirmed_preview_turn


def invoke_with_turn_read_cache(
    tool: Any,
    tool_name: str,
    args: dict,
    *,
    cacheable_read_tools: set[str],
    successful_read_results: dict[str, object] | None,
    successful_read_lock: Lock | None,
    mutation_signature: Callable[[str, dict], str],
    tool_result_status: Callable[[Any], str],
):
    """Invoke a tool once and reuse identical reads within the current turn."""
    read_signature = None
    if tool_name in cacheable_read_tools and successful_read_results is not None:
        read_signature = mutation_signature(tool_name, args)
        if successful_read_lock is None:
            cached_result = successful_read_results.get(read_signature)
        else:
            with successful_read_lock:
                cached_result = successful_read_results.get(read_signature)
        if cached_result is not None:
            if isinstance(cached_result, dict):
                return {
                    **cached_result,
                    "_internal_duplicate_read_suppressed": True,
                    "duplicate_suppressed": True,
                    "message": (
                        "This identical read already succeeded in the current customer "
                        "turn. Its result above is the authoritative cached result. Do "
                        "not call this or another equivalent read again; answer the "
                        "customer from the available evidence or ask for genuinely "
                        "missing customer input."
                    ),
                }
            return {
                "status": tool_result_status(cached_result),
                "data": cached_result,
                "_internal_duplicate_read_suppressed": True,
                "duplicate_suppressed": True,
                "message": (
                    "This identical read already succeeded in the current customer "
                    "turn. Use this cached result and do not call it again."
                ),
            }

    try:
        result = tool.invoke(args)
    except Exception as exc:
        result = {"status": "error", "error": str(exc)}

    if read_signature is not None:
        if successful_read_lock is None:
            successful_read_results.setdefault(read_signature, result)
        else:
            with successful_read_lock:
                successful_read_results.setdefault(read_signature, result)
    return result
