"""Phase 7 of REFACTOR_PLAN.md — policy/booking-history/cancel/reschedule/
loyalty/documents/staff-handoff reference tools. Same guard shape as
Phases 3-6: fast structural tests always run; live tests need real
OPENAI_API_KEY + Supabase credentials.
"""

import os

import pytest
from dotenv import load_dotenv

from app.agent.evidence import EvidenceStore
from app.tools import reference_tools as rt

load_dotenv(override=False)
_HAS_LIVE_CREDS = bool(os.getenv("OPENAI_API_KEY")) and bool(os.getenv("SUPABASE_URL"))
requires_live_llm = pytest.mark.skipif(
    not _HAS_LIVE_CREDS, reason="OPENAI_API_KEY/SUPABASE_URL not configured"
)
_HAS_SUPABASE_CREDS = bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
requires_live_supabase = pytest.mark.skipif(
    not _HAS_SUPABASE_CREDS, reason="SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY not configured"
)


class _FakeState:
    pending_mutation_target = None
    turn_counter = 4


def test_pending_mutation_target_is_remembered_across_a_confirmation_required_result():
    """The actual fix for a real bug confirmed live: cancel_booking's own
    preview (confirm_pet_name empty) identifies one specific booking, but
    without remembering its booking_ref, the model calling cancel_booking
    again with confirm_pet_name set (booking_ref omitted, as the model
    genuinely did) falls back to "no target given" and re-triggers
    "ambiguous, multiple active bookings" — even though the target was
    already uniquely identified one turn ago."""
    from app.agent.runtime import _update_pending_mutation_target

    evidence = EvidenceStore()
    state = _FakeState()

    # First call: booking_ref was already known (customer/model supplied it).
    result = {"ok": True, "code": "CONFIRMATION_REQUIRED", "data": {"booking_id": 387, "service_type": "GROOMING"}}
    _update_pending_mutation_target(state, evidence, "cancel_booking", {"booking_ref": "booking_abc"}, result)
    assert state.pending_mutation_target == {
        "tool": "cancel_booking", "booking_ref": "booking_abc", "minted_turn": 4,
    }

    # Real mutation succeeds -> cleared.
    _update_pending_mutation_target(state, evidence, "cancel_booking", {}, {"ok": True, "code": "OK"})
    assert state.pending_mutation_target is None


def test_pending_mutation_target_mints_a_ref_when_the_model_never_supplied_one():
    """The other real shape observed live: the underlying tool auto-
    resolved a single active booking on its own (booking_ref was never
    passed at all, just confirm_pet_name empty) — there is still a real,
    specific booking_id in the result to remember, so a ref gets minted
    from THAT instead of being left unset."""
    from app.agent.runtime import _update_pending_mutation_target

    evidence = EvidenceStore()
    state = _FakeState()
    result = {"ok": True, "code": "CONFIRMATION_REQUIRED", "data": {"booking_id": 512, "service_type": "BOARDING"}}
    _update_pending_mutation_target(state, evidence, "reschedule_booking", {}, result)

    assert state.pending_mutation_target is not None
    minted_ref = state.pending_mutation_target["booking_ref"]
    assert evidence.resolve(minted_ref) == {"booking_id": 512, "service_type": "BOARDING"}


def test_pending_mutation_target_is_cleared_when_the_customer_declines():
    from app.agent.runtime import _update_pending_mutation_target

    evidence = EvidenceStore()
    state = _FakeState()
    state.pending_mutation_target = {"tool": "cancel_booking", "booking_ref": "booking_abc", "minted_turn": 3}
    _update_pending_mutation_target(state, evidence, "cancel_booking", {}, {"ok": False, "code": "ACTION_DECLINED"})
    assert state.pending_mutation_target is None


def test_cancel_booking_rejects_an_unknown_booking_ref():
    evidence = EvidenceStore()
    result = rt.cancel_booking(evidence, company_id=1, customer_id=1, booking_ref="booking_doesnotexist")
    assert result == {"ok": False, "code": "UNKNOWN_OR_EXPIRED_BOOKING_REF"}


def test_reschedule_booking_rejects_an_unknown_datetime_ref():
    evidence = EvidenceStore()
    booking_ref = evidence.mint("booking", {"booking_id": 1, "service_type": "GROOMING"})
    result = rt.reschedule_booking(evidence, company_id=1, customer_id=1, booking_ref=booking_ref, datetime_ref="nope")
    assert result == {"ok": False, "code": "UNKNOWN_OR_EXPIRED_DATETIME_REF"}


def test_redeem_reward_rejects_unknown_refs():
    evidence = EvidenceStore()
    assert rt.redeem_reward(evidence, company_id=1, customer_id=1, coupon_ref="nope", payment_ref="nope") == {
        "ok": False, "code": "UNKNOWN_OR_EXPIRED_COUPON_REF",
    }
    coupon_ref = evidence.mint("coupon", {"coupon_id": 1, "reward_name": "Test"})
    assert rt.redeem_reward(evidence, company_id=1, customer_id=1, coupon_ref=coupon_ref, payment_ref="nope") == {
        "ok": False, "code": "UNKNOWN_OR_EXPIRED_PAYMENT_REF",
    }


