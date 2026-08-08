import json
from pathlib import Path

from langchain_core.messages import AIMessage

from app.context.company import get_company_config
from app.context.state import ConversationState
from app.orchestrator import (
    PawfectOrchestrator,
    TOOLS_BY_NAME,
    ToolLoopError,
    _tools_for_scenario,
)
from app.prompts.system_prompt import SYSTEM_PROMPT
from app.tools.document_tools import send_booking_confirmation
from app.tools.customer_tools import _extract_daycare_catalogue_options
from app.tools.booking_tools import _verify_flat_price_against_catalogue


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


def test_repeat_booking_dependencies_finish_history_and_catalogue_before_availability():
    calls = [
        _call("check_availability", "availability", date="2026-08-08"),
        _call("get_booking_service_options", "options", service_type="GROOMING"),
        _call("get_last_completed_booking", "history", service_type="GROOMING"),
    ]

    ordered, has_dependency = PawfectOrchestrator._ordered_tool_calls(calls)

    assert has_dependency is True
    assert [call["name"] for call in ordered] == [
        "get_last_completed_booking",
        "get_booking_service_options",
        "check_availability",
    ]


def test_make_booking_toolset_keeps_side_policy_but_excludes_latest_booking_read():
    names = {tool.name for tool in _tools_for_scenario("MAKE_BOOKING")}

    assert "get_last_completed_booking" in names
    assert "get_booking_service_options" in names
    assert "check_availability" in names
    assert "retrieve_policy" in names
    assert "get_latest_booking" not in names


def test_repeat_history_tool_schema_accepts_pet_and_service_scope():
    schema_model = TOOLS_BY_NAME["get_last_completed_booking"].args_schema
    schema = (
        schema_model.model_json_schema()
        if hasattr(schema_model, "model_json_schema")
        else schema_model.schema()
    )

    assert "pet_id" in schema["properties"]
    assert "service_type" in schema["properties"]


def test_narrative_catalogue_label_matches_booking_table_product_name():
    services, add_ons = _extract_daycare_catalogue_options(
        [
            {
                "content": (
                    "Cat Bathing Packages:\n"
                    "The Standard Bath - Groomers Choice package is priced at RM80."
                ),
                "metadata": {"pet_type": "cat", "service_type": "grooming"},
            }
        ]
    )

    assert add_ons == []
    assert services[0]["service_name"] == "Standard Bath - Groomers Choice"
    assert services[0]["price"] == 80


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
    assert "timezone" not in before["company"]
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


def test_nono_declines_add_ons_without_reopening_stale_loyalty_flow():
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        turn_counter=5,
        loyalty_decision="accepted",
        loyalty_offer_shown_turn=2,
        history=[
            {
                "role": "ai",
                "content": "Would you like to include any add-ons?",
            }
        ],
    )
    state.offered_add_on_options = [
        {"service_name": "Nail Clipping", "selection_kind": "add_on", "price": 15}
    ]

    PawfectOrchestrator._capture_explicit_add_on_decision(state, "nono")
    names = {tool.name for tool in PawfectOrchestrator._tools_for_turn(state, "nono")}

    assert PawfectOrchestrator._confirmation_intent("nono") == "negative"
    assert state.verified_facts["add_on_decision"]["value"] == "declined"
    assert not names & {
        "get_loyalty_balance",
        "check_coupon_eligibility",
        "register_loyalty_member",
        "redeem_reward",
    }
    assert "create_booking" in names


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


def test_daycare_time_range_is_cached_as_exact_duration():
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        service_type="DAYCARE",
    )

    PawfectOrchestrator._capture_explicit_daycare_duration(
        state, "早上9点到下午5点"
    )

    assert state.daycare_duration_minutes == 480


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
    assert services[1]["min_duration_minutes"] == 180
    assert services[1]["min_duration_exclusive"] is True
    assert [option["service_name"] for option in add_ons] == ["Splash Pool Session Add-on"]


def test_daycare_catalogue_marks_hourly_rates_for_total_price_validation():
    services, _ = _extract_daycare_catalogue_options([
        {"content": "Hourly Care - RM15/hour"}
    ])

    assert services == [
        {
            "service_name": "Hourly Care",
            "price": 15.0,
            "price_display": "RM15",
            "selection_kind": "service",
            "source": "company_rag",
            "pricing_unit": "hour",
        }
    ]


