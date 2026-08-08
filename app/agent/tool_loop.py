"""Tool-calling loop mechanics: ordering, batching, dispatch, and turn
bookkeeping around one customer turn's tool calls.

None of this decides *what* to call or *whether* an argument is trustworthy
— see guardrails.py for that. This module is the plumbing the loop in
app/orchestrator.py runs every iteration: which calls can run in parallel,
how a batch actually executes, how a single call's result gets cached or
redirected, and how one turn's evidence/history gets folded into state and
the final customer-facing response.

Organized in four sections:
  1. Trace/ordering/signature policy (pure, stateless)
  2. Batch planning and execution (parallel vs sequential, timeouts)
  3. Per-call dispatch (authoritative scope, confirmed-args replay, caching)
  4. Turn lifecycle (turn start, datetime resolution, response finalization)
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from threading import Lock
from typing import Any

from app.agent.response_grounding import (
    ground_daycare_recommendation_response,
    ground_direct_datetime_response,
    ground_document_delivery_response,
    ground_latest_availability_response,
    ground_membership_response,
    ground_booking_preview_response,
    ground_unavailable_profile_claims,
)
from app.context.runtime_context import customer_context_for_state
from app.tools.text_formatting import contains_chinese

# =============================================================================
# 1. Trace/ordering/signature policy
# =============================================================================

BATCH_DEPENDENCIES = {
    "get_last_completed_booking": {"find_pet_by_name", "get_pets"},
    "check_availability": {
        "resolve_datetime", "get_last_completed_booking", "get_booking_service_options"
    },
    "check_availability_range": {
        "resolve_datetime", "get_last_completed_booking", "get_booking_service_options"
    },
    "create_pet": {"create_customer"},
    "get_booking_service_options": {
        "create_pet", "find_pet_by_name", "get_pets", "get_last_completed_booking"
    },
    "create_booking": {
        "create_customer", "create_pet", "find_pet_by_name",
        "get_booking_service_options", "resolve_datetime",
        "check_availability", "check_availability_range",
        "update_pet_vaccination", "get_loyalty_balance",
        "check_coupon_eligibility",
    },
    "cancel_booking": {"get_latest_booking", "get_booking_by_id"},
    "reschedule_booking": {
        "get_latest_booking", "get_booking_by_id", "resolve_datetime",
        "check_availability", "check_availability_range",
    },
    "redeem_reward": {"create_booking", "check_coupon_eligibility"},
}


def mutation_signature(tool_name: str, args: dict) -> str:
    """Build the stable identity used to deduplicate one concrete action."""
    args = {
        key: value
        for key, value in args.items()
        if key != "idempotency_key" and not str(key).startswith("_internal_")
    }
    if tool_name == "register_loyalty_member":
        args = {key: value for key, value in args.items() if key != "confirmed"}
    if tool_name in {"cancel_booking", "reschedule_booking"}:
        args = {key: value for key, value in args.items() if key != "confirm_pet_name"}
    normalized = {
        key: (round(value, 2) if isinstance(value, float) else value)
        for key, value in sorted(args.items())
    }
    return f"{tool_name}:{json.dumps(normalized, sort_keys=True, default=str)}"


def tool_result_status(result) -> str:
    if isinstance(result, list):
        return "success" if result else "not_found"
    if not isinstance(result, dict):
        return "error"
    if result.get("status"):
        return str(result["status"])
    if result.get("error"):
        return "error"
    if result.get("found") is False:
        return "not_found"
    return "success"


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


def ordered_tool_calls(tool_calls: list[dict]) -> tuple[list[dict], bool]:
    """Return a stable topological order for dependencies in one response."""
    names = {call["name"] for call in tool_calls}
    remaining = list(enumerate(tool_calls))
    completed_names: set[str] = set()
    ordered: list[dict] = []
    has_dependency = False

    while remaining:
        progressed = False
        for position, (_, call) in enumerate(remaining):
            required = BATCH_DEPENDENCIES.get(call["name"], set()) & names
            if call["name"] != "update_conversation_state" and "update_conversation_state" in names:
                required = required | {"update_conversation_state"}
            if required:
                has_dependency = True
            if required <= completed_names:
                ordered.append(call)
                completed_names.add(call["name"])
                remaining.pop(position)
                progressed = True
                break
        if not progressed:
            ordered.extend(call for _, call in remaining)
            break
    return ordered, has_dependency


def response_requests_customer_input(content: str) -> bool:
    text = (content or "").strip().lower()
    return bool(re.search(
        r"[?？]|\b(?:what|which|when|where|who|could you|can you|please provide|please confirm)\b"
        r"|请问|哪一|什么时候|几点|可以告诉|请提供|请确认|请回复"
        r"|\b(?:apa|bila|di\s*mana|siapa|bolehkah|sila\s+(?:beritahu|sahkan|berikan))\b",
        text,
    ))


def _parsed_trace_result(item: dict):
    raw_result = item.get("result")
    try:
        return json.loads(raw_result) if isinstance(raw_result, str) else raw_result
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def trace_has_successful_mutation(trace: list[dict]) -> bool:
    mutation_tools = {"create_booking", "cancel_booking", "reschedule_booking", "redeem_reward"}
    return any(
        item.get("tool") in mutation_tools
        and isinstance(result := _parsed_trace_result(item), dict)
        and result.get("status") == "success"
        for item in trace
    )


def trace_has_successful_document_delivery(trace: list[dict]) -> bool:
    delivery_tools = {"create_booking", "reschedule_booking", "send_booking_confirmation"}
    for item in trace:
        if item.get("tool") not in delivery_tools:
            continue
        result = _parsed_trace_result(item)
        if not isinstance(result, dict) or result.get("status") != "success":
            continue
        data = result.get("data") or {}
        delivery_status = str(
            data.get("delivery_status")
            or data.get("confirmation_delivery_status")
            or ""
        )
        if delivery_status in {"sent", "sent_console"}:
            return True
    return False


def trace_has_document_delivery_attempt(trace: list[dict]) -> bool:
    delivery_tools = {"create_booking", "reschedule_booking", "send_booking_confirmation"}
    return any(item.get("tool") in delivery_tools for item in trace)


def trace_has_successful_availability(trace: list[dict]) -> bool:
    """Only a successful current-turn read can support offered times."""
    for item in trace:
        if item.get("tool") not in {"check_availability", "check_availability_range"}:
            continue
        result = _parsed_trace_result(item)
        if not isinstance(result, dict):
            continue
        if item.get("tool") == "check_availability_range" and isinstance(
            result.get("days"), list
        ):
            return True
        if tool_result_status(result) == "success":
            return True
    return False


def fresh_available_times(trace: list[dict]) -> set[str]:
    """Every real HH:MM time this turn's own check_availability/
    check_availability_range call(s) actually returned as available.

    create_booking has no confirmation_required step of its own (unlike
    cancel_booking/register_loyalty_member) — the "please confirm this
    booking" preview is composed entirely by the model from whatever
    availability evidence it has, which can be several turns old by the
    time the customer reaches an add-on/confirm step. A stated time that
    isn't in this set is exactly as fabricated as any other unsupported
    claim, just formatted as a summary line ("Check-in Time: 16:00")
    instead of a sentence — see _needs_tool_repair's booking-preview check.
    """
    times: set[str] = set()
    for item in trace:
        if item.get("tool") not in {"check_availability", "check_availability_range"}:
            continue
        result = _parsed_trace_result(item)
        if not isinstance(result, dict):
            continue
        if item.get("tool") == "check_availability_range":
            for day in result.get("days") or []:
                for slot in day.get("available_slots") or []:
                    times.add(str(slot)[:5])
            continue
        if tool_result_status(result) != "success":
            continue
        data = result.get("data") or {}
        for key in ("available_slots", "available_check_out_times"):
            for slot in data.get(key) or []:
                times.add(str(slot)[:5])
    return times


def successful_trace_tools(trace: list[dict]) -> set[str]:
    """Tool names with an actually successful result, not merely a call."""
    successful: set[str] = set()
    for item in trace:
        tool_name = item.get("tool")
        if not tool_name:
            continue
        if tool_result_status(_parsed_trace_result(item)) == "success":
            successful.add(str(tool_name))
    return successful


# =============================================================================
# 2. Batch planning and execution
# =============================================================================

def plan_tool_batch(
    tool_calls: list[dict],
    state: Any,
    *,
    ordered_tool_calls: Callable[[list[dict]], tuple[list[dict], bool]],
    mutation_signature: Callable[[str, dict], str],
    cacheable_read_tools: set[str],
    company_scoped_tools: set[str],
    customer_scoped_tools: set[str],
) -> tuple[list[dict], bool, str, str | None]:
    """Choose parallel or sequential execution without running any tool."""
    ordered_calls, has_dependency = ordered_tool_calls(tool_calls)
    read_signatures = [
        mutation_signature(
            tool_call["name"],
            {
                **dict(tool_call.get("args") or {}),
                **(
                    {"company_id": state.company_id}
                    if tool_call["name"] in company_scoped_tools
                    else {}
                ),
                **(
                    {"customer_id": state.customer_id}
                    if (
                        tool_call["name"] in customer_scoped_tools
                        and state.customer_id is not None
                    )
                    else {}
                ),
            },
        )
        for tool_call in tool_calls
        if tool_call["name"] in cacheable_read_tools
    ]
    has_duplicate_read_call = len(read_signatures) != len(set(read_signatures))
    unsafe_parallel_tools = sorted({
        call["name"]
        for call in tool_calls
        if call["name"] not in cacheable_read_tools
    })
    run_in_parallel = bool(
        len(tool_calls) > 1
        and not unsafe_parallel_tools
        and not has_dependency
        and not has_duplicate_read_call
    )
    if run_in_parallel:
        return ordered_calls, True, "parallel", None
    reason = (
        "single_call_in_iteration"
        if len(tool_calls) == 1
        else (
            "contains_dependency_chain"
            if has_dependency
            else (
                "contains_duplicate_read_calls"
                if has_duplicate_read_call
                else f"contains_non_parallel_safe_tools:{','.join(unsafe_parallel_tools)}"
            )
        )
    )
    return ordered_calls, False, "sequential", reason


def await_tool_future_result(
    future: Any,
    tool_call: dict,
    *,
    execution_mode: str,
    batch_started_at: float,
    submitted_at: dict[str, float],
    timeout_seconds: float,
    mutating_tool_names: set[str],
):
    """Bound one worker result and report unknown write outcomes safely."""
    timeout_origin = (
        batch_started_at
        if execution_mode == "parallel"
        else submitted_at.get(tool_call["id"], batch_started_at)
    )
    remaining_seconds = max(
        0.0,
        timeout_seconds - (time.perf_counter() - timeout_origin),
    )
    try:
        return future.result(timeout=remaining_seconds)
    except FutureTimeoutError:
        future.cancel()
        outcome_unknown = tool_call["name"] in mutating_tool_names
        return (
            {
                "status": "error",
                "error_code": (
                    "TOOL_TIMEOUT_OUTCOME_UNKNOWN"
                    if outcome_unknown
                    else "TOOL_TIMEOUT"
                ),
                "recoverable": not outcome_unknown,
                "message": (
                    f"{tool_call['name']} did not return within "
                    f"{timeout_seconds:g} seconds. "
                    + (
                        "Its write outcome is unknown: do not repeat the action or claim "
                        "success; staff must verify the record first."
                        if outcome_unknown
                        else "Do not claim success; retry once if useful or ask the customer "
                        "to try again."
                    )
                ),
                "handoff_required": outcome_unknown,
                "handoff_reason": (
                    "TOOL_TIMEOUT_OUTCOME_UNKNOWN" if outcome_unknown else None
                ),
            },
            round((time.perf_counter() - batch_started_at) * 1000, 1),
            0.0,
        )
    except Exception as exc:
        logging.getLogger(__name__).exception(
            "Tool worker failed for %s: %s", tool_call["name"], exc
        )
        return (
            {
                "status": "error",
                "error_code": "TOOL_WORKER_ERROR",
                "recoverable": False,
                "message": "The tool worker failed before returning a usable result.",
                "handoff_required": True,
                "handoff_reason": "TOOL_WORKER_ERROR",
            },
            round((time.perf_counter() - batch_started_at) * 1000, 1),
            0.0,
        )


def reused_failed_mutation(
    tool_name: str, failed_mutation_results: dict[str, dict]
) -> dict | None:
    cached_failure = failed_mutation_results.get(tool_name)
    if cached_failure is None:
        return None
    return {
        **cached_failure,
        "_internal_duplicate_mutation_suppressed": True,
        "message": (
            "This mutation already returned a non-recoverable operational "
            "failure during this customer turn. Do not call it again; explain "
            "the failure and continue without claiming success."
        ),
    }


def booking_availability_repair(
    records_by_id: dict[str, dict],
) -> tuple[dict, dict] | None:
    """Return exact availability and retry args from a rejected booking draft."""
    stale_booking_record = next(
        (
            record
            for record in records_by_id.values()
            if record["tool_call"]["name"] == "create_booking"
            and isinstance(record["result"], dict)
            and record["result"].get("error") == "UNVERIFIED_AVAILABILITY_SLOT"
            and isinstance(record["result"].get("_internal_required_args"), dict)
            and isinstance(record["result"].get("_internal_retry_args"), dict)
        ),
        None,
    )
    if stale_booking_record is None:
        return None
    result = stale_booking_record["result"]
    return result["_internal_required_args"], result["_internal_retry_args"]


def availability_result(records_by_id: dict[str, dict]):
    record = next(
        (
            item
            for item in records_by_id.values()
            if item["tool_call"]["name"] == "check_availability"
        ),
        None,
    )
    return record.get("result") if record is not None else None


def batch_has_duplicate_suppression(records_by_id: dict[str, dict]) -> bool:
    return any(
        isinstance(record.get("result"), dict)
        and (
            record["result"].get("_internal_duplicate_read_suppressed")
            or record["result"].get("_internal_duplicate_mutation_suppressed")
        )
        for record in records_by_id.values()
    )


# =============================================================================
# 3. Per-call dispatch
# =============================================================================

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


# =============================================================================
# 4. Turn lifecycle
# =============================================================================

def begin_turn_state(
    state: Any,
    user_message: str,
    *,
    is_repeat_booking_request: Callable[[str], bool],
    explicit_service_type: Callable[[str], str],
) -> None:
    """Advance one turn and expire authorization that is no longer current."""
    state.turn_counter += 1
    if state.active_scenario == "MAKE_BOOKING" and state.booking_flow_started_turn is None:
        state.booking_flow_started_turn = state.turn_counter
    state.pending_actions = {
        name: pending
        for name, pending in state.pending_actions.items()
        if int((pending or {}).get("preview_turn") or 0) >= state.turn_counter - 1
    }
    pending_booking = state.pending_booking_confirmation
    if (
        isinstance(pending_booking, dict)
        and pending_booking.get("preview_turn") is not None
        and int(pending_booking.get("preview_turn") or 0) < state.turn_counter - 1
    ):
        state.pending_booking_confirmation = None
    if is_repeat_booking_request(user_message):
        state.repeat_booking_template = None
        explicit_service = explicit_service_type(user_message)
        if explicit_service:
            state.service_type = explicit_service


def apply_datetime_resolution(
    state: Any,
    resolution: Any,
    *,
    cache_resolved_date: Callable[[Any, str, dict], None],
    compact_evidence_result: Callable[..., Any],
) -> None:
    if isinstance(resolution, dict) and not resolution.get("ambiguous"):
        state.current_datetime_resolution = resolution
        cache_resolved_date(state, "resolve_datetime", resolution)
        state.verified_facts["current_datetime_resolution"] = {
            "source": "deterministic_preprocessing",
            "turn": state.turn_counter,
            "result": compact_evidence_result(resolution),
        }
        return
    state.current_datetime_resolution = None
    state.verified_facts.pop("current_datetime_resolution", None)


def enrich_customer_context(customer: dict, state: Any) -> dict:
    """Surface authoritative session caches omitted by later identity reads."""
    if not customer.get("pets") and state.known_pets:
        customer = {**customer, "pets": state.known_pets}
    if not customer.get("latest_booking") and state.last_created_booking:
        customer = {
            **customer,
            "latest_booking": state.last_created_booking,
            "booking_context_status": "available",
        }
    return customer


def record_runtime_identity_evidence(customer: dict, state: Any) -> None:
    if customer.get("found"):
        state.verified_facts["resolved_customer"] = {
            "source": "runtime_identity",
            "customer_id": state.customer_id,
        }
    if state.known_pets:
        state.verified_facts["customer_pets"] = {
            "source": "runtime_identity",
            "pets": state.known_pets,
        }
    if state.latest_booking:
        state.verified_facts["latest_booking"] = {
            "source": "runtime_identity",
            "booking": state.latest_booking,
        }


def finalize_customer_response(
    orchestrator: Any,
    response: Any,
    *,
    customer: dict,
    company_context: dict,
    state: Any,
    user_message: str,
    trace: list[dict],
    is_first_message: bool,
    escalation_failed: bool,
    max_history_turns: int,
    is_staff_handoff_request: Callable[[str], bool],
    is_repeat_booking_request: Callable[[str], bool],
    trace_has_successful_document_delivery: Callable[[list[dict]], bool],
):
    """Apply deterministic grounders, presentation cleanup, and turn history."""
    final_customer = customer_context_for_state(customer, state)
    response = ground_direct_datetime_response(
        response, state, user_message,
        is_staff_handoff_request=is_staff_handoff_request,
    )
    response = ground_latest_availability_response(
        response, user_message, trace, state,
        is_repeat_booking_request=is_repeat_booking_request,
    )
    response = ground_daycare_recommendation_response(
        response, user_message, trace, state
    )
    response = ground_document_delivery_response(
        response, user_message, trace,
        trace_has_successful_delivery=trace_has_successful_document_delivery,
    )
    response = ground_membership_response(
        response, user_message, trace, state
    )
    response = ground_booking_preview_response(
        response, user_message, trace
    )
    response = ground_unavailable_profile_claims(
        response, user_message, final_customer
    )
    if is_first_message:
        response = orchestrator._ensure_first_message_greeting(
            response, final_customer, company_context, user_message
        )
    else:
        response = orchestrator._strip_redundant_greeting(response, final_customer)
    response = orchestrator._strip_internal_links(response)
    orchestrator._cache_loyalty_offer_presented(state, response.content, trace)
    if escalation_failed:
        warning = (
            "刚才无法把人工跟进请求写入系统；如果事情紧急，请直接联系员工。"
            if contains_chinese(user_message)
            else "I couldn't lodge the staff follow-up in our system just now. "
            "Please contact the team directly if this is urgent."
        )
        response = response.model_copy(
            update={"content": f"{response.content.rstrip()}\n\n{warning}"}
        )
    state.history.append({
        "role": "human", "content": user_message, "turn": state.turn_counter
    })
    state.history.append({
        "role": "ai", "content": response.content, "turn": state.turn_counter
    })
    state.history = state.history[-max_history_turns * 2 :]
    return response
