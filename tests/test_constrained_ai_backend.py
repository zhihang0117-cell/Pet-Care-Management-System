from datetime import date
import json

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.context.state import ConversationState
from app.db import relational_actions
from app.orchestrator import (
    ALL_TOOLS,
    MUTATING_TOOL_NAMES,
    PawfectOrchestrator,
    TOOLS_BY_NAME,
    _tools_for_scenario,
)


class _CapturingTool:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"status": "success", "data": {}}

    def invoke(self, args):
        self.calls.append(dict(args))
        return self.result


def _call(name, **args):
    return {"name": name, "id": f"{name}-1", "args": args}


def test_last_completed_lookup_is_scoped_to_owned_pet_service_and_done_status(monkeypatch):
    calls = []
    monkeypatch.setattr(
        relational_actions,
        "get_customer_pets",
        lambda context: {
            "status": "success",
            "data": {"pets": [{"pet_id": 1}, {"pet_id": 2}]},
        },
    )
    monkeypatch.setattr(
        relational_actions,
        "_pet_name_map",
        lambda client, company_id, pet_ids: {1: "Milo"},
    )
    monkeypatch.setattr(relational_actions, "today_business", lambda: date(2026, 8, 7))

    def fake_fetch(client, company_id, pet_ids, service_type):
        calls.append((company_id, list(pet_ids), service_type))
        return [
            {
                "grooming_booking_id": 999,
                "pet_id": 1,
                "service_name": "Wrong pending service",
                "booking_date": "2026-08-06",
                "booking_time": "16:00:00",
                "booking_status": "Pending",
                "price": 999,
            },
            {
                "grooming_booking_id": 339,
                "pet_id": 1,
                "service_name": "Standard Bath - Groomers Choice",
                "booking_date": "2026-08-03",
                "booking_time": "14:30:00",
                "booking_status": "Done",
                "price": 80,
                "add_on": "-",
                "add_on_price": 0,
            },
        ]

    monkeypatch.setattr(relational_actions, "_fetch_bookings_for_customer", fake_fetch)
    context = relational_actions.CustomerContext(company_id=7)
    context.resolved_customer_id = 42

    booking = relational_actions._collect_last_completed_customer_booking(
        context,
        object(),
        pet_id=1,
        service_type="GROOMING",
    )

    assert calls == [(7, [1], "GROOMING")]
    assert booking["booking_id"] == 339
    assert booking["package_name"] == "Standard Bath - Groomers Choice"
    assert booking["price"] == 80


def test_last_completed_lookup_rejects_pet_outside_customer_roster(monkeypatch):
    monkeypatch.setattr(
        relational_actions,
        "get_customer_pets",
        lambda context: {"status": "success", "data": {"pets": [{"pet_id": 1}]}},
    )
    context = relational_actions.CustomerContext(company_id=7)
    context.resolved_customer_id = 42

    assert relational_actions._collect_last_completed_customer_booking(
        context, object(), pet_id=999, service_type="GROOMING"
    ) is None


def test_catalogue_dependency_failure_is_not_reported_as_empty_success(monkeypatch):
    def fail_search(*args, **kwargs):
        raise ModuleNotFoundError("sentence_transformers")

    monkeypatch.setattr("app.rag.retriever.CompanyRAGRetriever.search", fail_search)

    result = TOOLS_BY_NAME["get_booking_service_options"].invoke(
        {"company_id": 7, "service_type": "GROOMING", "pet_id": ""}
    )

    assert result["status"] == "error"
    assert result["error_code"] == "SERVICE_CATALOGUE_UNAVAILABLE"
    assert result["data"]["service_options"] == []
    assert result["data"]["add_on_options"] == []
    assert result["success"] is False


def test_empty_catalogue_rag_result_is_not_reported_as_empty_success(monkeypatch):
    monkeypatch.setattr("app.rag.retriever.CompanyRAGRetriever.search", lambda *args, **kwargs: [])

    result = TOOLS_BY_NAME["get_booking_service_options"].invoke(
        {"company_id": 7, "service_type": "GROOMING", "pet_id": ""}
    )

    assert result["status"] == "error"
    assert result["error_code"] == "SERVICE_CATALOGUE_UNAVAILABLE"
    assert result["data"]["service_options"] == []


def test_last_completed_booking_preserves_database_error(monkeypatch):
    monkeypatch.setattr(
        relational_actions,
        "get_last_completed_booking_by_customer_id",
        lambda *args, **kwargs: {
            "status": "error",
            "data_found": False,
            "error": "connection refused",
        },
    )

    result = TOOLS_BY_NAME["get_last_completed_booking"].invoke(
        {
            "company_id": 7,
            "customer_id": 42,
            "service_type": "GROOMING",
            "pet_id": 1,
        }
    )

    assert result["status"] == "error"
    assert result["found"] is None
    assert result["error_code"] == "BOOKING_HISTORY_UNAVAILABLE"


