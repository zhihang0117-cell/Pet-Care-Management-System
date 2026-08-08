"""Deterministic guardrails: reject model-invented tool arguments, resolve
customer-owned references, and manage scenario/confirmation state.

None of this decides whether a tool call is a good idea — that's the
model's job, guided by the system prompt. This module only ever answers
narrow, mechanical questions ("did the customer actually say this, or did
the model fill in a plausible-looking value?", "which pet/scenario does
this session actually mean?") and returns either an accepted/corrected
value or a rejection dict the tool-calling loop feeds back so the model can
retry with real customer input instead of an invented one.

Organized in five sections:
  1. Pet/species resolution
  2. Confirmation-phrase classification
  3. Scenario state transitions
  4. Customer-text/evidence matching helpers
  5. Tool-argument scoping and rejection guardrails (the bulk of this file)
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.scenarios.loader import load_scenario

# =============================================================================
# 1. Pet/species resolution — resolve customer-owned pet references without
#    trusting a model-selected identifier.
# =============================================================================

DOG_WORDS_RE = re.compile(r"\b(?:dog|puppy|anjing)\b|犬|狗")
CAT_WORDS_RE = re.compile(r"\b(?:cat|kitten|kucing)\b|猫|貓")


def stated_species(user_message: str) -> str | None:
    text = str(user_message or "").casefold()
    has_dog = bool(DOG_WORDS_RE.search(text))
    has_cat = bool(CAT_WORDS_RE.search(text))
    if has_dog and not has_cat:
        return "dog"
    if has_cat and not has_dog:
        return "cat"
    return None


def known_pet_by_id(state: Any, pet_id: object) -> dict | None:
    """Return an owned roster entry, never a model-only pet identifier."""
    if pet_id is None or str(pet_id).strip() == "":
        return None
    try:
        target = int(pet_id)
    except (TypeError, ValueError):
        return None
    return next(
        (
            pet
            for pet in state.known_pets
            if str(pet.get("pet_id")) == str(target)
        ),
        None,
    )


def match_named_pet(state: Any, user_message: str) -> None:
    """Cache one unambiguous name, other-pet, or unique-species reference."""
    if not state.known_pets or not user_message:
        return
    text = user_message.casefold()

    def name_appears(name: object) -> bool:
        normalized = str(name or "").strip().casefold()
        if not normalized:
            return False
        if re.search(r"[㐀-鿿]", normalized):
            return normalized in text
        return bool(re.search(rf"\b{re.escape(normalized)}\b", text))

    matches = [
        pet for pet in state.known_pets if name_appears(pet.get("pet_name"))
    ]
    if not matches and len(state.known_pets) == 2 and re.search(
        r"\b(?:the\s+)?other\s+(?:one|pet)\b|另一个|另一只|另外一个|另外一只|"
        r"\b(?:yang\s+)?satu\s+lagi\b",
        text,
        re.IGNORECASE,
    ):
        previous = known_pet_by_id(state, state.pet_id)
        if previous is not None:
            matches = [
                pet
                for pet in state.known_pets
                if str(pet.get("pet_id")) != str(previous.get("pet_id"))
            ]
    if not matches:
        species = stated_species(user_message)
        if species:
            species_matches = [
                pet
                for pet in state.known_pets
                if str(pet.get("pet_type") or "").strip().casefold() == species
            ]
            if len(species_matches) == 1:
                matches = species_matches
    if len(matches) != 1:
        return

    selected = matches[0]
    state.pet_id = selected.get("pet_id")
    state.pet_type = selected.get("pet_type")
    state.pet_name = selected.get("pet_name")
    state.pet_size = selected.get("pet_size")
    state.pet_breed = selected.get("pet_breed")
    state.pet_selected_turn = state.turn_counter


# =============================================================================
# 2. Confirmation-phrase classification — fail-closed natural-language
#    affirmative/negative detection for standalone confirmation turns.
# =============================================================================

AFFIRMATIVE_RE = re.compile(
    r"^(?:yes|y|correct|confirm(?:ed)?|proceed|continue|go\s+ahead|book\s+it|"
    r"do\s+it|ok(?:ay)?|sure|ya|boleh|ya\s+boleh|teruskan|sahkan|可以|确认|"
    r"確認|正确|正確|对|對|没错|沒錯|是的|好|好的|同意|继续|繼續|没问题|"
    r"沒問題)$",
    re.IGNORECASE,
)
NEGATIVE_RE = re.compile(
    r"^(?:no(?:\s*no)?|n|none|cancel|stop|don't|do\s+not|tak|tidak|jangan|"
    r"不要|取消|不用|不确认|不確認)$",
    re.IGNORECASE,
)


def confirmation_intent(user_message: str) -> str | None:
    """Classify only a standalone affirmative/negative authorization.

    Politeness and repeated affirmative atoms are accepted, while any business
    detail left after normalization makes the message non-standalone.
    """
    compact = re.sub(
        r"[\s.!?,，。！？]+", " ", str(user_message or "").strip()
    ).strip()
    compact = re.sub(
        r"\b(?:please|kindly)\b|请|請|麻烦|麻煩",
        " ",
        compact,
        flags=re.IGNORECASE,
    )
    compact = re.sub(r"\s+", " ", compact).strip()
    if AFFIRMATIVE_RE.fullmatch(compact):
        return "affirmative"

    affirmative_atom = (
        r"(?:yes|y|correct|confirm(?:ed)?|proceed|continue|go\s+ahead|"
        r"book\s+it|do\s+it|ok(?:ay)?|sure|ya|boleh|teruskan|sahkan)"
    )
    if re.fullmatch(
        rf"{affirmative_atom}(?:\s+{affirmative_atom})+",
        compact,
        re.IGNORECASE,
    ):
        return "affirmative"

    cjk_compact = re.sub(r"\s+", "", compact)
    if re.fullmatch(
        r"(?:(?:可以|确认|確認|正确|正確|对|對|没错|沒錯|是的|好|好的|"
        r"同意|继续|繼續|没问题|沒問題)){1,4}(?:预约|預約)?",
        cjk_compact,
    ):
        return "affirmative"

    compact = re.sub(
        r"\s+(?:thanks?|thank\s+you)$", "", compact, flags=re.IGNORECASE
    ).strip()
    if NEGATIVE_RE.fullmatch(compact):
        return "negative"
    return None


# =============================================================================
# 3. Scenario state transitions
# =============================================================================

SCENARIO_CONFIRMING_TOOLS = {
    "create_booking": "MAKE_BOOKING",
    "cancel_booking": "CANCEL_BOOKING",
    "reschedule_booking": "RESCHEDULE_BOOKING",
    "redeem_reward": "LOYALTY_QUERY",
    "register_loyalty_member": "MEMBER",
    "send_booking_confirmation": "BOOKING_DOCUMENT",
}


def set_objective_from_scenario(state, scenario_name: str | None) -> None:
    if not scenario_name:
        state.objective = None
        return
    try:
        state.objective = load_scenario(scenario_name).get("goal")
    except ValueError:
        state.objective = None


def apply_scenario_update(state, result: dict) -> None:
    """Apply an observed update_conversation_state result to session state."""
    if not isinstance(result, dict) or result.get("error"):
        return

    previous_scenario = state.active_scenario
    requested_scenario = result.get("active_scenario")
    preserve_main_goal_for_side_question = bool(
        previous_scenario == "MAKE_BOOKING"
        and requested_scenario in {"POLICY_QUERY", "MEMBER"}
    )
    if preserve_main_goal_for_side_question:
        return

    state.active_scenario = requested_scenario
    state.current_step = result.get("current_step")
    if result.get("service_type"):
        state.service_type = result.get("service_type")
    elif state.active_scenario != "MAKE_BOOKING":
        state.service_type = None
    if str(state.service_type or "").upper() != "DAYCARE":
        state.daycare_duration_minutes = None
        state.verified_facts.pop("daycare_duration_minutes", None)
    if previous_scenario != state.active_scenario:
        if state.active_scenario == "MAKE_BOOKING":
            state.booking_flow_started_turn = state.turn_counter
        elif previous_scenario == "MAKE_BOOKING":
            state.booking_flow_started_turn = None
        state.pending_actions = {}
        state.pending_booking_confirmation = None
        state.offered_options = []
        state.offered_add_on_options = []
        state.missing_information = []
        state.missing_information_by_tool = {}
        if (
            state.active_scenario == "MAKE_BOOKING"
            and len(state.known_pets) > 1
            and state.pet_selected_turn != state.turn_counter
        ):
            state.pet_id = None
            state.pet_type = None
            state.pet_name = None
            state.pet_size = None
            state.pet_breed = None
    if previous_scenario == "MAKE_BOOKING" and state.active_scenario != "MAKE_BOOKING":
        state.preferred_staff = None
        state.loyalty_decision = None
        state.loyalty_offer_shown_turn = None
        state.repeat_booking_template = None
        state.pending_actions.pop("create_booking", None)
        state.verified_availability_slots = []
    set_objective_from_scenario(state, state.active_scenario)
    state.completion_status = "in_progress" if state.active_scenario else None


def sync_scenario_from_tool_call(state, tool_name: str, result: dict) -> None:
    """Release scenario routing after its concrete action succeeds."""
    confirmed = SCENARIO_CONFIRMING_TOOLS.get(tool_name)
    if (
        confirmed
        and confirmed == state.active_scenario
        and isinstance(result, dict)
        and result.get("status") == "success"
    ):
        state.active_scenario = None
        state.current_step = None
        state.service_type = None
        state.objective = None
        state.offered_options = []
        state.offered_add_on_options = []
        state.missing_information = []
        state.missing_information_by_tool = {}


# =============================================================================
# 4. Customer-text/evidence matching helpers
# =============================================================================

ADD_ON_REFERENCE_RE = re.compile(
    r"\badd[\s-]?on(?:s)?\b|附加(?:服务|服務)?|加购|加購|额外(?:服务|服務)?|"
    r"\b(?:tambahan|add-on)\b",
    re.IGNORECASE,
)


def recent_customer_text(
    state: Any, user_message: str, *, since_turn: int | None = None
) -> str:
    return " ".join(
        [str(user_message or "")]
        + [
            str(turn.get("content") or "")
            for turn in state.history
            if turn.get("role") == "human"
            and (
                since_turn is None
                or (
                    turn.get("turn") is not None
                    and int(turn.get("turn")) >= since_turn
                )
            )
        ]
    )


def customer_stated_value(value: object, customer_text: str) -> bool:
    def compact(item: object) -> str:
        return re.sub(r"[^\w㐀-鿿]+", "", str(item or "").casefold())

    needle = compact(value)
    return bool(needle and needle in compact(customer_text))


def reject_unconfirmed_optional_booking_fields(
    state: Any,
    args: dict,
    user_message: str,
    *,
    detect_ordinal_index: Callable[[str], int | None],
    normalize_option_label: Callable[[object], str],
) -> dict | None:
    """Reject staff/add-on values not authorized by this booking flow."""
    customer_text = recent_customer_text(
        state,
        user_message,
        since_turn=state.booking_flow_started_turn or state.turn_counter,
    )
    preferred_staff = str(args.get("preferred_staff") or "").strip()
    no_staff_preference = bool(re.search(
        r"\b(?:any|no\s+preferred|no\s+preference|whichever|whoever)\s+"
        r"(?:available\s+)?staff\b|\banyone\s+(?:available|is\s+fine)\b|"
        r"任何(?:员工|員工)|谁都可以|誰都可以|随便(?:哪位)?|隨便(?:哪位)?|"
        r"\b(?:mana-mana|tiada\s+pilihan)\s+(?:staf|staff)\b",
        str(user_message or ""),
        re.IGNORECASE,
    ))
    if no_staff_preference:
        state.preferred_staff = None
    if (
        preferred_staff
        and not no_staff_preference
        and str(state.preferred_staff or "").strip().casefold()
        != preferred_staff.casefold()
        and not customer_stated_value(preferred_staff, customer_text)
    ):
        return {
            "error": "UNCONFIRMED_PREFERRED_STAFF",
            "message": (
                "preferred_staff was not named by the customer. Leave it blank so staff "
                "assignment remains automatic, or ask for their preference."
            ),
        }
    if preferred_staff and no_staff_preference:
        return {
            "error": "STAFF_PREFERENCE_DECLINED",
            "message": (
                "The customer explicitly said any available staff is acceptable for this "
                "booking. Leave preferred_staff blank."
            ),
        }

    add_on = str(args.get("add_on") or "").strip()
    if not add_on:
        return None
    add_on_declined = bool(re.search(
        r"\b(?:no|without|skip)\s+add[\s-]?ons?\b|"
        r"\b(?:don['’]?t|do\s+not)\s+add\b|"
        r"不要(?:附加|加购|加購)|不用(?:附加|加购|加購)|不需要附加|"
        r"\b(?:tak|tidak)\s+(?:mahu|nak)\s+(?:tambahan|add[\s-]?on)\b",
        str(user_message or ""),
        re.IGNORECASE,
    ))
    if add_on_declined:
        state.verified_facts["add_on_decision"] = {
            "value": "declined",
            "source": "customer_message",
            "turn": state.turn_counter,
        }
        return {
            "error": "ADD_ON_DECLINED",
            "message": (
                "The customer explicitly declined add-ons for this booking. "
                "Leave add_on and add_on_price blank."
            ),
        }
    if customer_stated_value(add_on, customer_text):
        return None

    # A standalone "yes"/"confirm" directly answering the assistant's own
    # immediately-preceding add-on question IS an explicit selection — the
    # customer is not expected to retype the add-on's name back verbatim
    # just to agree to a yes/no question the assistant itself asked.
    # Confirmed live: a customer replying "yes! confirm!!!!!!!!" to "Would
    # you like to include the add-on... Please confirm!" was rejected on
    # every retry because customer_text never literally contained the
    # add-on's name, trapping the conversation in an unbreakable loop.
    previous_reply = next(
        (
            str(turn.get("content") or "")
            for turn in reversed(state.history)
            if turn.get("role") == "ai"
        ),
        "",
    )
    if (
        ADD_ON_REFERENCE_RE.search(previous_reply)
        and confirmation_intent(user_message) == "affirmative"
    ):
        return None

    selected_index = detect_ordinal_index(user_message)
    add_on_reference = bool(ADD_ON_REFERENCE_RE.search(str(user_message or "")))
    selected = (
        state.offered_add_on_options[selected_index - 1]
        if selected_index is not None
        and add_on_reference
        and 1 <= selected_index <= len(state.offered_add_on_options)
        else None
    )
    if (
        isinstance(selected, dict)
        and str(selected.get("selection_kind") or "") == "add_on"
        and normalize_option_label(
            selected.get("service_name") or selected.get("label")
        ) == normalize_option_label(add_on)
    ):
        return None

    repeat = state.repeat_booking_template or {}
    if (
        repeat.get("catalogue_validated")
        and normalize_option_label(repeat.get("add_on"))
        == normalize_option_label(add_on)
    ):
        return None
    return {
        "error": "UNCONFIRMED_ADD_ON_SELECTION",
        "message": (
            "The add-on is real but the customer did not select it. Do not add optional "
            "services automatically; ask for an explicit choice."
        ),
    }


def service_options_evidence_matches(
    state: Any,
    user_message: str,
    explicit_service_type: Callable[[str], str],
) -> bool:
    evidence = (state.verified_facts or {}).get("service_options")
    if not isinstance(evidence, dict) or evidence.get("status") != "success":
        return False
    args = evidence.get("args")
    if not isinstance(args, dict) or not args:
        return False
    requested_service = (
        explicit_service_type(user_message)
        or str(state.service_type or "").strip().upper()
    )
    evidence_service = str(args.get("service_type") or "").strip().upper()
    if requested_service and evidence_service != requested_service:
        return False
    if (
        state.pet_id is not None
        and requested_service == "GROOMING"
        and args.get("pet_id") in (None, "")
    ):
        return False
    if state.pet_id is not None and args.get("pet_id") not in (None, ""):
        try:
            if int(args["pet_id"]) != int(state.pet_id):
                return False
        except (TypeError, ValueError):
            return False
    return True


def policy_evidence_matches(
    state: Any,
    user_message: str,
    explicit_service_type: Callable[[str], str] = lambda _text: "",
) -> bool:
    evidence = (state.verified_facts or {}).get("policy_knowledge")
    if not isinstance(evidence, dict) or evidence.get("status") != "success":
        return False
    if evidence.get("turn") == state.turn_counter:
        return True
    args = evidence.get("args")
    query = str((args or {}).get("query") or "") if isinstance(args, dict) else ""
    if not query:
        return False

    # A word-overlap match below can be right for the wrong reason: "pickup"
    # and "hours" are shared by "grooming pickup hours" and "daycare pickup
    # hours", but those are two different services' policies. Only reject
    # when BOTH turns name an explicit, DIFFERENT service — an unstated
    # service on either side stays ambiguous-but-permitted, same as
    # service_options_evidence_matches above.
    current_service = explicit_service_type(user_message)
    previous_service = explicit_service_type(query)
    if current_service and previous_service and current_service != previous_service:
        return False

    def compact(value: str) -> str:
        return re.sub(r"[^a-z0-9㐀-鿿]+", "", value.casefold())

    current = compact(str(user_message or ""))
    previous = compact(query)
    if previous in current or current in previous:
        return True

    stop_words = {
        "what", "whats", "your", "about", "policy", "policies",
        "company", "please", "rule", "rules", "allowed", "required",
        "polisi", "syarat",
    }
    current_terms = {
        term
        for term in re.findall(r"[a-z0-9]{4,}", str(user_message or "").casefold())
        if term not in stop_words
    }
    previous_terms = {
        term
        for term in re.findall(r"[a-z0-9]{4,}", query.casefold())
        if term not in stop_words
    }
    return bool(current_terms & previous_terms)


# =============================================================================
# 5. Tool-argument scoping and rejection guardrails
# =============================================================================

# Every date argument each date-sensitive tool takes — used to enforce that
# it was actually produced by resolve_datetime (see reject_unverified_date)
# rather than computed/guessed by the model, which has been observed live,
# repeatedly, even after resolve_datetime existed and was available to call.
DATE_ARG_NAMES = {
    "check_availability": ("date", "check_out_date"),
    "check_availability_range": ("start_date", "end_date"),
    "create_booking": ("date", "check_out_date"),
    "reschedule_booking": ("new_date", "new_check_out_date"),
}


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


def reject_unverified_date(state: Any, tool_name: str, args: dict) -> dict | None:
    """None if every date argument present has actually come out of a
    resolve_datetime call this session; otherwise a rejection dict."""
    arg_names = DATE_ARG_NAMES.get(tool_name)
    if not arg_names:
        return None
    for arg_name in arg_names:
        value = args.get(arg_name)
        if value and value not in state.resolved_dates:
            return {
                "error": "UNVERIFIED_DATE",
                "message": (
                    f"{arg_name} ({value!r}) has not come out of a resolve_datetime "
                    "call this conversation. Call resolve_datetime on the customer's "
                    "ORIGINAL wording for this date, then retry with its exact date "
                    "output — never compute, guess, or reuse a self-adjusted date value."
                ),
            }
    return None


def reject_unconfirmed_height(state: Any, args: dict, user_message: str) -> dict | None:
    """
    None if some number in height_text actually appears (as a real
    number, not just any digit) somewhere in what the customer has
    actually said this session; otherwise a rejection dict. create_pet
    requiring height_text doesn't stop the model from simply inventing a
    plausible number instead of really asking — confirmed live
    (height_cm=30 sent with no customer message ever containing that
    number, back when this took an already-converted cm value). Checked
    against the raw number in height_text rather than a converted cm
    value, since e.g. "24 inches" only ever appears as "24" in what the
    customer actually typed. This can't verify the number is TRUE, only
    that it was actually said by the customer rather than fabricated
    wholesale.
    """
    height_text = args.get("height_text")
    if not height_text:
        return None
    stated_numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(height_text))]
    if not stated_numbers:
        return None
    # state.history is already capped to MAX_HISTORY_TURNS complete
    # human/AI pairs. Slicing its last 8 entries meant only about four
    # customer answers survived validation, which is shorter than the
    # customer+pet profile flow itself and caused earlier breed/species
    # answers to be rejected at the final create_pet call.
    recent_human_text = " ".join(
        [user_message or ""]
        + [t["content"] for t in state.history if t.get("role") == "human"]
    )
    mentioned_numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", recent_human_text)]
    if any(abs(n - s) <= 2 for n in mentioned_numbers for s in stated_numbers):
        return None
    return {
        "error": "UNCONFIRMED_HEIGHT",
        "message": (
            f"height_text ({height_text!r}) does not match any number the customer "
            "has actually said this conversation. Do not invent/estimate a height — "
            "ask the customer directly for their pet's height (any unit is fine — cm, "
            "inches, feet; a rough estimate is fine if they don't know exactly), then "
            "retry create_pet with their exact wording."
        ),
    }


def reject_species_mismatch(state: Any, args: dict, user_message: str) -> dict | None:
    """None unless the customer's current message states a species that
    contradicts the specific pet this tool call is about to use.

    Confirmed live: a customer asked about "Anjing" (Malay for dog)
    pricing while their only registered pet (Milo) is a real Cat — the
    model reflexively reused Milo's pet_id (system prompt: "when exactly
    one known pet exists, use it without asking which pet") and silently
    answered with Milo's cat pricing instead of noticing the mismatch.
    get_booking_service_options has no way to be asked for a species'
    pricing independent of a specific registered pet_id, so the model
    had no better tool call available either way — the fix is to force
    it to ask instead of silently guessing which pet/species is meant.

    Covers any tool call carrying a resolved pet_id (a DB lookup for
    that pet's real species), plus retrieve_policy calls backfilled from
    the session's cached state.pet_type a few lines above — both are the
    same failure shape: a known species silently overriding what the
    customer just said this turn.
    """
    stated = stated_species(user_message)
    if stated is None:
        return None

    known_species = ""
    pet_label = "the pet on file"
    pet_id = args.get("pet_id")
    if pet_id:
        if state.customer_id is None:
            return {
                "error": "PET_OWNERSHIP_UNVERIFIED",
                "message": (
                    "A pet_id cannot be used until the customer identity is resolved. "
                    "Resolve/create the customer and their pet first."
                ),
            }
        # Identity hydration and create_pet both maintain an owned roster.
        # Reuse that authoritative application state instead of issuing a
        # second, differently-scoped Supabase query from the orchestrator.
        # This keeps ownership in one boundary and also prevents lookup
        # exceptions here from escaping before the normal tool error path.
        known_pet = known_pet_by_id(state, pet_id)
        if known_pet is None:
            return {
                "error": "PET_NOT_OWNED_BY_CUSTOMER",
                "message": (
                    "The supplied pet_id does not belong to the resolved customer. "
                    "Use the customer's verified pet roster; do not retry with guessed IDs."
                ),
            }
        known_species = str(known_pet.get("pet_type") or "").strip().lower()
        pet_label = known_pet.get("pet_name") or pet_label
    elif args.get("pet_type"):
        known_species = str(args.get("pet_type") or "").strip().lower()
        pet_label = "the pet already on file"

    if known_species not in ("dog", "cat") or known_species == stated:
        return None

    return {
        "error": "SPECIES_MISMATCH",
        "message": (
            f"The customer's message says {stated!r}, but this call is about to use "
            f"{pet_label}'s real species ({known_species!r}). Do not silently answer "
            f"with {pet_label}'s species/pricing — ask the customer directly whether "
            f"they mean {pet_label} or a different pet before calling this tool again."
        ),
    }


def reject_unconfirmed_breed(state: Any, args: dict, user_message: str) -> dict | None:
    """Require breed to come from the customer without enforcing a breed list.

    Exact wording, a meaningful word overlap (for example, "golden" ->
    "Golden Retriever"), and common multilingual mixed/unknown answers are
    accepted. This prevents the model from filling a required schema field
    with an invented breed while leaving customers free to describe it in
    their own words.
    """
    breed = str(args.get("breed") or "").strip()
    if not breed or breed == "-":
        return {
            "error": "MISSING_BREED",
            "message": (
                "Breed has not been provided. Ask the customer for it without using a "
                "fixed script; if they do not know or the pet is mixed, preserve that answer."
            ),
        }

    generic_species = {
        "dog", "cat", "canine", "feline", "犬", "狗", "猫", "貓",
        "anjing", "kucing",
    }
    if breed.casefold() in generic_species:
        return {
            "error": "SPECIES_IS_NOT_BREED",
            "message": (
                f"{breed!r} is a species, not a breed. Ask for the breed, or use "
                "'unknown' only after the customer says they do not know."
            ),
        }

    recent_human_text = " ".join(
        [user_message or ""]
        + [turn["content"] for turn in state.history if turn.get("role") == "human"]
    )

    def compact(value: str) -> str:
        return re.sub(r"[^\w㐀-鿿]+", "", value.casefold())

    compact_breed = compact(breed)
    compact_human = compact(recent_human_text)
    if compact_breed and compact_breed in compact_human:
        return None

    # Permit useful natural shorthand without requiring the tool argument
    # to be a byte-for-byte copy of the customer's sentence.
    meaningful_parts = [
        compact(part)
        for part in re.split(r"[^\w㐀-鿿]+", breed)
        if len(compact(part)) >= 3
    ]
    if any(part in compact_human for part in meaningful_parts):
        return None

    unknown_markers = {
        "unknown", "unsure", "notsure", "dontknow", "donotknow", "noidea",
        "taktahu", "tidaktahu", "kurangpasti",
        "不知道", "不清楚", "不确定", "不確定", "不晓得", "不曉得",
    }
    mixed_markers = {
        "mixed", "mixedbreed", "crossbreed", "mongrel", "kacukan",
        "混种", "混種", "米克斯", "串串",
    }
    normalized_answer = compact_breed
    if normalized_answer in {"unknown", "unsure"} and any(
        compact(marker) in compact_human for marker in unknown_markers
    ):
        return None
    if normalized_answer in {"mixed", "mixedbreed"} and any(
        compact(marker) in compact_human for marker in mixed_markers
    ):
        return None

    return {
        "error": "UNCONFIRMED_BREED",
        "message": (
            f"breed ({breed!r}) is not supported by anything the customer has said in "
            "this conversation. Do not infer it; ask naturally, and accept mixed or "
            "unknown when that is the customer's answer."
        ),
    }


def canonicalize_explicit_breed_answer(args: dict, user_message: str) -> dict:
    """Repair only unambiguous mixed/unknown answers from this turn.

    The model has repeatedly mapped "I don't know the breed" to "mixed",
    or sent the species as breed after the customer answered "mixed". Both
    lose the customer's meaning and then trigger a guard rejection loop.
    Current-turn wording is authoritative enough to canonicalize these two
    special answers without guessing an actual breed.
    """
    text = str(user_message or "").casefold()
    mixed_answer = bool(
        re.search(
            r"\b(?:mixed\s*breed|cross\s*breed|crossbreed|mongrel|kacukan)\b"
            r"|混种|混種|米克斯|串串",
            text,
        )
    )
    unknown_answer = bool(
        re.search(
            r"(?:\b(?:don['’]?t|do\s+not|not\s+sure|unsure|unknown|no\s+idea)\b"
            r".{0,24}\b(?:breed|kind)\b)"
            r"|(?:\b(?:breed|kind)\b.{0,24}"
            r"\b(?:don['’]?t\s+know|do\s+not\s+know|not\s+sure|unknown)\b)"
            r"|(?:(?:不知道|不清楚|不确定|不確定|不晓得|不曉得).{0,8}(?:品种|品種))"
            r"|(?:(?:品种|品種).{0,8}(?:不知道|不清楚|不确定|不確定|不晓得|不曉得))"
            r"|(?:\b(?:tak\s+tahu|tidak\s+tahu|kurang\s+pasti)\b.{0,24}"
            r"\b(?:baka|breed)\b)",
            text,
        )
    )
    if mixed_answer:
        return {**args, "breed": "mixed"}
    if unknown_answer:
        return {**args, "breed": "unknown"}
    return args


def reject_unconfirmed_registration_fields(
    state: Any, tool_name: str, args: dict, user_message: str
) -> dict | None:
    """Block profile fields the model invented instead of collecting."""
    customer_text = recent_customer_text(state, user_message)
    if tool_name == "create_customer":
        full_name = str(args.get("full_name") or "").strip()
        if not customer_stated_value(full_name, customer_text):
            return {
                "error": "UNCONFIRMED_CUSTOMER_NAME",
                "message": (
                    "full_name was not stated by the customer in this conversation. "
                    "Ask for their name and preserve exactly what they provide."
                ),
            }
        address = str(args.get("address") or "").strip()
        if address and address != "-" and not customer_stated_value(address, customer_text):
            return {
                "error": "UNCONFIRMED_CUSTOMER_ADDRESS",
                "message": "address was not stated by the customer; omit it instead of inventing one.",
            }
        return None

    if tool_name == "create_pet":
        pet_name = str(args.get("pet_name") or "").strip()
        if not customer_stated_value(pet_name, customer_text):
            return {
                "error": "UNCONFIRMED_PET_NAME",
                "message": "pet_name was not stated by the customer; ask for it instead of inventing one.",
            }
        pet_type = str(args.get("pet_type") or "").strip().casefold()
        species_words = {
            "dog": DOG_WORDS_RE,
            "cat": CAT_WORDS_RE,
        }
        if pet_type in species_words and not species_words[pet_type].search(customer_text.casefold()):
            return {
                "error": "UNCONFIRMED_PET_TYPE",
                "message": "pet_type was not stated by the customer; ask whether the pet is a cat or dog.",
            }
    return None


def reject_unconfirmed_vaccination_expiry(
    state: Any, args: dict, user_message: str
) -> dict | None:
    value = str(args.get("vaccination_expiry_text") or "").strip()
    customer_text = recent_customer_text(state, user_message)
    if value and customer_stated_value(value, customer_text):
        return None
    return {
        "error": "UNCONFIRMED_VACCINATION_EXPIRY",
        "message": (
            "vaccination_expiry_text must be the customer's own date wording. "
            "Ask for the expiry date and pass their wording through unchanged."
        ),
    }


def reject_mismatched_coupon(state: Any, args: dict, user_message: str) -> dict | None:
    """
    None if redeem_reward's coupon_id matches what the customer actually
    asked for by RM amount in recent conversation, or if there's nothing
    to cross-check against; otherwise a rejection dict.

    Confirmed live: customer asked "can I use the RM20 voucher?", model
    replied "I've submitted the request to redeem your RM20 voucher",
    but the actual redeem_reward call used the RM10 Voucher's coupon_id
    (200 points requested, not RM20's 400) — a real, silent swap that
    would have shown staff the wrong voucher to approve. Only rejects
    when a DIFFERENT cached coupon's amount was recently mentioned and
    the chosen one's wasn't — never blocks a redemption made without
    ever stating an RM amount at all (e.g. "redeem points for a free
    grooming session" has no number to check).
    """
    if not state.known_coupons:
        return {
            "error": "NO_VERIFIED_COUPON",
            "message": (
                "No eligible coupon has been observed from check_coupon_eligibility. "
                "Fetch eligibility and use the customer's exact selected coupon."
            ),
        }
    try:
        coupon_id = int(args.get("coupon_id"))
    except (TypeError, ValueError):
        return {
            "error": "UNVERIFIED_COUPON_ID",
            "message": "coupon_id must be an exact ID from the verified eligible coupon list.",
        }
    chosen = next((c for c in state.known_coupons if c.get("coupon_id") == coupon_id), None)
    if chosen is None:
        return {
            "error": "UNVERIFIED_COUPON_ID",
            "message": f"coupon_id {coupon_id!r} is not in the verified eligible coupon list.",
        }
    recent_human_text = " ".join(
        [user_message or ""]
        + [t["content"] for t in state.history[-8:] if t.get("role") == "human"]
    ).lower()
    mentioned_amounts = set(re.findall(r"rm\s*(\d+)", recent_human_text))
    if not mentioned_amounts:
        return None
    chosen_amount = str(chosen.get("discount_value") or "").strip()
    if chosen_amount in mentioned_amounts:
        return None
    better = next(
        (
            c for c in state.known_coupons
            if str(c.get("discount_value") or "").strip() in mentioned_amounts
            and c.get("coupon_id") != coupon_id
        ),
        None,
    )
    if better is None:
        return None
    return {
        "error": "COUPON_MISMATCH",
        "message": (
            f"coupon_id {coupon_id} is {chosen.get('reward_name')!r}, but the customer's "
            f"recent messages mention RM{'/RM'.join(sorted(mentioned_amounts))} — that "
            f"matches {better.get('reward_name')!r} (coupon_id {better.get('coupon_id')}) "
            "instead. Retry redeem_reward with the coupon_id that actually matches what "
            "the customer asked for."
        ),
    }


def reject_unverified_payment_id(state: Any, args: dict) -> dict | None:
    """
    None if redeem_reward's payment_id matches the real payment_id from
    this session's most recent successful create_booking, or if there's
    no such cached value to check against (e.g. redeeming against a
    booking from an earlier session); otherwise a rejection dict.

    Confirmed live: in the very same turn create_booking returned a real
    payment_id (636) in its own result, the model's redeem_reward call
    used payment_id=1 instead — a fabricated placeholder, not anything
    create_booking actually returned. redeem_reward's own docstring
    already says never to guess this; this is the deterministic backstop
    for when that gets ignored anyway.
    """
    # This is only a same-session cross-check: if create_booking hasn't run
    # this session, there is no cached payment_id to compare against, and
    # that's the ordinary, legitimate case of a customer redeeming against a
    # booking from an EARLIER session (as the docstring above says) — not
    # something to block. Confirmed live: this used to reject every such
    # redemption ("No payment_id from a successful create_booking exists in
    # this session"), trapping a customer who wanted to redeem points on an
    # older unpaid booking with no way through. The real ownership/validity
    # check for payment_id still happens at the data layer (redeem_reward
    # only accepts a payment_id that actually belongs to this customer); this
    # guardrail's own job is narrower — only catch the model substituting a
    # DIFFERENT id than the one it just verified this exact turn.
    if state.last_created_payment_id is None:
        return None
    try:
        given = int(args.get("payment_id"))
    except (TypeError, ValueError):
        given = None
    if given == state.last_created_payment_id:
        return None
    return {
        "error": "UNVERIFIED_PAYMENT_ID",
        "message": (
            f"payment_id {args.get('payment_id')!r} does not match {state.last_created_payment_id}, "
            "the real payment_id this session's own create_booking result actually returned. "
            "Never guess or invent a payment_id — retry redeem_reward with the exact "
            "payment_id (or _internal_payment_id) value from that result."
        ),
    }


def strip_unconfirmed_pet_name(
    state: Any, args: dict, tool_name: str, user_message: str
) -> dict:
    """
    cancel_booking/reschedule_booking require confirm_pet_name to match
    the target booking's real pet — but the model already knows that
    name from context and can just fill it in itself instead of
    genuinely waiting for the customer to type it (confirmed live: a
    plain "yes please", not the pet's name, still produced
    confirm_pet_name="Rex" and the cancellation went through). If the
    name given doesn't actually appear in what the customer just typed
    THIS turn, drop it so the tool's own confirmation_required gate
    fires for real instead of trusting the model's say-so.
    """
    if tool_name not in ("cancel_booking", "reschedule_booking"):
        return args
    name = str(args.get("confirm_pet_name") or "").strip()
    if not name:
        return args
    pending = state.pending_booking_confirmation
    # Even if the customer's initial action request contains the pet's
    # name ("cancel Milo's booking"), that is target identification, not
    # confirmation of a preview they have not seen yet. Only a later
    # customer turn may authorize the already-previewed target/change.
    if (
        not pending
        or pending.get("tool") != tool_name
        or pending.get("preview_turn") is None
        or int(pending.get("preview_turn") or 0) != state.turn_counter - 1
    ):
        return {**args, "confirm_pet_name": ""}
    # Word-boundary match, not bare substring — a short/common pet name
    # (e.g. "Bo") would otherwise be satisfied by an unrelated word like
    # "about" or "book" appearing anywhere in the customer's message.
    if re.search(rf"\b{re.escape(name.lower())}\b", (user_message or "").lower()):
        return args
    return {**args, "confirm_pet_name": ""}


def normalize_clock(value: object) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", text)
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else text.casefold()


def reject_unverified_booking_payload(
    state: Any, args: dict, confirmed_preview_turn: int | None = None
) -> dict | None:
    service_type = str(args.get("service_type") or "").strip().upper()
    pet_id = args.get("pet_id")
    package = str(args.get("package_name") or "").strip().casefold()
    try:
        price = round(float(args.get("price")), 2)
    except (TypeError, ValueError):
        price = None

    requested_time = normalize_clock(args.get("time"))
    requested_check_out_time = normalize_clock(args.get("check_out_time"))
    try:
        requested_duration = int(args.get("duration_minutes"))
    except (TypeError, ValueError):
        requested_duration = None
    if service_type == "DAYCARE" and requested_duration is None:
        def clock_minutes(value: str) -> int | None:
            match = re.fullmatch(r"(\d{2}):(\d{2})", value)
            if not match:
                return None
            return int(match.group(1)) * 60 + int(match.group(2))

        start_minutes = clock_minutes(requested_time)
        end_minutes = clock_minutes(requested_check_out_time)
        if start_minutes is not None and end_minutes is not None and end_minutes > start_minutes:
            requested_duration = end_minutes - start_minutes

    candidates = [
        option for option in state.verified_service_options
        if str(option.get("service_type") or "").upper() == service_type
        and (not option.get("pet_id") or str(option.get("pet_id")) == str(pet_id))
        and str(option.get("service_name") or option.get("room_type") or "").strip().casefold() == package
    ]
    def option_price_matches(option: dict) -> bool:
        if price is None or option.get("price") in (None, ""):
            return False
        expected = float(option["price"])
        if (
            service_type == "DAYCARE"
            and str(option.get("pricing_unit") or "").casefold() == "hour"
        ):
            if requested_duration is None or requested_duration <= 0:
                return False
            expected *= requested_duration / 60
        return abs(expected - price) <= 0.01

    chosen = next((option for option in candidates if option_price_matches(option)), None)
    if chosen is None:
        return {
            "error": "UNVERIFIED_SERVICE_OPTION",
            "message": (
                "package_name/price is not an exact option observed from "
                "get_booking_service_options for this service and pet. Fetch the catalogue, "
                "let the customer select an option, and reuse its exact name and price."
            ),
        }

    if service_type == "DAYCARE" and requested_duration is not None:
        exact = chosen.get("duration_minutes")
        minimum = chosen.get("min_duration_minutes")
        maximum = chosen.get("max_duration_minutes")
        applicable = True
        if exact not in (None, ""):
            applicable = requested_duration == int(exact)
        if minimum not in (None, ""):
            applicable = applicable and (
                requested_duration > int(minimum)
                if chosen.get("min_duration_exclusive")
                else requested_duration >= int(minimum)
            )
        if maximum not in (None, ""):
            applicable = applicable and (
                requested_duration < int(maximum)
                if chosen.get("max_duration_exclusive")
                else requested_duration <= int(maximum)
            )
        if not applicable:
            return {
                "error": "PACKAGE_NOT_APPLICABLE_FOR_DURATION",
                "message": (
                    "The selected daycare package does not cover the complete visit "
                    "duration. Fetch the catalogue and use the applicable duration tier."
                ),
            }

    if args.get("add_on"):
        add_on_name = str(args.get("add_on") or "").strip().casefold()
        try:
            add_on_price = round(float(args.get("add_on_price")), 2)
        except (TypeError, ValueError):
            add_on_price = None
        valid_add_on = any(
            str(option.get("service_type") or "").upper() == service_type
            and str(option.get("selection_kind") or "") == "add_on"
            and str(option.get("service_name") or "").strip().casefold() == add_on_name
            and add_on_price is not None
            and option.get("price") not in (None, "")
            and abs(float(option["price"]) - add_on_price) <= 0.01
            for option in state.verified_service_options
        )
        if not valid_add_on:
            return {
                "error": "UNVERIFIED_ADD_ON",
                "message": "The add-on name/price was not observed as an exact catalogue option.",
            }

    requested_date = str(args.get("date") or "")
    requested_room = str(
        args.get("package_name") if service_type == "BOARDING" else args.get("room_type") or ""
    ).strip().casefold()
    requested_check_out_date = str(args.get("check_out_date") or "").strip()
    requested_preferred_staff = str(args.get("preferred_staff") or "").strip().casefold()

    def slot_matches_constraints(slot: dict) -> bool:
        try:
            verified_this_turn = int(slot.get("verified_turn")) == int(state.turn_counter)
        except (TypeError, ValueError):
            verified_this_turn = False
        if not verified_this_turn and confirmed_preview_turn is not None:
            try:
                verified_this_turn = (
                    int(slot.get("verified_turn")) == int(confirmed_preview_turn)
                )
            except (TypeError, ValueError):
                verified_this_turn = False
        if (
            not verified_this_turn
            or str(slot.get("service_type") or "").upper() != service_type
            or str(slot.get("date") or "") != requested_date
            or normalize_clock(slot.get("time")) != requested_time
        ):
            return False
        if requested_preferred_staff != str(slot.get("preferred_staff") or "").strip().casefold():
            return False
        if service_type == "BOARDING":
            return (
                bool(requested_room)
                and str(slot.get("room_type") or "").strip().casefold() == requested_room
                and bool(requested_check_out_date)
                and str(slot.get("check_out_date") or "") == requested_check_out_date
                and normalize_clock(slot.get("check_out_time")) == requested_check_out_time
            )
        if service_type == "DAYCARE":
            try:
                checked_duration = int(slot.get("duration_minutes"))
            except (TypeError, ValueError):
                return False
            return (
                requested_duration is not None
                and checked_duration == requested_duration
                and (
                    not requested_check_out_time
                    or normalize_clock(slot.get("check_out_time")) == requested_check_out_time
                )
            )
        if service_type == "GROOMING" and requested_duration is not None:
            # Unlike DAYCARE, a GROOMING duration_minutes is often legitimately
            # omitted (the server then defaults to 90) — only enforced when
            # the model actually supplies one, and only against whatever
            # duration was actually verified as available. Without this, a
            # duration submitted to create_booking could silently diverge
            # from the one shown as available (e.g. a package needing 150
            # minutes booked under the 90-minute default), reserving too
            # little staff time and letting the next customer's appointment
            # be booked into a slot that's still physically in use.
            try:
                checked_duration = int(slot.get("duration_minutes"))
            except (TypeError, ValueError):
                checked_duration = None
            if checked_duration is not None and checked_duration != requested_duration:
                return False
        return True

    slot_verified = any(
        slot_matches_constraints(slot)
        for slot in state.verified_availability_slots
    )
    if not slot_verified:
        return {
            "error": "UNVERIFIED_AVAILABILITY_SLOT",
            "message": (
                "This exact service/date/time was not returned as available by "
                "check_availability with the same room/stay, duration, and staff constraints. "
                "Verify the complete request and let the customer select the observed slot."
            ),
        }
    return None


def availability_args_for_booking(args: dict) -> dict:
    """Build an exact fresh availability probe from a proposed booking."""
    service_type = str(args.get("service_type") or "").strip().upper()
    return {
        "company_id": args.get("company_id"),
        "service_type": service_type,
        "date": args.get("date") or "",
        "time": args.get("time") or "",
        "room_type": (
            args.get("package_name") if service_type == "BOARDING"
            else args.get("room_type") or ""
        ),
        "check_out_date": args.get("check_out_date") or "",
        "customer_id": args.get("customer_id") or "",
        "pet_id": args.get("pet_id") or "",
        "exclude_booking_id": "",
        "duration_minutes": args.get("duration_minutes"),
        "check_out_time": args.get("check_out_time") or "",
        "preferred_staff": args.get("preferred_staff") or "",
        "selection_target": "CHECK_IN",
        "check_in_time": "",
    }


def availability_result_contains_booking(result: dict, booking_args: dict) -> bool:
    if not isinstance(result, dict) or result.get("status") != "success":
        return False
    requested = normalize_clock(booking_args.get("time"))
    return any(
        normalize_clock(slot) == requested
        for slot in (result.get("data") or {}).get("available_slots") or []
    )


def customer_selected_booking_time(
    state: Any,
    args: dict,
    user_message: str,
    *,
    resolve_ordinal_reference: Callable[[Any, str], dict | None],
) -> bool:
    """True only when this turn explicitly selects the proposed time."""
    requested = normalize_clock(args.get("time"))
    resolved = state.current_datetime_resolution or {}
    if requested and normalize_clock(resolved.get("time")) == requested:
        return True
    selected = resolve_ordinal_reference(state, user_message)
    return bool(
        selected
        and requested
        and normalize_clock(selected.get("slot")) == requested
    )
