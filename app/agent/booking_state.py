"""Dependency invalidation for ConversationState.booking
(BookingWorkingState) — the ONLY intended way to mutate it.

Not a rigid step-by-step flow: the customer may still provide everything
in one message, revise one detail later, or jump straight to confirming.
This only encodes what changing ONE already-selected detail means for the
others — the same "package changes -> old availability is no longer
authoritative" reasoning app/tools/reference_tools.py's preview_booking
already applies at write time, applied one step earlier so a stale
selection is never even offered back to the customer as if it still
stood.

Each function is a no-op when the new value matches what's already
selected (nothing actually changed), so calling one on every tool
dispatch — regardless of whether this turn's message actually changed
anything — is always safe.
"""

from __future__ import annotations

from typing import Any


def change_pet(state: Any, pet_ref: str | None) -> None:
    booking = state.booking
    if not pet_ref or booking.pet_ref == pet_ref:
        return
    booking.pet_ref = pet_ref
    booking.selected_option_ref = None
    booking.selected_add_on_refs = []
    booking.selected_slot_ref = None
    booking.preview_ref = None
    booking.preferred_staff = None
    booking.revision += 1


def change_service(state: Any, service_type: str | None) -> None:
    booking = state.booking
    if not service_type:
        return
    service_type = service_type.upper()
    if booking.service_type == service_type:
        return
    booking.service_type = service_type
    booking.selected_option_ref = None
    booking.selected_add_on_refs = []
    booking.selected_slot_ref = None
    booking.preview_ref = None
    booking.preferred_staff = None
    booking.revision += 1


def change_option(state: Any, option_ref: str | None) -> None:
    booking = state.booking
    if not option_ref or booking.selected_option_ref == option_ref:
        return
    booking.selected_option_ref = option_ref
    # A package change means the old availability check is no longer
    # authoritative for it (different duration/room/price basis) — the old
    # preview definitely isn't.
    booking.selected_slot_ref = None
    booking.preview_ref = None
    booking.revision += 1


def change_datetime(state: Any, datetime_ref: str | None, signature: tuple) -> None:
    """`signature` is the resolved (date, time, period) the ref stands for
    — see BookingWorkingState.datetime_signature for why ref identity
    alone can't be used for change detection here."""
    booking = state.booking
    if not datetime_ref:
        return
    if booking.datetime_signature == signature:
        # Same date/time content, but keep the freshest ref for hydration
        # (an earlier ref for identical content may since have expired).
        booking.datetime_ref = datetime_ref
        return
    booking.datetime_ref = datetime_ref
    booking.datetime_signature = signature
    booking.selected_slot_ref = None
    booking.preview_ref = None
    booking.revision += 1


def change_check_out_datetime(state: Any, datetime_ref: str | None, signature: tuple) -> None:
    booking = state.booking
    if not datetime_ref:
        return
    if booking.check_out_datetime_signature == signature:
        booking.check_out_datetime_ref = datetime_ref
        return
    booking.check_out_datetime_ref = datetime_ref
    booking.check_out_datetime_signature = signature
    booking.selected_slot_ref = None
    booking.preview_ref = None
    booking.revision += 1


def change_staff(state: Any, preferred_staff: str | None) -> None:
    booking = state.booking
    preferred_staff = preferred_staff or None
    if booking.preferred_staff == preferred_staff:
        return
    booking.preferred_staff = preferred_staff
    booking.selected_slot_ref = None
    booking.preview_ref = None
    booking.revision += 1


def change_slot(state: Any, slot_ref: str | None) -> None:
    booking = state.booking
    if not slot_ref or booking.selected_slot_ref == slot_ref:
        return
    booking.selected_slot_ref = slot_ref
    # Picking a slot doesn't itself change any other booking-defining
    # condition, so no revision bump — only the (now superseded) preview
    # built from whatever slot was selected before is stale.
    booking.preview_ref = None


def clear_slot_and_preview(state: Any) -> None:
    """A genuine write-time rejection (a real staff/room/pet conflict, an
    eligibility check) means the previewed slot is no longer good — but,
    unlike a full reset(), the pet/service/option/date the customer chose
    to GET there are still perfectly valid to retry with a different time,
    so only the slot/preview are cleared here."""
    booking = state.booking
    booking.selected_slot_ref = None
    booking.preview_ref = None


def reset(state: Any) -> None:
    """A finished (successful or genuinely write-rejected) booking attempt
    leaves nothing behind for the next, possibly unrelated, request — same
    reasoning ConversationState.active_options/active_slots/
    daycare_duration_minutes are already reset for on confirm_booking
    success/rejection."""
    from app.context.booking_working_state import BookingWorkingState

    state.booking = BookingWorkingState()
