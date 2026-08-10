from app.context.state import ConversationState
from app.db.relational_actions import _booking_interval
from app.orchestrator import PawfectOrchestrator


def test_existing_grooming_uses_persisted_duration():
    row = {"booking_time": "13:00:00", "duration_minutes": 150}
    assert _booking_interval(row, "GROOMING", "2026-08-11") == [(13 * 60, 15 * 60 + 30)]


def test_exact_active_slot_selection_keeps_full_context():
    state = ConversationState(phone_number="+60123456789", company_id="1")
    state.active_slots = [{
        "slot": "14:30",
        "service_type": "GROOMING",
        "date": "2026-08-17",
        "duration_minutes": 90,
        "room_type": "",
        "check_out_date": "",
        "check_out_time": "",
    }]
    orch = object.__new__(PawfectOrchestrator)
    selected = orch._resolve_active_slot_reference(state, "2:30pm")
    assert selected is not None
    state.selected_slot = selected
    bound = orch._bind_selected_slot_to_availability_args(
        state,
        {"service_type": "DAYCARE", "date": "2099-01-01", "time": "2:30pm"},
        "2:30pm",
    )
    assert bound["service_type"] == "GROOMING"
    assert bound["date"] == "2026-08-17"
    assert bound["time"] == "14:30"
    assert bound["duration_minutes"] == 90


def test_exact_unavailable_replaces_old_slot_list():
    state = ConversationState(phone_number="+60123456789", company_id="1")
    state.active_slots = [{"slot": "14:30"}]
    orch = object.__new__(PawfectOrchestrator)
    result = {
        "status": "success",
        "data": {
            "service_type": "GROOMING",
            "booking_date": "2026-08-17",
            "requested_time": "14:30",
            "requested_time_available": False,
            "availability_scope": "exact",
            "available_slots": [],
            "all_available_slots": ["12:00", "13:00", "15:00"],
            "service_duration_minutes": 90,
        },
    }
    orch._cache_offered_options(
        state,
        "check_availability",
        result,
        {"service_type": "GROOMING", "date": "2026-08-17", "time": "14:30"},
    )
    assert state.selected_slot is None
    assert [x["slot"] for x in state.active_slots] == ["12:00", "13:00", "15:00"]
    assert "14:30" not in [x["slot"] for x in state.active_slots]
