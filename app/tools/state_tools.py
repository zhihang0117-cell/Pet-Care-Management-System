from langchain_core.tools import tool
from typing import Literal

from app.scenarios.loader import valid_steps


@tool
def update_conversation_state(
    active_scenario: Literal[
        "MAKE_BOOKING", "CANCEL_BOOKING", "RESCHEDULE_BOOKING", "LOYALTY_QUERY",
        "MEMBER", "PAYMENT_QUERY", "ENQUIRY", "BOOKING_DOCUMENT", "POLICY_QUERY", ""
    ],
    current_step: str = "",
    service_type: Literal["GROOMING", "DAYCARE", "BOARDING", ""] = "",
) -> dict:
    """
    Declare or switch the current business goal for this conversation.

    Use this when declaring or switching the business goal improves ongoing
    context and console observability. It is bookkeeping, not a prerequisite
    for a useful read/action tool, and it may be called alongside that next
    useful tool rather than consuming a turn by itself. Pass one of:
    MAKE_BOOKING, CANCEL_BOOKING, RESCHEDULE_BOOKING, LOYALTY_QUERY, MEMBER,
    PAYMENT_QUERY, ENQUIRY, BOOKING_DOCUMENT, POLICY_QUERY, or "" to clear it once the scenario's goal is complete
    (e.g. right after a booking is confirmed, cancelled, or rescheduled).

    During MAKE_BOOKING, pass service_type (GROOMING/DAYCARE/BOARDING) once
    you know it. current_step is optional orientation only: it is validated
    against the scenario vocabulary when supplied, but never enforces order.
    Skip facts already verified and move directly to the most useful next
    action when the customer already gave enough information.

    This only updates internal conversation bookkeeping and never touches
    customer data. Do not call it merely to acknowledge the customer's
    message, and do not repeat it when the goal has not changed.
    """
    resolved_scenario = active_scenario or None
    resolved_step = current_step or None
    note = None
    if resolved_scenario and resolved_step:
        known_steps = valid_steps(resolved_scenario, service_type or None)
        if known_steps is not None and resolved_step not in known_steps:
            note = (
                f"Unrecognized current_step {resolved_step!r} for {resolved_scenario} "
                f"— not stored. Valid steps: {sorted(known_steps)}"
            )
            resolved_step = None
    result = {
        "active_scenario": resolved_scenario,
        "current_step": resolved_step,
        "service_type": service_type or None,
    }
    if note:
        result["note"] = note
    return result
