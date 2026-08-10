"""Phase 6 of REFACTOR_PLAN.md — app/agent/runtime.py's handle_turn(), the
new core loop. Deterministic tests for the authorization gate run without
credentials; the full end-to-end conversation test needs a real
OPENAI_API_KEY + Supabase, same guard shape as Phases 3-5.
"""

import os

import pytest
from dotenv import load_dotenv

from app.agent import policy
from app.agent.evidence import EvidenceStore
from app.agent.runtime import (
    UnknownPetRefError,
    _confirm_booking_hit_a_genuine_write_rejection,
    _option_named_or_delegated,
    _resolve_pet_ref,
)

load_dotenv(override=False)
_HAS_LIVE_CREDS = bool(os.getenv("OPENAI_API_KEY")) and bool(os.getenv("SUPABASE_URL"))
requires_live_llm = pytest.mark.skipif(
    not _HAS_LIVE_CREDS, reason="OPENAI_API_KEY/SUPABASE_URL not configured"
)


def test_resolve_pet_ref_accepts_a_real_customer_pet():
    customer = {"pets": [{"ref": "1", "name": "Milo"}, {"ref": "2", "name": "Coco"}]}
    assert _resolve_pet_ref("2", customer) == 2


def test_resolve_pet_ref_rejects_a_ref_not_belonging_to_this_customer():
    customer = {"pets": [{"ref": "1", "name": "Milo"}]}
    with pytest.raises(UnknownPetRefError):
        _resolve_pet_ref("99", customer)


def test_genuine_write_rejection_detected_only_when_the_real_write_was_reached():
    """Real gap confirmed 2026-08-10: a GENUINE write-time rejection (a
    real staff/room/pet conflict the database caught) left
    pending_preview_ref/active_slots standing — this is the pure decision
    logic behind that fix, extracted so it's directly testable without a
    live write-conflict scenario."""
    # A real write attempt that the database genuinely rejected.
    assert _confirm_booking_hit_a_genuine_write_rejection(
        {"ok": False, "code": "This pet already has a booking that overlaps this time"}, None,
    ) is True
    # policy.authorize_confirm blocked it before the real write — the
    # customer just hasn't confirmed yet, the preview is still valid.
    assert _confirm_booking_hit_a_genuine_write_rejection(
        {"ok": False, "code": "CONFIRMATION_REQUIRED"},
        {"ok": False, "code": "CONFIRMATION_REQUIRED"},
    ) is False
    # A real success is obviously not a rejection at all.
    assert _confirm_booking_hit_a_genuine_write_rejection(
        {"ok": True, "code": "OK"}, None,
    ) is False


def test_option_named_or_delegated_catches_the_reproduced_live_failure():
    """Real Agent-control gap confirmed 2026-08-10, reproduced live twice:
    "book grooming this Friday" reached check_availability having silently
    used "Luxury Bath - DAVIS" — zero relation to anything the customer
    said. This is the pure heuristic behind the fix."""
    assert _option_named_or_delegated("book grooming for Milo this Friday", "Luxury Bath - DAVIS") is False
    assert _option_named_or_delegated("I want a bath for my dog", "Luxury Bath - DAVIS") is False, (
        "bath/fur/grooming are generic words shared by ~every option name — "
        "must not count as a match on their own"
    )


def test_option_named_or_delegated_allows_a_real_customer_choice_through():
    assert _option_named_or_delegated("the standard grooming please", "Standard Bath - Groomers Choice") is True
    assert _option_named_or_delegated("Milo standard grooming this Friday at 1pm", "Standard Bath - Groomers Choice") is True


def test_option_named_or_delegated_recognizes_delegation_phrases():
    for phrase in ("give me the cheapest one", "same as last time please", "you choose", "any package is fine"):
        assert _option_named_or_delegated(phrase, "Luxury Bath - DAVIS") is True, phrase


# The dedicated authorize_confirm unit tests moved to
# tests/test_refactor_phase8_policy.py (Phase 8 consolidated the
# near-duplicate _authorize_confirm_booking/_authorize_confirm_membership
# into one app.agent.policy.authorize_confirm). The end-to-end same-turn
# check below still lives here since it's exercising handle_turn()'s real
# integration, not the gate's own logic in isolation.


