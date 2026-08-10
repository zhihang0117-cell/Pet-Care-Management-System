"""Phase 2 of REFACTOR_PLAN.md — normalize_tool_result() validated against
REAL result shapes captured from live tool calls earlier in this session
(real Supabase data, real gpt-4o-mini calls), not hypothetical ones.
"""

from app.agent.envelope import normalize_tool_result


def test_relational_actions_result_shape_success():
    # Real check_available_slots success result (BOARDING, Neptune Room,
    # captured live earlier this session) — includes the pet_already_booked
    # field added today.
    raw = {
        "action": "check_available_slots",
        "status": "success",
        "success": True,
        "data_found": True,
        "data": {
            "service_type": "BOARDING",
            "booking_date": "2026-08-10",
            "available_slots": [],
            "room_type": "Neptune Room",
            "pet_already_booked": {
                "service_type": "BOARDING",
                "booking_id": 204,
                "check_in_date": "2026-08-08",
                "check_out_date": "2026-08-11",
            },
        },
        "error": None,
        "handoff_required": False,
        "handoff_reason": None,
    }
    result = normalize_tool_result(raw)
    assert result["ok"] is True
    assert result["code"] == "OK"
    assert result["data"]["pet_already_booked"]["booking_id"] == 204
    assert result["needs"] == []
    # No TOP-LEVEL booking_id here — this is an availability check, not a
    # booking; the 204 is nested inside pet_already_booked, a fact about a
    # DIFFERENT existing booking, not this call's own identity.
    assert result["evidence"] is None
    assert result["recoverable"] is True
    assert result["handoff"] is False


def test_relational_actions_result_shape_missing_information():
    # Real check_available_slots BOARDING missing_information result.
    raw = {
        "action": "check_available_slots",
        "status": "missing_information",
        "success": True,
        "data_found": False,
        "data": {
            "service_type": "BOARDING",
            "missing_fields": ["specific_check_in_date", "room_type", "check_out_date"],
            "days": [],
        },
        "error": (
            "Boarding choices require a specific check-in date, selected room, and "
            "check-out date before final time slots can be offered."
        ),
        "handoff_required": False,
        "handoff_reason": None,
    }
    result = normalize_tool_result(raw)
    assert result["ok"] is True  # the call itself succeeded — it's a real, complete answer
    assert result["code"] == "EVIDENCE_MISSING"
    assert result["needs"] == ["specific_check_in_date", "room_type", "check_out_date"]


def test_check_availability_range_top_level_shape_has_no_nested_data_key():
    # Real check_availability_range success result — fields live at the top
    # level (no "data" key at all), unlike relational_actions._result().
    raw = {
        "status": "success",
        "service_type": "GROOMING",
        "start_date": "2026-08-10",
        "end_date": "2026-08-16",
        "days": [{"date": "2026-08-10", "available_slots": ["09:00:00"]}],
    }
    result = normalize_tool_result(raw)
    assert result["ok"] is True
    assert result["code"] == "OK"
    assert result["data"]["days"][0]["date"] == "2026-08-10"


def test_inline_tool_error_with_no_status_key_at_all():
    # Real create_booking rejection — no "status" key, just "error"/"message".
    raw = {
        "error": "UNVERIFIED_SERVICE_OPTION",
        "message": (
            "package_name/price is not an exact option observed from "
            "get_booking_service_options for this service and pet."
        ),
    }
    result = normalize_tool_result(raw)
    assert result["ok"] is False
    assert result["code"] == "UNVERIFIED_SERVICE_OPTION"


def test_guardrail_rejection_shape():
    # Real reject_unverified_booking_payload rejection (the exact bug fixed
    # earlier today).
    raw = {
        "error": "UNVERIFIED_AVAILABILITY_SLOT",
        "message": (
            "This exact service/date/time was not returned as available by "
            "check_availability with the same room/stay, duration, and staff constraints."
        ),
    }
    result = normalize_tool_result(raw)
    assert result["ok"] is False
    assert result["code"] == "UNVERIFIED_AVAILABILITY_SLOT"
    assert result["recoverable"] is True  # customer can just pick a different time


def test_bare_data_dict_with_no_status_or_error_key():
    # Real resolve_datetime result — plain data, no "status"/"error" at all.
    raw = {
        "date": "2026-08-10",
        "date_range": None,
        "time": None,
        "end_time": None,
        "period": None,
        "duration_minutes": None,
        "ambiguous": False,
        "needs_time_selection": False,
        "raw_text": "next monday",
    }
    result = normalize_tool_result(raw)
    assert result["ok"] is True
    assert result["code"] == "OK"
    assert result["needs"] == []
    assert result["data"]["date"] == "2026-08-10"


def test_ambiguous_resolve_datetime_result_surfaces_as_a_need_not_a_failure():
    raw = {"date": None, "date_range": None, "time": None, "ambiguous": True}
    result = normalize_tool_result(raw)
    assert result["ok"] is True  # resolve_datetime itself never "fails"
    assert result["needs"] == ["date_or_time"]


def test_evidence_is_extracted_from_a_real_successful_write():
    # Real create_booking success result (booking_id=363, created and
    # cleaned up during today's live testing).
    raw = {
        "action": "create_booking",
        "status": "success",
        "success": True,
        "data_found": True,
        "data": {
            "booking_id": 363,
            "service_type": "BOARDING",
            "booking_date": "2026-09-21",
            "check_in_date": "2026-09-21",
        },
        "error": None,
        "handoff_required": False,
        "handoff_reason": None,
    }
    result = normalize_tool_result(raw)
    assert result["ok"] is True
    assert result["evidence"] == {"ref_kind": "booking_id", "ref": 363}


def test_confirmation_required_shape_is_ok_not_a_failure():
    # Real create_booking preview result.
    raw = {
        "status": "confirmation_required",
        "error_code": "ACTION_PREVIEW_CREATED",
        "data": {"action": "create_booking", "preview": {"package_name": "Nova Deluxe", "price": 198}},
        "message": "Reply yes to create the booking.",
    }
    result = normalize_tool_result(raw)
    assert result["ok"] is True
    assert result["code"] == "ACTION_PREVIEW_CREATED"


def test_malformed_non_dict_result_is_never_silently_accepted():
    result = normalize_tool_result("not a dict")
    assert result["ok"] is False
    assert result["code"] == "MALFORMED_RESULT"
    assert result["recoverable"] is False
