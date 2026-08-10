from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from app.context.runtime_context import resolve_identity
from app.context.state import ConversationState
from app.db import relational_actions
from app.db.customer_context import canonical_phone_number, phones_match, validate_phone_number
from app.db.date_normalization import extract_customer_date, parse_week_range
from app.db.relational_actions import (
    _time_slots_for_day,
    _room_capacity_status,
    _serialize_daycare_booking,
    check_available_slots,
    create_pet,
    register_loyalty_member,
)
from app.db.slot_holds import SlotHoldRegistry
from app.db.customer_context import CustomerContext
from app.db.time_normalization import extract_duration_minutes, extract_time_from_message, extract_time_range
from app.documents import service as document_service
from app.tools import availability_tools


def _resolve_identity_for_test(company_id: str, state) -> dict:
    """Same calling convention app.agent.runtime.handle_turn() uses — these
    tests exercise the real, shared app.context.runtime_context.
    resolve_identity() directly rather than through the now-deleted
    PawfectOrchestrator._resolve_identity adapter."""
    return resolve_identity(
        company_id, state,
        tool_executor=ThreadPoolExecutor(max_workers=2),
        timeout_seconds=10,
        cache_single_pet=lambda s, pets: None,
    )


def test_phone_identity_never_matches_a_short_suffix():
    assert phones_match("+60 12-345 6705", "012-345 6705")
    assert canonical_phone_number("012-345 6705") == "+60123456705"
    assert not phones_match("+60 12-345 6705", "6705")
    try:
        validate_phone_number("6705")
    except ValueError:
        pass
    else:
        raise AssertionError("short phone fragments must be rejected")


def test_create_pet_database_boundary_refuses_missing_or_species_as_breed():
    context = CustomerContext(company_id=1, resolved_customer_id=9)

    missing = create_pet(context, pet_name="Milo", pet_type="dog", height_cm=30, breed="")
    species = create_pet(context, pet_name="Milo", pet_type="dog", height_cm=30, breed="dog")

    assert missing["status"] == "missing_information"
    assert missing["data"]["missing_fields"] == ["breed"]
    assert species["status"] == "missing_information"
    assert species["data"]["missing_fields"] == ["breed"]


def test_boarding_with_no_room_at_all_still_hard_blocks():
    """room_type is the one genuinely unconditional requirement — nothing
    can be checked (not even a preliminary check-in time) without knowing
    which room. check_out_date is no longer listed here: it's only
    reported missing once room_type is actually known (see
    test_boarding_room_known_offers_preliminary_checkin_times below) —
    reporting it as missing before that would be misleading, the customer
    needs to state the room first regardless."""
    context = CustomerContext(company_id=1)

    boarding = check_available_slots(
        context,
        {"service_type": "BOARDING", "entities": {"preferred_date": "2026-08-10"}},
    )

    assert boarding["status"] == "missing_information"
    assert boarding["data"]["available_slots"] == []
    assert boarding["data"]["missing_fields"] == ["room_type"]


def test_boarding_room_known_offers_preliminary_checkin_times(monkeypatch):
    """Explicit product decision: once the customer has picked a room,
    show real check-in times immediately even without a check_out_date yet
    — instead of blocking on "how many nights?" before ever showing a
    single time. Confirmed live this was producing a genuine contradiction
    (a time shown as available, then reported unavailable one turn later
    once the missing piece was finally supplied) since the two turns were
    validating completely different things (a hard missing_information
    block that skipped straight to nothing, vs. the real full validation)."""
    monkeypatch.setattr(
        relational_actions,
        "_business_hours_for_date",
        lambda company_id, target_date: ("09:30", "18:30", None),
    )
    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: object())
    monkeypatch.setattr(relational_actions, "_shared_booking_holds", lambda client, company_id: None)
    monkeypatch.setattr(
        relational_actions,
        "_staff_day_roster",
        lambda client, company_id, target_date, service_type=None: [
            {"staff_id": 7, "staff_name": "Ari"}
        ],
    )
    monkeypatch.setattr(
        relational_actions, "_cross_service_staff_bookings",
        lambda client, company_id, staff_ids, date_str: [],
    )
    context = CustomerContext(company_id=1)

    result = check_available_slots(
        context,
        {
            "service_type": "BOARDING",
            "entities": {"preferred_date": "2026-08-10", "room_type": "Sirius Room"},
        },
    )

    assert result["status"] == "success"
    assert result["data"]["preliminary"] is True
    assert result["data"]["still_needs"] == ["check_out_date"]
    assert "09:30:00" in result["data"]["available_slots"]


def test_daycare_with_nothing_stated_offers_preliminary_checkin_times(monkeypatch):
    """Same product decision, DAYCARE side: a bare check-in enquiry (no
    duration/checkout yet) now shows real drop-off times instead of
    blocking on "how long will you stay?" first."""
    monkeypatch.setattr(
        relational_actions,
        "_business_hours_for_date",
        lambda company_id, target_date: ("09:30", "18:30", None),
    )
    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: object())
    monkeypatch.setattr(relational_actions, "_shared_booking_holds", lambda client, company_id: None)
    monkeypatch.setattr(
        relational_actions,
        "_staff_day_roster",
        lambda client, company_id, target_date, service_type=None: [
            {"staff_id": 7, "staff_name": "Ari"}
        ],
    )
    monkeypatch.setattr(
        relational_actions, "_cross_service_staff_bookings",
        lambda client, company_id, staff_ids, date_str: [],
    )
    context = CustomerContext(company_id=1)

    daycare = check_available_slots(
        context,
        {"service_type": "DAYCARE", "entities": {"preferred_date": "2026-08-10"}},
    )

    assert daycare["status"] == "success"
    assert daycare["data"]["preliminary"] is True
    assert daycare["data"]["still_needs"] == ["check_out_time_or_duration_minutes"]
    assert "09:30:00" in daycare["data"]["available_slots"]


def test_1730_can_be_a_daycare_pickup_without_being_a_late_start(monkeypatch):
    monkeypatch.setattr(
        relational_actions,
        "_business_hours_for_date",
        lambda company_id, target_date: ("09:30", "18:30", None),
    )
    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: object())
    monkeypatch.setattr(relational_actions, "_shared_booking_holds", lambda client, company_id: None)
    monkeypatch.setattr(
        relational_actions,
        "_staff_day_roster",
        lambda client, company_id, target_date, service_type=None: [
            {"staff_id": 7, "staff_name": "Ari"}
        ],
    )
    monkeypatch.setattr(
        relational_actions,
        "_cross_service_staff_bookings",
        lambda client, company_id, staff_ids, date_str: [],
    )
    context = CustomerContext(company_id=1)

    pickup_choices = check_available_slots(
        context,
        {
            "service_type": "DAYCARE",
            "entities": {
                "preferred_date": "2026-08-10",
                "preferred_time": "14:30",
                "check_in_time": "14:30",
                "selection_target": "CHECK_OUT",
            },
        },
    )
    late_starts = check_available_slots(
        context,
        {
            "service_type": "DAYCARE",
            "entities": {
                "preferred_date": "2026-08-10",
                "preferred_time": "17:30",
                "duration_minutes": 180,
            },
        },
    )

    assert "17:30:00" in pickup_choices["data"]["available_check_out_times"]
    assert "18:30:00" in pickup_choices["data"]["available_check_out_times"]
    assert pickup_choices["data"]["available_slots"] == []
    assert "17:30:00" not in late_starts["data"]["available_slots"]


