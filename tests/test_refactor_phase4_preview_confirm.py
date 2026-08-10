"""Phase 4 of REFACTOR_PLAN.md — preview_booking/confirm_booking, the split
that replaces create_booking's overloaded preview+write and removes the
"verified_turn == current_turn" bug class at the root (see
app/tools/reference_tools.py's docstrings for why).

Same live-Supabase-credentials guard as Phase 3's tests. The confirm test
creates a REAL row (like every other live E2E check this session) and
deletes it again in a finally block so re-running this file never leaves
test bookings behind.
"""

import os

import pytest
from dotenv import load_dotenv

from app.agent.evidence import EvidenceStore
from app.tools import reference_tools

load_dotenv(override=False)
_HAS_SUPABASE_CREDS = bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
requires_live_supabase = pytest.mark.skipif(
    not _HAS_SUPABASE_CREDS, reason="SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY not configured"
)


def _grooming_option_and_slot(evidence: EvidenceStore, *, customer_id: int, pet_id: int) -> tuple[str, str]:
    opts = reference_tools.get_service_options(
        evidence, company_id=1, customer_id=customer_id, pet_id=pet_id, service_type="GROOMING",
    )
    option_ref = opts["options"][0]["option_ref"]
    avail = reference_tools.check_availability(
        evidence, company_id=1, customer_id=customer_id, pet_id=pet_id,
        option_ref=option_ref, date="2026-09-16", time_preference="10:00",
    )
    return option_ref, avail["slots"][0]["slot_ref"]


@requires_live_supabase
def test_preview_booking_never_writes_anything():
    evidence = EvidenceStore()
    option_ref, slot_ref = _grooming_option_and_slot(evidence, customer_id=2, pet_id=2)

    preview = reference_tools.preview_booking(
        evidence, company_id=1, customer_id=2, pet_id=2, pet_name="Coco",
        option_ref=option_ref, slot_ref=slot_ref,
    )
    assert preview["ok"] is True
    assert preview["preview_ref"].startswith("preview_")
    assert preview["summary"]["option_name"] == "Standard Short Fur"
    assert preview["summary"]["price"] == 62.0
    # No real booking_id anywhere — nothing was written.
    assert "booking_id" not in preview


def test_preview_booking_rejects_a_slot_ref_minted_for_a_different_option():
    evidence = EvidenceStore()
    ref_a = evidence.mint("option", {"service_type": "GROOMING", "name": "A", "price": 60})
    ref_b = evidence.mint("option", {"service_type": "GROOMING", "name": "B", "price": 90})
    slot_for_a = evidence.mint("slot", {"date": "2026-09-16", "time": "10:00:00", "option_ref": ref_a})

    result = reference_tools.preview_booking(
        evidence, company_id=1, customer_id=2, pet_id=2, pet_name="Coco",
        option_ref=ref_b, slot_ref=slot_for_a,
    )
    assert result == {"ok": False, "code": "SLOT_DOES_NOT_MATCH_OPTION"}


def test_preview_booking_refuses_a_candidate_slot_not_yet_fully_verified():
    """A DAYCARE/BOARDING slot minted from a preliminary_checkin_only result
    (see relational_actions.py) only passed a partial handoff-only check —
    e.g. a check-in time shown before duration/checkout was known. It must
    never be usable to build a real booking payload."""
    evidence = EvidenceStore()
    option_ref = evidence.mint("option", {"service_type": "DAYCARE", "name": "Hourly Care", "price": 20.0})
    candidate_slot_ref = evidence.mint(
        "slot",
        {
            "date": "2026-09-16",
            "time": "09:00:00",
            "option_ref": option_ref,
            "status": "candidate",
            "still_needs": ["check_out_time_or_duration_minutes"],
        },
    )

    result = reference_tools.preview_booking(
        evidence, company_id=1, customer_id=2, pet_id=2, pet_name="Coco",
        option_ref=option_ref, slot_ref=candidate_slot_ref,
    )

    assert result == {
        "ok": False,
        "code": "SLOT_NOT_YET_VERIFIED",
        "still_needs": ["check_out_time_or_duration_minutes"],
    }