@requires_live_supabase
def test_create_customer_rejects_a_phone_number_that_already_has_a_real_account():
    """Item #7 of the 2026-08-10 review: without create_customer/create_pet,
    a brand-new customer could never complete a V2 booking at all. This
    checks the underlying, unmodified business logic's own defensive
    check — company_id=1's phone +60123456701 (Alicia Lee) is a real seed
    customer."""
    result = rt.create_customer(company_id=1, full_name="Someone Else", phone_number="+60123456701")
    assert result["ok"] is False


def test_create_pet_refuses_when_the_customer_is_not_yet_registered():
    """Same guard app.agent.runtime._bind_tools's create_pet applies before
    even reaching the network — tested here directly against the
    underlying company/customer_id=None shape it guards against."""
    result = rt.create_pet(
        company_id=1, customer_id=None, pet_name="Rex", pet_type="dog",
        height_text="24 inches", breed="Labrador",
    )
    assert result["ok"] is False


@requires_live_supabase
def test_create_customer_then_create_pet_writes_real_rows_and_is_cleaned_up():
    """End-to-end against real data: a brand-new phone number registers,
    then registers a pet for that new customer_id — writes real rows,
    verified, then deleted so re-running this test never leaves junk data
    behind."""
    from app.db.supabase_client import get_supabase_client

    client = get_supabase_client()
    test_phone = "+60 19-999 0001"
    # Guard against a leftover row from a previous interrupted run.
    client.table("customer").delete().eq("company_id", 1).eq("phone_number", test_phone.replace(" ", "").replace("-", "")).execute()

    customer_id = None
    try:
        result = rt.create_customer(company_id=1, full_name="Test Newcomer", phone_number=test_phone)
        assert result["ok"] is True, result
        customer_id = result["data"]["customer_id"]

        pet_result = rt.create_pet(
            company_id=1, customer_id=customer_id, pet_name="TestPetRex", pet_type="dog",
            height_text="24 inches", breed="Labrador",
        )
        assert pet_result["ok"] is True, pet_result
        assert pet_result["data"]["verified_size"]

        row = client.table("pet").select("*").eq("customer_id", customer_id).eq("pet_name", "TestPetRex").execute().data
        assert row and row[0]["breed"] == "Labrador"
    finally:
        if customer_id is not None:
            client.table("pet").delete().eq("customer_id", customer_id).execute()
            client.table("customer").delete().eq("customer_id", customer_id).execute()


def test_confirm_membership_rejects_an_unknown_member_ref():
    evidence = EvidenceStore()
    assert rt.confirm_membership(evidence, member_ref="nope") == {
        "ok": False,
        "code": "UNKNOWN_OR_EXPIRED_MEMBER_REF",
        "message": "This membership preview has expired or was never created. Call preview_membership again.",
    }


@requires_live_supabase
def test_preview_membership_returns_the_real_account_immediately_for_an_existing_member():
    """customer_id=2 (Jason Lim) is already a real Silver-tier member in
    seed data — no preview/confirm dance needed for that case."""
    evidence = EvidenceStore()
    result = rt.preview_membership(evidence, company_id=1, customer_id=2)
    assert result["ok"] is True
    assert result["already_member"] is True
    assert result["account"]["tier"]
    assert "member_ref" not in result


@requires_live_supabase
def test_get_active_bookings_mints_a_ref_per_real_booking():
    evidence = EvidenceStore()
    result = rt.get_active_bookings(evidence, company_id=1, customer_id=1)
    assert result["ok"] is True
    assert len(result["bookings"]) > 0
    first = result["bookings"][0]
    assert first["booking_ref"].startswith("booking_")
    resolved = evidence.resolve(first["booking_ref"])
    assert resolved["service_type"] == first["service_type"]


@requires_live_supabase
def test_cancel_booking_preview_step_never_mutates_anything():
    evidence = EvidenceStore()
    before = rt.get_active_bookings(evidence, company_id=1, customer_id=1)
    target = before["bookings"][0]

    preview = rt.cancel_booking(evidence, company_id=1, customer_id=1, booking_ref=target["booking_ref"], confirm_pet_name="")
    assert preview["ok"] is True
    assert preview["code"] == "CONFIRMATION_REQUIRED"
    assert preview["data"]["pet_name"] == target["pet_name"]

    after = rt.get_active_bookings(evidence, company_id=1, customer_id=1)
    assert len(after["bookings"]) == len(before["bookings"])