def test_check_availability_has_no_raw_date_parameter_in_its_schema():
    """The actual fix for "the model can still pass a self-computed date
    string, and Supabase will happily verify a slot against the WRONG
    date" — not just documentation asking the model not to, but the tool
    schema itself has no date/check_out_date field at all, only
    datetime_ref/check_out_datetime_ref (which only resolve_datetime can
    mint). Checked structurally against the tool's own schema, not by
    reading source."""
    from app.agent.runtime import _bind_tools

    class _FakeState:
        daycare_duration_minutes = None

    evidence = EvidenceStore()
    tools = _bind_tools(
        evidence, company_id=1, customer_id=1, customer={"pets": []}, pet_name_by_ref={}, state=_FakeState(),
        phone_number="+60123456789",
    )
    check_availability = next(t for t in tools if t.name == "check_availability")
    assert "date" not in check_availability.args
    assert "check_out_date" not in check_availability.args
    assert "datetime_ref" in check_availability.args
    assert "check_out_datetime_ref" in check_availability.args


@requires_live_llm
def test_grooming_this_friday_never_reaches_availability_with_a_silently_chosen_package():
    """Real Agent-control gap confirmed 2026-08-10, reproduced live: "book
    grooming for Milo this Friday" (a pet and a date, nothing else — no
    package, no time, no delegated preference) used to reach
    check_availability/preview_booking having silently picked a specific
    package (once "Luxury Bath - DAVIS", RM160) out of ten real options.
    Verified availability is not the same as a customer selection."""
    from langchain_openai import ChatOpenAI

    from app.agent.evidence import EvidenceStoreRegistry
    from app.agent.runtime import handle_turn
    from app.context.memory import ConversationMemory

    memory = ConversationMemory()
    evidence_registry = EvidenceStoreRegistry()
    model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    company_context = {"company_id": 1, "company_name": "Happy Paws Center", "business_date": "2026-08-09"}
    phone = "+60123456701"  # Alicia / Milo

    r1 = handle_turn(
        memory=memory, evidence_registry=evidence_registry, model=model,
        company_context=company_context, phone_number=phone,
        user_message="book grooming for Milo this Friday",
    )
    availability_calls = [t for t in r1.trace if t["tool"] == "check_availability" and t["result"].get("ok")]
    assert not availability_calls, (
        f"check_availability succeeded with a package the customer never chose or delegated — trace: {r1.trace}"
    )
    preview_calls = [t for t in r1.trace if t["tool"] == "preview_booking" and t["result"].get("ok")]
    assert not preview_calls, f"reached a preview without a real customer selection — trace: {r1.trace}"


@requires_live_llm
def test_active_slots_lets_a_later_turn_pick_a_time_by_ordinal_without_recalling_check_availability():
    """Item #9 of the 2026-08-10 review: active_options already let "the
    cheapest one" resolve without a fresh get_service_options call — this
    is the same fix for a just-shown TIME list. Real gap confirmed: without
    it, "12 please" on the turn after a slot list was shown had no
    slot_ref to resolve against at all."""
    from langchain_openai import ChatOpenAI

    from app.agent.evidence import EvidenceStoreRegistry
    from app.agent.runtime import handle_turn
    from app.context.memory import ConversationMemory

    memory = ConversationMemory()
    evidence_registry = EvidenceStoreRegistry()
    model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    company_context = {"company_id": 1, "company_name": "Happy Paws Center", "business_date": "2026-08-09"}
    phone = "+60123456701"  # Alicia / Milo

    r1 = handle_turn(
        memory=memory, evidence_registry=evidence_registry, model=model,
        company_context=company_context, phone_number=phone,
        user_message="What grooming times are available for Milo, standard short fur bath, on 2026-10-05 morning?",
    )
    availability_calls = [t for t in r1.trace if t["tool"] == "check_availability" and t["result"].get("ok")]
    assert availability_calls, f"model never checked availability — trace: {r1.trace}"
    shown_slots = availability_calls[-1]["result"]["slots"]
    # A period query ("morning") deliberately doesn't commit to one exact
    # time, so this turn should show a real LIST rather than reaching a
    # single preview_booking — needed so turn 2's pick is unambiguously an
    # ordinal choice, not a same-turn-ambiguous "confirm the pending
    # preview" (ConversationState.pending_preview_ref would otherwise make
    # a short turn 2 message genuinely ambiguous between the two, and
    # AgentContext.pending_action is deliberately checked FIRST).
    assert len(shown_slots) > 1, f"need multiple real slots to test an ordinal pick — trace: {r1.trace}"

    state = memory.get(phone, "1")
    assert state.active_slots == shown_slots

    r2 = handle_turn(
        memory=memory, evidence_registry=evidence_registry, model=model,
        company_context=company_context, phone_number=phone,
        user_message="Book the first one.",
    )
    # The model may still choose to recheck (never wrong to), but it must
    # NOT need a second check_availability call to find a real slot_ref —
    # active_slots already had it. What matters is preview_booking gets a
    # real, resolvable slot_ref either way.
    preview_calls = [t for t in r2.trace if t["tool"] == "preview_booking" and t["result"].get("ok")]
    assert preview_calls, f"model never reached a real preview from the ordinal pick — trace: {r2.trace}"