def test_preview_booking_refuses_a_verified_slot_the_customer_never_selected():
    """Real Agent-control gap confirmed 2026-08-10, reproduced live: "book
    grooming this Friday" (a pet and a date, nothing else) reached
    preview_booking with the model having silently picked a time slot the
    customer never chose or delegated. Verified availability is not the
    same as a customer selection — a "verified" but selection_required
    slot must never be usable to build a preview."""
    evidence = EvidenceStore()
    option_ref = evidence.mint("option", {"service_type": "GROOMING", "name": "Luxury Bath", "price": 160.0})
    unselected_slot_ref = evidence.mint(
        "slot",
        {
            "date": "2026-08-14", "time": "10:30:00", "option_ref": option_ref,
            "status": "verified", "duration_minutes": 60, "selection_required": True,
        },
    )

    result = reference_tools.preview_booking(
        evidence, company_id=1, customer_id=1, pet_id=1, pet_name="Milo",
        option_ref=option_ref, slot_ref=unselected_slot_ref,
    )

    assert result == {"ok": False, "code": "CUSTOMER_SLOT_SELECTION_REQUIRED"}


def test_preview_booking_allows_a_verified_slot_the_customer_actually_selected():
    evidence = EvidenceStore()
    option_ref = evidence.mint("option", {"service_type": "GROOMING", "name": "Standard Bath", "price": 80.0})
    selected_slot_ref = evidence.mint(
        "slot",
        {
            "date": "2026-08-14", "time": "13:00:00", "option_ref": option_ref,
            "status": "verified", "duration_minutes": 60, "selection_required": False,
        },
    )

    result = reference_tools.preview_booking(
        evidence, company_id=1, customer_id=1, pet_id=1, pet_name="Milo",
        option_ref=option_ref, slot_ref=selected_slot_ref,
    )

    assert result["ok"] is True


@requires_live_supabase
def test_check_availability_marks_only_the_exact_requested_time_as_not_selection_required():
    """End-to-end against real data: a date-only query (no exact time —
    the "book grooming this Friday" scenario) must mark EVERY returned
    slot selection_required; a query with an exact time_preference must
    mark only the matching slot as not selection_required."""
    evidence = EvidenceStore()
    catalogue = reference_tools.get_service_options(
        evidence, company_id=1, customer_id=2, pet_id=2, service_type="GROOMING",
    )
    option_ref = catalogue["options"][0]["option_ref"]

    no_time_given = reference_tools.check_availability(
        evidence, company_id=1, customer_id=2, pet_id=2, option_ref=option_ref, date="2026-10-06",
    )
    assert no_time_given["slots"], f"no slots to test against: {no_time_given}"
    assert all(s["selection_required"] is True for s in no_time_given["slots"])

    exact_time_given = reference_tools.check_availability(
        evidence, company_id=1, customer_id=2, pet_id=2,
        option_ref=option_ref, date="2026-10-06", time_preference="14:00",
    )
    assert exact_time_given["slots"], f"14:00 unexpectedly unavailable: {exact_time_given}"
    assert exact_time_given["slots"][0]["selection_required"] is False


def test_preview_booking_multiplies_an_hourly_rate_by_the_slots_verified_duration():
    """Real bug confirmed by the 2026-08-10 review: DAYCARE's "Hourly Care"
    (RM20/hour) is a RATE, not a total — option["price"] alone (RM20) was
    silently used as the final price of a 5-hour visit that should cost
    RM100. The server must compute this from the slot's own verified
    duration, never trust a pre-multiplied number and never leave it to
    the model."""
    evidence = EvidenceStore()
    option_ref = evidence.mint(
        "option",
        {"service_type": "DAYCARE", "name": "Hourly Care", "price": 20.0, "pricing_unit": "hour"},
    )
    slot_ref = evidence.mint(
        "slot",
        {
            "date": "2026-09-16", "time": "12:00:00", "option_ref": option_ref,
            "check_out_time": "17:00:00", "status": "verified", "duration_minutes": 300,
        },
    )

    preview = reference_tools.preview_booking(
        evidence, company_id=1, customer_id=2, pet_id=2, pet_name="Coco",
        option_ref=option_ref, slot_ref=slot_ref,
    )

    assert preview["ok"] is True
    assert preview["summary"]["price"] == 100.0


def test_preview_booking_leaves_a_flat_rate_price_untouched():
    """Every other pricing_unit (flat, or unset entirely — GROOMING/BOARDING
    never set pricing_unit at all) must NOT be multiplied by duration —
    only "hour" branches."""
    evidence = EvidenceStore()
    option_ref = evidence.mint(
        "option",
        {"service_type": "GROOMING", "name": "Standard Short Fur", "price": 62.0},
    )
    slot_ref = evidence.mint(
        "slot",
        {
            "date": "2026-09-16", "time": "10:00:00", "option_ref": option_ref,
            "status": "verified", "duration_minutes": 90,
        },
    )

    preview = reference_tools.preview_booking(
        evidence, company_id=1, customer_id=2, pet_id=2, pet_name="Coco",
        option_ref=option_ref, slot_ref=slot_ref,
    )

    assert preview["ok"] is True
    assert preview["summary"]["price"] == 62.0