@requires_live_llm
def test_full_cancel_conversation_actually_cancels_a_dedicated_test_booking():
    """Creates its OWN real booking first (never touches real seed data),
    then runs a real 2-turn cancel conversation through handle_turn() and
    verifies the booking is genuinely cancelled in Supabase afterward."""
    from langchain_openai import ChatOpenAI

    from app.agent.evidence import EvidenceStoreRegistry
    from app.agent.runtime import handle_turn
    from app.context.memory import ConversationMemory
    from app.db.supabase_client import get_supabase_client

    setup_evidence = EvidenceStore()
    opts = rt.get_service_options(setup_evidence, company_id=1, customer_id=2, pet_id=2, service_type="GROOMING")
    option_ref = opts["options"][0]["option_ref"]
    avail = rt.check_availability(
        setup_evidence, company_id=1, customer_id=2, pet_id=2,
        option_ref=option_ref, date="2026-09-22",
    )
    assert avail["slots"], f"no real slots available on the setup date — pick a different one: {avail}"
    # A date-only query's slots are all selection_required (2026-08-10 —
    # "verified" is not "selected"; see test_refactor_phase4_preview_confirm.py's
    # dedicated tests for that guard). This setup helper needs a REAL
    # booking, so re-check with the exact time of the first candidate to
    # get a non-selection_required slot_ref, exactly as a real customer
    # picking one would.
    exact_time = avail["slots"][0]["time"][:5]
    avail = rt.check_availability(
        setup_evidence, company_id=1, customer_id=2, pet_id=2,
        option_ref=option_ref, date="2026-09-22", time_preference=exact_time,
    )
    assert avail["slots"], f"the exact time re-check found nothing: {avail}"
    slot_ref = avail["slots"][0]["slot_ref"]
    preview = rt.preview_booking(
        setup_evidence, company_id=1, customer_id=2, pet_id=2, pet_name="Coco",
        option_ref=option_ref, slot_ref=slot_ref,
    )
    confirmed = rt.confirm_booking(setup_evidence, preview_ref=preview["preview_ref"])
    assert confirmed["ok"] is True
    booking_id = confirmed["data"]["booking_id"]

    memory = ConversationMemory()
    evidence_registry = EvidenceStoreRegistry()
    model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    company_context = {"company_id": 1, "company_name": "Happy Paws Center", "business_date": "2026-08-09"}
    phone = "+60123456702"  # Jason / Coco

    try:
        handle_turn(
            memory=memory, evidence_registry=evidence_registry, model=model,
            company_context=company_context, phone_number=phone, user_message="hi",
        )
        # Realistic conversational pacing, not a rigid fixed-turn script —
        # the model may reasonably take an extra turn to narrow down which
        # booking before previewing it. What matters is that it eventually
        # reaches a real preview and then a real cancellation, carrying the
        # SAME booking_ref forward the whole time (never re-triggering
        # "which booking?" once a specific target was already identified —
        # see ConversationState.pending_mutation_target).
        turns = [
            "I want to cancel Coco's grooming appointment on 2026-09-22.",
            "Yes, that one — please cancel it.",
            "Coco",
            "Yes",
        ]
        all_trace: list[dict] = []
        cancelled = False
        target_established = False
        for message in turns:
            result = handle_turn(
                memory=memory, evidence_registry=evidence_registry, model=model,
                company_context=company_context, phone_number=phone, user_message=message,
            )
            all_trace.extend(result.trace)
            # ok=True alone isn't enough — CONFIRMATION_REQUIRED (the preview
            # step) is also reported as ok=True (a successful call, just not
            # a completed cancellation yet). Only "OK" is the real mutation.
            if any(t["tool"] == "cancel_booking" and t["result"].get("code") == "OK" for t in result.trace):
                cancelled = True
                break
            if any(t["tool"] == "cancel_booking" and t["result"].get("code") == "CONFIRMATION_REQUIRED" for t in result.trace):
                target_established = True
            elif target_established:
                # Only a regression AFTER a specific target was already
                # identified is the actual bug (losing pending_mutation_target
                # across a turn) — an ambiguous FIRST attempt, before
                # anything has been narrowed down yet, is normal.
                ambiguous_again = [
                    t for t in result.trace
                    if t["tool"] == "cancel_booking" and t["result"].get("code") == "AMBIGUOUS_BOOKING"
                ]
                assert not ambiguous_again, f"lost track of the already-identified target — trace: {result.trace}"

        assert cancelled, f"model never completed the cancellation across {len(turns)} turns — trace: {all_trace}"

        client = get_supabase_client()
        row = (
            client.table("grooming_booking").select("booking_status")
            .eq("company_id", 1).eq("grooming_booking_id", booking_id).execute().data
        )
        assert row and row[0]["booking_status"] == "Cancelled"
    finally:
        client = get_supabase_client()
        payment_row = (
            client.table("grooming_booking").select("payment_id")
            .eq("company_id", 1).eq("grooming_booking_id", booking_id).execute().data
        )
        payment_id = payment_row[0]["payment_id"] if payment_row else None
        if payment_id:
            client.table("payment").delete().eq("company_id", 1).eq("payment_id", payment_id).execute()