def test_daycare_hourly_rate_ends_when_above_three_hour_flat_tier_begins():
    services, _ = _extract_daycare_catalogue_options([
        {
            "content": (
                "Hourly Care - RM20/hour\n"
                "Daycare Above 3 Hours - RM55"
            )
        }
    ])

    hourly = next(option for option in services if option["pricing_unit"] == "hour")
    assert hourly["max_duration_minutes"] == 180
    assert hourly["max_duration_exclusive"] is False


def test_daycare_recommendation_uses_duration_ranges_and_calculated_hourly_total():
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        service_type="DAYCARE",
        daycare_duration_minutes=240,
    )
    trace = [{
        "tool": "get_booking_service_options",
        "args": {"service_type": "DAYCARE", "pet_id": 7},
        "result": json.dumps({
            "status": "success",
            "data": {
                "service_options": [
                    {
                        "service_name": "Daycare 3 Hours",
                        "price": 45,
                        "pricing_unit": "flat",
                        "duration_minutes": 180,
                    },
                    {
                        "service_name": "Daycare Above 3 Hours",
                        "price": 55,
                        "pricing_unit": "flat",
                        "min_duration_minutes": 180,
                        "min_duration_exclusive": True,
                    },
                    {
                        "service_name": "Hourly Care",
                        "price": 15,
                        "pricing_unit": "hour",
                        "max_duration_minutes": 180,
                        "max_duration_exclusive": False,
                    },
                ]
            },
        }),
    }]

    grounded = PawfectOrchestrator._ground_daycare_recommendation_response(
        AIMessage(content="Here is the entire menu."),
        "Which daycare service should I choose for four hours?",
        trace,
        state,
    )

    assert "Daycare Above 3 Hours — RM55" in grounded.content
    assert "Hourly Care" not in grounded.content
    assert "Daycare 3 Hours" not in grounded.content


def test_grooming_catalogue_uses_only_selected_size_and_inherits_add_on_section():
    rows = [
        {
            "content": (
                "Dog Bathing Packages:\n\n"
                "For XS size dogs (Below 25cm) - Standard Short Fur is RM35; "
                "Premium Long Fur is RM79.\n\n"
                "For S size dogs (25cm - 40cm) - Standard Short Fur is RM43; "
                "Premium Long Fur is RM88.\n\n"
                "For M size dogs (40cm - 55cm) - Standard Short Fur is RM62; "
                "Premium Long Fur is RM118."
            ),
            "metadata": {"section_title": "Dog Bathing Packages"},
        },
        {
            "content": (
                "Basic Grooming Add-ons:\n\n"
                "Nail Clipping is priced at RM15.\n\n"
                "Teeth Brushing is priced at RM10."
            ),
            "metadata": {
                "main_header": "Grooming Add-on Price",
                "section_title": "Basic Grooming Add-ons",
            },
        },
    ]

    services, add_ons = _extract_daycare_catalogue_options(rows, pet_size="S")

    assert {(item["service_name"], item["price"]) for item in services} == {
        ("Standard Short Fur", 43.0),
        ("Premium Long Fur", 88.0),
    }
    assert ("Premium Long Fur", 79.0) not in {
        (item["service_name"], item["price"]) for item in services
    }
    assert {(item["service_name"], item["price"]) for item in add_ons} == {
        ("Nail Clipping", 15.0),
        ("Teeth Brushing", 10.0),
    }
    assert all(item["selection_kind"] == "add_on" for item in add_ons)


def test_booking_price_verifier_accepts_s_grooming_package_and_teeth_add_on(monkeypatch):
    rows = [
        {
            "content": (
                "Dog Bathing Packages:\n"
                "For S size - Standard Short Fur is RM43; Premium Long Fur is RM88."
            ),
            "metadata": {"section_title": "Dog Bathing Packages"},
        },
        {
            "content": "Basic Grooming Add-ons:\nTeeth Brushing is priced at RM10.",
            "metadata": {"main_header": "Grooming Add-on Price"},
        },
    ]

    monkeypatch.setattr(
        "app.rag.retriever.CompanyRAGRetriever.search",
        lambda *_args, **_kwargs: rows,
    )

    assert _verify_flat_price_against_catalogue(
        1,
        "GROOMING",
        "Premium Long Fur",
        88,
        pet_type="dog",
        pet_size="S",
        add_on="Teeth Brushing",
        add_on_price=10,
    ) is None


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