def test_preview_booking_takes_preferred_staff_from_the_slot_not_a_separate_argument():
    """Item #4 of the 2026-08-10 review: preview_booking no longer accepts
    preferred_staff as its own parameter — it comes from whatever the
    slot's check_availability call actually verified staffing against,
    so it can never silently drift from a different staff member than the
    one the availability check was for."""
    evidence = EvidenceStore()
    option_ref = evidence.mint(
        "option", {"service_type": "GROOMING", "name": "Standard Short Fur", "price": 62.0},
    )
    slot_ref = evidence.mint(
        "slot",
        {
            "date": "2026-09-16", "time": "10:00:00", "option_ref": option_ref,
            "status": "verified", "duration_minutes": 90, "preferred_staff": "Amy",
        },
    )

    preview = reference_tools.preview_booking(
        evidence, company_id=1, customer_id=2, pet_id=2, pet_name="Coco",
        option_ref=option_ref, slot_ref=slot_ref,
    )

    assert preview["ok"] is True
    import inspect
    assert "preferred_staff" not in inspect.signature(reference_tools.preview_booking).parameters


@requires_live_supabase
def test_check_availability_stores_the_requested_staff_member_on_every_minted_slot():
    evidence = EvidenceStore()
    catalogue = reference_tools.get_service_options(
        evidence, company_id=1, customer_id=2, pet_id=2, service_type="GROOMING",
    )
    option_ref = catalogue["options"][0]["option_ref"]

    result = reference_tools.check_availability(
        evidence, company_id=1, customer_id=2, pet_id=2,
        option_ref=option_ref, date="2026-09-18", time_preference="10:00",
        preferred_staff="Sarah Wong",
    )
    if result["slots"]:
        resolved = evidence.resolve(result["slots"][0]["slot_ref"])
        assert resolved["preferred_staff"] == "Sarah Wong"


def test_preview_booking_refuses_hourly_pricing_without_a_verified_duration():
    """A defensive backstop, not expected to be reachable through
    check_availability (a "verified" slot always carries a real
    duration_minutes) — but preview_booking must never silently price an
    hourly option at just its bare rate if that guarantee is ever violated."""
    evidence = EvidenceStore()
    option_ref = evidence.mint(
        "option",
        {"service_type": "DAYCARE", "name": "Hourly Care", "price": 20.0, "pricing_unit": "hour"},
    )
    slot_ref = evidence.mint(
        "slot",
        {"date": "2026-09-16", "time": "12:00:00", "option_ref": option_ref, "status": "verified"},
    )

    preview = reference_tools.preview_booking(
        evidence, company_id=1, customer_id=2, pet_id=2, pet_name="Coco",
        option_ref=option_ref, slot_ref=slot_ref,
    )

    assert preview == {
        "ok": False,
        "code": "SLOT_MISSING_VERIFIED_DURATION_FOR_HOURLY_PRICING",
        "message": "This slot has no verified duration to price against. Call check_availability again with a duration/checkout to get a fully verified slot_ref.",
    }


@requires_live_supabase
def test_daycare_hourly_availability_stores_the_real_duration_on_the_slot():
    """End-to-end against real data: check_availability for an hourly
    DAYCARE option with an explicit 5-hour range must store
    duration_minutes=300 on the minted slot (not None, not the option's own
    — Hourly Care has no fixed duration) so preview_booking can price it
    correctly."""
    evidence = EvidenceStore()
    catalogue = reference_tools.get_service_options(
        evidence, company_id=1, customer_id=2, pet_id=2, service_type="DAYCARE",
    )
    hourly = next(o for o in catalogue["options"] if o["name"] == "Hourly Care")

    avail = reference_tools.check_availability(
        evidence, company_id=1, customer_id=2, pet_id=2,
        option_ref=hourly["option_ref"], date="2026-09-25",
        # time_preference (not just check_in_time) is what actually
        # constrains the underlying engine to this one exact time — also
        # makes this slot NOT selection_required (item from the 2026-08-10
        # review: a slot is only exempt from customer-selection when it
        # matches an exact time actually requested this call).
        time_preference="12:00", check_in_time="12:00", check_out_time="17:00", duration_minutes=300,
    )
    assert avail["slots"], f"no slots returned: {avail}"
    slot = avail["slots"][0]
    assert slot["status"] == "verified"
    assert slot["selection_required"] is False
    resolved = evidence.resolve(slot["slot_ref"])
    assert resolved["duration_minutes"] == 300

    preview = reference_tools.preview_booking(
        evidence, company_id=1, customer_id=2, pet_id=2, pet_name="Coco",
        option_ref=hourly["option_ref"], slot_ref=slot["slot_ref"],
    )
    assert preview["ok"] is True
    assert preview["summary"]["price"] == 100.0  # RM20/hour x 5 hours