def test_daycare_period_check_in_with_fixed_checkout_offers_a_slot_near_closing(monkeypatch):
    """Regression: "daycare tomorrow morning, pick up at 6pm" (time="morning",
    a period word, plus a real check_out_time) previously ignored
    check_out_time entirely whenever check-in wasn't already an exact clock
    string, silently falling back to the flat 180-minute DAYCARE default.
    A candidate close to closing (whose true, short duration to the fixed
    checkout easily fits) was wrongly excluded because the fictitious
    3-hour window overran business hours."""
    monkeypatch.setattr(
        relational_actions,
        "_business_hours_for_date",
        lambda company_id, target_date: ("09:00", "18:30", None),
    )
    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: object())
    monkeypatch.setattr(relational_actions, "_shared_booking_holds", lambda client, company_id: None)
    monkeypatch.setattr(
        relational_actions,
        "_staff_day_roster",
        lambda client, company_id, target_date, service_type=None: [
            {"staff_id": 7, "staff_name": "Ari"}
        ],
    )
    monkeypatch.setattr(
        relational_actions,
        "_cross_service_staff_bookings",
        lambda client, company_id, staff_ids, date_str: [],
    )
    context = CustomerContext(company_id=1)

    result = check_available_slots(
        context,
        {
            "service_type": "DAYCARE",
            "entities": {
                "preferred_date": "2026-08-10",
                "preferred_time": "morning",
                "check_out_time": "18:00",
            },
        },
    )

    assert result["status"] == "success"
    # True duration for a 17:30 start to the fixed 18:00 checkout is only
    # 30 minutes — well within business hours — so it must be offered.
    assert "17:30:00" in result["data"]["available_slots"]


def test_daycare_period_check_in_with_fixed_checkout_still_detects_a_real_conflict(monkeypatch):
    """Flip side of the fix above: an early candidate's TRUE duration (to the
    fixed checkout) must be checked for staff conflicts, not just a
    fictitious 3-hour window — otherwise a real conflict outside that
    3-hour window is missed and an unavailable start gets offered anyway."""
    monkeypatch.setattr(
        relational_actions,
        "_business_hours_for_date",
        lambda company_id, target_date: ("09:00", "18:30", None),
    )
    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: object())
    monkeypatch.setattr(relational_actions, "_shared_booking_holds", lambda client, company_id: None)
    monkeypatch.setattr(
        relational_actions,
        "_staff_day_roster",
        lambda client, company_id, target_date, service_type=None: [
            {"staff_id": 7, "staff_name": "Ari"}
        ],
    )
    # Ari already has a 14:00-15:00 booking — outside the OLD fictitious
    # 3-hour window for a 09:00 start (09:00-12:00), but well inside the
    # TRUE full span to an 18:00 checkout (09:00-18:00).
    monkeypatch.setattr(
        relational_actions,
        "_cross_service_staff_bookings",
        lambda client, company_id, staff_ids, date_str: [{
            "staff_id": 7,
            "check_in_time": "14:00:00",
            "check_out_time": "15:00:00",
            "booking_status": "Scheduled",
            "_service_type": "DAYCARE",
        }],
    )
    context = CustomerContext(company_id=1)

    result = check_available_slots(
        context,
        {
            "service_type": "DAYCARE",
            "entities": {
                "preferred_date": "2026-08-10",
                "preferred_time": "morning",
                "check_out_time": "18:00",
            },
        },
    )

    assert result["status"] == "success"
    assert "09:00:00" not in result["data"]["available_slots"]
    # A start after the conflicting booking ends is still genuinely free.
    assert "15:00:00" in result["data"]["available_slots"]


def test_daycare_offers_a_long_visit_when_staff_cover_it_between_them(monkeypatch):
    """Confirmed live: requiring ONE staff member free for an entire long
    visit made a real 6-hour DAYCARE request return zero slots for the
    whole day, even though the business's total staffing that day was
    ample — each of several staff members merely had one short unrelated
    booking somewhere in the middle, at a DIFFERENT time each, so no
    single person was free continuously but the roster together covered
    every moment. Flip side of the single-staff real-conflict test above:
    with multiple staff whose gaps don't overlap, the visit must be
    offered."""
    monkeypatch.setattr(
        relational_actions,
        "_business_hours_for_date",
        lambda company_id, target_date: ("09:30", "18:30", None),
    )
    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: object())
    monkeypatch.setattr(relational_actions, "_shared_booking_holds", lambda client, company_id: None)
    monkeypatch.setattr(
        relational_actions,
        "_staff_day_roster",
        lambda client, company_id, target_date, service_type=None: [
            {"staff_id": 1, "staff_name": "A"},
            {"staff_id": 2, "staff_name": "B"},
        ],
    )
    # A is busy 11:00-12:00; B is busy 14:00-15:00 — neither window overlaps
    # the other, so together they cover the whole 09:30-15:30 visit even
    # though neither is free for its full length alone.
    monkeypatch.setattr(
        relational_actions,
        "_cross_service_staff_bookings",
        lambda client, company_id, staff_ids, date_str: [
            {"staff_id": 1, "check_in_time": "11:00:00", "check_out_time": "12:00:00",
             "booking_status": "Scheduled", "_service_type": "DAYCARE"},
            {"staff_id": 2, "check_in_time": "14:00:00", "check_out_time": "15:00:00",
             "booking_status": "Scheduled", "_service_type": "DAYCARE"},
        ],
    )
    context = CustomerContext(company_id=1)

    result = check_available_slots(
        context,
        {
            "service_type": "DAYCARE",
            "entities": {"preferred_date": "2026-08-10", "duration_minutes": 360},
        },
    )

    assert result["status"] == "success"
    assert "09:30:00" in result["data"]["available_slots"]


def test_half_hour_slots_are_available_even_when_business_opens_on_the_hour():
    slots = _time_slots_for_day("09:00", "18:30")

    assert "09:30:00" in slots
    assert "17:30:00" in slots


def test_daycare_availability_rejects_checkout_not_after_checkin(monkeypatch):
    monkeypatch.setattr(
        relational_actions,
        "_business_hours_for_date",
        lambda company_id, target_date: ("09:00", "18:00", None),
    )
    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: object())
    monkeypatch.setattr(relational_actions, "_shared_booking_holds", lambda client, company_id: None)
    monkeypatch.setattr(
        relational_actions,
        "_staff_day_roster",
        lambda client, company_id, target_date, service_type=None: [
            {"staff_id": 7, "staff_name": "Ari"}
        ],
    )
    monkeypatch.setattr(
        relational_actions,
        "_cross_service_staff_bookings",
        lambda client, company_id, staff_ids, date_str: [],
    )

    result = check_available_slots(
        CustomerContext(company_id=1),
        {
            "service_type": "DAYCARE",
            "entities": {
                "preferred_date": "2026-08-10",
                "preferred_time": "10:00",
                "check_out_time": "09:00",
            },
        },
    )

    assert result["status"] == "error"
    assert result["data"]["available_slots"] == []
    assert "must be after" in result["error"]


def test_unknown_service_does_not_silently_return_grooming_slots():
    result = check_available_slots(
        CustomerContext(company_id=1),
        {"service_type": "DAY_CARE", "entities": {"preferred_date": "2026-08-10"}},
    )

    assert result["status"] == "error"
    assert result["data"]["available_slots"] == []


def test_read_side_active_statuses_match_database_conflict_statuses():
    assert relational_actions._booking_blocks_availability({"booking_status": "Pending"})
    assert relational_actions._booking_blocks_availability({"booking_status": "Scheduled"})
    assert not relational_actions._booking_blocks_availability({"booking_status": "Done"})
    assert not relational_actions._booking_blocks_availability({"booking_status": "mystery-status"})


def test_boarding_checkout_date_is_free_for_another_pet_service():
    pet_bookings = [
        {
            "_service_type": "GROOMING",
            "grooming_booking_id": 12,
            "booking_date": "2026-08-12",
            "booking_time": "10:00",
            "booking_status": "Scheduled",
        }
    ]

    assert relational_actions._pet_free_for_boarding_stay(
        pet_bookings,
        date(2026, 8, 10),
        date(2026, 8, 12),
        check_in_time="14:00",
        check_out_time="10:00",
    )

    pet_bookings[0]["booking_time"] = "09:00"
    assert not relational_actions._pet_free_for_boarding_stay(
        pet_bookings,
        date(2026, 8, 10),
        date(2026, 8, 12),
        check_in_time="14:00",
        check_out_time="10:00",
    )