def test_fresh_empty_coupon_lookup_clears_stale_eligibility():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.known_coupons = [
        {"coupon_id": 5, "reward_name": "RM10 Voucher", "discount_value": 10}
    ]

    PawfectOrchestrator._cache_known_coupons(
        state,
        "check_coupon_eligibility",
        {"status": "success", "data": {"eligible_coupons": []}},
    )

    assert state.known_coupons == []


def test_optional_booking_fields_must_be_selected_by_customer():
    state = ConversationState(phone_number="+60123456705", company_id="1")

    invented_staff = PawfectOrchestrator._reject_unconfirmed_optional_booking_fields(
        state,
        {"preferred_staff": "Sarah Wong"},
        "any staff is fine",
    )
    invented_add_on = PawfectOrchestrator._reject_unconfirmed_optional_booking_fields(
        state,
        {"add_on": "Teeth Brushing"},
        "standard bath only",
    )
    selected_add_on = PawfectOrchestrator._reject_unconfirmed_optional_booking_fields(
        state,
        {"add_on": "Teeth Brushing"},
        "please add Teeth Brushing",
    )

    assert invented_staff["error"] == "STAFF_PREFERENCE_DECLINED"
    assert invented_add_on["error"] == "UNCONFIRMED_ADD_ON_SELECTION"
    assert selected_add_on is None


def test_old_booking_preferences_do_not_authorize_current_booking_fields():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.turn_counter = 6
    state.booking_flow_started_turn = 5
    state.history = [
        {"role": "human", "content": "Add Teeth Brushing and use Alice", "turn": 2},
        {"role": "ai", "content": "That earlier booking is complete.", "turn": 2},
    ]

    stale_add_on = PawfectOrchestrator._reject_unconfirmed_optional_booking_fields(
        state,
        {"add_on": "Teeth Brushing"},
        "No add-ons for this booking",
    )
    state.preferred_staff = "Alice"
    stale_staff = PawfectOrchestrator._reject_unconfirmed_optional_booking_fields(
        state,
        {"preferred_staff": "Alice"},
        "Any staff is fine this time",
    )

    assert stale_add_on["error"] == "ADD_ON_DECLINED"
    assert stale_staff["error"] == "STAFF_PREFERENCE_DECLINED"
    assert state.preferred_staff is None


def test_catalogue_main_services_and_add_ons_have_separate_ordinal_namespaces():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    PawfectOrchestrator._cache_offered_options(
        state,
        "get_booking_service_options",
        {
            "status": "success",
            "data": {
                "service_options": [
                    {"service_name": "Standard Bath", "selection_kind": "service"},
                    {"service_name": "Premium Bath", "selection_kind": "service"},
                ],
                "add_on_options": [
                    {"service_name": "Teeth Brushing", "selection_kind": "add_on"},
                    {"service_name": "Nail Trim", "selection_kind": "add_on"},
                ],
            },
        },
    )
    orchestrator = object.__new__(PawfectOrchestrator)

    assert [item["service_name"] for item in state.offered_options] == [
        "Standard Bath", "Premium Bath"
    ]
    assert [item["service_name"] for item in state.offered_add_on_options] == [
        "Teeth Brushing", "Nail Trim"
    ]
    assert orchestrator._resolve_ordinal_reference(state, "the second one")["service_name"] == "Premium Bath"
    assert orchestrator._resolve_ordinal_reference(state, "the second add-on")["service_name"] == "Nail Trim"


def test_structured_catalogue_is_not_parsed_and_cached_twice():
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        known_pets=[{"pet_id": 7, "pet_size": "S"}],
    )
    PawfectOrchestrator._cache_booking_evidence(
        state,
        "get_booking_service_options",
        {
            "status": "success",
            "data": {
                "service_options": [{
                    "service_name": "Standard Bath",
                    "price": 43,
                    "selection_kind": "service",
                }],
                "add_on_options": [],
            },
            "detailed_pricing_by_size": [{"content": "Standard Bath: RM43"}],
        },
        {"service_type": "GROOMING", "pet_id": 7},
    )

    assert len(state.verified_service_options) == 1


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


def test_checkout_availability_becomes_the_only_reusable_offered_options():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    PawfectOrchestrator._cache_offered_options(
        state,
        "check_availability",
        {
            "status": "success",
            "data": {
                "selection_target": "CHECK_OUT",
                "check_in_time": "14:30",
                "available_slots": [],
                "available_check_out_times": ["16:30:00", "17:30:00"],
            },
        },
    )

    assert [option["slot"] for option in state.offered_options] == ["16:30:00", "17:30:00"]
    assert all(option["selection_target"] == "CHECK_OUT" for option in state.offered_options)