def test_scenario_capabilities_are_fail_closed():
    for scenario in (None, "", "NOT_A_REAL_SCENARIO", "PAYMENT_QUERY", "ENQUIRY", "POLICY_QUERY"):
        names = {tool.name for tool in _tools_for_scenario(scenario)}
        assert not (names & MUTATING_TOOL_NAMES)

    assert "create_booking" in {tool.name for tool in _tools_for_scenario("MAKE_BOOKING")}
    assert "cancel_booking" not in {tool.name for tool in _tools_for_scenario("MAKE_BOOKING")}
    assert "cancel_booking" in {tool.name for tool in _tools_for_scenario("CANCEL_BOOKING")}
    assert "send_booking_confirmation" in {
        tool.name for tool in _tools_for_scenario("BOOKING_DOCUMENT")
    }


def test_every_openai_tool_schema_is_strict_compatible():
    def assert_strict_objects(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
                assert set(value.get("required", [])) == set(value.get("properties", {}))
            for nested in value.values():
                assert_strict_objects(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_strict_objects(nested)

    for tool in ALL_TOOLS:
        converted = convert_to_openai_tool(tool, strict=True)["function"]
        assert converted["strict"] is True
        assert_strict_objects(converted["parameters"])


def _booking_state():
    state = ConversationState(
        phone_number="+60123456705",
        company_id="7",
        active_scenario="MAKE_BOOKING",
        customer_id=22,
        pet_id=9,
        pet_name="Milo",
    )
    state.known_pets = [{"pet_id": 9, "pet_name": "Milo", "pet_type": "cat"}]
    state.loyalty_decision = "declined"
    state.resolved_dates = ["2026-08-15"]
    state.verified_service_options = [
        {
            "service_type": "GROOMING",
            "pet_id": 9,
            "service_name": "Basic Grooming",
            "price": 50,
        }
    ]
    state.verified_availability_slots = [
        {
            "service_type": "GROOMING",
            "verified_turn": 1,
            "date": "2026-08-15",
            "time": "10:00",
            "room_type": "",
        }
    ]
    return state


def test_booking_requires_exact_preview_then_standalone_confirmation(monkeypatch):
    capturing = _CapturingTool({"status": "success", "data": {"booking_id": 91, "payment_id": 92}})
    monkeypatch.setitem(TOOLS_BY_NAME, "create_booking", capturing)
    orchestrator = object.__new__(PawfectOrchestrator)
    state = _booking_state()
    args = {
        "service_type": "GROOMING",
        "pet_id": 9,
        "pet_name": "Milo",
        "package_name": "Basic Grooming",
        "date": "2026-08-15",
        "time": "10:00",
        "price": 50,
    }

    state.turn_counter = 1
    preview = orchestrator._run_tool(_call("create_booking", **args), state, "please book this")
    assert preview["status"] == "confirmation_required"
    assert capturing.calls == []

    # A customer-requested detail change is a new action, not approval of the
    # first preview. A standalone "yes" is tested separately as exact-payload
    # authorization even if the model itself mutates arguments.
    state.turn_counter = 2
    changed = orchestrator._run_tool(
        _call("create_booking", **{**args, "time": "10:30"}), state, "yes, make it 10:30"
    )
    assert changed["error"] == "UNVERIFIED_AVAILABILITY_SLOT"
    assert capturing.calls == []

    state.turn_counter = 3
    state.verified_availability_slots[0]["verified_turn"] = 3
    unrelated = orchestrator._run_tool(
        _call("create_booking", **args), state, "what time do you close?"
    )
    assert unrelated["status"] == "confirmation_required"
    assert capturing.calls == []

    state.turn_counter = 4
    state.verified_availability_slots[0]["verified_turn"] = 4
    completed = orchestrator._run_tool(_call("create_booking", **args), state, "确认")
    assert completed["status"] == "success"
    assert len(capturing.calls) == 1


def test_correct_confirms_exact_preview_with_add_on_and_previous_turn_slot(monkeypatch):
    capturing = _CapturingTool({"status": "success", "data": {"booking_id": 91}})
    monkeypatch.setitem(TOOLS_BY_NAME, "create_booking", capturing)
    orchestrator = object.__new__(PawfectOrchestrator)
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        customer_id=22,
        pet_id=32,
        pet_name="Lucky",
        pet_type="dog",
        pet_size="S",
        loyalty_decision="declined",
    )
    state.known_pets = [{
        "pet_id": 32,
        "pet_name": "Lucky",
        "pet_type": "dog",
        "pet_size": "S",
    }]
    state.resolved_dates = ["2026-08-15"]
    state.verified_service_options = [
        {
            "service_type": "GROOMING",
            "pet_id": 32,
            "service_name": "Premium Long Fur",
            "price": 88,
            "selection_kind": "service",
        },
        {
            "service_type": "GROOMING",
            "pet_id": 32,
            "service_name": "Teeth Brushing",
            "price": 10,
            "selection_kind": "add_on",
        },
    ]
    state.verified_availability_slots = [{
        "service_type": "GROOMING",
        "verified_turn": 1,
        "date": "2026-08-15",
        "time": "10:00",
        "room_type": "",
        "preferred_staff": "",
    }]
    state.history = [
        {"role": "human", "content": "Premium Long Fur with Teeth Brushing, please"},
        {"role": "ai", "content": "I will prepare those exact choices."},
    ]
    exact_args = {
        "service_type": "GROOMING",
        "pet_id": 32,
        "pet_name": "Lucky",
        "package_name": "Premium Long Fur",
        "date": "2026-08-15",
        "time": "10:00",
        "price": 88,
        "add_on": "Teeth Brushing",
        "add_on_price": 10,
    }

    state.turn_counter = 1
    preview = orchestrator._run_tool(
        _call("create_booking", **exact_args), state, "please book this"
    )
    assert preview["status"] == "confirmation_required"
    assert capturing.calls == []

    state.turn_counter = 2
    completed = orchestrator._run_tool(
        _call(
            "create_booking",
            service_type="GROOMING",
            pet_id=32,
            pet_name="Lucky",
            package_name="Standard Short Fur",
            date="2026-08-15",
            time="",
            price=43,
        ),
        state,
        "正确",
    )

    assert completed["status"] == "success"
    assert capturing.calls == [{
        **exact_args,
        "company_id": "1",
        "customer_id": 22,
    }]


def test_boarding_slot_evidence_must_match_room_and_full_stay():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.verified_service_options = [
        {
            "service_type": "BOARDING",
            "pet_id": 9,
            "room_type": "Mars Room",
            "price": 78,
        }
    ]
    args = {
        "service_type": "BOARDING",
        "pet_id": 9,
        "package_name": "Mars Room",
        "date": "2026-08-10",
        "time": "17:30",
        "check_out_date": "2026-08-11",
        "price": 78,
    }

    # An earlier staff-only check with no room/stay must not authorize this write.
    state.verified_availability_slots = [
        {
            "service_type": "BOARDING",
            "verified_turn": 0,
            "date": "2026-08-10",
            "time": "17:30",
            "room_type": "",
        }
    ]
    blocked = PawfectOrchestrator._reject_unverified_booking_payload(state, args)
    assert blocked["error"] == "UNVERIFIED_AVAILABILITY_SLOT"

    state.verified_availability_slots = [
        {
            "service_type": "BOARDING",
            "verified_turn": 0,
            "date": "2026-08-10",
            "time": "17:30",
            "room_type": "Mars Room",
            "check_out_date": "2026-08-11",
            "check_out_time": "",
            "duration_minutes": 30,
            "preferred_staff": "",
        }
    ]
    assert PawfectOrchestrator._reject_unverified_booking_payload(state, args) is None
    state.turn_counter = 1
    assert (
        PawfectOrchestrator._reject_unverified_booking_payload(state, args)["error"]
        == "UNVERIFIED_AVAILABILITY_SLOT"
    )


def test_daycare_slot_evidence_must_match_visit_duration():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.verified_service_options = [
        {
            "service_type": "DAYCARE",
            "pet_id": 9,
            "service_name": "Hourly Care",
            "price": 60,
        }
    ]
    state.verified_availability_slots = [
        {
            "service_type": "DAYCARE",
            "verified_turn": 0,
            "date": "2026-08-10",
            "time": "15:30",
            "room_type": "",
            "duration_minutes": 180,
            "preferred_staff": "",
        }
    ]
    args = {
        "service_type": "DAYCARE",
        "pet_id": 9,
        "package_name": "Hourly Care",
        "date": "2026-08-10",
        "time": "15:30",
        "duration_minutes": 240,
        "price": 60,
    }

    blocked = PawfectOrchestrator._reject_unverified_booking_payload(state, args)
    assert blocked["error"] == "UNVERIFIED_AVAILABILITY_SLOT"


def test_hourly_daycare_catalogue_accepts_verified_rate_times_duration():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.verified_service_options = [
        {
            "service_type": "DAYCARE",
            "pet_id": 9,
            "service_name": "Hourly Care",
            "price": 15,
            "pricing_unit": "hour",
        }
    ]
    state.verified_availability_slots = [
        {
            "service_type": "DAYCARE",
            "verified_turn": 0,
            "date": "2026-08-10",
            "time": "14:30",
            "duration_minutes": 180,
            "check_out_time": "17:30",
            "preferred_staff": "",
        }
    ]
    args = {
        "service_type": "DAYCARE",
        "pet_id": 9,
        "package_name": "Hourly Care",
        "date": "2026-08-10",
        "time": "14:30",
        "check_out_time": "17:30",
        "duration_minutes": 180,
        "price": 45,
    }

    assert PawfectOrchestrator._reject_unverified_booking_payload(state, args) is None
    assert PawfectOrchestrator._reject_unverified_booking_payload(
        state, {**args, "price": 15}
    )["error"] == "UNVERIFIED_SERVICE_OPTION"


def test_reschedule_confirmation_is_bound_to_exact_new_details(monkeypatch):
    capturing = _CapturingTool({"status": "success", "data": {"booking_id": 91}})
    monkeypatch.setitem(TOOLS_BY_NAME, "reschedule_booking", capturing)
    orchestrator = object.__new__(PawfectOrchestrator)
    state = ConversationState(
        phone_number="+60123456705",
        company_id="7",
        active_scenario="RESCHEDULE_BOOKING",
        customer_id=22,
    )
    state.resolved_dates = ["2026-08-15", "2026-08-16"]
    preview_args = {
        "company_id": "7",
        "customer_id": 22,
        "booking_id": 91,
        "service_type": "GROOMING",
        "new_date": "2026-08-15",
        "new_time": "10:00",
    }
    state.pending_booking_confirmation = {
        "tool": "reschedule_booking",
        "booking_id": 91,
        "service_type": "GROOMING",
        "change_signature": PawfectOrchestrator._mutation_signature(
            "reschedule_booking", preview_args
        ),
    }

    changed = orchestrator._run_tool(
        _call(
            "reschedule_booking",
            company_id="7",
            customer_id=22,
            booking_id=91,
            service_type="GROOMING",
            new_date="2026-08-16",
            new_time="10:00",
            confirm_pet_name="Milo",
        ),
        state,
        "Milo",
    )

    assert changed["error"] == "RESCHEDULE_DETAILS_CHANGED"
    assert capturing.calls == []


def test_pickup_choice_evidence_binds_fixed_checkin_and_selected_pickup():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.verified_service_options = [
        {
            "service_type": "DAYCARE",
            "pet_id": 9,
            "service_name": "Hourly Care",
            "price": 60,
        }
    ]
    PawfectOrchestrator._cache_booking_evidence(
        state,
        "check_availability",
        {
            "status": "success",
            "data": {
                "service_type": "DAYCARE",
                "selection_target": "CHECK_OUT",
                "booking_date": "2026-08-10",
                "check_in_time": "14:30",
                "check_out_date": "2026-08-10",
                "available_check_out_times": ["17:30:00"],
            },
        },
        {
            "service_type": "DAYCARE",
            "date": "2026-08-10",
            "selection_target": "CHECK_OUT",
            "check_in_time": "14:30",
        },
    )
    exact = {
        "service_type": "DAYCARE",
        "pet_id": 9,
        "package_name": "Hourly Care",
        "date": "2026-08-10",
        "time": "14:30",
        "check_out_time": "17:30",
        "price": 60,
    }

    assert PawfectOrchestrator._reject_unverified_booking_payload(state, exact) is None
    changed_pickup = {**exact, "check_out_time": "16:30"}
    assert (
        PawfectOrchestrator._reject_unverified_booking_payload(state, changed_pickup)["error"]
        == "UNVERIFIED_AVAILABILITY_SLOT"
    )


def test_membership_confirmation_cannot_be_inferred_from_unrelated_turn(monkeypatch):
    capturing = _CapturingTool({"status": "success", "data": {"member_id": 3}})
    monkeypatch.setitem(TOOLS_BY_NAME, "register_loyalty_member", capturing)
    orchestrator = object.__new__(PawfectOrchestrator)
    state = ConversationState(
        phone_number="+60123456705", company_id="7", active_scenario="MEMBER", customer_id=22
    )
    args = {"confirmed": True}
    state.pending_actions["register_loyalty_member"] = {
        "signature": orchestrator._mutation_signature("register_loyalty_member", args),
        "args": args,
        "preview_turn": 1,
    }

    state.turn_counter = 2
    blocked = orchestrator._run_tool(
        _call("register_loyalty_member", **args), state, "what are your opening hours?"
    )
    assert blocked["status"] == "confirmation_required"
    assert capturing.calls == []

    state.turn_counter = 3
    assert orchestrator._run_tool(
        _call("register_loyalty_member", **args), state, "yes"
    )["status"] == "success"
    assert len(capturing.calls) == 1


def test_membership_yes_forces_exact_pending_write_and_cannot_jump_to_greeting(monkeypatch):
    capturing = _CapturingTool({
        "status": "success",
        "data": {
            "loyalty_id": 81,
            "tier": "Bronze",
            "points_balance": 0,
            "already_member": False,
        },
    })
    capturing.name = "register_loyalty_member"
    monkeypatch.setitem(TOOLS_BY_NAME, "register_loyalty_member", capturing)

    class _ForgetfulMembershipModel:
        def __init__(self):
            self.bound_names = []
            self.registration_called = False

        def bind_tools(self, tools, **_kwargs):
            self.bound_names = [tool.name for tool in tools]
            return self

        def invoke(self, _messages):
            if self.bound_names == ["register_loyalty_member"] and not self.registration_called:
                self.registration_called = True
                # Simulate the real failure: the model loses confirmed=true
                # and invents identity fields even after the customer says yes.
                return AIMessage(
                    content="",
                    tool_calls=[_call(
                        "register_loyalty_member",
                        company_id="wrong",
                        customer_id="wrong",
                        confirmed=False,
                    )],
                )
            # Simulate the other observed failure: after a successful tool call
            # the model tries to restart the conversation with a greeting.
            return AIMessage(content="Hi Alicia! How can I help you?")

    orchestrator = object.__new__(PawfectOrchestrator)
    orchestrator._base_model = _ForgetfulMembershipModel()
    orchestrator._resolve_identity = lambda _company_id, _state: {
        "found": True,
        "customer_id": 22,
        "full_name": "Alicia Lee",
    }
    orchestrator._save_escalation_message = lambda *_args: None
    state = ConversationState(
        phone_number="+60123456705",
        company_id="7",
        active_scenario="MEMBER",
        customer_id=22,
        customer_name="Alicia Lee",
        turn_counter=1,
        history=[
            {"role": "human", "content": "I want to join as a member"},
            {"role": "ai", "content": "Would you like me to enrol you?"},
        ],
    )
    exact_args = {"company_id": "7", "customer_id": 22, "confirmed": True}
    state.pending_actions["register_loyalty_member"] = {
        "signature": orchestrator._mutation_signature(
            "register_loyalty_member", exact_args
        ),
        "args": exact_args,
        "preview_turn": 1,
    }

    response, trace = orchestrator.invoke_with_trace(
        {"company_id": "7", "company_name": "Pawfect", "timezone": "Asia/Kuala_Lumpur"},
        state,
        "yes",
    )

    assert capturing.calls == [exact_args]
    assert [item["tool"] for item in trace] == ["register_loyalty_member"]
    assert "membership is now active" in response.content
    assert "Bronze" in response.content
    assert not response.content.startswith("Hi")
    assert state.active_scenario is None
    assert "register_loyalty_member" not in state.pending_actions


def test_redemption_requires_verified_ids_and_separate_confirmation(monkeypatch):
    capturing = _CapturingTool({"status": "success", "data": {"redemption_id": 77}})
    monkeypatch.setitem(TOOLS_BY_NAME, "redeem_reward", capturing)
    orchestrator = object.__new__(PawfectOrchestrator)
    state = ConversationState(
        phone_number="+60123456705", company_id="7", active_scenario="LOYALTY_QUERY", customer_id=22
    )
    args = {"payment_id": 92, "coupon_id": 5}

    state.turn_counter = 1
    no_ids = orchestrator._run_tool(_call("redeem_reward", **args), state, "redeem it")
    assert no_ids["error"] == "NO_VERIFIED_PAYMENT_ID"

    state.last_created_payment_id = 92
    state.known_coupons = [{"coupon_id": 5, "reward_name": "RM10 Voucher", "discount_value": 10}]
    preview = orchestrator._run_tool(_call("redeem_reward", **args), state, "redeem it")
    assert preview["status"] == "confirmation_required"
    assert capturing.calls == []

    state.turn_counter = 2
    completed = orchestrator._run_tool(_call("redeem_reward", **args), state, "teruskan")
    assert completed["status"] == "success"
    assert len(capturing.calls) == 1


def test_payment_history_scope_is_always_from_session(monkeypatch):
    capturing = _CapturingTool()
    monkeypatch.setitem(TOOLS_BY_NAME, "get_payment_history", capturing)
    state = ConversationState(
        phone_number="+60123456705",
        company_id="7",
        active_scenario="PAYMENT_QUERY",
        customer_id=22,
    )
    result = object.__new__(PawfectOrchestrator)._run_tool(
        _call("get_payment_history", company_id="999", customer_id="999"), state
    )
    assert result["status"] == "success"
    assert capturing.calls == [{"company_id": "7", "customer_id": 22}]


def test_identical_successful_read_is_cached_and_forces_final_response(monkeypatch):
    capturing = _CapturingTool({
        "status": "success",
        "data": {
            "loyalty_points": 120,
            "eligible_coupons": [
                {"coupon_id": 5, "reward_name": "RM10 Voucher", "discount_value": 10}
            ],
        },
    })
    monkeypatch.setitem(TOOLS_BY_NAME, "check_coupon_eligibility", capturing)

    class _RepeatingModel:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, *_args, **_kwargs):
            return self

        def invoke(self, _messages):
            self.calls += 1
            if self.calls <= 2:
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "check_coupon_eligibility",
                        "id": f"coupon-{self.calls}",
                        "args": {"company_id": "wrong", "customer_id": "wrong"},
                    }],
                )
            return AIMessage(content="You have 120 loyalty points and an eligible RM10 coupon.")

    model = _RepeatingModel()
    orchestrator = object.__new__(PawfectOrchestrator)
    orchestrator._base_model = model
    orchestrator._resolve_identity = lambda _company_id, _state: {
        "found": True,
        "customer_id": 22,
        "full_name": "Alicia Lee",
    }
    orchestrator._save_escalation_message = lambda *_args: None
    state = ConversationState(
        phone_number="+60123456705",
        company_id="7",
        active_scenario="LOYALTY_QUERY",
        customer_id=22,
        customer_name="Alicia Lee",
    )

    response, trace = orchestrator.invoke_with_trace(
        {"company_id": "7", "company_name": "Pawfect", "timezone": "Asia/Kuala_Lumpur"},
        state,
        "Do I have a voucher?",
    )

    assert model.calls == 3
    assert capturing.calls == [{"company_id": "7", "customer_id": 22}]
    assert len(trace) == 2
    assert json.loads(trace[1]["result"])["_internal_duplicate_read_suppressed"] is True
    assert "RM10 coupon" in response.content
    assert state.loyalty_offer_shown_turn == state.turn_counter == 1