@requires_live_llm
def test_boarding_flow_resolves_two_separate_dates_via_two_datetime_refs():
    """The exact real scenario an external review flagged: BOARDING needs
    a check-in AND a check-out date from one customer message, and
    check_availability must never receive either as a raw string — both
    have to come from their own resolve_datetime call's datetime_ref."""
    from langchain_openai import ChatOpenAI

    from app.agent.evidence import EvidenceStoreRegistry
    from app.agent.runtime import handle_turn
    from app.context.memory import ConversationMemory

    memory = ConversationMemory()
    evidence_registry = EvidenceStoreRegistry()
    model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    company_context = {"company_id": 1, "company_name": "Happy Paws Center", "business_date": "2026-08-09"}
    phone = "+60123456701"  # Alicia / Milo

    handle_turn(
        memory=memory, evidence_registry=evidence_registry, model=model,
        company_context=company_context, phone_number=phone, user_message="hi",
    )
    r2 = handle_turn(
        memory=memory, evidence_registry=evidence_registry, model=model,
        company_context=company_context, phone_number=phone,
        user_message="Book Milo into the Sirius Room for boarding, checking in 2026-09-21 and checking out 2026-09-23.",
    )
    availability_calls = [t for t in r2.trace if t["tool"] == "check_availability"]
    assert availability_calls, f"model never called check_availability — trace: {r2.trace}"
    # A same-day check-in/check-out slip (both dates resolving to the
    # first date mentioned) is a real, structurally-caught error the model
    # can recover from within the same turn (check_availability's own
    # error message tells it to re-resolve the check-out expression) — the
    # LAST successful call is the one whose args reflect what actually got
    # verified/previewed, not necessarily the first attempt.
    successful_calls = [t for t in availability_calls if t["result"].get("ok")]
    assert successful_calls, f"check_availability never succeeded — trace: {r2.trace}"
    args = successful_calls[-1]["args"]
    assert "date" not in args and "check_out_date" not in args  # not even offered as an option
    assert args.get("datetime_ref")
    assert args.get("check_out_datetime_ref"), f"BOARDING check-out was never resolved to its own ref — args: {args}"

    evidence = evidence_registry.get(phone, "1")
    check_in = evidence.resolve(args["datetime_ref"])
    check_out = evidence.resolve(args["check_out_datetime_ref"])
    assert check_in["date"] == "2026-09-21"
    assert check_out["date"] == "2026-09-23"