def test_grooming_booking_interval_uses_the_rows_own_duration_not_a_hardcoded_90():
    """Real bug confirmed 2026-08-10: this used to hardcode 90 minutes
    regardless of the row's own duration_minutes column, while the SQL
    write-side conflict check (coalesce(b.duration_minutes, 90)) uses the
    real stored value — a grooming_booking row with any other duration
    (the schema allows 1-1440) made read-side availability disagree with
    what the database actually enforces."""
    assert relational_actions._booking_interval(
        {"booking_time": "10:00", "duration_minutes": 150}, "GROOMING",
    ) == [(600, 750)]
    # No duration_minutes stored at all -> Python's own 60-minute default
    # (service_duration_minutes("GROOMING")) — not reachable for any row
    # written through the app itself (create_booking always writes an
    # explicit value); the SQL schema/RPC's own coalesce(..., 90) fallback
    # was deliberately left unchanged (a live migration is a separate
    # decision).
    assert relational_actions._booking_interval(
        {"booking_time": "10:00"}, "GROOMING",
    ) == [(600, 660)]


def test_boarded_pet_blocks_a_mid_stay_grooming_slot_matching_sql_semantics():
    """Real bug confirmed 2026-08-10: this used to only treat a boarded
    pet as occupied during its exact check-in/check-out handoff instants,
    so a genuine mid-stay grooming slot would be reported "available" here
    even though the SQL write-side conflict check (pet_has_conflicting_
    booking, full check-in-to-check-out overlap, unconditional — see
    backend/sql/booking_conflict_prevention_migration.sql) would reject it
    at write time. Matches SQL's stricter full-stay-occupied semantics."""
    pet_bookings = [
        {
            "_service_type": "BOARDING",
            "boarding_booking_id": 1,
            "check_in_date": "2026-08-10",
            "check_out_date": "2026-08-12",
            "booking_status": "Scheduled",
        }
    ]
    # 2026-08-11 (a day squarely inside the stay, nowhere near either
    # handoff) must now be blocked for ANY other service that day.
    assert not relational_actions._pet_free_for_interval(
        pet_bookings, "2026-08-11", 600, 690,  # 10:00-11:30
    )
    # A day genuinely outside the stay is unaffected.
    assert relational_actions._pet_free_for_interval(
        pet_bookings, "2026-08-13", 600, 690,
    )


def test_new_boarding_request_blocked_by_a_mid_stay_grooming_slot_matching_sql_semantics():
    """The reverse direction of the test above: an EXISTING grooming
    appointment squarely inside a NEW boarding stay's span must now block
    it too — the SQL check overlaps the new booking's full span against
    every other booking unconditionally, in both directions."""
    pet_bookings = [
        {
            "_service_type": "GROOMING",
            "grooming_booking_id": 1,
            "booking_date": "2026-08-11",
            "booking_time": "10:00",
            "booking_status": "Scheduled",
        }
    ]
    assert not relational_actions._pet_free_for_boarding_stay(
        pet_bookings,
        date(2026, 8, 10),
        date(2026, 8, 12),
        check_in_time="14:00",
        check_out_time="10:00",
    )


def test_range_availability_threads_customer_pet_and_staff_constraints(monkeypatch):
    calls = []

    class Repo:
        def check_availability(self, company_id, *, service, date, time, intent_json=None):
            calls.append(intent_json)
            return {"status": "success", "data": {"available_slots": ["10:00:00"]}}

    monkeypatch.setattr(availability_tools, "get_relational_repository", lambda: Repo())

    result = availability_tools.check_availability_range.invoke(
        {
            "company_id": 1,
            "service_type": "DAYCARE",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "duration_minutes": 240,
            "customer_id": 31,
            "pet_id": 44,
            "exclude_booking_id": 55,
            "preferred_staff": "Ari",
        }
    )

    assert result["status"] == "success"
    assert calls == [
        {
            "entities": {
                "duration_minutes": 240,
                "customer_id": 31,
                "pet_id": 44,
                "exclude_booking_id": 55,
                "preferred_staff": "Ari",
            }
        }
    ]


def test_overlapping_room_holds_conflict_even_when_date_ranges_differ():
    holds = SlotHoldRegistry(ttl_seconds=60)
    holds.acquire((1, "BOARDING_ROOM", "Mars Room", "2026-08-01", "2026-08-03"), "10")

    assert holds.count_overlapping_room_holds(
        1, "Mars Room", "2026-08-02", "2026-08-04", "11"
    ) == 1
    assert holds.count_overlapping_room_holds(
        1, "Mars Room", "2026-08-03", "2026-08-04", "11"
    ) == 0


def test_staff_holds_block_only_the_overlapping_employee_interval():
    holds = SlotHoldRegistry(ttl_seconds=60)
    holds.acquire((1, "STAFF", 7, "2026-08-10", 600, 690, "GROOMING"), "10")

    assert holds.staff_held_by_other(1, 7, "2026-08-10", 630, 720, "11")
    assert not holds.staff_held_by_other(1, 8, "2026-08-10", 630, 720, "11")
    assert not holds.staff_held_by_other(1, 7, "2026-08-10", 690, 720, "11")


def test_one_held_employee_does_not_hide_another_available_employee(monkeypatch):
    holds = SlotHoldRegistry(ttl_seconds=60)
    holds.acquire((1, "STAFF", 7, "2026-08-10", 540, 630, "DAYCARE"), "10")
    monkeypatch.setattr(relational_actions, "SLOT_HOLDS", holds)
    monkeypatch.setattr(
        relational_actions,
        "_business_hours_for_date",
        lambda company_id, target_date: ("09:00", "12:00", None),
    )
    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: object())
    monkeypatch.setattr(relational_actions, "_shared_booking_holds", lambda client, company_id: None)
    monkeypatch.setattr(relational_actions, "_acquire_shared_hold", lambda client, payload: None)
    monkeypatch.setattr(
        relational_actions,
        "_staff_day_roster",
        lambda client, company_id, target_date, service_type=None: [
            {"staff_id": 7, "staff_name": "Ari"},
            {"staff_id": 8, "staff_name": "Bea"},
        ],
    )
    monkeypatch.setattr(
        relational_actions,
        "_cross_service_staff_bookings",
        lambda client, company_id, staff_ids, date_str: [],
    )

    result = check_available_slots(
        CustomerContext(company_id=1, resolved_customer_id=11),
        {
            "service_type": "GROOMING",
            "entities": {
                "preferred_date": "2026-08-10",
                "preferred_time": "09:00",
                "duration_minutes": 90,
            },
        },
    )

    assert "09:00:00" in result["data"]["available_slots"]
    assert result["data"]["slot_held"]["staff_id"] == 8


def test_reschedule_exclusion_is_scoped_to_the_booking_service():
    rows = [
        {"_service_type": "GROOMING", "grooming_booking_id": 5},
        {"_service_type": "DAYCARE", "daycare_booking_id": 5},
    ]

    remaining = relational_actions._without_excluded_booking(rows, "GROOMING", 5)

    assert remaining == [{"_service_type": "DAYCARE", "daycare_booking_id": 5}]