def test_identical_failed_read_is_cached_and_cannot_loop_to_iteration_limit(monkeypatch):
    capturing = _CapturingTool({
        "status": "error",
        "error_code": "SUPABASE_UNAVAILABLE",
        "message": "Database connection failed.",
    })
    monkeypatch.setitem(TOOLS_BY_NAME, "check_coupon_eligibility", capturing)

    class _RepeatingFailureModel:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, *_args, **_kwargs):
            return self

        def invoke(self, _messages):
            self.calls += 1
            if self.calls <= 2:
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "check_coupon_eligibility",
                        "id": f"coupon-failure-{self.calls}",
                        "args": {"company_id": "wrong", "customer_id": "wrong"},
                    }],
                )
            return AIMessage(content="I can't verify your coupons right now. Please try again shortly.")

    model = _RepeatingFailureModel()
    orchestrator = object.__new__(PawfectOrchestrator)
    orchestrator._base_model = model
    orchestrator._resolve_identity = lambda _company_id, _state: {
        "found": True,
        "customer_id": 22,
        "full_name": "Alicia Lee",
    }
    orchestrator._save_escalation_message = lambda *_args: None
    state = ConversationState(
        phone_number="+60123456705",
        company_id="7",
        active_scenario="LOYALTY_QUERY",
        customer_id=22,
    )

    response, trace = orchestrator.invoke_with_trace(
        {"company_id": "7", "company_name": "Pawfect", "timezone": "Asia/Kuala_Lumpur"},
        state,
        "Do I have a voucher?",
    )

    assert model.calls == 3
    assert len(capturing.calls) == 1
    assert len(trace) == 2
    duplicate = json.loads(trace[1]["result"])
    assert duplicate["status"] == "error"
    assert duplicate["_internal_duplicate_read_suppressed"] is True
    assert "can't verify" in response.content