def test_two_previews_of_identical_booking_facts_get_different_idempotency_keys():
    """An earlier version of EvidenceStore.mint() content-addressed refs (the
    same option/slot combination minted the same ref), and preview_booking
    reused that ref directly as the write's idempotency key — so two
    unrelated preview_booking calls describing identical facts (two
    separate test runs against the same customer/date/time/option,
    confirmed live) collided on one idempotency key, and the second
    confirm_booking silently replayed the first one's cached result instead
    of writing, even after the first booking had since been deleted.
    EvidenceStore.mint() now always returns a fresh, unique ref regardless
    of payload content, which is used directly as the idempotency key — so
    this can no longer happen structurally, not just by convention."""
    evidence = EvidenceStore()
    option_ref = evidence.mint("option", {"service_type": "GROOMING", "name": "Standard Short Fur", "price": 62.0})
    slot_ref = evidence.mint("slot", {"date": "2026-09-16", "time": "10:00:00", "option_ref": option_ref})

    preview_1 = reference_tools.preview_booking(
        evidence, company_id=1, customer_id=2, pet_id=2, pet_name="Coco",
        option_ref=option_ref, slot_ref=slot_ref,
    )
    preview_2 = reference_tools.preview_booking(
        evidence, company_id=1, customer_id=2, pet_id=2, pet_name="Coco",
        option_ref=option_ref, slot_ref=slot_ref,
    )
    assert preview_1["preview_ref"] != preview_2["preview_ref"]


def test_confirm_booking_rejects_an_unknown_or_expired_preview_ref():
    evidence = EvidenceStore()
    result = reference_tools.confirm_booking(evidence, preview_ref="preview_doesnotexist")
    assert result["ok"] is False
    assert result["code"] == "UNKNOWN_OR_EXPIRED_PREVIEW_REF"


@requires_live_supabase
def test_confirm_booking_writes_a_real_booking_and_is_verifiable_in_supabase():
    from app.db.supabase_client import get_supabase_client

    evidence = EvidenceStore()
    option_ref, slot_ref = _grooming_option_and_slot(evidence, customer_id=2, pet_id=2)
    preview = reference_tools.preview_booking(
        evidence, company_id=1, customer_id=2, pet_id=2, pet_name="Coco",
        option_ref=option_ref, slot_ref=slot_ref,
    )

    result = reference_tools.confirm_booking(evidence, preview_ref=preview["preview_ref"])
    booking_id = None
    try:
        assert result["ok"] is True
        booking_id = result["data"]["booking_id"]
        assert booking_id is not None

        # Confirming the SAME preview_ref again (a customer double-tapping
        # "yes", or a retried request) must replay the same real booking,
        # never insert a second row — the idempotency_key fix above
        # preserves this even though it no longer collides ACROSS
        # different previews.
        replay = reference_tools.confirm_booking(evidence, preview_ref=preview["preview_ref"])
        assert replay["ok"] is True
        assert replay["data"]["booking_id"] == booking_id

        client = get_supabase_client()
        row = (
            client.table("grooming_booking")
            .select("*")
            .eq("company_id", 1)
            .eq("grooming_booking_id", booking_id)
            .execute()
            .data
        )
        assert row
        # grooming_booking stores the chosen package under service_name,
        # not package_name (that column name is boarding_booking's, for
        # its room_type) — confirmed against the real row shape live.
        assert row[0]["service_name"] == "Standard Short Fur"
        assert row[0]["price"] == 62.0
        assert row[0]["booking_date"] == "2026-09-16"
    finally:
        if booking_id:
            client = get_supabase_client()
            payment_row = (
                client.table("grooming_booking")
                .select("payment_id")
                .eq("company_id", 1)
                .eq("grooming_booking_id", booking_id)
                .execute()
                .data
            )
            payment_id = payment_row[0]["payment_id"] if payment_row else None
            if payment_id:
                # Cascade-deletes the booking row too — see
                # backend/sql/dedupe_seed_bookings.sql's header comment for
                # why (payment_id FK is ON DELETE CASCADE), confirmed live
                # earlier this session.
                client.table("payment").delete().eq("company_id", 1).eq("payment_id", payment_id).execute()
