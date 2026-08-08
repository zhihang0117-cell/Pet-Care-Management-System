"""Authoritative argument preparation for booking-related tool calls."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def scope_repeat_history(
    args: dict,
    state: Any,
    user_message: str,
    *,
    explicit_service_type: Callable[[str], str],
) -> dict:
    if state.pet_id is not None:
        args = {**args, "pet_id": state.pet_id}
    repeat_service = (
        explicit_service_type(user_message)
        or str(state.service_type or "").strip().upper()
    )
    if repeat_service:
        args = {**args, "service_type": repeat_service}
    return args


def scope_catalogue_or_booking_pet(
    tool_name: str,
    args: dict,
    state: Any,
    user_message: str,
    *,
    known_pet_by_id: Callable[[Any, Any], dict | None],
    is_repeat_booking_request: Callable[[str], bool],
    explicit_service_type: Callable[[str], str],
) -> tuple[dict, dict | None]:
    """Bind catalogue/booking calls to one verified customer pet."""
    if tool_name not in {"get_booking_service_options", "create_booking"}:
        return args, None

    if len(state.known_pets) > 1:
        cached_pet = known_pet_by_id(state, state.pet_id)
        model_pet = known_pet_by_id(state, args.get("pet_id"))
        if state.pet_selected_turn == state.turn_counter:
            known = cached_pet
        elif cached_pet is not None and args.get("pet_id") in (None, ""):
            known = cached_pet
        elif (
            cached_pet is not None
            and model_pet is not None
            and model_pet.get("pet_id") == cached_pet.get("pet_id")
        ):
            known = model_pet
        else:
            known = None
        if known is None:
            return args, {
                "status": "missing_information",
                "error_code": "PET_SELECTION_REQUIRED",
                "message": (
                    "Several pets are registered and the current message did not "
                    "unambiguously select the pet represented by this tool call. "
                    "Ask which pet; do not choose or switch pet_id yourself."
                ),
            }
        args = {**args, "pet_id": known.get("pet_id")}
    else:
        known = known_pet_by_id(state, args.get("pet_id"))
        if known is None and state.pet_id is not None:
            args = {**args, "pet_id": state.pet_id}
            known = known_pet_by_id(state, state.pet_id)

    if tool_name == "create_booking" and known is not None:
        args = {**args, "pet_name": known.get("pet_name")}
        if state.service_type:
            args = {**args, "service_type": state.service_type}
    if tool_name == "get_booking_service_options" and is_repeat_booking_request(
        user_message
    ):
        repeat_service = (
            explicit_service_type(user_message)
            or str(state.service_type or "").strip().upper()
        )
        if repeat_service:
            args = {**args, "service_type": repeat_service}
    return args, None


def scope_availability_pet(
    args: dict,
    state: Any,
    *,
    known_pet_by_id: Callable[[Any, Any], dict | None],
) -> tuple[dict, dict | None]:
    if len(state.known_pets) > 1:
        known = known_pet_by_id(state, state.pet_id)
        if known is None:
            return args, {
                "status": "missing_information",
                "error_code": "PET_SELECTION_REQUIRED",
                "message": (
                    "Several pets are registered and none was selected for this "
                    "booking. Ask which pet before checking availability."
                ),
            }
        return {**args, "pet_id": known.get("pet_id")}, None
    if state.pet_id is not None:
        known = known_pet_by_id(state, args.get("pet_id"))
        args = {**args, "pet_id": (known or {}).get("pet_id") or state.pet_id}
    return args, None


def apply_repeat_availability_filters(
    tool_name: str,
    args: dict,
    state: Any,
    user_message: str,
    *,
    explicit_service_type: Callable[[str], str],
) -> dict:
    repeat_service = (
        explicit_service_type(user_message)
        or str(state.service_type or "").strip().upper()
    )
    resolved = state.current_datetime_resolution or {}
    time_filter = resolved.get("time") or resolved.get("period") or ""
    if tool_name == "check_availability":
        return {
            **args,
            "service_type": repeat_service or args.get("service_type"),
            "date": resolved.get("date") or args.get("date"),
            "time": time_filter or args.get("time") or "",
        }
    date_range = resolved.get("date_range") or {}
    return {
        **args,
        "service_type": repeat_service or args.get("service_type"),
        "start_date": date_range.get("start") or args.get("start_date"),
        "end_date": date_range.get("end") or args.get("end_date"),
        "time": time_filter or args.get("time") or "",
    }


def infer_document_service_type(args: dict, state: Any) -> dict:
    if not args.get("booking_id") or args.get("service_type"):
        return args
    try:
        requested_booking_id = int(args["booking_id"])
    except (TypeError, ValueError):
        requested_booking_id = None
    matching_booking = next(
        (
            booking
            for booking in (state.last_created_booking, state.latest_booking)
            if booking
            and booking.get("booking_id") == requested_booking_id
            and (booking.get("service_type") or booking.get("last_service_type"))
        ),
        None,
    )
    if not matching_booking:
        return args
    return {
        **args,
        "service_type": (
            matching_booking.get("service_type")
            or matching_booking.get("last_service_type")
        ),
    }


def restore_pending_change_target(tool_name: str, args: dict, state: Any) -> dict:
    pending = state.pending_booking_confirmation
    if not (
        tool_name in {"cancel_booking", "reschedule_booking"}
        and not args.get("booking_id")
        and args.get("confirm_pet_name")
        and pending
        and pending.get("tool") == tool_name
    ):
        return args
    return {
        **args,
        "booking_id": pending["booking_id"],
        "service_type": pending.get("service_type") or args.get("service_type"),
    }


def correct_change_service_type(tool_name: str, args: dict, state: Any) -> dict:
    if tool_name not in {"cancel_booking", "reschedule_booking"} or not args.get(
        "booking_id"
    ):
        return args
    try:
        target_booking_id = int(args["booking_id"])
    except (TypeError, ValueError):
        target_booking_id = None
    if target_booking_id is None:
        return args
    match = next(
        (
            option
            for option in state.offered_options
            if option.get("booking_id") == target_booking_id
            and option.get("service_type")
        ),
        None,
    )
    return {**args, "service_type": match["service_type"]} if match else args


def reject_changed_reschedule(
    tool_name: str,
    args: dict,
    state: Any,
    *,
    mutation_signature: Callable[[str, dict], str],
) -> dict | None:
    pending = state.pending_booking_confirmation
    if not (
        tool_name == "reschedule_booking"
        and args.get("confirm_pet_name")
        and pending
        and pending.get("tool") == "reschedule_booking"
    ):
        return None
    expected_signature = pending.get("change_signature")
    if expected_signature and mutation_signature("reschedule_booking", args) != expected_signature:
        return {
            "error": "RESCHEDULE_DETAILS_CHANGED",
            "message": (
                "The booking/date/time details differ from the change the customer "
                "was shown. No write occurred. Preview the new exact details and "
                "ask for confirmation again."
            ),
        }
    return None


def scope_policy_pet(args: dict, state: Any) -> dict:
    authoritative_species = str(state.pet_type or "").strip().lower()
    if authoritative_species not in {"dog", "cat"}:
        model_species = str(args.get("pet_type") or "").strip().lower()
        authoritative_species = model_species if model_species in {"dog", "cat"} else ""
    if authoritative_species:
        args = {**args, "pet_type": authoritative_species}
    if not args.get("pet_size") and state.pet_size:
        args = {**args, "pet_size": state.pet_size}
    return args
