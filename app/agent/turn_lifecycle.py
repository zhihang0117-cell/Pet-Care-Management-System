"""Conversation turn initialization and customer-facing response finalization."""

from __future__ import annotations

from collections.abc import Callable
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
