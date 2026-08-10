"""Unit tests for app/agent/booking_hydration.py's verified-argument
hydration — fills a DROPPED tool argument from already-selected
BookingWorkingState, never something the model never established."""

from app.agent.booking_hydration import hydrate_tool_args
from app.context.state import ConversationState


def _state_with_selection():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.booking.pet_ref = "1"
    state.booking.selected_option_ref = "option_standard"
    state.booking.selected_slot_ref = "slot_1"
    state.booking.datetime_ref = "datetime_a"
    state.booking.preferred_staff = "Ari"
    state.booking.preview_ref = "preview_1"
    return state


def test_check_availability_fills_only_the_dropped_option_ref():
    state = _state_with_selection()
    args = hydrate_tool_args(
        state, {}, "check_availability",
        {"pet_ref": "1", "option_ref": "", "datetime_ref": "datetime_b"},
    )
    assert args["option_ref"] == "option_standard"
    # The explicitly-supplied datetime_ref must never be overridden.
    assert args["datetime_ref"] == "datetime_b"


def test_check_availability_prefers_the_current_messages_own_datetime_over_a_stale_selection():
    state = _state_with_selection()
    args = hydrate_tool_args(
        state, {"current_message_datetime_ref": "datetime_fresh"}, "check_availability",
        {"pet_ref": "1", "option_ref": "option_standard", "datetime_ref": ""},
    )
    assert args["datetime_ref"] == "datetime_fresh"


def test_check_availability_falls_back_to_the_stored_selection_when_nothing_fresher_exists():
    state = _state_with_selection()
    args = hydrate_tool_args(
        state, {}, "check_availability",
        {"pet_ref": "1", "option_ref": "option_standard", "datetime_ref": ""},
    )
    assert args["datetime_ref"] == "datetime_a"


def test_preview_booking_fills_dropped_option_and_slot_refs():
    state = _state_with_selection()
    args = hydrate_tool_args(
        state, {}, "preview_booking",
        {"pet_ref": "1", "option_ref": "", "slot_ref": ""},
    )
    assert args["option_ref"] == "option_standard"
    assert args["slot_ref"] == "slot_1"


def test_confirm_booking_fills_a_dropped_preview_ref():
    state = _state_with_selection()
    args = hydrate_tool_args(state, {}, "confirm_booking", {"preview_ref": ""})
    assert args["preview_ref"] == "preview_1"


def test_never_invents_anything_that_was_never_selected():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    args = hydrate_tool_args(
        state, {}, "check_availability",
        {"pet_ref": "", "option_ref": "", "datetime_ref": ""},
    )
    assert args["pet_ref"] == ""
    assert args["option_ref"] == ""
    assert args["datetime_ref"] == ""


def test_untouched_tool_names_pass_through_unchanged():
    state = _state_with_selection()
    args = hydrate_tool_args(state, {}, "get_loyalty", {})
    assert args == {}