@requires_live_llm
def test_datetime_established_on_an_earlier_turn_survives_a_bare_time_only_turn():
    """Real bug confirmed 2026-08-10: handle_turn() used to unconditionally
    OVERWRITE state.current_datetime_resolution with each turn's own raw
    resolve_datetime result — app.agent.tool_loop.apply_datetime_resolution
    already fixed exactly this for the live orchestrator earlier this
    session (merge onto the existing date when a later turn resolves only
    a bare time), but it was never migrated to app.agent.runtime. A date
    given on turn 1 ("2026-11-10") must still be there after a turn 2 that
    only gives a time ("2pm works", date=None on its own)."""
    from langchain_openai import ChatOpenAI

    from app.agent.evidence import EvidenceStoreRegistry
    from app.agent.runtime import handle_turn
    from app.context.memory import ConversationMemory

    memory = ConversationMemory()
    evidence_registry = EvidenceStoreRegistry()
    model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    company_context = {"company_id": 1, "company_name": "Happy Paws Center", "business_date": "2026-08-09"}
    phone = "+60123456701"  # Alicia / Milo

    handle_turn(
        memory=memory, evidence_registry=evidence_registry, model=model,
        company_context=company_context, phone_number=phone,
        user_message="I want grooming for Milo, standard short fur bath, on 2026-11-10",
    )
    state = memory.get(phone, "1")
    assert state.current_datetime_resolution["date"] == "2026-11-10"

    handle_turn(
        memory=memory, evidence_registry=evidence_registry, model=model,
        company_context=company_context, phone_number=phone, user_message="2pm works",
    )
    # The date must survive even though this turn's own text has none —
    # this is the actual fix; deliberately not asserting anything about
    # what the model does with it next (turn 1 already reached a full
    # single-time preview here, so a short turn 2 is genuinely ambiguous
    # between "confirm that" and "pick 2pm instead" — the same real
    # ambiguity documented on test_active_slots_lets_a_later_turn_pick_a_
    # time_by_ordinal... above; not this fix's concern).
    assert state.current_datetime_resolution["date"] == "2026-11-10"
    assert state.current_datetime_resolution["time"] == "14:00"


@requires_live_llm
def test_full_conversation_previews_then_confirms_across_two_real_turns():
    from langchain_openai import ChatOpenAI

    from app.agent.evidence import EvidenceStoreRegistry
    from app.agent.runtime import handle_turn
    from app.context.memory import ConversationMemory
    from app.db.supabase_client import get_supabase_client

    memory = ConversationMemory()
    evidence_registry = EvidenceStoreRegistry()
    model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    company_context = {"company_id": 1, "company_name": "Happy Paws Center", "business_date": "2026-08-09"}
    phone = "+60123456701"  # Alicia / Milo

    r1 = handle_turn(
        memory=memory, evidence_registry=evidence_registry, model=model,
        company_context=company_context, phone_number=phone, user_message="hi",
    )
    assert r1.reply

    r2 = handle_turn(
        memory=memory, evidence_registry=evidence_registry, model=model,
        company_context=company_context, phone_number=phone,
        user_message="Book Milo for grooming, the standard short fur bath, on 2026-09-17 at 10am.",
    )
    preview_calls = [t for t in r2.trace if t["tool"] == "preview_booking" and t["result"].get("ok")]
    assert preview_calls, f"model never reached a real preview — trace: {r2.trace}"

    # SAME-turn confirm attempt (deliberately, not letting the model try it
    # itself — proving the gate independent of whether the model would ever
    # actually do this): must be rejected.
    preview_ref = preview_calls[0]["result"]["preview_ref"]
    state = memory.get(phone, "1")
    same_turn_rejection = policy.authorize_confirm(
        preview_ref, evidence=evidence_registry.get(phone, "1"),
        state=state, user_message="yes",
    )
    assert same_turn_rejection is not None
    assert same_turn_rejection["code"] == "CONFIRMATION_MUST_BE_A_LATER_TURN"

    booking_id = None
    try:
        r3 = handle_turn(
            memory=memory, evidence_registry=evidence_registry, model=model,
            company_context=company_context, phone_number=phone, user_message="yes go ahead",
        )
        confirm_calls = [t for t in r3.trace if t["tool"] == "confirm_booking" and t["result"].get("ok")]
        assert confirm_calls, f"model never confirmed on the later turn — trace: {r3.trace}"
        booking_id = confirm_calls[0]["result"]["data"]["booking_id"]

        # Item #6 of the 2026-08-10 review: get_loyalty must be forced
        # deterministically right after a real confirm_booking success
        # (force_loyalty_check in handle_turn), not left to the prompt
        # alone — same real reliability gap the live orchestrator hit
        # (confirmed live: it treated a successful booking as "done" and
        # skipped the bonus call even when explicitly told to).
        loyalty_calls = [t for t in r3.trace if t["tool"] == "get_loyalty"]
        assert loyalty_calls, f"get_loyalty was never forced after confirm_booking — trace: {r3.trace}"

        client = get_supabase_client()
        row = (
            client.table("grooming_booking").select("*")
            .eq("company_id", 1).eq("grooming_booking_id", booking_id).execute().data
        )
        assert row and row[0]["booking_date"] == "2026-09-17"
    finally:
        if booking_id:
            client = get_supabase_client()
            payment_row = (
                client.table("grooming_booking").select("payment_id")
                .eq("company_id", 1).eq("grooming_booking_id", booking_id).execute().data
            )
            payment_id = payment_row[0]["payment_id"] if payment_row else None
            if payment_id:
                client.table("payment").delete().eq("company_id", 1).eq("payment_id", payment_id).execute()
            # The real bug this cleanup itself had: only the payment row was
            # ever deleted, never the grooming_booking row — every
            # successful run left a real booking for Milo at 2026-09-17
            # 10am standing, which then made the SAME slot look genuinely
            # unavailable (a real pet double-booking, correctly rejected)
            # on the next run. Self-poisoning, confirmed live.
            client.table("grooming_booking").delete().eq("company_id", 1).eq("grooming_booking_id", booking_id).execute()


