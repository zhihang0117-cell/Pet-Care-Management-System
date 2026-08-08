"""
Real regression tests against app/orchestrator.py's deterministic pet-context
helpers. The previous version of this file only asserted a dict literal
equaled the string defined two lines above it — it never imported or called
any real code, so it could never fail regardless of what the orchestrator
actually did. These tests exercise the real functions, including the
multi-pet pet_id mixup found and fixed this session (see _known_pet_by_id and
the get_booking_service_options/create_booking override in _run_tool).
"""

from app.agent.guardrails import known_pet_by_id, match_named_pet, reject_unconfirmed_breed
from app.context.state import ConversationState
from app.orchestrator import PawfectOrchestrator, TOOLS_BY_NAME


def _state_with_two_pets() -> ConversationState:
    state = ConversationState(phone_number="+60 12-345 6705", company_id="1")
    state.known_pets = [
        {"pet_id": 5, "pet_type": "Dog", "pet_name": "Simba", "pet_size": "S"},
        {"pet_id": 25, "pet_type": "Dog", "pet_name": "Lili", "pet_size": "S"},
    ]
    return state


def test_match_named_pet_resolves_the_pet_named_in_this_message():
    state = _state_with_two_pets()
    match_named_pet(state, "what grooming options for Lili?")
    assert state.pet_id == 25
    assert state.pet_name == "Lili"


def test_match_named_pet_resolves_unambiguous_other_pet_reference():
    state = _state_with_two_pets()
    state.pet_id = 5  # simulate an earlier turn having resolved Simba
    match_named_pet(state, "and what about my other pet?")
    assert state.pet_id == 25
    assert state.pet_name == "Lili"


def test_match_named_pet_supports_cjk_names_without_word_boundaries():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.turn_counter = 3
    state.known_pets = [
        {"pet_id": 7, "pet_type": "Dog", "pet_name": "小白", "pet_size": "S"}
    ]

    match_named_pet(state, "帮小白预约美容")

    assert state.pet_id == 7
    assert state.pet_selected_turn == 3


def test_match_named_pet_resolves_unique_species_reference():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.turn_counter = 4
    state.known_pets = [
        {"pet_id": 5, "pet_type": "Dog", "pet_name": "Simba", "pet_size": "S"},
        {"pet_id": 25, "pet_type": "Cat", "pet_name": "Lili", "pet_size": "S"},
    ]
    state.pet_id = 5

    match_named_pet(state, "what about my cat?")

    assert state.pet_id == 25
    assert state.pet_selected_turn == 4


def test_multi_pet_tool_uses_unambiguous_other_pet_not_stale_state(monkeypatch):
    class CapturingTool:
        def invoke(self, args):
            return dict(args)

    monkeypatch.setitem(TOOLS_BY_NAME, "get_booking_service_options", CapturingTool())
    state = _state_with_two_pets()
    state.customer_id = 42
    state.pet_id = 5
    state.pet_name = "Simba"
    state.pet_selected_turn = 1
    state.turn_counter = 2
    match_named_pet(state, "and what about my other pet?")

    result = object.__new__(PawfectOrchestrator)._run_tool(
        {
            "name": "get_booking_service_options",
            "args": {"service_type": "GROOMING", "pet_id": 25},
        },
        state=state,
        user_message="and what about my other pet?",
    )

    assert result["pet_id"] == 25
    assert result["customer_id"] == 42


def test_general_catalogue_enquiry_does_not_require_customer_identity(monkeypatch):
    class CapturingTool:
        def invoke(self, args):
            return dict(args)

    monkeypatch.setitem(TOOLS_BY_NAME, "get_booking_service_options", CapturingTool())
    state = ConversationState(phone_number="+60123456705", company_id="7")

    result = object.__new__(PawfectOrchestrator)._run_tool(
        {
            "name": "get_booking_service_options",
            "args": {"service_type": "BOARDING"},
        },
        state=state,
        user_message="What boarding rooms do you have?",
    )

    assert result == {"company_id": "7", "service_type": "BOARDING"}


def test_multi_pet_tool_reuses_current_flow_pet_when_model_omits_id(monkeypatch):
    class CapturingTool:
        def invoke(self, args):
            return dict(args)

    monkeypatch.setitem(TOOLS_BY_NAME, "get_booking_service_options", CapturingTool())
    state = _state_with_two_pets()
    state.customer_id = 42
    state.pet_id = 25
    state.pet_selected_turn = 1
    state.turn_counter = 2

    result = object.__new__(PawfectOrchestrator)._run_tool(
        {"name": "get_booking_service_options", "args": {"service_type": "GROOMING"}},
        state=state,
        user_message="show me the packages",
    )

    assert result["pet_id"] == 25


