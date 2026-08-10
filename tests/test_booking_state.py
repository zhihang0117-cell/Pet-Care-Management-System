"""Unit tests for app/agent/booking_state.py's dependency-invalidation
rules — the persistent "what has the customer actually selected" memory
proposed 2026-08-10 to fix a real live gap: a customer picked a real
service option by ordinal with zero date/time ever mentioned, and the
model silently invented both and went straight to a preview instead of
asking or recommending."""

from app.agent import booking_state
from app.context.state import ConversationState


def _state():
    return ConversationState(phone_number="+60123456705", company_id="1")


def test_selecting_an_option_is_a_no_op_when_unchanged():
    state = _state()
    booking_state.change_option(state, "option_abc")
    state.booking.selected_slot_ref = "slot_1"
    state.booking.preview_ref = "preview_1"
    revision_before = state.booking.revision

    booking_state.change_option(state, "option_abc")

    assert state.booking.selected_slot_ref == "slot_1"
    assert state.booking.preview_ref == "preview_1"
    assert state.booking.revision == revision_before


def test_changing_the_option_invalidates_slot_and_preview_not_pet_or_datetime():
    state = _state()
    booking_state.change_pet(state, "1")
    booking_state.change_datetime(state, "datetime_a", ("2026-08-21", "10:00", None))
    booking_state.change_option(state, "option_standard")
    state.booking.selected_slot_ref = "slot_1"
    state.booking.preview_ref = "preview_1"

    booking_state.change_option(state, "option_premium")

    assert state.booking.selected_option_ref == "option_premium"
    assert state.booking.selected_slot_ref is None
    assert state.booking.preview_ref is None
    # Pet and datetime are unrelated to which package was picked.
    assert state.booking.pet_ref == "1"
    assert state.booking.datetime_ref == "datetime_a"
    # 4 real changes so far: pet set, datetime set, option set (Standard),
    # option changed (Standard -> Premium).
    assert state.booking.revision == 4


def test_changing_pet_invalidates_the_whole_downstream_selection():
    state = _state()
    booking_state.change_option(state, "option_standard")
    booking_state.change_staff(state, "Ari")
    state.booking.selected_slot_ref = "slot_1"
    state.booking.preview_ref = "preview_1"

    booking_state.change_pet(state, "2")

    assert state.booking.pet_ref == "2"
    assert state.booking.selected_option_ref is None
    assert state.booking.selected_slot_ref is None
    assert state.booking.preview_ref is None
    assert state.booking.preferred_staff is None


def test_changing_service_invalidates_the_option_but_not_the_pet():
    state = _state()
    booking_state.change_pet(state, "1")
    booking_state.change_service(state, "grooming")
    booking_state.change_option(state, "option_standard")

    booking_state.change_service(state, "DAYCARE")

    assert state.booking.service_type == "DAYCARE"
    assert state.booking.selected_option_ref is None
    assert state.booking.pet_ref == "1"


def test_datetime_content_identity_ignores_the_always_fresh_ref():
    """EvidenceStore.mint() always returns a fresh ref even for identical
    content — comparing by ref string alone would treat every single
    resolve_datetime call as a brand new date and spuriously wipe a still-
    valid selected slot/preview on every turn. The real (date, time,
    period) content is what must actually differ."""
    state = _state()
    booking_state.change_datetime(state, "datetime_first", ("2026-08-21", "10:00", None))
    booking_state.change_option(state, "option_standard")
    state.booking.selected_slot_ref = "slot_1"
    state.booking.preview_ref = "preview_1"
    revision_before = state.booking.revision

    # A second resolve_datetime call for the SAME date/time text mints a
    # different ref, but the content is identical.
    booking_state.change_datetime(state, "datetime_second", ("2026-08-21", "10:00", None))

    assert state.booking.datetime_ref == "datetime_second"  # freshest ref kept
    assert state.booking.selected_slot_ref == "slot_1"  # NOT invalidated
    assert state.booking.preview_ref == "preview_1"
    assert state.booking.revision == revision_before


def test_datetime_content_change_invalidates_slot_and_preview():
    state = _state()
    booking_state.change_datetime(state, "datetime_a", ("2026-08-21", "10:00", None))
    state.booking.selected_slot_ref = "slot_1"
    state.booking.preview_ref = "preview_1"

    booking_state.change_datetime(state, "datetime_b", ("2026-08-21", "13:00", None))

    assert state.booking.selected_slot_ref is None
    assert state.booking.preview_ref is None


def test_changing_slot_only_clears_the_preview_no_revision_bump():
    """Picking a slot doesn't change any booking-defining condition, so it
    must not bump revision — only the superseded preview is stale."""
    state = _state()
    booking_state.change_option(state, "option_standard")
    revision_before = state.booking.revision
    state.booking.preview_ref = "preview_1"

    booking_state.change_slot(state, "slot_new")

    assert state.booking.selected_slot_ref == "slot_new"
    assert state.booking.preview_ref is None
    assert state.booking.revision == revision_before


def test_reset_clears_everything_for_the_next_unrelated_request():
    state = _state()
    booking_state.change_pet(state, "1")
    booking_state.change_service(state, "GROOMING")
    booking_state.change_option(state, "option_standard")
    booking_state.change_datetime(state, "datetime_a", ("2026-08-21", "10:00", None))
    state.booking.selected_slot_ref = "slot_1"
    state.booking.preview_ref = "preview_1"

    booking_state.reset(state)

    assert state.booking.service_type is None
    assert state.booking.pet_ref is None
    assert state.booking.selected_option_ref is None
    assert state.booking.datetime_ref is None
    assert state.booking.selected_slot_ref is None
    assert state.booking.preview_ref is None
    assert state.booking.revision == 0
