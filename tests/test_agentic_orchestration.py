import json
from pathlib import Path

from app.context.company import get_company_config
from app.context.state import ConversationState
from app.orchestrator import PawfectOrchestrator, TOOLS_BY_NAME, ToolLoopError
from app.prompts.system_prompt import SYSTEM_PROMPT
from app.tools.document_tools import send_booking_confirmation
from app.tools.customer_tools import _extract_daycare_catalogue_options


def _call(name, call_id, **args):
    return {"name": name, "id": call_id, "args": args}


def test_scenarios_describe_evidence_and_completion_not_a_rigid_script():
    scenario_dir = Path(__file__).resolve().parents[1] / "app" / "scenarios"
    for path in scenario_dir.glob("*.json"):
        scenario = json.loads(path.read_text())
        assert scenario["goal"]
        assert scenario["required_evidence"]
        assert scenario["completion_condition"]
        assert "must follow every step" not in scenario.get("flow_policy", "").lower()


def test_dependency_order_resolves_dates_before_availability():
    calls = [
        _call("check_availability", "availability", date="2026-08-08"),
        _call("resolve_datetime", "date", text="this Saturday"),
    ]
    ordered, has_dependency = PawfectOrchestrator._ordered_tool_calls(calls)
    assert has_dependency is True
    assert [call["name"] for call in ordered] == ["resolve_datetime", "check_availability"]


def test_state_update_runs_before_sibling_business_tools():
    calls = [
        _call("get_booking_service_options", "options", service_type="GROOMING"),
        _call("update_conversation_state", "state", active_scenario="MAKE_BOOKING"),
    ]
    ordered, has_dependency = PawfectOrchestrator._ordered_tool_calls(calls)
    assert has_dependency is True
    assert ordered[0]["name"] == "update_conversation_state"


def test_runtime_context_refreshes_scenario_and_business_clock():
    orchestrator = object.__new__(PawfectOrchestrator)
    state = ConversationState(phone_number="+60123456705", company_id="7")
    customer = {"found": True, "customer_id": 10, "full_name": "Alicia Lee"}

    before = orchestrator._runtime_context(
        {"company_id": "7", "company_name": "Pawfect", "timezone": "Asia/Kuala_Lumpur"},
        customer,
        state,
        available_tools=[],
    )
    orchestrator._apply_tool_result_state(
        state,
        _call("update_conversation_state", "state"),
        {"active_scenario": "MAKE_BOOKING", "current_step": None, "service_type": "DAYCARE"},
    )
    after = orchestrator._runtime_context(
        {"company_id": "7", "company_name": "Pawfect", "timezone": "Asia/Kuala_Lumpur"},
        customer,
        state,
        available_tools=[],
    )

    assert before["scenario_definition"] is None
    assert after["scenario_definition"]["scenario"] == "MAKE_BOOKING"
    assert after["conversation_state"]["service_type"] == "DAYCARE"
    assert after["company"]["business_date"]
    assert after["company"]["business_datetime"]


def test_company_config_exposes_current_safe_database_profile(monkeypatch):
    monkeypatch.setattr(
        "app.db.relational_actions.get_company_information",
        lambda context: {
            "status": "success",
            "data": {
                "company": {
                    "company_name": "Pawfect Care",
                    "business_description": "Pet grooming and daycare",
                    "street_address": "12 Jalan Pets",
                    "city": "Kuala Lumpur",
                    "state": "WP Kuala Lumpur",
                    "postcode": "50000",
                    "country": "Malaysia",
                }
            },
        },
    )
    monkeypatch.setattr(
        "app.context.company._real_business_hours",
        lambda company_id: [{"day": "Monday", "open": "09:00", "close": "18:00"}],
    )

    company = get_company_config("7")

    assert company["company_id"] == "7"
    assert company["company_profile_status"] == "success"
    assert company["company_name"] == "Pawfect Care"
    assert company["business_description"] == "Pet grooming and daycare"
    assert company["address"] == "12 Jalan Pets, Kuala Lumpur, WP Kuala Lumpur, 50000, Malaysia"
    assert company["street_address"] == "12 Jalan Pets"
    assert company["business_hours"][0]["open"] == "09:00"


def test_company_config_does_not_invent_profile_when_database_read_fails(monkeypatch):
    monkeypatch.setattr(
        "app.db.relational_actions.get_company_information",
        lambda context: {"status": "error", "data": {}, "error": "database unavailable"},
    )
    monkeypatch.setattr("app.context.company._real_business_hours", lambda company_id: [])

    company = get_company_config("7")

    assert company["company_profile_status"] == "error"
    assert company["company_name"] is None
    assert company["address"] is None
    assert "database unavailable" not in str(company)


def test_company_scope_is_injected_even_when_model_omits_it(monkeypatch):
    class CapturingTool:
        def invoke(self, args):
            return dict(args)

    monkeypatch.setitem(TOOLS_BY_NAME, "retrieve_policy", CapturingTool())
    state = ConversationState(phone_number="+60123456705", company_id="7")
    tool_call = _call("retrieve_policy", "policy", query="opening hours")

    result = object.__new__(PawfectOrchestrator)._run_tool(tool_call, state=state)

    assert result["company_id"] == "7"
    assert tool_call["args"]["company_id"] == "7"