def test_multi_pet_tool_rejects_model_only_pet_switch(monkeypatch):
    class CapturingTool:
        def invoke(self, args):
            raise AssertionError("tool must not run")

    monkeypatch.setitem(TOOLS_BY_NAME, "get_booking_service_options", CapturingTool())
    state = _state_with_two_pets()
    state.customer_id = 42
    state.pet_id = 5
    state.pet_selected_turn = 1
    state.turn_counter = 2

    result = object.__new__(PawfectOrchestrator)._run_tool(
        {
            "name": "get_booking_service_options",
            "args": {"service_type": "GROOMING", "pet_id": 25},
        },
        state=state,
        user_message="show me the packages",
    )

    assert result["error_code"] == "PET_SELECTION_REQUIRED"


def test_known_pet_by_id_finds_a_real_pet():
    state = _state_with_two_pets()
    pet = known_pet_by_id(state, 25)
    assert pet is not None
    assert pet["pet_name"] == "Lili"


def test_known_pet_by_id_rejects_a_pet_id_the_customer_does_not_own():
    # This is the guard that stops _run_tool from trusting a hallucinated/
    # mismatched pet_id the model supplied for get_booking_service_options/
    # create_booking.
    state = _state_with_two_pets()
    assert known_pet_by_id(state, 999) is None


def test_known_pet_by_id_handles_missing_or_blank_pet_id():
    state = _state_with_two_pets()
    assert known_pet_by_id(state, None) is None
    assert known_pet_by_id(state, "") is None


def test_run_tool_overrides_model_company_id_with_session_tenant(monkeypatch):
    class CapturingTool:
        def invoke(self, args):
            return dict(args)

    monkeypatch.setitem(TOOLS_BY_NAME, "tenant_probe", CapturingTool())
    state = ConversationState(phone_number="+60123456705", company_id="7")
    tool_call = {
        "name": "tenant_probe",
        "args": {"company_id": "999", "query": "company policy"},
    }

    result = object.__new__(PawfectOrchestrator)._run_tool(tool_call, state=state)

    assert result["company_id"] == "7"
    assert tool_call["args"]["company_id"] == "7"


def test_repeat_history_overrides_model_pet_and_service_with_customer_request(monkeypatch):
    class CapturingTool:
        def invoke(self, args):
            return dict(args)

    monkeypatch.setitem(TOOLS_BY_NAME, "get_last_completed_booking", CapturingTool())
    state = ConversationState(
        phone_number="+60123456705",
        company_id="7",
        customer_id=42,
        pet_id=25,
        service_type="GROOMING",
    )
    state.known_pets = [
        {"pet_id": 25, "pet_type": "Cat", "pet_name": "Milo", "pet_size": "M"}
    ]
    tool_call = {
        "name": "get_last_completed_booking",
        "args": {
            "company_id": "999",
            "customer_id": 999,
            "pet_id": 999,
            "service_type": "BOARDING",
        },
    }

    result = object.__new__(PawfectOrchestrator)._run_tool(
        tool_call,
        state=state,
        user_message="grooming like last time for Milo",
    )

    assert result["company_id"] == "7"
    assert result["customer_id"] == 42
    assert result["pet_id"] == 25
    assert result["service_type"] == "GROOMING"


def test_repeat_template_must_match_current_catalogue_before_availability():
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        service_type="GROOMING",
        pet_id=1,
        current_datetime_resolution={
            "date": "2026-08-08",
            "date_range": None,
            "period": "afternoon",
        },
    )
    PawfectOrchestrator._cache_repeat_booking_template(
        state,
        "get_last_completed_booking",
        {
            "found": True,
            "booking_id": 339,
            "pet_id": 1,
            "pet_name": "Milo",
            "last_service_type": "GROOMING",
            "package_name": "Standard Bath - Groomers Choice",
            "price": 80,
            "add_on": "-",
            "add_on_price": 0,
        },
        {"service_type": "GROOMING", "pet_id": 1},
    )
    state.verified_service_options = [
        {
            "service_type": "GROOMING",
            "pet_id": 1,
            "service_name": "Standard Bath - Groomers Choice",
            "price": 80,
            "selection_kind": "service",
        }
    ]
    PawfectOrchestrator._cache_repeat_booking_template(
        state,
        "get_booking_service_options",
        {"status": "success", "data": {}},
        {"service_type": "GROOMING", "pet_id": 1},
    )

    assert state.repeat_booking_template["catalogue_validated"] is True
    assert state.repeat_booking_template["package_name"] == "Standard Bath - Groomers Choice"
    assert PawfectOrchestrator._next_repeat_booking_tool(
        state,
        "grooming like last time for Milo next Saturday afternoon",
        [
            {"tool": "get_last_completed_booking"},
            {"tool": "get_booking_service_options"},
        ],
    ) == "check_availability"


