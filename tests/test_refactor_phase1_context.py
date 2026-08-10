"""Phase 1 of REFACTOR_PLAN.md — proves BookingDraft/AgentContext can
represent a real in-progress booking using today's actual
ConversationState field shapes, before anything live is wired to them.
"""

from app.context.booking_draft import BookingDraft, derive_booking_draft
from app.context.builder import build_agent_context
from app.context.state import ConversationState


def _state_mid_boarding_flow() -> ConversationState:
    """Shape lifted directly from real fields today's live code populates:
    verified_service_options (app/orchestrator.py's _cache_booking_evidence)
    and verified_availability_slots (its BOARDING check_availability
    branch) — see those call sites for the exact keys used."""
    state = ConversationState(phone_number="+60123456701", company_id="1")
    state.pet_id = 1
    state.pet_name = "Milo"
    state.service_type = "BOARDING"
    state.verified_service_options = [
        {
            "service_type": "BOARDING",
            "pet_id": "1",
            "room_type": "Sirius Room",
            "price": 68.0,
            "selection_kind": "service",
        }
    ]
    state.verified_availability_slots = [
        {
            "service_type": "BOARDING",
            "verified_turn": 3,
            "date": "2026-08-10",
            "time": "10:00:00",
            "room_type": "Sirius Room",
            "check_out_date": "2026-08-12",
            "check_out_time": "12:00:00",
            "duration_minutes": None,
            "preferred_staff": "",
        }
    ]
    state.turn_counter = 3
    return state


def test_booking_draft_captures_a_real_boarding_flow():
    draft = derive_booking_draft(_state_mid_boarding_flow())
    assert draft.pet_ref == "1"
    assert draft.pet_name == "Milo"
    assert draft.service_type == "BOARDING"
    assert draft.option_name == "Sirius Room"
    assert draft.base_price == 68.0
    assert draft.check_in_date == "2026-08-10"
    assert draft.check_in_time == "10:00:00"
    assert draft.check_out_date == "2026-08-12"
    assert draft.check_out_time == "12:00:00"
    assert draft.availability_ref is not None
    # Nothing here required a check_out_time on the CACHED slot to already
    # equal some later customer-stated value — the exact class of bug fixed
    # in app/agent/guardrails.py's reject_unverified_booking_payload today.


def test_booking_draft_is_not_ready_for_preview_without_a_selected_slot():
    state = ConversationState(phone_number="+60123456701", company_id="1")
    state.pet_id = 1
    state.service_type = "BOARDING"
    draft = derive_booking_draft(state)
    assert draft.is_ready_for_preview() is False


def test_booking_draft_is_ready_for_preview_once_option_and_slot_are_verified():
    draft = derive_booking_draft(_state_mid_boarding_flow())
    assert draft.is_ready_for_preview() is True


def test_booking_draft_public_dict_omits_null_fields():
    draft = BookingDraft(pet_ref="1", service_type="BOARDING")
    public = draft.as_public_dict()
    assert public == {"pet_ref": "1", "service_type": "BOARDING"}
    assert "option_ref" not in public


def test_booking_draft_does_not_guess_a_pick_from_an_ambiguous_catalogue_cache():
    """Confirmed live (real gpt-4o-mini call, real RAG catalogue): after the
    model lists a whole GROOMING catalogue (10 packages + 24 add-ons, none
    picked yet), verified_service_options ends up with every one of those
    entries cached, add-ons included, in whatever order the tool returned
    them. Naively trusting "the last cached entry" picked up a stray add-on
    (e.g. "Single Wave Spa") instead of leaving the pick genuinely unknown —
    exactly the kind of ambiguity a single BookingDraft is supposed to make
    impossible to get wrong."""
    state = ConversationState(phone_number="+60123456702", company_id="1")
    state.pet_id = 2
    state.pet_name = "Coco"
    state.service_type = "GROOMING"
    state.verified_service_options = [
        {"service_type": "GROOMING", "selection_kind": "service", "service_name": "Standard Short Fur", "price": 62.0},
        {"service_type": "GROOMING", "selection_kind": "service", "service_name": "Standard Long Fur", "price": 75.0},
        {"service_type": "GROOMING", "selection_kind": "add_on", "service_name": "Single Wave Spa for XS-XXL", "price": 15.0},
    ]

    draft = derive_booking_draft(state)
    assert draft.option_name is None
    assert draft.base_price is None


