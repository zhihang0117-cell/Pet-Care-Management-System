"""
Real regression tests against app/orchestrator.py's deterministic pet-context
helpers. The previous version of this file only asserted a dict literal
equaled the string defined two lines above it — it never imported or called
any real code, so it could never fail regardless of what the orchestrator
actually did. These tests exercise the real functions, including the
multi-pet pet_id mixup found and fixed this session (see _known_pet_by_id and
the get_booking_service_options/create_booking override in _run_tool).
"""

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
    PawfectOrchestrator._match_named_pet(state, "what grooming options for Lili?")
    assert state.pet_id == 25
    assert state.pet_name == "Lili"


def test_match_named_pet_does_nothing_when_no_known_pet_is_named():
    state = _state_with_two_pets()
    state.pet_id = 5  # simulate an earlier turn having resolved Simba
    PawfectOrchestrator._match_named_pet(state, "and what about my other pet?")
    # No name match this turn -> must not guess; stale state.pet_id is left
    # for _known_pet_by_id/_run_tool's override to handle, not overwritten here.
    assert state.pet_id == 5


def test_known_pet_by_id_finds_a_real_pet():
    state = _state_with_two_pets()
    pet = PawfectOrchestrator._known_pet_by_id(state, 25)
    assert pet is not None
    assert pet["pet_name"] == "Lili"


def test_known_pet_by_id_rejects_a_pet_id_the_customer_does_not_own():
    # This is the guard that stops _run_tool from trusting a hallucinated/
    # mismatched pet_id the model supplied for get_booking_service_options/
    # create_booking.
    state = _state_with_two_pets()
    assert PawfectOrchestrator._known_pet_by_id(state, 999) is None


def test_known_pet_by_id_handles_missing_or_blank_pet_id():
    state = _state_with_two_pets()
    assert PawfectOrchestrator._known_pet_by_id(state, None) is None
    assert PawfectOrchestrator._known_pet_by_id(state, "") is None


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


def test_create_pet_breed_guard_accepts_customer_wording_and_flexible_unknown_answers():
    state = ConversationState(phone_number="+60123456705", company_id="1")

    assert PawfectOrchestrator._reject_unconfirmed_breed(
        state, {"breed": "Golden Retriever"}, "Milo is a golden"
    ) is None
    assert PawfectOrchestrator._reject_unconfirmed_breed(
        state, {"breed": "unknown"}, "我不清楚它是什么品种"
    ) is None
    assert PawfectOrchestrator._reject_unconfirmed_breed(
        state, {"breed": "mixed"}, "Dia anjing kacukan"
    ) is None
    args_schema = TOOLS_BY_NAME["create_pet"].args_schema
    schema = args_schema.model_json_schema() if hasattr(args_schema, "model_json_schema") else args_schema.schema()
    assert "breed" in schema["required"]


def test_create_pet_breed_guard_rejects_missing_invented_or_species_values():
    state = ConversationState(phone_number="+60123456705", company_id="1")

    assert PawfectOrchestrator._reject_unconfirmed_breed(state, {"breed": ""}, "It is a dog")[
        "error"
    ] == "MISSING_BREED"
    assert PawfectOrchestrator._reject_unconfirmed_breed(
        state, {"breed": "Poodle"}, "It is a dog"
    )["error"] == "UNCONFIRMED_BREED"
    assert PawfectOrchestrator._reject_unconfirmed_breed(
        state, {"breed": "dog"}, "It is a dog"
    )["error"] == "SPECIES_IS_NOT_BREED"
    assert PawfectOrchestrator._reject_unconfirmed_breed(
        state, {"breed": "mixed"}, "I don't know the breed"
    )["error"] == "UNCONFIRMED_BREED"