def test_repeat_template_with_retired_package_does_not_advance_to_availability():
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        current_datetime_resolution={"date": "2026-08-08"},
        repeat_booking_template={
            "pet_id": 1,
            "service_type": "GROOMING",
            "catalogue_validated": False,
        },
    )

    assert PawfectOrchestrator._next_repeat_booking_tool(
        state,
        "grooming like last time for Milo next Saturday afternoon",
        [
            {"tool": "get_last_completed_booking"},
            {"tool": "get_booking_service_options"},
        ],
    ) is None


def test_cancel_pet_name_in_initial_request_cannot_skip_preview(monkeypatch):
    class CapturingTool:
        def __init__(self):
            self.calls = []

        def invoke(self, args):
            self.calls.append(dict(args))
            return {"status": "confirmation_required", "data": {"booking_id": 91}}

    capturing = CapturingTool()
    monkeypatch.setitem(TOOLS_BY_NAME, "cancel_booking", capturing)
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        customer_id=1,
        active_scenario="CANCEL_BOOKING",
        turn_counter=1,
    )

    object.__new__(PawfectOrchestrator)._run_tool(
        {
            "name": "cancel_booking",
            "args": {
                "booking_id": 91,
                "service_type": "GROOMING",
                "confirm_pet_name": "Milo",
            },
        },
        state,
        "cancel Milo's booking",
    )

    assert capturing.calls[0]["confirm_pet_name"] == ""


def test_cancel_pet_name_is_accepted_only_after_prior_turn_preview(monkeypatch):
    class CapturingTool:
        def __init__(self):
            self.calls = []

        def invoke(self, args):
            self.calls.append(dict(args))
            return {"status": "success", "data": {"booking_id": 91}}

    capturing = CapturingTool()
    monkeypatch.setitem(TOOLS_BY_NAME, "cancel_booking", capturing)
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        customer_id=1,
        active_scenario="CANCEL_BOOKING",
        turn_counter=2,
        pending_booking_confirmation={
            "tool": "cancel_booking",
            "booking_id": 91,
            "service_type": "GROOMING",
            "preview_turn": 1,
        },
    )

    object.__new__(PawfectOrchestrator)._run_tool(
        {
            "name": "cancel_booking",
            "args": {
                "booking_id": 91,
                "service_type": "GROOMING",
                "confirm_pet_name": "Milo",
            },
        },
        state,
        "Milo",
    )

    assert capturing.calls[0]["confirm_pet_name"] == "Milo"


def test_registration_writes_reject_model_invented_customer_and_pet_names(monkeypatch):
    class CapturingTool:
        def invoke(self, args):
            return {"status": "success", "data": dict(args)}

    monkeypatch.setitem(TOOLS_BY_NAME, "create_customer", CapturingTool())
    monkeypatch.setitem(TOOLS_BY_NAME, "create_pet", CapturingTool())
    orchestrator = object.__new__(PawfectOrchestrator)
    new_customer = ConversationState(
        phone_number="+60123456705", company_id="1", active_scenario="MAKE_BOOKING"
    )

    customer_result = orchestrator._run_tool(
        {"name": "create_customer", "args": {"full_name": "Invented Person"}},
        new_customer,
        "I need to make a booking",
    )
    assert customer_result["error"] == "UNCONFIRMED_CUSTOMER_NAME"

    existing_customer = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        customer_id=1,
        active_scenario="MAKE_BOOKING",
    )
    pet_result = orchestrator._run_tool(
        {
            "name": "create_pet",
            "args": {
                "pet_name": "Ghost",
                "pet_type": "cat",
                "height_text": "30 cm",
                "breed": "mixed",
            },
        },
        existing_customer,
        "I have a cat, mixed breed, around 30 cm",
    )
    assert pet_result["error"] == "UNCONFIRMED_PET_NAME"