def test_latest_availability_result_deterministically_replaces_unverified_times():
    response = AIMessage(content="5:30 and 6:30 are both available.")
    trace = [
        {
            "tool": "check_availability",
            "result": json.dumps(
                {
                    "status": "success",
                    "data": {
                        "selection_target": "CHECK_OUT",
                        "available_slots": [],
                        "available_check_out_times": ["17:30:00"],
                    },
                }
            ),
        }
    ]

    grounded = PawfectOrchestrator._ground_latest_availability_response(
        response, "我可以几点接？", trace
    )

    assert "17:30" in grounded.content
    assert "18:30" not in grounded.content
    assert "只有" in grounded.content


def test_repeat_availability_reply_names_validated_historical_package_not_full_catalogue():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.repeat_booking_template = {
        "pet_name": "Milo",
        "package_name": "Standard Bath - Groomers Choice",
        "price": 80,
        "add_on": "",
        "catalogue_validated": True,
    }
    trace = [
        {
            "tool": "check_availability",
            "result": json.dumps(
                {
                    "status": "success",
                    "data": {
                        "selection_target": "CHECK_IN",
                        "available_slots": ["14:00:00", "15:30:00"],
                    },
                }
            ),
        }
    ]

    grounded = PawfectOrchestrator._ground_latest_availability_response(
        AIMessage(content="Here is every grooming package..."),
        "grooming like last time for Milo next Saturday afternoon",
        trace,
        state,
    )

    assert "Milo's last visit" in grounded.content
    assert "Standard Bath - Groomers Choice" in grounded.content
    assert "RM80" in grounded.content
    assert "14:00" in grounded.content
    assert "15:30" in grounded.content
    assert "every grooming package" not in grounded.content


def test_stale_repeat_template_does_not_leak_into_unrelated_availability_reply():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.repeat_booking_template = {
        "pet_name": "Milo",
        "package_name": "Old Bath",
        "price": 80,
        "catalogue_validated": True,
    }
    grounded = PawfectOrchestrator._ground_latest_availability_response(
        AIMessage(content="model text"),
        "Is daycare available tomorrow?",
        [{
            "tool": "check_availability",
            "result": {"status": "success", "data": {"available_slots": ["10:00"]}},
        }],
        state,
    )

    assert "Old Bath" not in grounded.content
    assert "Milo's last visit" not in grounded.content


def test_empty_rag_list_is_not_successful_policy_evidence():
    assert PawfectOrchestrator._tool_result_status([]) == "not_found"
    assert "retrieve_policy" not in PawfectOrchestrator._successful_trace_tools([
        {"tool": "retrieve_policy", "result": "[]"}
    ])


def test_policy_side_question_does_not_replace_active_booking_scenario():
    orchestrator = object.__new__(PawfectOrchestrator)
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        service_type="GROOMING",
        objective="Create the requested booking.",
        offered_options=[{"service_name": "Standard Bath"}],
    )
    orchestrator._apply_tool_result_state(
        state,
        {"name": "update_conversation_state", "args": {"active_scenario": "POLICY_QUERY"}},
        {"active_scenario": "POLICY_QUERY", "current_step": None, "service_type": None},
    )

    assert state.active_scenario == "MAKE_BOOKING"
    assert state.service_type == "GROOMING"
    assert state.offered_options == [{"service_name": "Standard Bath"}]


def test_abandoning_booking_flow_clears_booking_only_ephemera():
    orchestrator = object.__new__(PawfectOrchestrator)
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        preferred_staff="Sarah",
        loyalty_decision="declined",
        repeat_booking_template={"catalogue_validated": True},
        pending_actions={"create_booking": {"signature": "old"}},
        verified_availability_slots=[{"time": "10:00"}],
    )
    orchestrator._apply_tool_result_state(
        state,
        {"name": "update_conversation_state", "args": {"active_scenario": "ENQUIRY"}},
        {"active_scenario": "ENQUIRY", "current_step": None, "service_type": None},
    )

    assert state.preferred_staff is None
    assert state.loyalty_decision is None
    assert state.repeat_booking_template is None
    assert "create_booking" not in state.pending_actions
    assert state.verified_availability_slots == []


