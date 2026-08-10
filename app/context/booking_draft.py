"""BookingDraft — one consolidated view of an in-progress booking.

Phase 1 of REFACTOR_PLAN.md. Today's ConversationState (app/context/state.py)
spreads booking-in-progress facts across ~10 separate fields (pet_id,
service_type, preferred_staff, daycare_duration_minutes,
verified_service_options, verified_availability_slots, pending_actions,
resolved_dates, ...) that each grew independently to patch one specific bug.
BookingDraft is the single structure the rest of the refactor (reference-
based tools, preview_booking/confirm_booking) is meant to fill in and read
from instead.

This module is purely additive right now: derive_booking_draft() is a
READ-ONLY projection of the existing scattered fields, called from nowhere
in the live request path yet. Its only job in this phase is proving the
consolidated shape can actually represent every real in-progress booking
case the current fragmented fields handle — validated against real
ConversationState instances, not assumed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, fields
from typing import Any


@dataclass
class BookingDraft:
    pet_ref: str | None = None
    pet_name: str | None = None
    service_type: str | None = None

    option_ref: str | None = None
    option_name: str | None = None
    base_price: float | None = None

    add_on_ref: str | None = None
    add_on_name: str | None = None
    add_on_price: float | None = None

    check_in_date: str | None = None
    check_in_time: str | None = None
    check_out_date: str | None = None
    check_out_time: str | None = None

    duration_minutes: int | None = None
    preferred_staff: str | None = None
    care_notes: str | None = None

    # Opaque references a future reference-based tool layer (Phase 3) would
    # mint and resolve server-side — not real booking-table IDs. For now
    # these are best-effort synthetic keys derived from the same evidence
    # verified_availability_slots/pending_actions already carry.
    availability_ref: str | None = None
    preview_ref: str | None = None

    def is_ready_for_preview(self) -> bool:
        """Whether every fact preview_booking (Phase 4) would require is
        already present — mirrors create_booking's own current minimum
        (see app/tools/booking_tools.py's docstring), just expressed as one
        predicate over one object instead of scattered field checks."""
        if not (self.pet_ref and self.service_type and self.option_ref and self.base_price):
            return False
        if not (self.check_in_date and self.check_in_time):
            return False
        if self.service_type == "BOARDING" and not self.check_out_date:
            return False
        return bool(self.availability_ref)

    def as_public_dict(self) -> dict[str, Any]:
        """Non-null fields only — what AgentContext actually shows the model."""
        return {f.name: getattr(self, f.name) for f in fields(self) if getattr(self, f.name) is not None}


def derive_booking_draft(state: Any) -> BookingDraft:
    """Read-only projection of ConversationState's current booking-in-
    progress fields onto one BookingDraft. Never writes back to state."""
    draft = BookingDraft()

    draft.pet_ref = str(state.pet_id) if getattr(state, "pet_id", None) is not None else None
    draft.pet_name = getattr(state, "pet_name", None)
    draft.service_type = getattr(state, "service_type", None)

    # verified_service_options is a raw CATALOGUE cache — every option the
    # model was shown, services and add-ons mixed together with no marker
    # for which one (if any) the customer actually picked. Naively trusting
    # "the last cached entry" is wrong: confirmed live, it picked up a
    # stray add-on instead of the customer's real service choice the
    # moment more than one option had been shown. Only trust it when it is
    # genuinely unambiguous (exactly one non-add-on entry cached); otherwise
    # leave option_name/base_price unset rather than guess, and prefer the
    # pending preview's own args below once one exists — that is the one
    # place today's state layout captures an actual, unambiguous pick.
    options = getattr(state, "verified_service_options", None) or []
    service_options = [o for o in options if str(o.get("selection_kind") or "service") != "add_on"]
    if len(service_options) == 1:
        option = service_options[0]
        name = option.get("service_name") or option.get("room_type") or option.get("label")
        draft.option_ref = _synthetic_ref("option", name, option.get("price"))
        draft.option_name = name
        draft.base_price = option.get("price")

    preview_args = None
    pending_actions = getattr(state, "pending_actions", None) or {}
    preview = pending_actions.get("create_booking")
    if preview:
        preview_args = preview.get("args") or {}
        # The pending preview's own args are the one place a specific,
        # unambiguous selection genuinely exists today — override any
        # catalogue guess above with it.
        package_name = preview_args.get("package_name")
        if package_name:
            draft.option_ref = _synthetic_ref("option", package_name, preview_args.get("price"))
            draft.option_name = package_name
        if preview_args.get("price") is not None:
            draft.base_price = preview_args.get("price")
        add_on_name = preview_args.get("add_on")
        if add_on_name:
            draft.add_on_ref = _synthetic_ref("addon", add_on_name, preview_args.get("add_on_price"))
            draft.add_on_name = add_on_name
            draft.add_on_price = preview_args.get("add_on_price")

    # Same ambiguity as verified_service_options above, same fix: this
    # accumulates every slot ever verified this session (see
    # app/orchestrator.py's dedup-and-extend), not narrowed to "the one the
    # customer picked" — the last entry is only trustworthy when it is the
    # only one cached.
    slots = getattr(state, "verified_availability_slots", None) or []
    if len(slots) == 1:
        slot = slots[0]
        draft.check_in_date = slot.get("date")
        draft.check_in_time = slot.get("time")
        draft.check_out_date = slot.get("check_out_date") or None
        draft.check_out_time = slot.get("check_out_time") or None
        if slot.get("duration_minutes"):
            draft.duration_minutes = slot.get("duration_minutes")
        if slot.get("preferred_staff"):
            draft.preferred_staff = slot.get("preferred_staff")
        draft.availability_ref = _synthetic_ref(
            "slot", slot.get("service_type"), slot.get("date"), slot.get("time"),
            slot.get("room_type"), slot.get("check_out_date"), slot.get("check_out_time"),
        )

    if preview_args:
        # Overrides the single-slot guess above with the pending preview's
        # own date/time — the unambiguous "what's about to be booked" once
        # one exists.
        if preview_args.get("date"):
            draft.check_in_date = preview_args.get("date")
        if preview_args.get("time"):
            draft.check_in_time = preview_args.get("time")
        if preview_args.get("check_out_date"):
            draft.check_out_date = preview_args.get("check_out_date")
        if preview_args.get("check_out_time"):
            draft.check_out_time = preview_args.get("check_out_time")
        if preview_args.get("duration_minutes") is not None:
            draft.duration_minutes = preview_args.get("duration_minutes")
        if preview_args.get("preferred_staff"):
            draft.preferred_staff = preview_args.get("preferred_staff")
        if draft.availability_ref is None:
            draft.availability_ref = _synthetic_ref(
                "slot", draft.service_type, draft.check_in_date, draft.check_in_time,
                draft.option_name, draft.check_out_date, draft.check_out_time,
            )

    if not draft.preferred_staff:
        draft.preferred_staff = getattr(state, "preferred_staff", None)
    if not draft.duration_minutes and getattr(state, "daycare_duration_minutes", None):
        draft.duration_minutes = state.daycare_duration_minutes

    if preview:
        draft.preview_ref = preview.get("signature")

    return draft


def _synthetic_ref(*parts: object) -> str:
    """Placeholder ref for display purposes only — this module has no
    EvidenceStore of its own to mint a real one against (see
    app.agent.evidence.EvidenceStore, which Phase 3's actual reference
    tools use). Only needs to look like a ref, not resolve as one."""
    kind = parts[0] if parts else "ref"
    return f"{kind}_{uuid.uuid4().hex[:10]}"
