"""Conversation-scoped authorization for optional booking fields."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


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
        return re.sub(r"[^\w\u3400-\u9fff]+", "", str(item or "").casefold())

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