def test_vaccination_update_rejects_model_computed_expiry_wording(monkeypatch):
    class CapturingTool:
        def invoke(self, args):
            return {"status": "success", "data": dict(args)}

    monkeypatch.setitem(TOOLS_BY_NAME, "update_pet_vaccination", CapturingTool())
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        customer_id=1,
        pet_id=9,
        active_scenario="MAKE_BOOKING",
    )

    result = object.__new__(PawfectOrchestrator)._run_tool(
        {
            "name": "update_pet_vaccination",
            "args": {"pet_id": 9, "vaccination_expiry_text": "30 August 2027"},
        },
        state,
        "the vaccination expires next year",
    )

    assert result["error"] == "UNCONFIRMED_VACCINATION_EXPIRY"


def test_create_pet_breed_guard_accepts_customer_wording_and_flexible_unknown_answers():
    state = ConversationState(phone_number="+60123456705", company_id="1")

    assert reject_unconfirmed_breed(
        state, {"breed": "Golden Retriever"}, "Milo is a golden"
    ) is None
    assert reject_unconfirmed_breed(
        state, {"breed": "unknown"}, "我不清楚它是什么品种"
    ) is None
    assert reject_unconfirmed_breed(
        state, {"breed": "mixed"}, "Dia anjing kacukan"
    ) is None
    args_schema = TOOLS_BY_NAME["create_pet"].args_schema
    schema = args_schema.model_json_schema() if hasattr(args_schema, "model_json_schema") else args_schema.schema()
    assert "breed" in schema["required"]


def test_create_pet_breed_guard_keeps_answers_from_the_full_profile_flow():
    state = ConversationState(phone_number="+60123456705", company_id="1")
    state.history = [
        {"role": "human", "content": "Her breed is British Shorthair"},
        {"role": "ai", "content": "What is her name?"},
        {"role": "human", "content": "Luna"},
        {"role": "ai", "content": "Cat or dog?"},
        {"role": "human", "content": "Cat"},
        {"role": "ai", "content": "How tall?"},
        {"role": "human", "content": "Around 28 cm"},
        {"role": "ai", "content": "Let me create the profile."},
    ]

    assert reject_unconfirmed_breed(
        state, {"breed": "British Shorthair"}, "yes"
    ) is None


def test_create_pet_repairs_explicit_unknown_instead_of_rejecting_wrong_mixed_mapping(monkeypatch):
    class CapturingTool:
        def __init__(self):
            self.calls = []

        def invoke(self, args):
            self.calls.append(dict(args))
            return {"status": "success", "data": {"pet": dict(args)}}

    capturing = CapturingTool()
    monkeypatch.setitem(TOOLS_BY_NAME, "create_pet", capturing)
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        customer_id=10,
        active_scenario="MAKE_BOOKING",
    )
    state.history = [
        {"role": "human", "content": "My pet is a dog named Milo"},
        {"role": "ai", "content": "What breed is Milo?"},
    ]

    result = object.__new__(PawfectOrchestrator)._run_tool(
        {
            "name": "create_pet",
            "args": {
                "pet_name": "Milo",
                "pet_type": "dog",
                "height_text": "30 cm",
                "breed": "mixed",
            },
        },
        state,
        "I don't know Milo's breed, and he is around 30 cm tall",
    )

    assert result["status"] == "success"
    assert capturing.calls[0]["breed"] == "unknown"


def test_new_pet_prompt_requires_breed_in_addition_to_name_species_and_height():
    from app.prompts.system_prompt import SYSTEM_PROMPT

    registration = SYSTEM_PROMPT.split("CUSTOMER AND PET REGISTRATION", 1)[1]
    assert "collect name, species, breed, and height" in registration


def test_create_pet_breed_guard_rejects_missing_invented_or_species_values():
    state = ConversationState(phone_number="+60123456705", company_id="1")

    assert reject_unconfirmed_breed(state, {"breed": ""}, "It is a dog")[
        "error"
    ] == "MISSING_BREED"
    assert reject_unconfirmed_breed(
        state, {"breed": "Poodle"}, "It is a dog"
    )["error"] == "UNCONFIRMED_BREED"
    assert reject_unconfirmed_breed(
        state, {"breed": "dog"}, "It is a dog"
    )["error"] == "SPECIES_IS_NOT_BREED"
    assert reject_unconfirmed_breed(
        state, {"breed": "mixed"}, "I don't know the breed"
    )["error"] == "UNCONFIRMED_BREED"