def test_customer_scope_is_injected_even_when_model_omits_it(monkeypatch):
    class CapturingTool:
        def invoke(self, args):
            return dict(args)

    monkeypatch.setitem(TOOLS_BY_NAME, "get_pets", CapturingTool())
    state = ConversationState(phone_number="+60123456705", company_id="7", customer_id=42)
    tool_call = _call("get_pets", "pets")

    result = object.__new__(PawfectOrchestrator)._run_tool(tool_call, state=state)

    assert result["company_id"] == "7"
    assert result["customer_id"] == 42


def test_loyalty_decline_is_explicit_but_generic_yes_is_not():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.turn_counter = 1
    PawfectOrchestrator._capture_explicit_loyalty_decision(
        state, "No voucher please, just proceed with the booking"
    )
    assert state.loyalty_decision == "declined"

    ambiguous = ConversationState(phone_number="+60123456705", company_id="1")
    ambiguous.turn_counter = 1
    PawfectOrchestrator._capture_explicit_loyalty_decision(ambiguous, "yes")
    assert ambiguous.loyalty_decision is None


def test_short_no_need_declines_a_real_prior_loyalty_offer():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.turn_counter = 3
    state.loyalty_offer_shown_turn = 2

    PawfectOrchestrator._capture_explicit_loyalty_decision(state, "no need")

    assert state.loyalty_decision == "declined"


def test_daycare_duration_survives_side_flow_and_is_injected_into_checks_and_write():
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        service_type="DAYCARE",
    )
    state.turn_counter = 2
    PawfectOrchestrator._capture_explicit_daycare_duration(state, "daycare 三个小时")

    availability = PawfectOrchestrator._inject_cached_daycare_duration(
        state,
        "check_availability",
        {"service_type": "DAYCARE", "date": "2026-08-07", "time": "11:30"},
    )
    booking = PawfectOrchestrator._inject_cached_daycare_duration(
        state,
        "create_booking",
        {"service_type": "DAYCARE", "date": "2026-08-07", "time": "11:30"},
    )

    assert state.daycare_duration_minutes == 180
    assert availability["duration_minutes"] == 180
    assert booking["duration_minutes"] == 180


def test_exact_pickup_wins_over_cached_daycare_duration():
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        service_type="DAYCARE",
        daycare_duration_minutes=180,
    )
    args = PawfectOrchestrator._inject_cached_daycare_duration(
        state,
        "create_booking",
        {"service_type": "DAYCARE", "check_out_time": "17:00"},
    )

    assert "duration_minutes" not in args


def test_daycare_catalogue_separates_add_ons_and_only_exposes_exact_duration():
    services, add_ons = _extract_daycare_catalogue_options([
        {
            "content": (
                "Daycare Packages:\n"
                "1. Daycare 3 Hours - RM45\n"
                "2. Daycare Above 3 Hours - RM55\n"
                "3. Splash Pool Session Add-on - RM15"
            )
        }
    ])

    assert [option["service_name"] for option in services] == [
        "Daycare 3 Hours",
        "Daycare Above 3 Hours",
    ]
    assert services[0]["duration_minutes"] == 180
    assert "duration_minutes" not in services[1]
    assert [option["service_name"] for option in add_ons] == ["Splash Pool Session Add-on"]


def test_evidence_is_bounded_and_internal_delivery_fields_are_removed():
    compact = PawfectOrchestrator._compact_evidence_result(
        {
            "status": "success",
            "data": {"booking_id": 8, "_internal_confirmation_url": "signed-secret"},
            "long": "x" * 900,
        }
    )
    encoded = json.dumps(compact)
    assert "_internal_confirmation_url" not in encoded
    assert "signed-secret" not in encoded
    assert len(compact["long"]) < 900


def test_unrelated_tool_success_does_not_erase_missing_information():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    PawfectOrchestrator._update_agent_evidence(
        state,
        "get_booking_service_options",
        {"status": "missing_information", "data": {"missing_fields": ["service_type"]}},
    )
    PawfectOrchestrator._update_agent_evidence(
        state,
        "resolve_datetime",
        {"status": "success", "date": "2026-08-08"},
    )
    assert state.missing_information == ["service_type"]

    PawfectOrchestrator._update_agent_evidence(
        state,
        "get_booking_service_options",
        {"status": "success", "data": {"service_options": []}},
    )
    assert state.missing_information == []


def test_range_availability_becomes_reusable_offered_options():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    PawfectOrchestrator._cache_offered_options(
        state,
        "check_availability_range",
        {
            "start_date": "2026-08-04",
            "end_date": "2026-08-05",
            "days": [
                {"date": "2026-08-04", "weekday": "Tuesday", "available_slots": ["10:00", "14:00"]}
            ],
        },
    )
    assert state.offered_options == [
        {"label": "2026-08-04 10:00", "date": "2026-08-04", "weekday": "Tuesday", "slot": "10:00"},
        {"label": "2026-08-04 14:00", "date": "2026-08-04", "weekday": "Tuesday", "slot": "14:00"},
    ]