def test_reschedule_probe_excludes_a_groomings_own_current_slot(monkeypatch):
    """Regression: a GROOMING (or DAYCARE) reschedule's own pre-check probe
    previously only excluded the booking's own row from the availability
    check for BOARDING, so shifting a grooming appointment to a new time
    that overlaps its OWN current slot (same staff, same day — the ordinary
    "push my appointment back 15 minutes" case) saw that slot as already
    taken by itself and was wrongly rejected, even though the atomic write
    RPC already excludes the booking correctly and would accept it."""
    booking = {
        "booking_id": 501,
        "service_type": "GROOMING",
        "pet_id": 9,
        "pet_name": "Milo",
        "staff_id": 7,
        "booking_date": "2026-08-10",
        "booking_time": "10:00:00",
    }
    monkeypatch.setattr(
        relational_actions, "_locate_active_customer_booking", lambda context, intent_json: (booking, None)
    )
    monkeypatch.setattr(relational_actions, "_verify_booking_belongs_to_customer", lambda context, b: True)

    class _OkVaccination:
        ok = True
        errors: list[str] = []

    monkeypatch.setattr(
        "app.validation.validator.check_vaccination_eligibility",
        lambda *a, **kw: _OkVaccination(),
    )
    monkeypatch.setattr(
        relational_actions,
        "_business_hours_for_date",
        lambda company_id, target_date: ("09:00", "18:00", None),
    )

    captured_probes = []

    def fake_slot_is_available(context, intent_json, *, booking_date, booking_time):
        captured_probes.append(dict(intent_json["entities"]))
        return True

    monkeypatch.setattr(relational_actions, "_slot_is_available", fake_slot_is_available)
    monkeypatch.setattr(
        relational_actions, "_service_table", lambda service_type: "grooming_booking"
    )

    class _FakeTable:
        def update(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def execute(self):
            return SimpleNamespace(data=[{**booking, "booking_date": "2026-08-10", "booking_time": "10:15:00"}])

    class _FakeClient:
        def table(self, _name):
            return _FakeTable()

    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: _FakeClient())

    context = CustomerContext(company_id=1)
    context.resolved_customer_id = 42

    relational_actions.reschedule_booking(
        context,
        {
            "entities": {
                "confirm_pet_name": "milo",
                "new_preferred_date": "2026-08-10",
                "new_preferred_time": "10:15",
            }
        },
    )

    assert captured_probes, "expected the availability probe to run"
    probe_entities = captured_probes[0]
    assert probe_entities["exclude_booking_id"] == 501
    assert probe_entities["pet_id"] == 9


def test_boarding_reschedule_preserves_stay_length_when_checkout_date_is_not_restated(monkeypatch):
    """Regression: "reschedule from 25 aug to 28 aug, remain the time" (a
    customer keeping the same stay length while only shifting the check-in
    date) previously had no server-side fallback — BOARDING reschedule
    required new_check_out_date to be explicitly supplied, so the model
    computed one itself (28 Aug + the original 3 nights = 31 Aug), which can
    never pass resolve_datetime verification since the customer's own words
    never named "31 Aug" — a dead end for a completely ordinary request.
    Mirrors DAYCARE reschedule's existing duration-preserving fallback."""
    booking = {
        "booking_id": 701,
        "service_type": "BOARDING",
        "pet_id": 9,
        "pet_name": "Milo",
        "staff_id": None,
        "room_type": "Sirius Room",
        "check_in_date": "2026-08-25",
        "check_out_date": "2026-08-28",
        "check_in_time": "11:00",
        "check_out_time": "12:00",
    }
    monkeypatch.setattr(
        relational_actions, "_locate_active_customer_booking", lambda context, intent_json: (booking, None)
    )
    monkeypatch.setattr(relational_actions, "_verify_booking_belongs_to_customer", lambda context, b: True)

    class _OkVaccination:
        ok = True
        errors: list[str] = []

    monkeypatch.setattr(
        "app.validation.validator.check_vaccination_eligibility",
        lambda *a, **kw: _OkVaccination(),
    )
    monkeypatch.setattr(
        relational_actions,
        "_business_hours_for_date",
        lambda company_id, target_date: ("09:00", "18:00", None),
    )

    captured_probes = []

    def fake_slot_is_available(context, intent_json, *, booking_date, booking_time):
        captured_probes.append(dict(intent_json["entities"]))
        return True

    monkeypatch.setattr(relational_actions, "_slot_is_available", fake_slot_is_available)
    monkeypatch.setattr(
        relational_actions, "_service_table", lambda service_type: "boarding_booking"
    )

    class _FakeTable:
        def update(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def execute(self):
            return SimpleNamespace(data=[{**booking, "check_in_date": "2026-08-28", "check_out_date": "2026-08-31"}])

    class _FakeClient:
        def table(self, _name):
            return _FakeTable()

    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: _FakeClient())

    context = CustomerContext(company_id=1)
    context.resolved_customer_id = 42

    result = relational_actions.reschedule_booking(
        context,
        {
            "entities": {
                "confirm_pet_name": "milo",
                "new_preferred_date": "2026-08-28",
                "new_preferred_time": "11:00",
                # Deliberately no new_check_out_date/new_check_out_time —
                # the customer only said "remain the time".
            }
        },
    )

    assert result.get("error") != "UNVERIFIED_DATE"
    assert captured_probes, "expected the availability probe to run"
    # Original stay was 3 nights (25th-28th); shifted to a 28th check-in,
    # the preserved-length check-out must be the 31st — computed
    # server-side, never passed through the model.
    assert captured_probes[0]["check_out_date"] == "2026-08-31"


def test_boarding_requires_one_persistable_staff_member_for_both_handoffs(monkeypatch):
    """The current schema has one boarding_booking.staff_id and the final
    conflict RPC validates that person at both handoff events. Read-side
    availability must not promise a stay supported only by two different
    employees, because the later create then rejects it as a conflict."""
    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def in_(self, *_a, **_k): return self
        def execute(self):
            return SimpleNamespace(data=self.rows)

    class _Client:
        def __init__(self, tables):
            self.tables = tables

        def table(self, name):
            return _Query(self.tables.get(name, []))

    ari = {"staff_id": 7, "staff_name": "Ari", "status": "active", "off_days_json": [],
           "provides_service": True, "service_types_json": ["GROOMING", "DAYCARE", "BOARDING"]}
    ben = {"staff_id": 9, "staff_name": "Ben", "status": "active", "off_days_json": [],
           "provides_service": True, "service_types_json": ["GROOMING", "DAYCARE", "BOARDING"]}
    tables = {
        "staff": [ari, ben],
        # Ari is off on the checkout day and Ben is off on the check-in day:
        # both dates have someone on duty, but no single staff_id can be
        # persisted safely for the full booking.
        "leave": [
            {"staff_id": 7, "start_date": "2026-08-12", "end_date": "2026-08-12", "status": "Approved"},
            {"staff_id": 9, "start_date": "2026-08-10", "end_date": "2026-08-10", "status": "Approved"},
        ],
        "grooming_booking": [], "daycare_booking": [], "boarding_booking": [],
        "room": [{"capacity": 2, "room_type": "Mars Room"}],
    }
    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: _Client(tables))
    monkeypatch.setattr(
        relational_actions, "_business_hours_for_date", lambda company_id, target_date: ("09:00", "18:00", None)
    )
    monkeypatch.setattr(relational_actions, "_shared_booking_holds", lambda client, company_id: None)

    context = CustomerContext(company_id=1)
    result = check_available_slots(
        context,
        {
            "service_type": "BOARDING",
            "entities": {
                "preferred_date": "2026-08-10",
                "room_type": "Mars Room",
                "check_out_date": "2026-08-12",
            },
        },
    )

    assert result["status"] == "success"
    assert result["data"]["available_slots"] == []


def test_boarding_checkout_selection_requires_the_persistable_staff_member(monkeypatch):
    """The CHECK_OUT selection path must apply the same one-staff contract
    as CHECK_IN selection and the final atomic write."""
    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def in_(self, *_a, **_k): return self
        def execute(self):
            return SimpleNamespace(data=self.rows)

    class _Client:
        def __init__(self, tables):
            self.tables = tables

        def table(self, name):
            return _Query(self.tables.get(name, []))

    ari = {"staff_id": 7, "staff_name": "Ari", "status": "active", "off_days_json": [],
           "provides_service": True, "service_types_json": ["GROOMING", "DAYCARE", "BOARDING"]}
    ben = {"staff_id": 9, "staff_name": "Ben", "status": "active", "off_days_json": [],
           "provides_service": True, "service_types_json": ["GROOMING", "DAYCARE", "BOARDING"]}
    tables = {
        "staff": [ari, ben],
        "leave": [
            {"staff_id": 7, "start_date": "2026-08-12", "end_date": "2026-08-12", "status": "Approved"},
            {"staff_id": 9, "start_date": "2026-08-10", "end_date": "2026-08-10", "status": "Approved"},
        ],
        "grooming_booking": [], "daycare_booking": [], "boarding_booking": [],
        "room": [{"capacity": 2, "room_type": "Mars Room"}],
    }
    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: _Client(tables))
    monkeypatch.setattr(
        relational_actions, "_business_hours_for_date", lambda company_id, target_date: ("09:00", "18:00", None)
    )
    monkeypatch.setattr(relational_actions, "_shared_booking_holds", lambda client, company_id: None)

    context = CustomerContext(company_id=1)
    result = check_available_slots(
        context,
        {
            "service_type": "BOARDING",
            "entities": {
                "preferred_date": "2026-08-10",
                "room_type": "Mars Room",
                "check_out_date": "2026-08-12",
                "selection_target": "CHECK_OUT",
                "check_in_time": "10:00",
            },
        },
    )

    assert result["status"] == "success"
    assert result["data"]["available_check_out_times"] == []


