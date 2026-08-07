from datetime import date

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.context.state import ConversationState
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

    # A detail change is a new action, not approval of the first preview.
    state.turn_counter = 2
    changed = orchestrator._run_tool(
        _call("create_booking", **{**args, "time": "10:30"}), state, "yes"
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