def test_successful_action_releases_scenario_but_keeps_booking_evidence():
    orchestrator = object.__new__(PawfectOrchestrator)
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        service_type="GROOMING",
        current_step="CREATE_BOOKING",
    )
    result = {
        "status": "success",
        "data": {
            "booking_id": 91,
            "service_type": "GROOMING",
            "pet_id": 3,
            "pet_name": "Milo",
            "booking_date": "2026-08-08",
            "booking_time": "10:00",
            "payment_id": 55,
        },
    }
    orchestrator._apply_tool_result_state(state, _call("create_booking", "create"), result)

    assert state.active_scenario is None
    assert state.last_created_booking["booking_id"] == 91
    assert state.verified_facts["created_booking"]["status"] == "success"


def test_tool_repair_is_narrow_and_reuses_existing_evidence():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    assert PawfectOrchestrator._needs_tool_repair(
        state, "Please book Milo", "Milo's booking is confirmed.", []
    )
    assert not PawfectOrchestrator._needs_tool_repair(
        state, "Please book Milo", "Which date would you prefer?", []
    )

    state.verified_facts["service_options"] = {"status": "success"}
    assert not PawfectOrchestrator._needs_tool_repair(
        state, "What is the grooming price?", "The verified option is RM50.", []
    )

    unrelated_trace = [{"tool": "retrieve_policy", "result": '{"status":"success"}'}]
    assert PawfectOrchestrator._needs_tool_repair(
        ConversationState(phone_number="+60123456705", company_id="1"),
        "Is 10 AM available?",
        "10 AM is available.",
        unrelated_trace,
    )
    assert PawfectOrchestrator._needs_tool_repair(
        ConversationState(phone_number="+60123456705", company_id="1"),
        "Where is my confirmation slip?",
        "It has been sent as a document.",
        [],
    )


def test_prompt_keeps_recommendations_evidence_based_and_optional():
    assert "Helpful recommendations are a core capability" in SYSTEM_PROMPT
    assert "suggest only options supported by current-company evidence" in SYSTEM_PROMPT
    assert "do not repeat a declined" in SYSTEM_PROMPT
    assert "Wording is free; there is no fixed greeting sentence" in SYSTEM_PROMPT


def test_tool_loop_error_preserves_completed_trace():
    trace = [{"tool": "resolve_datetime", "result": '{"status":"success"}'}]
    error = ToolLoopError("did not converge", trace)
    assert error.trace == trace
    assert error.trace is not trace


def test_confirmation_resend_resolves_latest_booking_and_dispatches(monkeypatch):
    class Repo:
        def get_latest_booking(self, company_id, customer_id):
            assert (company_id, customer_id) == (7, 42)
            return {
                "status": "success",
                "data": {"booking_id": 91, "last_service_type": "GROOMING"},
            }

        def get_booking_by_id(self, company_id, customer_id, booking_id, service_type):
            assert (company_id, customer_id, booking_id, service_type) == (7, 42, 91, "GROOMING")
            return {
                "status": "success",
                "data": {"booking_id": 91, "service_type": "GROOMING", "pet_id": 3, "pet_name": "Milo"},
            }

    monkeypatch.setattr("app.tools.document_tools.get_relational_repository", lambda: Repo())
    monkeypatch.setattr(
        "app.documents.service.generate_and_send_booking_confirmation",
        lambda company_id, booking: {
            "status": "success",
            "document_url": "https://example.test/confirmation.pdf",
            "send_result": {"status": "sent_console"},
        },
    )

    result = send_booking_confirmation.invoke({"company_id": 7, "customer_id": 42})

    assert result["status"] == "success"
    assert result["data"]["booking_id"] == 91
    assert result["data"]["delivery_status"] == "sent_console"
    assert result["data"]["_internal_document_url"].endswith("confirmation.pdf")


def test_confirmation_resend_never_claims_success_when_delivery_failed(monkeypatch):
    class Repo:
        def get_booking_by_id(self, *_args):
            return {
                "status": "success",
                "data": {"booking_id": 91, "service_type": "GROOMING", "pet_id": 3},
            }

    monkeypatch.setattr("app.tools.document_tools.get_relational_repository", lambda: Repo())
    monkeypatch.setattr(
        "app.documents.service.generate_and_send_booking_confirmation",
        lambda company_id, booking: {
            "status": "success",
            "document_url": "https://example.test/confirmation.pdf",
            "send_result": {"status": "not_configured"},
        },
    )

    result = send_booking_confirmation.invoke({
        "company_id": 7,
        "customer_id": 42,
        "booking_id": 91,
        "service_type": "GROOMING",
    })

    assert result["status"] == "error"
    assert result["handoff_required"] is True
    assert result["data"]["delivery_status"] == "not_configured"