def test_fresh_boarding_check_never_excludes_an_existing_booking_implicitly(monkeypatch):
    exclusions = []
    monkeypatch.setattr(
        relational_actions,
        "_business_hours_for_date",
        lambda company_id, target_date: ("09:00", "12:00", None),
    )
    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: object())
    monkeypatch.setattr(relational_actions, "_shared_booking_holds", lambda client, company_id: None)
    monkeypatch.setattr(
        relational_actions,
        "_staff_day_roster",
        lambda client, company_id, target_date, service_type=None: [
            {"staff_id": 7, "staff_name": "Ari"}
        ],
    )
    monkeypatch.setattr(
        relational_actions,
        "_cross_service_staff_bookings",
        lambda client, company_id, staff_ids, date_str: [],
    )

    def room_status(context, room_type, check_in_date, check_out_date, exclude_booking_id=None):
        exclusions.append(exclude_booking_id)
        return {"capacity": 1, "booked_count": 0, "available": True, "held_for_minutes": None}

    monkeypatch.setattr(relational_actions, "_room_capacity_status", room_status)

    result = check_available_slots(
        CustomerContext(company_id=1),
        {
            "service_type": "BOARDING",
            "entities": {
                "preferred_date": "2026-08-10",
                "check_out_date": "2026-08-11",
                "room_type": "Mars Room",
            },
        },
    )

    assert result["status"] == "success"
    assert exclusions == [None]


def test_grooming_availability_uses_the_selected_service_duration(monkeypatch):
    monkeypatch.setattr(
        relational_actions,
        "_business_hours_for_date",
        lambda company_id, target_date: ("09:00", "12:00", None),
    )
    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: object())
    monkeypatch.setattr(relational_actions, "_shared_booking_holds", lambda client, company_id: None)
    monkeypatch.setattr(
        relational_actions,
        "_staff_day_roster",
        lambda client, company_id, target_date, service_type=None: [
            {"staff_id": 7, "staff_name": "Ari"}
        ],
    )
    monkeypatch.setattr(
        relational_actions,
        "_cross_service_staff_bookings",
        lambda client, company_id, staff_ids, date_str: [
            {
                "_service_type": "DAYCARE",
                "staff_id": 7,
                "booking_date": date_str,
                "check_in_time": "10:30",
                "check_out_time": "11:30",
                "booking_status": "Scheduled",
            }
        ],
    )

    one_hour = check_available_slots(
        CustomerContext(company_id=1),
        {
            "service_type": "GROOMING",
            "entities": {"preferred_date": "2026-08-10", "duration_minutes": 60},
        },
    )
    ninety_minutes = check_available_slots(
        CustomerContext(company_id=1),
        {
            "service_type": "GROOMING",
            "entities": {"preferred_date": "2026-08-10", "duration_minutes": 90},
        },
    )

    assert "09:30:00" in one_hour["data"]["available_slots"]
    assert "09:30:00" not in ninety_minutes["data"]["available_slots"]


def test_member_registration_fails_closed_and_uses_atomic_rpc(monkeypatch):
    context = CustomerContext(company_id=1, resolved_customer_id=9)
    monkeypatch.setattr(
        relational_actions,
        "check_loyalty_points",
        lambda _context: {"status": "error", "data": {}, "error": "database offline"},
    )
    assert register_loyalty_member(context, confirmed=True)["status"] == "error"

    calls = []

    class Rpc:
        def execute(self):
            return SimpleNamespace(
                data={
                    "member": {"loyalty_id": 3, "points_balance": 0, "tier": "Bronze"},
                    "already_member": False,
                }
            )

    class Client:
        def rpc(self, name, payload):
            calls.append((name, payload))
            return Rpc()

    monkeypatch.setattr(
        relational_actions,
        "check_loyalty_points",
        lambda _context: {"status": "not_found", "data": {}},
    )
    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: Client())

    result = register_loyalty_member(context, confirmed=True)

    assert result["status"] == "success"
    assert result["data"]["loyalty_id"] == 3
    assert calls[0][0] == "register_loyalty_member_atomic"


def test_availability_range_is_capped_to_fourteen_days(monkeypatch):
    calls = []

    class Repo:
        def check_availability(self, company_id, *, service, date, time, intent_json=None):
            calls.append(date)
            return {"status": "success", "data": {"available_slots": []}}

    monkeypatch.setattr(availability_tools, "get_relational_repository", lambda: Repo())
    monkeypatch.setattr(availability_tools, "booking_window_error", lambda _date: None)
    monkeypatch.setattr(availability_tools, "max_bookable_date", lambda: date(2026, 8, 31))

    result = availability_tools.check_availability_range.invoke(
        {
            "company_id": 1,
            "service_type": "GROOMING",
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
        }
    )

    assert result["status"] == "success"
    assert len(calls) == 14
    assert calls[-1] == "2026-08-14"


def test_unknown_booking_status_is_not_treated_as_valid_history():
    assert relational_actions.is_qualifying_previous_booking_status("Scheduled")
    assert not relational_actions.is_qualifying_previous_booking_status("mystery-status")


def test_chinese_date_time_and_duration_are_deterministic():
    assert extract_time_from_message("晚上7点半") == "19:30"
    assert extract_time_from_message("早上十点") == "10:00"
    assert extract_duration_minutes("需要三个小时") == 180
    assert extract_duration_minutes("for 2.5 hours") == 150
    assert extract_time_range("下午5点半到晚上8点半") == ("17:30", "20:30", 180)
    assert extract_customer_date("大后天早上") is not None
    assert parse_week_range("下周下午") is not None


def test_time_range_extraction_ignores_an_earlier_unrelated_to():
    """Real bug confirmed live (2026-08-10): the old implementation split
    on only the FIRST "to"/"until"/etc. match in the whole message. "I want
    TO book daycare ... 12pm TO 5pm" has an earlier, unrelated "to" (want
    to book) that made the split land on "I want" / "book daycare ...
    12pm to 5pm" — neither half is a bare time, so the real range later in
    the same sentence was silently never reached."""
    assert extract_time_range(
        "Hi I want to book daycare hourly care for Coco on 1 October, 12pm to 5pm"
    ) == ("12:00", "17:00", 300)
    assert extract_time_range("please go to the store from 2pm to 4pm") == ("14:00", "16:00", 120)


def test_chinese_next_weekday_modifier_is_not_dropped_inside_a_sentence():
    # Thursday. "下个星期五" (next Friday) embedded in a longer sentence
    # previously matched the bare "星期五" substring first and silently
    # returned this Friday instead of next Friday — see extract_customer_date.
    thursday = date(2026, 8, 6)
    this_friday = date(2026, 8, 7)
    next_friday = date(2026, 8, 14)

    assert extract_customer_date("下个星期五大概中午这样", today=thursday) == next_friday
    assert extract_customer_date("下个星期5大概中午这样", today=thursday) == next_friday
    assert extract_customer_date("下个星期5", today=thursday) == extract_customer_date(
        "下个星期五", today=thursday
    )
    # Bare weekday (no "next" modifier) must still resolve to the nearest
    # occurring Friday, unaffected by the prefix-ordering fix.
    assert extract_customer_date("星期五", today=thursday) == this_friday