def test_failed_read_tool_is_not_accepted_as_grounding_evidence():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    failed_policy = [{
        "tool": "retrieve_policy",
        "result": json.dumps({"status": "error", "error": "RAG unavailable"}),
    }]
    failed_catalogue = [{
        "tool": "get_booking_service_options",
        "result": json.dumps({
            "status": "error",
            "error_code": "SERVICE_CATALOGUE_UNAVAILABLE",
        }),
    }]

    assert PawfectOrchestrator._needs_tool_repair(
        state,
        "What is the cancellation policy?",
        "The policy allows cancellation at any time.",
        failed_policy,
    ) is True
    assert PawfectOrchestrator._needs_tool_repair(
        state,
        "What grooming packages are available?",
        "The standard package costs RM80.",
        failed_catalogue,
    ) is True


def test_failed_document_lookup_cannot_be_rendered_as_sent():
    response = AIMessage(content="Your confirmation PDF was sent successfully.")
    trace = [{
        "tool": "send_booking_confirmation",
        "result": json.dumps({
            "status": "not_found",
            "message": "No booking could be resolved.",
            "handoff_required": False,
        }),
    }]

    grounded = PawfectOrchestrator._ground_document_delivery_response(
        response,
        "Please send my confirmation document",
        trace,
    )

    assert "not sent" in grounded.content
    assert "sent successfully" not in grounded.content


def test_successful_action_releases_scenario_but_keeps_booking_evidence():
    orchestrator = object.__new__(PawfectOrchestrator)
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        service_type="GROOMING",
        current_step="CREATE_BOOKING",
        loyalty_decision="declined",
        loyalty_offer_shown_turn=1,
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
    assert state.loyalty_decision is None
    assert state.loyalty_offer_shown_turn is None


def test_created_booking_immediately_becomes_available_runtime_context():
    orchestrator = object.__new__(PawfectOrchestrator)
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        booking_context_status="not_found",
    )
    result = {
        "status": "success",
        "data": {
            "booking_id": 92,
            "service_type": "GROOMING",
            "pet_id": 3,
            "pet_name": "Milo",
            "booking_date": "2026-08-09",
            "booking_time": "11:00",
        },
    }

    orchestrator._apply_tool_result_state(state, _call("create_booking", "create"), result)
    customer = orchestrator._customer_context_for_state({"found": True}, state)

    assert state.booking_context_status == "available"
    assert customer["latest_booking"]["booking_id"] == 92


def test_new_multi_pet_booking_clears_stale_pet_unless_named_this_turn():
    orchestrator = object.__new__(PawfectOrchestrator)
    pets = [
        {"pet_id": 1, "pet_name": "Milo", "pet_type": "cat"},
        {"pet_id": 2, "pet_name": "Luna", "pet_type": "dog"},
    ]
    state = ConversationState(
        phone_number="+60123456705", company_id="1", pet_id=1, pet_name="Milo"
    )
    state.known_pets = pets
    state.turn_counter = 4
    orchestrator._apply_tool_result_state(
        state,
        _call("update_conversation_state", "state"),
        {"active_scenario": "MAKE_BOOKING", "service_type": "GROOMING"},
    )
    assert state.pet_id is None

    state.active_scenario = None
    state.pet_id = 2
    state.pet_name = "Luna"
    state.pet_selected_turn = 4
    orchestrator._apply_tool_result_state(
        state,
        _call("update_conversation_state", "state-2"),
        {"active_scenario": "MAKE_BOOKING", "service_type": "GROOMING"},
    )
    assert state.pet_id == 2


def test_membership_success_does_not_end_active_booking_side_flow():
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        service_type="GROOMING",
    )
    PawfectOrchestrator._sync_scenario_from_tool_call(
        state, "register_loyalty_member", {"status": "success"}
    )
    assert state.active_scenario == "MAKE_BOOKING"


def test_cancelled_booking_forces_latest_active_booking_refresh():
    orchestrator = object.__new__(PawfectOrchestrator)
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="CANCEL_BOOKING",
        latest_booking={"booking_id": 10},
        booking_context_status="available",
    )
    orchestrator._apply_tool_result_state(
        state,
        _call("cancel_booking", "cancel"),
        {"status": "success", "data": {"booking_id": 10, "service_type": "GROOMING"}},
    )
    assert state.latest_booking is None
    assert state.booking_context_status == "unavailable"