def test_repeat_booking_guard_replaces_catalogue_dump_with_scoped_history_and_availability(
    monkeypatch,
):
    history_tool = _CapturingTool({
        "found": True,
        "booking_id": 339,
        "pet_id": 1,
        "pet_name": "Milo",
        "last_service_type": "GROOMING",
        "package_name": "Standard Bath - Groomers Choice",
        "price": 80,
        "add_on": "-",
        "add_on_price": 0,
    })
    options_tool = _CapturingTool({
        "status": "success",
        "data": {
            "service_type": "GROOMING",
            "service_options": [{
                "service_name": "Standard Bath - Groomers Choice",
                "price": 80,
                "selection_kind": "service",
            }],
            "add_on_options": [],
        },
        "detailed_pricing_by_size": [],
    })
    availability_tool = _CapturingTool({
        "status": "success",
        "data": {
            "service_type": "GROOMING",
            "booking_date": "2026-08-15",
            "selection_target": "CHECK_IN",
            "available_slots": ["14:00:00", "15:30:00"],
        },
    })
    history_tool.name = "get_last_completed_booking"
    options_tool.name = "get_booking_service_options"
    availability_tool.name = "check_availability"
    monkeypatch.setitem(TOOLS_BY_NAME, "get_last_completed_booking", history_tool)
    monkeypatch.setitem(TOOLS_BY_NAME, "get_booking_service_options", options_tool)
    monkeypatch.setitem(TOOLS_BY_NAME, "check_availability", availability_tool)
    monkeypatch.setattr("app.tools.calendar_tools.today_business", lambda: date(2026, 8, 7))

    class _PrematureCatalogueModel:
        def __init__(self):
            self.bound_names = []

        def bind_tools(self, tools, **_kwargs):
            self.bound_names = [tool.name for tool in tools]
            return self

        def invoke(self, _messages):
            if self.bound_names == ["get_last_completed_booking"]:
                return AIMessage(
                    content="",
                    tool_calls=[_call(
                        "get_last_completed_booking",
                        company_id="wrong",
                        customer_id="wrong",
                    )],
                )
            if self.bound_names == ["get_booking_service_options"]:
                return AIMessage(
                    content="",
                    tool_calls=[_call(
                        "get_booking_service_options",
                        company_id="wrong",
                        service_type="BOARDING",
                    )],
                )
            if self.bound_names == ["check_availability"]:
                return AIMessage(
                    content="",
                    tool_calls=[_call(
                        "check_availability",
                        company_id="wrong",
                        service_type="BOARDING",
                        date="2099-01-01",
                    )],
                )
            if availability_tool.calls:
                return AIMessage(content="I booked it for 4 PM.")
            return AIMessage(content="Here is every grooming package. Pick one.")

    orchestrator = object.__new__(PawfectOrchestrator)
    orchestrator._base_model = _PrematureCatalogueModel()
    orchestrator._resolve_identity = lambda _company_id, _state: {
        "found": True,
        "customer_id": 1,
        "full_name": "Alicia Lee",
        "pets": [{"pet_id": 1, "pet_name": "Milo", "pet_type": "Cat", "size": "M"}],
    }
    orchestrator._save_escalation_message = lambda *_args: None
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        service_type="GROOMING",
        customer_id=1,
        customer_name="Alicia Lee",
        pet_id=1,
        pet_name="Milo",
        pet_type="Cat",
        pet_size="M",
        known_pets=[{
            "pet_id": 1,
            "pet_name": "Milo",
            "pet_type": "Cat",
            "pet_size": "M",
        }],
    )

    response, trace = orchestrator.invoke_with_trace(
        {"company_id": "1", "company_name": "Pawfect", "timezone": "Asia/Kuala_Lumpur"},
        state,
        "can i book for next saturday afternoon grooming for Milo like last time?",
    )

    assert [item["tool"] for item in trace] == [
        "get_last_completed_booking",
        "get_booking_service_options",
        "check_availability",
    ]
    assert "retrieve_policy" not in {item["tool"] for item in trace}
    assert history_tool.calls[0]["pet_id"] == 1
    assert history_tool.calls[0]["service_type"] == "GROOMING"
    assert options_tool.calls[0]["pet_id"] == 1
    assert options_tool.calls[0]["service_type"] == "GROOMING"
    assert availability_tool.calls[0]["date"] == "2026-08-15"
    assert availability_tool.calls[0]["time"] == "afternoon"
    assert availability_tool.calls[0]["service_type"] == "GROOMING"
    assert "Standard Bath - Groomers Choice" in response.content
    assert "14:00" in response.content
    assert "15:30" in response.content
    assert "I booked it" not in response.content