@requires_live_llm
def test_genuine_confirm_rejection_clears_the_shown_catalogue_not_just_the_preview(monkeypatch):
    """Real gap confirmed live 2026-08-10, during the post-V1-decommission
    Grooming -> Daycare -> Boarding smoke test: a genuine confirm_booking
    write rejection already cleared pending_preview_ref/active_slots, but
    left state.active_options (the just-shown catalogue) standing. A
    customer whose booking then failed and who pivoted to a DIFFERENT
    service ("let's do boarding instead") could have the model reuse the
    stale, wrong-service option_ref instead of calling get_service_options
    again — confirmed live: a Daycare confirm rejection was immediately
    followed by a Boarding request that silently previewed a Daycare
    option. This forces a deterministic write rejection (rather than
    depending on real, mutable vaccination data staying expired) and
    checks the catalogue is actually cleared."""
    from langchain_openai import ChatOpenAI

    from app.agent.evidence import EvidenceStoreRegistry
    from app.agent.runtime import handle_turn
    from app.context.memory import ConversationMemory

    memory = ConversationMemory()
    evidence_registry = EvidenceStoreRegistry()
    model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    company_context = {"company_id": 1, "company_name": "Happy Paws Center", "business_date": "2026-08-09"}
    phone = "+60123456701"  # Alicia / Milo

    handle_turn(
        memory=memory, evidence_registry=evidence_registry, model=model,
        company_context=company_context, phone_number=phone, user_message="hi",
    )
    r2 = handle_turn(
        memory=memory, evidence_registry=evidence_registry, model=model,
        company_context=company_context, phone_number=phone,
        user_message="Book Milo for grooming, the standard short fur bath, on 2026-09-24 at 10am.",
    )
    preview_calls = [t for t in r2.trace if t["tool"] == "preview_booking" and t["result"].get("ok")]
    assert preview_calls, f"model never reached a real preview — trace: {r2.trace}"
    state = memory.get(phone, "1")
    assert state.active_options, "get_service_options should have populated active_options"

    from app.tools import booking_tools

    monkeypatch.setattr(
        booking_tools.create_booking,
        "func",
        lambda **_kwargs: {"status": "error", "error": "This pet already has a booking that overlaps this time"},
    )
    r3 = handle_turn(
        memory=memory, evidence_registry=evidence_registry, model=model,
        company_context=company_context, phone_number=phone, user_message="yes go ahead",
    )
    rejected_calls = [t for t in r3.trace if t["tool"] == "confirm_booking" and not t["result"].get("ok")]
    assert rejected_calls, f"model never attempted the forced-rejection confirm — trace: {r3.trace}"
    assert state.pending_preview_ref is None
    assert state.active_slots is None
    assert state.active_options is None, "the stale catalogue must not survive a genuine write rejection"