def test_booking_draft_prefers_the_pending_previews_own_args_once_one_exists():
    """A pending create_booking preview's own args are the one place today's
    state layout captures an actual, unambiguous selection — they must win
    over any catalogue-cache guess, including when the cache is itself
    ambiguous (multiple options still sitting there from earlier turns)."""
    state = ConversationState(phone_number="+60123456702", company_id="1")
    state.pet_id = 2
    state.pet_name = "Coco"
    state.service_type = "GROOMING"
    state.verified_service_options = [
        {"service_type": "GROOMING", "selection_kind": "service", "service_name": "Standard Short Fur", "price": 62.0},
        {"service_type": "GROOMING", "selection_kind": "service", "service_name": "Standard Long Fur", "price": 75.0},
    ]
    state.pending_actions["create_booking"] = {
        "signature": "sig-abc123",
        "args": {
            "package_name": "Standard Long Fur",
            "price": 75.0,
            "date": "2026-08-13",
            "time": "14:00",
            "add_on": "Nail Clipping",
            "add_on_price": 15.0,
        },
        "preview_turn": 4,
        "scenario": "MAKE_BOOKING",
    }

    draft = derive_booking_draft(state)
    assert draft.option_name == "Standard Long Fur"
    assert draft.base_price == 75.0
    assert draft.check_in_date == "2026-08-13"
    assert draft.check_in_time == "14:00"
    assert draft.add_on_name == "Nail Clipping"
    assert draft.add_on_price == 15.0
    assert draft.preview_ref == "sig-abc123"


def test_agent_context_is_small_and_compact_compared_to_the_full_state_dump():
    state = _state_mid_boarding_flow()
    # turn_counter=3 implies real prior turns — reflect that in history too,
    # since is_first_message reads history, not turn_counter.
    state.history = [{"role": "human", "content": "..."}, {"role": "ai", "content": "..."}]
    customer = {
        "found": True,
        "first_name": "Alicia",
        "pets": [{"pet_id": 1, "pet_name": "Milo", "pet_type": "Cat", "pet_size": "M"}],
        "recent_booking": {"service_name": "Grooming", "booking_date": "2026-08-08"},
        "upcoming_booking": {"service_name": "Daycare", "booking_date": "2026-08-11"},
        "loyalty_account": {"points_balance": 387, "tier": "Silver"},
        "loyalty_context_status": "available",
    }
    company_context = {"company_name": "Happy Paws Center", "business_date": "2026-08-09"}

    context = build_agent_context(state, customer, company_context)

    # Fixed top-level shape, not a dump of ConversationState.__dict__ (today's
    # RUNTIME_CONTEXT exposes ~20+ state fields every turn regardless of
    # relevance — see app/context/state.py).
    assert set(context.keys()) == {
        "company", "customer", "goal", "is_first_message",
        "recent_booking", "upcoming_booking", "loyalty_account",
        "loyalty_context_status", "evidence", "pending_action",
        "current_message_datetime",
    }
    assert context["loyalty_account"]["tier"] == "Silver"
    assert context["customer"]["pets"][0]["ref"] == "1"
    # booking_draft itself removed from AgentContext (item #8 of the
    # 2026-08-10 review — dead weight, never referenced by
    # SYSTEM_PROMPT_V2, its refs can't resolve against EvidenceStore).
    # derive_booking_draft() is still exercised directly by the tests
    # above this one; only its injection into the model-facing context
    # is gone.
    assert context["evidence"]["service_option"] == "verified"
    assert context["evidence"]["availability"] == "verified"
    assert "booking_preview" not in context["evidence"]  # no pending create_booking preview yet
    assert context["recent_booking"]["service_name"] == "Grooming"
    assert context["upcoming_booking"]["service_name"] == "Daycare"
    # Mid-flow fixture already has turns in history — not a first message.
    assert context["is_first_message"] is False


def test_agent_context_marks_a_genuinely_first_message():
    state = _state_mid_boarding_flow()
    assert state.history == []  # fixture's default — nothing to override
    customer = {"found": True, "first_name": "Alicia", "pets": []}
    company_context = {"company_name": "Happy Paws Center"}

    context = build_agent_context(state, customer, company_context)

    assert context["is_first_message"] is True