def test_cross_turn_daycare_slot_selection_is_rechecked_then_enters_exact_preview(monkeypatch):
    availability_tool = _CapturingTool({
        "status": "success",
        "data": {
            "service_type": "DAYCARE",
            "selection_target": "CHECK_IN",
            "booking_date": "2026-08-15",
            "available_slots": ["10:00:00"],
            "service_duration_minutes": 180,
            "check_out_time": None,
        },
    })
    create_tool = _CapturingTool({"status": "success", "data": {"booking_id": 91}})
    availability_tool.name = "check_availability"
    create_tool.name = "create_booking"
    monkeypatch.setitem(TOOLS_BY_NAME, "check_availability", availability_tool)
    monkeypatch.setitem(TOOLS_BY_NAME, "create_booking", create_tool)

    exact_booking = {
        "company_id": "1",
        "customer_id": 22,
        "pet_id": 7,
        "pet_name": "Yoyo",
        "service_type": "DAYCARE",
        "package_name": "Daycare 3 Hours",
        "date": "2026-08-15",
        "time": "10:00",
        "price": 45,
        "check_out_date": "",
        "check_out_time": "",
        "preferred_staff": "",
        "add_on": "",
        "add_on_price": None,
        "duration_minutes": 180,
    }

    class _DaycareSelectionModel:
        def __init__(self):
            self.bound_names = []
            self.initial_create_sent = False

        def bind_tools(self, tools, **_kwargs):
            self.bound_names = [tool.name for tool in tools]
            return self

        def invoke(self, _messages):
            if self.bound_names == ["check_availability"]:
                return AIMessage(content="", tool_calls=[_call(
                    "check_availability",
                    company_id="wrong",
                    service_type="GROOMING",
                    date="2099-01-01",
                    time="14:00",
                )])
            if self.bound_names == ["create_booking"]:
                return AIMessage(content="", tool_calls=[_call(
                    "create_booking",
                    company_id="wrong",
                    customer_id="wrong",
                    pet_id=999,
                    pet_name="Wrong",
                    service_type="GROOMING",
                    package_name="Wrong",
                    date="2099-01-01",
                    time="14:00",
                    price=999,
                )])
            if not self.initial_create_sent:
                self.initial_create_sent = True
                return AIMessage(content="", tool_calls=[_call("create_booking", **exact_booking)])
            return AIMessage(content="It looks like there was an issue with 10:00.")

    orchestrator = object.__new__(PawfectOrchestrator)
    orchestrator._base_model = _DaycareSelectionModel()
    orchestrator._resolve_identity = lambda _company_id, _state: {
        "found": True,
        "customer_id": 22,
        "full_name": "Alicia Lee",
        "pets": [{"pet_id": 7, "pet_name": "Yoyo", "pet_type": "Dog", "size": "S"}],
        "pets_context_status": "available",
        "booking_context_status": "not_found",
    }
    orchestrator._save_escalation_message = lambda *_args: None
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        service_type="DAYCARE",
        customer_id=22,
        customer_name="Alicia Lee",
        pet_id=7,
        pet_name="Yoyo",
        pet_type="Dog",
        pet_size="S",
        known_pets=[{
            "pet_id": 7,
            "pet_name": "Yoyo",
            "pet_type": "Dog",
            "pet_size": "S",
            "pet_breed": "Poodle",
        }],
        pets_context_status="available",
        booking_context_status="not_found",
        daycare_duration_minutes=180,
        loyalty_decision="declined",
        resolved_dates=["2026-08-15"],
        turn_counter=1,
        history=[
            {"role": "human", "content": "Which drop-off time is available?"},
            {"role": "ai", "content": "10:00, 10:30, or 11:00."},
        ],
        verified_service_options=[{
            "service_type": "DAYCARE",
            "pet_id": 7,
            "service_name": "Daycare 3 Hours",
            "price": 45,
            "pricing_unit": "flat",
            "duration_minutes": 180,
        }],
        verified_availability_slots=[{
            "service_type": "DAYCARE",
            "verified_turn": 1,
            "date": "2026-08-15",
            "time": "10:00:00",
            "room_type": "",
            "check_out_date": "",
            "check_out_time": "",
            "duration_minutes": 180,
            "preferred_staff": "",
        }],
    )

    response, trace = orchestrator.invoke_with_trace(
        {"company_id": "1", "company_name": "Pawfect", "timezone": "Asia/Kuala_Lumpur"},
        state,
        "10:00",
    )

    assert [item["tool"] for item in trace] == [
        "create_booking", "check_availability", "create_booking"
    ], trace
    assert availability_tool.calls[0]["service_type"] == "DAYCARE"
    assert availability_tool.calls[0]["date"] == "2026-08-15"
    assert availability_tool.calls[0]["time"] == "10:00"
    assert availability_tool.calls[0]["duration_minutes"] == 180
    assert create_tool.calls == []  # exact preview first; no write before the next yes
    assert "Please confirm this booking for Yoyo" in response.content
    assert "10:00 for 180 minutes" in response.content
    assert "create_booking" in state.pending_actions