def test_runtime_context_recursively_removes_internal_tool_fields():
    orchestrator = object.__new__(PawfectOrchestrator)
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.last_mutation = {
        "tool": "create_booking",
        "result": {
            "data": {"booking_id": 1},
            "_internal_payment_id": 99,
            "nested": {"_internal_confirmation_url": "private"},
        },
    }
    context = orchestrator._runtime_context(
        {"company_id": "1", "timezone": "Asia/Kuala_Lumpur"},
        {"found": True},
        state,
    )
    serialized = json.dumps(context)
    assert "_internal_payment_id" not in serialized
    assert "_internal_confirmation_url" not in serialized


def test_tool_repair_is_narrow_and_reuses_existing_evidence():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    assert PawfectOrchestrator._needs_tool_repair(
        state, "Please book Milo", "Milo's booking is confirmed.", []
    )
    assert not PawfectOrchestrator._needs_tool_repair(
        state, "Please book Milo", "Which date would you prefer?", []
    )

    state.verified_facts["service_options"] = {
        "status": "success",
        "tool": "get_booking_service_options",
        "args": {"service_type": "GROOMING"},
    }
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
    stale_availability = ConversationState(phone_number="+60123456705", company_id="1")
    stale_availability.verified_facts["availability"] = {"status": "success", "turn": 1}
    assert PawfectOrchestrator._needs_tool_repair(
        stale_availability,
        "Is 10 AM still available?",
        "10 AM is available.",
        [],
    )
    assert not PawfectOrchestrator._needs_tool_repair(
        stale_availability,
        "Is 10 AM still available?",
        "10 AM is available.",
        [{"tool": "check_availability", "result": '{"status":"success"}'}],
    )
    assert PawfectOrchestrator._needs_tool_repair(
        stale_availability,
        "Is 10 AM still available?",
        "10 AM is available.",
        [{"tool": "check_availability", "result": '{"status":"error"}'}],
    )
    assert PawfectOrchestrator._needs_tool_repair(
        ConversationState(phone_number="+60123456705", company_id="1"),
        "Where is my confirmation slip?",
        "It has been sent as a document.",
        [],
    )


def test_tool_repair_rejects_stale_evidence_from_a_different_scope():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.turn_counter = 4
    state.verified_facts["service_options"] = {
        "turn": 1,
        "tool": "get_booking_service_options",
        "status": "success",
        "args": {"service_type": "GROOMING", "pet_id": 5},
    }
    assert PawfectOrchestrator._needs_tool_repair(
        state,
        "What is the boarding room price?",
        "The boarding room costs RM50.",
        [],
    )

    state.verified_facts = {
        "policy_knowledge": {
            "turn": 1,
            "tool": "retrieve_policy",
            "status": "success",
            "args": {"query": "bringing your own pet food policy"},
        }
    }
    assert PawfectOrchestrator._needs_tool_repair(
        state,
        "What is your cancellation policy?",
        "Free cancellation is allowed.",
        [],
    )


def test_tool_repair_catches_booking_status_and_policy_claims_behind_a_question():
    # Same bypass shape as the price/availability/coupon/vaccination claims
    # above: a fabricated status/policy claim immediately followed by a
    # follow-up question must still be caught, not skipped by the
    # customer-input early return.
    assert PawfectOrchestrator._needs_tool_repair(
        ConversationState(phone_number="+60123456705", company_id="1"),
        "What's the status of my booking?",
        "Your booking is confirmed for tomorrow at 10am. Anything else I can help with?",
        [],
    )
    assert PawfectOrchestrator._needs_tool_repair(
        ConversationState(phone_number="+60123456705", company_id="1"),
        "What's your policy on bringing my own pet food?",
        "Yes, that's allowed. Would you like to know anything else about our policies?",
        [],
    )

    # Real evidence (this turn or cached) must still be accepted, even with
    # a trailing question.
    booking_trace = [{"tool": "get_latest_booking", "result": '{"status":"success"}'}]
    assert not PawfectOrchestrator._needs_tool_repair(
        ConversationState(phone_number="+60123456705", company_id="1"),
        "What's the status of my booking?",
        "Your booking is confirmed for tomorrow at 10am. Anything else I can help with?",
        booking_trace,
    )
    state_with_policy = ConversationState(phone_number="+60123456705", company_id="1")
    state_with_policy.verified_facts["policy_knowledge"] = {
        "status": "success",
        "tool": "retrieve_policy",
        "args": {"query": "policy on bringing my own pet food"},
    }
    assert not PawfectOrchestrator._needs_tool_repair(
        state_with_policy,
        "What's your policy on bringing my own pet food?",
        "Yes, that's allowed. Would you like to know anything else about our policies?",
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