def test_english_next_weekday_modifier_is_not_silently_ignored():
    # Found via scenario testing, not code reading: `days_ahead == 0 or
    # modifier == "next" and days_ahead == 0` in parse_customer_date's
    # English weekday branch is, by operator precedence, exactly
    # `days_ahead == 0` — the `modifier == "next"` clause never changed the
    # outcome. "next friday" said on any day but Friday itself silently
    # returned THIS Friday, ignoring "next" entirely, while the equivalent
    # Chinese/Malay branches (_parse_chinese_date/_parse_malay_date) already
    # handled the same "next" modifier correctly.
    thursday = date(2026, 8, 6)
    assert extract_customer_date("this friday", today=thursday) == date(2026, 8, 7)
    assert extract_customer_date("next friday", today=thursday) == date(2026, 8, 14)
    # Bare weekday (no modifier) must still resolve to the nearest occurrence.
    assert extract_customer_date("friday", today=thursday) == date(2026, 8, 7)
    # today IS Thursday: bare "thursday" and "next thursday" both correctly
    # roll to next week (the one case the old buggy condition happened to
    # get right, since days_ahead == 0 covers it regardless of "next").
    assert extract_customer_date("thursday", today=thursday) == date(2026, 8, 13)
    assert extract_customer_date("next thursday", today=thursday) == date(2026, 8, 13)


def test_weekday_modifiers_use_calendar_week_boundaries_in_all_languages():
    # Every day in this reference week must resolve "next Tuesday" to the
    # same Tuesday in the following calendar week.  Adding seven days to the
    # nearest occurrence used to jump to Aug 18 from Wednesday onward.
    week_start = date(2026, 8, 3)  # Monday
    this_tuesday = date(2026, 8, 4)
    next_tuesday = date(2026, 8, 11)

    for offset in range(7):
        reference = week_start + timedelta(days=offset)
        for phrase in ("next tuesday", "下个星期二", "下周二", "selasa depan"):
            assert extract_customer_date(phrase, today=reference) == next_tuesday
        for phrase in ("this tuesday", "这个星期二", "这周二", "selasa ini"):
            # Regression: "this Tuesday" used to always mean this ISO
            # calendar week's Tuesday, even asked from Wed onward — once
            # the week rolls past Tuesday, that's a PAST date. Confirmed
            # live: "this Saturday" asked on a Sunday resolved to
            # yesterday. No real customer means an already-passed day when
            # booking a future service, so once the reference day is past
            # this_tuesday, "this Tuesday" rolls forward to next_tuesday
            # instead — Mon/Tue (on or before the target) still get the
            # current week's Tuesday.
            expected = this_tuesday if reference <= this_tuesday else next_tuesday
            assert extract_customer_date(phrase, today=reference) == expected


def test_this_weekday_never_resolves_to_a_date_in_the_past():
    """Confirmed live: "this Saturday" asked on a Sunday (the week has
    already rolled past Saturday) resolved to YESTERDAY's Saturday — a
    real customer booking a future service never means an already-passed
    day, no matter which day of the week they're asking from."""
    sunday = date(2026, 8, 9)
    assert extract_customer_date("this saturday", today=sunday) == date(2026, 8, 15)
    assert extract_customer_date("this monday", today=sunday) == date(2026, 8, 10)
    assert extract_customer_date("这个星期六", today=sunday) == date(2026, 8, 15)

    # Still resolves within the current week when the target day hasn't
    # passed yet.
    tuesday = date(2026, 8, 4)
    assert extract_customer_date("this saturday", today=tuesday) == date(2026, 8, 8)
    # Asking about today's own weekday still means today, not next week.
    assert extract_customer_date("this saturday", today=date(2026, 8, 8)) == date(2026, 8, 8)


def test_chinese_weekday_modifier_allows_customer_whitespace():
    friday = date(2026, 8, 7)
    assert extract_customer_date("下个 星期二下午", today=friday) == date(2026, 8, 11)


def test_resolve_datetime_uses_business_local_today_not_server_utc_date(monkeypatch):
    # The container runs in UTC with no TZ set. resolve_datetime previously
    # called extract_customer_date/parse_week_range with no `today`, so they
    # fell back to date.today() — the server's naive UTC date — even though
    # the tool's own docstring claims "business-local time,
    # Asia/Kuala_Lumpur". Malaysia is UTC+8, so any customer message sent
    # during Malaysia's local 00:00-07:59 (UTC 16:00-23:59 the day before)
    # was resolved a full calendar day behind the real business-local date.
    # This pins resolve_datetime to actually use today_business(), not
    # whatever date.today() happens to return.
    import app.tools.calendar_tools as calendar_tools

    fake_business_today = date(2026, 8, 14)  # a Friday
    monkeypatch.setattr(calendar_tools, "today_business", lambda: fake_business_today)

    result = calendar_tools.resolve_datetime.invoke({"text": "tomorrow"})
    assert result["date"] == (fake_business_today + timedelta(days=1)).isoformat()


def test_existing_booking_survives_beyond_first_turn(monkeypatch):
    latest = {
        "booking_id": 88,
        "service_type": "DAYCARE",
        "booking_date": "2026-08-08",
        "booking_status": "Scheduled",
    }

    class Repo:
        def get_customer_by_phone(self, *_args):
            return {
                "status": "success",
                "data_found": True,
                "data": {"customer_id": 9, "full_name": "Alicia Lee", "address": "KL"},
            }

        def list_customer_pets(self, *_args):
            return {"status": "success", "data": {"pets": []}}

        def get_latest_booking(self, *_args):
            return {"status": "success", "data": latest}

    monkeypatch.setattr("app.context.runtime_context.get_relational_repository", lambda: Repo())
    state = ConversationState(phone_number="+60123456705", company_id="1")
    first = _resolve_identity_for_test("1", state)
    assert first["latest_booking"]["booking_id"] == 88
    state.history.append({"role": "human", "content": "hi"})
    second = _resolve_identity_for_test("1", state)
    assert second["latest_booking"]["booking_id"] == 88


def test_loyalty_account_is_hydrated_once_per_session_for_an_existing_customer(monkeypatch):
    """Real gap confirmed 2026-08-10: an existing customer's loyalty/member
    status was only ever looked up after an explicit loyalty question or
    forced once post-booking — a pre-booking recommendation turn had zero
    member context, even for a real Gold-tier member. Hydrated once per
    session, same mechanism/pattern as recent_booking/upcoming_booking."""
    loyalty_calls = {"count": 0}

    class Repo:
        def get_customer_by_phone(self, *_args):
            return {
                "status": "success", "data_found": True,
                "data": {"customer_id": 9, "full_name": "Alicia Lee", "address": "KL"},
            }

        def list_customer_pets(self, *_args):
            return {"status": "success", "data": {"pets": []}}

        def get_latest_booking(self, *_args):
            return {"status": "not_found", "data": None}

        def get_loyalty_account(self, *_args):
            loyalty_calls["count"] += 1
            return {"status": "success", "data": {"points_balance": 860, "tier": "Gold"}}

    monkeypatch.setattr("app.context.runtime_context.get_relational_repository", lambda: Repo())
    state = ConversationState(phone_number="+60123456705", company_id="1")

    first = _resolve_identity_for_test("1", state)
    assert first["loyalty_account"] == {"points_balance": 860, "tier": "Gold"}
    assert first["loyalty_context_status"] == "available"
    assert loyalty_calls["count"] == 1

    state.history.append({"role": "human", "content": "what grooming do you recommend?"})
    second = _resolve_identity_for_test("1", state)
    assert second["loyalty_account"]["tier"] == "Gold"
    # Session-level, not per-turn — must not re-query once hydrated.
    assert loyalty_calls["count"] == 1