def test_loyalty_lookup_alone_does_not_mark_offer_as_presented():
    state = ConversationState(phone_number="+60123456705", company_id="7")
    state.turn_counter = 4
    trace = [{
        "tool": "check_coupon_eligibility",
        "result": json.dumps({"status": "success", "data": {"eligible_coupons": []}}),
    }]

    PawfectOrchestrator._cache_loyalty_offer_presented(state, "Which date works for you?", trace)
    assert state.loyalty_offer_shown_turn is None

    PawfectOrchestrator._cache_loyalty_offer_presented(
        state, "You do not currently have an eligible coupon.", trace
    )
    assert state.loyalty_offer_shown_turn == 4


def test_datetime_is_pre_resolved_and_explicit_staff_handoff_is_saved(monkeypatch):
    class _Model:
        def bind_tools(self, *_args, **_kwargs):
            return self

        def invoke(self, _messages):
            return AIMessage(content="已按系统日期处理。")

    monkeypatch.setattr("app.tools.calendar_tools.today_business", lambda: date(2026, 8, 7))
    saved = []
    orchestrator = object.__new__(PawfectOrchestrator)
    orchestrator._base_model = _Model()
    orchestrator._resolve_identity = lambda _company_id, _state: {
        "found": True,
        "customer_id": 22,
        "full_name": "Alicia Lee",
    }
    orchestrator._save_escalation_message = lambda *args: saved.append(args)
    state = ConversationState(
        phone_number="+60123456705", company_id="7", customer_id=22, customer_name="Alicia Lee"
    )

    orchestrator.invoke_with_trace(
        {"company_id": "7", "company_name": "Pawfect", "timezone": "Asia/Kuala_Lumpur"},
        state,
        "下个星期六，请联系员工跟进",
    )

    assert state.current_datetime_resolution["date"] == "2026-08-15"
    assert "2026-08-15" in state.resolved_dates
    assert saved and saved[0][-1] == "CUSTOMER_REQUESTED_STAFF_HANDOFF"
    assert state.verified_facts["staff_enquiry"]["saved"] is True

    response, _trace = orchestrator.invoke_with_trace(
        {"company_id": "7", "company_name": "Pawfect", "timezone": "Asia/Kuala_Lumpur"},
        state,
        "下个星期六呢？",
    )
    assert response.content == "日期是 2026-08-15（星期六）。"


