"""Deterministic scenario transitions for conversation state."""

from __future__ import annotations

from app.scenarios.loader import load_scenario


SCENARIO_CONFIRMING_TOOLS = {
    "create_booking": "MAKE_BOOKING",
    "cancel_booking": "CANCEL_BOOKING",
    "reschedule_booking": "RESCHEDULE_BOOKING",
    "redeem_reward": "LOYALTY_QUERY",
    "register_loyalty_member": "MEMBER",
    "send_booking_confirmation": "BOOKING_DOCUMENT",
}


def set_objective_from_scenario(state, scenario_name: str | None) -> None:
    if not scenario_name:
        state.objective = None
        return
    try:
        state.objective = load_scenario(scenario_name).get("goal")
    except ValueError:
        state.objective = None


def apply_scenario_update(state, result: dict) -> None:
    """Apply an observed update_conversation_state result to session state."""
    if not isinstance(result, dict) or result.get("error"):
        return

    previous_scenario = state.active_scenario
    requested_scenario = result.get("active_scenario")
    preserve_main_goal_for_side_question = bool(
        previous_scenario == "MAKE_BOOKING"
        and requested_scenario in {"POLICY_QUERY", "MEMBER"}
    )
    if preserve_main_goal_for_side_question:
        return

    state.active_scenario = requested_scenario
    state.current_step = result.get("current_step")
    if result.get("service_type"):
        state.service_type = result.get("service_type")
    elif state.active_scenario != "MAKE_BOOKING":
        state.service_type = None
    if str(state.service_type or "").upper() != "DAYCARE":
        state.daycare_duration_minutes = None
        state.verified_facts.pop("daycare_duration_minutes", None)
    if previous_scenario != state.active_scenario:
        if state.active_scenario == "MAKE_BOOKING":
            state.booking_flow_started_turn = state.turn_counter
        elif previous_scenario == "MAKE_BOOKING":
            state.booking_flow_started_turn = None
        state.pending_actions = {}
        state.pending_booking_confirmation = None
        state.offered_options = []
        state.offered_add_on_options = []
        state.missing_information = []
        state.missing_information_by_tool = {}
        if (
            state.active_scenario == "MAKE_BOOKING"
            and len(state.known_pets) > 1
            and state.pet_selected_turn != state.turn_counter
        ):
            state.pet_id = None
            state.pet_type = None
            state.pet_name = None
            state.pet_size = None
            state.pet_breed = None
    if previous_scenario == "MAKE_BOOKING" and state.active_scenario != "MAKE_BOOKING":
        state.preferred_staff = None
        state.loyalty_decision = None
        state.loyalty_offer_shown_turn = None
        state.repeat_booking_template = None
        state.pending_actions.pop("create_booking", None)
        state.verified_availability_slots = []
    set_objective_from_scenario(state, state.active_scenario)
    state.completion_status = "in_progress" if state.active_scenario else None


def sync_scenario_from_tool_call(state, tool_name: str, result: dict) -> None:
    """Release scenario routing after its concrete action succeeds."""
    confirmed = SCENARIO_CONFIRMING_TOOLS.get(tool_name)
    if (
        confirmed
        and confirmed == state.active_scenario
        and isinstance(result, dict)
        and result.get("status") == "success"
    ):
        state.active_scenario = None
        state.current_step = None
        state.service_type = None
        state.objective = None
        state.offered_options = []
        state.offered_add_on_options = []
        state.missing_information = []
        state.missing_information_by_tool = {}