def test_loyalty_account_none_for_a_genuine_non_member_is_distinct_from_not_yet_hydrated(monkeypatch):
    class Repo:
        def get_customer_by_phone(self, *_args):
            return {
                "status": "success", "data_found": True,
                "data": {"customer_id": 9, "full_name": "Alicia Lee", "address": "KL"},
            }

        def list_customer_pets(self, *_args):
            return {"status": "success", "data": {"pets": []}}

        def get_latest_booking(self, *_args):
            return {"status": "not_found", "data": None}

        def get_loyalty_account(self, *_args):
            return {"status": "not_found"}

    monkeypatch.setattr("app.context.runtime_context.get_relational_repository", lambda: Repo())
    state = ConversationState(phone_number="+60123456705", company_id="1")

    customer = _resolve_identity_for_test("1", state)
    assert customer["loyalty_account"] is None
    # "available" (verified genuinely no account) — not "unavailable" (a
    # read that hasn't happened/failed) — so this isn't re-queried forever.
    assert customer["loyalty_context_status"] == "available"


def test_profile_hydration_is_not_tied_to_greeting_and_retries_failed_pet_read(monkeypatch):
    class Repo:
        def __init__(self):
            self.pet_calls = 0

        def get_customer_by_phone(self, *_args):
            raise AssertionError("cached customer identity should remain authoritative")

        def list_customer_pets(self, *_args):
            self.pet_calls += 1
            if self.pet_calls == 1:
                return {"status": "error", "error": "temporary Supabase failure"}
            return {
                "status": "success",
                "data": {
                    "pets": [{
                        "pet_id": 31,
                        "pet_name": "Snowy",
                        "pet_type": "Cat",
                        "size": "S",
                        "breed": "Domestic Shorthair",
                    }]
                },
            }

        def get_latest_booking(self, *_args):
            return {"status": "not_found", "data": None}

    repo = Repo()
    monkeypatch.setattr("app.context.runtime_context.get_relational_repository", lambda: repo)
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        customer_id=9,
        customer_name="Alicia Lee",
        # This is deliberately not a greeting and not the first session turn.
        history=[
            {"role": "human", "content": "I want to join as a member"},
            {"role": "ai", "content": "Would you like to join?"},
        ],
    )

    unavailable = _resolve_identity_for_test("1", state)
    assert unavailable["found"] is True
    assert unavailable["pets"] is None
    assert unavailable["pets_context_status"] == "unavailable"
    assert state.booking_context_status == "not_found"

    hydrated = _resolve_identity_for_test("1", state)
    assert repo.pet_calls == 2
    assert hydrated["pets_context_status"] == "available"
    assert hydrated["pets"][0]["pet_name"] == "Snowy"
    # V2 resolves pet identity fresh each call via AgentContext.customer.
    # pets[].ref (see app.agent.runtime._resolve_pet_ref) rather than a
    # state.pet_id side-channel auto-populated for a single-pet customer —
    # PawfectOrchestrator._cache_single_pet's job pre-decommission — so
    # there is no state.pet_id/pet_name equivalent to assert here anymore.

    # A successful empty/non-empty result is cached; only failed reads retry.
    _resolve_identity_for_test("1", state)
    assert repo.pet_calls == 2


def test_pdf_paths_do_not_collide_across_service_tables(monkeypatch):
    uploaded = []
    monkeypatch.setattr(document_service, "_fill_pet_name", lambda _company, booking: booking)
    monkeypatch.setattr(document_service, "get_billing_profile", lambda _company: {"company_name": "P", "currency": "RM"})
    monkeypatch.setattr(document_service, "_customer_for_pet", lambda *_args: {"customer_id": 1, "customer_name": "A", "phone_number": "+60123456705"})
    monkeypatch.setattr(document_service, "_staff_name", lambda *_args: "Sam")
    monkeypatch.setattr(document_service, "_loyalty_snapshot", lambda *_args: None)
    monkeypatch.setattr(document_service, "build_booking_confirmation_pdf", lambda *_args: b"pdf")
    monkeypatch.setattr(
        document_service,
        "upload_customer_document",
        lambda _company, _category, filename, _content: uploaded.append(filename) or f"https://example/{filename}",
    )
    monkeypatch.setattr(document_service, "send_whatsapp_document", lambda *_args: {"status": "sent"})

    for service_type, pet_id in (("GROOMING", 1), ("DAYCARE", 2)):
        result = document_service.generate_and_send_booking_confirmation(
            1,
            {"booking_id": 7, "service_type": service_type, "pet_id": pet_id},
        )
        assert result["status"] == "success"
    assert uploaded == ["grooming-7.pdf", "daycare-7.pdf"]


def test_booking_confirmation_reports_delivery_failed_when_whatsapp_send_fails(monkeypatch):
    monkeypatch.setattr(document_service, "_fill_pet_name", lambda _company, booking: booking)
    monkeypatch.setattr(document_service, "get_billing_profile", lambda _company: {"company_name": "P", "currency": "RM"})
    monkeypatch.setattr(document_service, "_customer_for_pet", lambda *_args: {"customer_id": 1, "customer_name": "A", "phone_number": "+60123456705"})
    monkeypatch.setattr(document_service, "_staff_name", lambda *_args: "Sam")
    monkeypatch.setattr(document_service, "_loyalty_snapshot", lambda *_args: None)
    monkeypatch.setattr(document_service, "build_booking_confirmation_pdf", lambda *_args: b"pdf")
    monkeypatch.setattr(
        document_service,
        "upload_customer_document",
        lambda *_args: "https://example/grooming-7.pdf",
    )
    # The PDF was generated and stored fine, but WhatsApp was never
    # configured for this company — the actual failure mode this bug hid.
    monkeypatch.setattr(document_service, "send_whatsapp_document", lambda *_args: {"status": "not_configured"})

    result = document_service.generate_and_send_booking_confirmation(
        1, {"booking_id": 7, "service_type": "GROOMING", "pet_id": 1}
    )

    assert result["status"] == "delivery_failed"


def test_invoice_reports_delivery_failed_when_whatsapp_send_errors(monkeypatch):
    monkeypatch.setattr(document_service, "_fill_pet_name", lambda _company, booking: booking)
    monkeypatch.setattr(document_service, "get_billing_profile", lambda _company: {"company_name": "P", "currency": "RM", "invoice_prefix": "INV"})
    monkeypatch.setattr(document_service, "_customer_for_pet", lambda *_args: {"customer_id": 1, "customer_name": "A", "phone_number": "+60123456705"})
    monkeypatch.setattr(document_service, "_staff_name", lambda *_args: "Sam")
    monkeypatch.setattr(document_service, "_loyalty_snapshot", lambda *_args: None)
    monkeypatch.setattr(document_service, "_redemption_for_payment", lambda *_args: None)
    monkeypatch.setattr(document_service, "build_invoice_pdf", lambda *_args: b"pdf")
    monkeypatch.setattr(
        document_service,
        "upload_customer_document",
        lambda *_args: "https://example/invoice-9.pdf",
    )
    monkeypatch.setattr(document_service, "send_whatsapp_document", lambda *_args: {"status": "error", "error": "provider timeout"})

    result = document_service.generate_and_send_invoice(
        1, {"payment_id": 9}, {"booking_id": 7, "service_type": "GROOMING", "pet_id": 1}, send=True
    )

    assert result["status"] == "delivery_failed"
    # The document was still generated/stored — only delivery failed.
    assert result["document_url"] == "https://example/invoice-9.pdf"


def test_daycare_add_on_is_exposed_to_pdf_and_payment_layers():
    serialized = _serialize_daycare_booking(
        {
            "daycare_booking_id": 4,
            "package_type": "Hourly Care",
            "add_on": "Webcam access",
            "add_on_price": 12,
        }
    )
    assert serialized["add_on"] == "Webcam access"
    assert serialized["add_on_price"] == 12


class _FakeQuery:
    def __init__(self, table, data):
        self.table = table
        self.data = data

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=self.data[self.table])


class _FakeClient:
    def __init__(self, data):
        self.data = data

    def table(self, name):
        return _FakeQuery(name, self.data)


