"""Verified-argument hydration — fills a dropped tool argument from
ConversationState.booking (BookingWorkingState) ONLY when the model
omitted it, and ONLY with something already verified/selected this
session. Never fills in anything the model didn't already establish
itself (a package, price, pet, or date the model never actually
resolved/selected is never invented here) — this is recovery from the
model forgetting to restate a fact it already has, not a substitute for
the model resolving/selecting it in the first place.

Real gap this closes: without it, a model that correctly remembers "the
customer picked Standard Bath" in its own reasoning could still drop
option_ref from a later check_availability call (a plain argument-
omission mistake, not a decision to change anything) and get a hard
UNKNOWN_OR_EXPIRED_OPTION_REF error instead of the call just working —
forcing either a wasted retry turn or, worse, the model reaching for
SOME option_ref instead (see app/agent/booking_state.py's docstring for
the exact live failure this produced)."""

from __future__ import annotations

from typing import Any


def hydrate_tool_args(state: Any, context: dict, tool_name: str, args: dict) -> dict:
    booking = state.booking
    args = dict(args)

    if tool_name == "check_availability":
        if not args.get("pet_ref") and booking.pet_ref:
            args["pet_ref"] = booking.pet_ref
        if not args.get("option_ref") and booking.selected_option_ref:
            args["option_ref"] = booking.selected_option_ref
        if not args.get("datetime_ref"):
            # The CURRENT message's own resolved date/time (if this turn
            # stated one) always wins over an older selection — a bare
            # omission recovers the LAST real selection, never something
            # staler than what the customer just said.
            if context.get("current_message_datetime_ref"):
                args["datetime_ref"] = context["current_message_datetime_ref"]
            elif booking.datetime_ref:
                args["datetime_ref"] = booking.datetime_ref
        if not args.get("preferred_staff") and booking.preferred_staff:
            args["preferred_staff"] = booking.preferred_staff

    if tool_name == "preview_booking":
        if not args.get("pet_ref") and booking.pet_ref:
            args["pet_ref"] = booking.pet_ref
        if not args.get("option_ref") and booking.selected_option_ref:
            args["option_ref"] = booking.selected_option_ref
        if not args.get("slot_ref") and booking.selected_slot_ref:
            args["slot_ref"] = booking.selected_slot_ref

    if tool_name == "confirm_booking":
        if not args.get("preview_ref") and booking.preview_ref:
            args["preview_ref"] = booking.preview_ref

    return args