def test_invoice_boundary_rejects_unpaid_and_accepts_paid(monkeypatch):
    import main

    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def execute(self):
            return type("Result", (), {"data": self.rows})()

    class _Client:
        def __init__(self, rows_by_table):
            self.rows_by_table = rows_by_table

        def table(self, name):
            return _Query(self.rows_by_table.get(name, []))

    generated = []
    monkeypatch.setattr(main, "generate_and_send_invoice", lambda *args, **kwargs: generated.append((args, kwargs)) or {"status": "success"})

    monkeypatch.setattr(
        "app.db.supabase_client.get_supabase_client",
        lambda: _Client({"payment": [{"payment_id": 1, "status": "Pending"}]}),
    )
    rejected = main.documents_invoice(main.InvoiceRequest(company_id=7, payment_id=1))
    assert rejected["error"] == "INVOICE_REQUIRES_PAID_PAYMENT"
    assert generated == []

    monkeypatch.setattr(
        "app.db.supabase_client.get_supabase_client",
        lambda: _Client({
            "payment": [{"payment_id": 1, "status": "Paid"}],
            "grooming_booking": [{"grooming_booking_id": 8, "payment_id": 1}],
        }),
    )
    assert main.documents_invoice(main.InvoiceRequest(company_id=7, payment_id=1))["status"] == "success"
    assert len(generated) == 1


def test_outbound_notice_history_uses_request_tenant(monkeypatch):
    import main

    calls = []
    state = type("State", (), {"history": []})()

    class _Memory:
        def get(self, phone_number, company_id):
            calls.append((phone_number, company_id))
            return state

        def save(self, value):
            assert value is state

    monkeypatch.setattr(main, "_memory", _Memory())
    main._seed_notice_into_history(77, "+60123456705", "Your booking is ready")

    assert calls == [("+60123456705", "77")]
    assert state.history[-1]["content"] == "Your booking is ready"