def test_boarding_room_is_occupied_on_every_intermediate_date(monkeypatch):
    client = _FakeClient(
        {
            "room": [{"capacity": 1}],
            "boarding_booking": [
                {
                    "boarding_booking_id": 1,
                    "check_in_date": "2026-08-01",
                    "check_out_date": "2026-08-10",
                    "booking_status": "Scheduled",
                }
            ],
        }
    )
    monkeypatch.setattr("app.db.relational_actions.get_supabase_client", lambda: client)
    monkeypatch.setattr("app.db.relational_actions._shared_booking_holds", lambda _client, _company: None)
    context = CustomerContext(company_id=1)
    middle = _room_capacity_status(context, "Mars", "2026-08-05", "2026-08-06")
    after_checkout = _room_capacity_status(context, "Mars", "2026-08-10", "2026-08-11")
    assert middle == {"capacity": 1, "booked_count": 1, "available": False, "held_for_minutes": None}
    assert after_checkout == {"capacity": 1, "booked_count": 0, "available": True, "held_for_minutes": None}


def test_sql_blocks_full_daycare_interval_and_links_redemption_to_payment():
    root = Path(__file__).resolve().parents[1]
    conflict_sql = (root / "backend/sql/booking_conflict_prevention_migration.sql").read_text()
    redemption_sql = (root / "backend/sql/verify_payment_function.sql").read_text()
    assert "b.booking_date + b.check_in_time, b.booking_date + b.check_out_time" in conflict_sql
    assert "v_checkout + interval '30 minutes'" in conflict_sql
    assert "b.check_in_date <= p_date and p_date < b.check_out_date" in conflict_sql
    assert "set redemption_id = v_redemption_id" in redemption_sql


def test_payment_status_for_reads_linked_payment_when_not_already_present(monkeypatch):
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
        def __init__(self, rows):
            self.rows = rows

        def table(self, _name):
            return _Query(self.rows)

    monkeypatch.setattr(
        document_service, "get_supabase_client", lambda: _Client([{"status": "Paid"}])
    )
    assert document_service._payment_status_for(1, {"payment_id": 9}) == "Paid"

    # Already resolved upstream (create_booking's own result) — used as-is,
    # no extra DB round-trip.
    assert document_service._payment_status_for(1, {"payment_status": "Refunded"}) == "Refunded"

    # No payment linked at all — fails safe to "Pending" rather than raising.
    assert document_service._payment_status_for(1, {}) == "Pending"


def test_booking_confirmation_shows_payment_status_not_booking_status(monkeypatch):
    monkeypatch.setattr(document_service, "_fill_pet_name", lambda _company, booking: booking)
    monkeypatch.setattr(document_service, "get_billing_profile", lambda _company: {"company_name": "P", "currency": "RM"})
    monkeypatch.setattr(document_service, "_customer_for_pet", lambda *_args: {"customer_id": 1, "customer_name": "A", "phone_number": "+60123456705"})
    monkeypatch.setattr(document_service, "_staff_name", lambda *_args: "Sam")
    monkeypatch.setattr(document_service, "_loyalty_snapshot", lambda *_args: None)
    monkeypatch.setattr(document_service, "_payment_status_for", lambda *_args: "Pending")
    monkeypatch.setattr(document_service, "upload_customer_document", lambda *_args: "https://example/grooming-7.pdf")
    monkeypatch.setattr(document_service, "send_whatsapp_document", lambda *_args: {"status": "sent"})

    captured = {}

    def fake_build_pdf(_profile, booking, *_rest):
        captured.update(booking)
        return b"pdf"

    monkeypatch.setattr(document_service, "build_booking_confirmation_pdf", fake_build_pdf)

    # A reschedule-style booking dict: booking_status is "Scheduled", but no
    # payment_status was ever attached by the caller.
    document_service.generate_and_send_booking_confirmation(
        1, {"booking_id": 7, "service_type": "GROOMING", "pet_id": 1, "booking_status": "Scheduled"}
    )

    assert captured["payment_status"] == "Pending"


def test_boarding_create_booking_does_not_require_the_same_staff_at_check_in_and_check_out(monkeypatch):
    """Regression: the SAME "same staff must do both ends" bug the
    availability read-side already had (see the check_available_slots
    fixes above) also existed as a THIRD, independent copy in
    create_booking's own write-path staff selection — confirmed by an
    external audit. The read side offering a slot no longer meant the
    write side would actually accept it: check_available_slots (fixed)
    would show the slot as bookable with disjoint check-in/check-out
    staff, and then create_booking's own staff selection intersected
    check-in-free staff with check-out-free staff, silently emptying the
    candidate pool and forcing an unrelated-looking failure on confirm."""
    ari = {"staff_id": 7, "staff_name": "Ari", "status": "active", "off_days_json": [],
           "provides_service": True, "service_types_json": ["GROOMING", "DAYCARE", "BOARDING"]}
    ben = {"staff_id": 9, "staff_name": "Ben", "status": "active", "off_days_json": [],
           "provides_service": True, "service_types_json": ["GROOMING", "DAYCARE", "BOARDING"]}

    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def in_(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def execute(self):
            return SimpleNamespace(data=self.rows)

    tables = {
        "staff": [ari, ben],
        # Ari off on the checkout day; Ben off on the check-in day — same
        # disjoint-roster setup as the availability-side regression test.
        "leave": [
            {"staff_id": 7, "start_date": "2026-08-12", "end_date": "2026-08-12", "status": "Approved"},
            {"staff_id": 9, "start_date": "2026-08-10", "end_date": "2026-08-10", "status": "Approved"},
        ],
        "grooming_booking": [], "daycare_booking": [], "boarding_booking": [],
        "ai_mutation_idempotency": [],
    }

    class _Client:
        def table(self, name):
            return _Query(tables.get(name, []))

    monkeypatch.setattr(relational_actions, "get_supabase_client", lambda: _Client())
    monkeypatch.setattr(
        relational_actions, "_business_hours_for_date",
        lambda company_id, target_date: ("09:00", "18:00", None),
    )
    monkeypatch.setattr(
        relational_actions, "get_pets_by_customer_id",
        lambda context: {"status": "success", "data": {"pets": [{"pet_id": 1}]}},
    )

    class _OkVaccination:
        ok = True
        errors: list[str] = []

    monkeypatch.setattr(
        "app.validation.validator.check_vaccination_eligibility", lambda *a, **kw: _OkVaccination()
    )
    monkeypatch.setattr(relational_actions, "_slot_is_available", lambda *a, **kw: True)

    captured_free_staff = []

    def fake_select_staff_id(available_staff):
        captured_free_staff.append(list(available_staff))
        return available_staff[0]["staff_id"] if available_staff else None

    monkeypatch.setattr("app.db.booking_draft.select_staff_id", fake_select_staff_id)

    context = CustomerContext(company_id=1)
    context.resolved_customer_id = 42
    result = relational_actions.create_booking(
        context,
        {
            "service_type": "BOARDING",
            "entities": {
                "pet_id": 1,
                "room_type": "Mars Room",
                "preferred_date": "2026-08-10",
                "preferred_time": "11:00",
                "check_out_date": "2026-08-12",
                "check_out_time": "12:00",
                "idempotency_key": "a" * 32,
            },
        },
    )

    assert result.get("error") != (
        "No qualified staff member is free for the check-out "
        "handoff at that date/time. Ask the customer for a "
        "different check-out time, or a different check-out date."
    )
    assert captured_free_staff, "expected staff selection to actually run"
    # Ari (check-in day roster) must NOT have been emptied out just because
    # Ben (not Ari) is the one covering the check-out day.
    assert {s["staff_id"] for s in captured_free_staff[0]} == {7}


def test_boarding_range_check_redirects_to_the_singular_tool_by_name():
    """Confirmed live: without an explicit tool-name redirect in this
    error, the model repeatedly re-resolved the same dates and retried
    check_availability_range for BOARDING instead of switching to
    check_availability (singular) — burning through MAX_TOOL_ITERATIONS
    without ever converging on a response."""
    result = availability_tools.check_availability_range.invoke(
        {
            "company_id": 1,
            "service_type": "BOARDING",
            "start_date": "2026-08-11",
            "end_date": "2026-08-13",
        }
    )
    assert result["status"] == "missing_information"
    assert "check_availability_range does not support BOARDING" in result["error"]
    assert "check_availability" in result["error"]
    assert "2026-08-11" in result["error"]
    assert "2026-08-13" in result["error"]
