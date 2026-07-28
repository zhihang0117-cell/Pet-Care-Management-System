"""
Multi-turn session continuation for read-only database actions.

Merges short follow-up replies into a pending availability, booking-status, or
loyalty lookup when the customer is answering a prior missing-info question.
Clears pending state when the customer clearly changes topic.

Production should use the same session fields backed by persistent storage.
"""

from __future__ import annotations

import copy
import re

from intent_schema import POLICY_RAG_SCENARIOS
from booking_draft import (
    AWAIT_BOOKING_CONFIRMATION,
    build_confirm_booking_intent,
    build_orphan_confirmation_intent,
    is_booking_confirmation_message,
)
from session_store import (
    BOOKING_CHOOSE_REPEAT_OR_NEW,
    COLLECT_CUSTOMER_NAME,
    REPEAT_BOOKING_PENDING_ACTION,
)

READ_ONLY_PENDING_ACTIONS = {
    "CHECK_AVAILABILITY": "check_available_slots",
    "VIEW_BOOKING_STATUS": "check_booking_status",
    "CHECK_LOYALTY_POINTS": "check_loyalty_points",
    "CHECK_MEMBERSHIP_STATUS": "check_loyalty_points",
}

LOYALTY_SCENARIOS = {"CHECK_LOYALTY_POINTS", "CHECK_MEMBERSHIP_STATUS"}

_INTERRUPTION_SCENARIOS = {
    *POLICY_RAG_SCENARIOS,
    "CHECK_LOYALTY_POINTS",
    "CHECK_MEMBERSHIP_STATUS",
    "LOYALTY_ACCOUNT_INQUIRY",
    "CHECK_COUPON_ELIGIBILITY",
    "VIEW_BOOKING_STATUS",
    "VIEW_PAYMENT_HISTORY",
    "VIEW_REDEMPTION_HISTORY",
    "VIEW_MESSAGE_HISTORY",
    "VIEW_COMPANY_INFORMATION",
    "VIEW_STAFF_DIRECTORY",
    "VIEW_ACCOUNT_STATUS",
}

_PENDING_FLOW_FIELDS = (
    "pending_action",
    "missing_fields",
    "collected_entities",
    "sub_flow",
    "booking_missing_snapshot",
    "booking_scenario_snapshot",
    "booking_creation_flow",
    "last_intent",
    "last_scenario_intent",
    "last_service_type",
    "pet_name",
    "pet_id",
    "pet_type",
    "pet_size",
    "pet_height",
    "service_package",
    "service_options",
    "service_options_for",
    "selected_package",
    "selected_addons",
    "preferred_date",
    "preferred_time",
    "selected_slot",
    "price_quote",
    "draft_booking_payload",
    "availability_result",
    "current_step",
    "completed_fields",
)

_TOPIC_SHIFT_PHRASES = re.compile(
    r"\b("
    r"actually|instead|never\s+mind|forget\s+(that|it)|"
    r"what\s+is\s+your|tell\s+me\s+about|how\s+does\s+your|"
    r"cancellation\s+policy|refund\s+policy"
    r")\b",
    re.I,
)

_TIME_HINT = re.compile(
    r"\b("
    r"morning|afternoon|evening|night|noon|"
    r"\d{1,2}(:\d{2})?\s*(am|pm)"
    r")\b",
    re.I,
)

_DATE_HINT = re.compile(
    r"\b("
    r"today|tomorrow|next\s+week|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+\d{4})?"
    r")\b",
    re.I,
)

_SERVICE_HINT = re.compile(r"\b(grooming|daycare|boarding|bath)\b", re.I)


def _entity_value(entities: dict, key: str) -> str:
    return str(entities.get(key) or "").strip()


def _message_has_time_hint(message: str) -> bool:
    text = str(message or "").strip()
    return bool(_TIME_HINT.search(text) or re.fullmatch(r"(?:am|pm)", text, re.I))


def _message_has_date_hint(message: str) -> bool:
    return bool(_DATE_HINT.search(message or ""))


def _extract_preferred_date(message: str) -> str:
    text = str(message or "").strip()
    match = _DATE_HINT.search(text)
    return match.group(0) if match else ""


def _extract_preferred_time(message: str) -> str:
    from time_normalization import extract_time_from_message

    return extract_time_from_message(message) or ""


def _extract_service_type(message: str, fallback: str = "") -> str:
    match = _SERVICE_HINT.search(message or "")
    if match:
        return match.group(1).upper()
    return fallback


def extract_fields_from_message(message: str, target_fields: list[str], session=None) -> dict[str, str]:
    """Parse follow-up message values for known missing entity fields."""
    extracted: dict[str, str] = {}
    if session is not None:
        suspended = dict(getattr(session, "suspended_task", None) or {})
        expected = str(suspended.get("expected_field") or "").strip()
        if expected and expected not in target_fields:
            target_fields = [expected, *list(target_fields)]
    for field in target_fields:
        if field == "preferred_time":
            value = _extract_preferred_time(message)
            if value:
                extracted[field] = value
        elif field == "preferred_date":
            value = _extract_preferred_date(message)
            if value:
                extracted[field] = value
        elif field == "service_type":
            value = _extract_service_type(message)
            if value:
                extracted[field] = value
        elif field == "service_package":
            options = list(getattr(session, "service_options", []) or []) if session is not None else []
            pet_kind = str(getattr(session, "pet_type", "") or "").strip().upper()
            if pet_kind in {"CAT", "DOG"}:
                opposite = "dog " if pet_kind == "CAT" else "cat "
                options = [
                    option
                    for option in options
                    if not str(option.get("service_name") or "")
                    .strip()
                    .lower()
                    .startswith(opposite)
                ]
            text = str(message or "").strip()
            selected = None
            number_match = re.fullmatch(r"(?:option\s*)?(\d+)[.!]?", text, re.I)
            if number_match:
                index = int(number_match.group(1)) - 1
                if 0 <= index < len(options):
                    selected = options[index]
            if selected is None:
                lowered = text.lower()
                selected = next(
                    (
                        option
                        for option in options
                        if (
                            str(option.get("service_name") or "").strip().lower()
                            in lowered
                            or lowered
                            in str(option.get("service_name") or "").strip().lower()
                            or str(option.get("service_name") or "")
                            .split("(", 1)[0]
                            .strip()
                            .lower()
                            in lowered
                        )
                    ),
                    None,
                )
            if selected:
                value = str(selected.get("service_name") or "").strip()
                if value:
                    extracted[field] = value
                    session.service_package = value
            else:
                from package_selection import extract_service_package

                value = extract_service_package(text)
                if value:
                    extracted[field] = value
        elif field == "pet_name":
            from booking_flow import extract_pet_confirmation, get_customer_pets_for_session

            pets = get_customer_pets_for_session(session) if session is not None else []
            value = extract_pet_confirmation(message, pets)
            if value:
                extracted[field] = value
        elif field == "phone_number":
            digits = re.sub(r"\D", "", message or "")
            if len(digits) >= 8:
                extracted[field] = message.strip()
        elif field == "full_name":
            from booking_flow import extract_customer_name_from_message

            expect_name = (
                str(getattr(session, "pending_action", "") or "").strip() == COLLECT_CUSTOMER_NAME
                or str(getattr(session, "current_step", "") or "").strip() == "ASK_CUSTOMER_NAME"
            )
            value = extract_customer_name_from_message(message, expect_name=expect_name)
            if value:
                extracted[field] = value
        elif field == "repeat_or_new_service_choice":
            from booking_flow import parse_repeat_or_new_choice

            choice = parse_repeat_or_new_choice(message)
            if choice:
                extracted[field] = choice
                if choice in {"GROOMING", "DAYCARE", "BOARDING"}:
                    extracted["service_type"] = choice
        elif field in {"pet_type", "pet_size_or_height"}:
            from booking_service_info import extract_pet_price_info

            price_info = extract_pet_price_info(message)
            if price_info:
                extracted.update(price_info)
    from pet_extraction import extract_pet_profile_from_message

    profile = extract_pet_profile_from_message(message)
    if profile:
        extracted.update({k: v for k, v in profile.items() if k not in extracted})
    return extracted


def resolve_pending_intent_before_llm(session, user_message: str) -> dict | None:
    """Use the active memory step for definite follow-up answers before calling an LLM."""
    pending = str(getattr(session, "pending_action", "") or "").strip()
    missing = list(getattr(session, "missing_fields", []) or [])
    if not pending:
        return None

    from booking_draft import is_booking_confirmation_message
    from booking_flow import (
        extract_customer_name_from_message,
        parse_repeat_or_new_choice,
    )
    from session_store import AWAIT_BOOKING_CONFIRMATION, SLOT_AVAILABLE_PENDING

    definite = False
    scenario = str(getattr(session, "last_scenario_intent", "") or "MAKE_BOOKING").strip()
    main_intent = "BOOKING_INTENT"

    if pending == COLLECT_CUSTOMER_NAME:
        definite = bool(extract_customer_name_from_message(user_message, expect_name=True))
        scenario = "COLLECT_CUSTOMER_NAME"
        main_intent = "GREETING_INTENT"
    elif pending == BOOKING_CHOOSE_REPEAT_OR_NEW:
        definite = bool(parse_repeat_or_new_choice(user_message))
        scenario = "MAKE_BOOKING"
    elif pending == AWAIT_BOOKING_CONFIRMATION:
        definite = bool(is_booking_confirmation_message(user_message))
        scenario = "CONFIRM_BOOKING"
    elif pending == SLOT_AVAILABLE_PENDING:
        from booking_draft import is_slot_acceptance_message

        definite = bool(
            is_slot_acceptance_message(user_message)
            or _extract_preferred_time(user_message)
        )
        scenario = "MAKE_BOOKING"
    elif missing:
        definite = bool(extract_fields_from_message(user_message, missing, session))

    if not definite:
        return None
    if scenario in LOYALTY_SCENARIOS:
        main_intent = "LOYALTY_INTENT"
    return {
        "main_intent": main_intent,
        "scenario_intent": scenario,
        "service_type": str(getattr(session, "last_service_type", "") or "UNKNOWN"),
        "entities": {},
        "missing_information": missing,
        "retrieval_needed": False,
        "retrieval_source": [],
        "database_action_needed": False,
        "database_action": "",
        "next_action": "continue_pending_flow",
        "confidence": 0.99,
        "reason": "Definite answer to the active conversation-memory step",
    }


def capture_pending_flow(session) -> dict:
    """Snapshot an unfinished flow so a read-only interruption cannot erase it."""
    import copy

    if not str(getattr(session, "pending_action", "") or "").strip():
        return {}
    return {
        field: copy.deepcopy(getattr(session, field))
        for field in _PENDING_FLOW_FIELDS
    }


def should_resume_pending_flow(snapshot: dict, intent_json: dict) -> bool:
    """Return True when this turn temporarily answered another supported question."""
    if not snapshot:
        return False
    scenario = str(intent_json.get("scenario_intent") or "").strip()
    return scenario in _INTERRUPTION_SCENARIOS and not bool(
        intent_json.get("booking_supporting_service_info")
        or intent_json.get("booking_supporting_info_needed")
    )


def restore_pending_flow(session, snapshot: dict) -> None:
    """Restore only flow state; identity/profile memory remains current."""
    import copy

    for field, value in snapshot.items():
        setattr(session, field, copy.deepcopy(value))


def append_pending_resume_prompt(reply: str, session, intent_json: dict) -> str:
    """Bring the customer back to the exact unfinished step after an interruption."""
    from booking_flow import build_missing_field_reply

    missing = list(getattr(session, "missing_fields", []) or [])
    if not missing:
        return str(reply or "").strip()
    question = build_missing_field_reply(missing, session, intent_json)
    if not question:
        return str(reply or "").strip()
    text = str(reply or "").strip()
    if question.lower() in text.lower():
        return text
    return f"{text}\n\nTo continue where we left off: {question}".strip()


def apply_read_only_entity_rules(intent_json: dict, user_message: str) -> dict:
    """
    Normalize read-only availability fields.

    A date is enough to query the day. When no time is supplied the database
    returns real available slots instead of asking the customer to guess first.
    """
    updated = copy.deepcopy(intent_json)
    scenario = str(updated.get("scenario_intent") or "").strip()
    if scenario != "CHECK_AVAILABILITY":
        return updated

    entities = dict(updated.get("entities") or {})
    missing = list(updated.get("missing_information") or [])

    service_type = str(updated.get("service_type") or "UNKNOWN").strip()
    if service_type == "UNKNOWN":
        extracted_service = _extract_service_type(user_message)
        if extracted_service:
            updated["service_type"] = extracted_service
            entities["service_type"] = extracted_service

    if not _entity_value(entities, "preferred_date") and _message_has_date_hint(user_message):
        entities["preferred_date"] = _extract_preferred_date(user_message)

    has_date = bool(_entity_value(entities, "preferred_date")) or _message_has_date_hint(user_message)
    if has_date:
        missing = [field for field in missing if field != "preferred_time"]

    updated["entities"] = entities
    updated["missing_information"] = missing
    return updated


def is_topic_change(user_message: str, intent_json: dict, session) -> bool:
    """Return True when the customer starts a new request instead of continuing."""
    from booking_service_info import (
        has_clear_booking_signal,
        is_active_booking_flow,
        is_service_package_price_question,
        is_standalone_non_booking_enquiry,
        is_unrelated_policy_topic,
    )

    message = str(user_message or "")
    pending = str(getattr(session, "pending_action", "") or "").strip()
    if not pending:
        return False

    scenario = str(intent_json.get("scenario_intent") or "UNKNOWN").strip()

    if is_active_booking_flow(session) and is_service_package_price_question(message):
        return False

    if intent_json.get("booking_supporting_service_info") or intent_json.get(
        "booking_supporting_info_needed"
    ):
        return False

    if intent_json.get("booking_interruption"):
        return False

    if str(getattr(session, "sub_flow", "") or "").strip() in {
        "booking_price_pending_info",
        "booking_service_info",
    }:
        return False

    if is_unrelated_policy_topic(message, scenario):
        if is_active_booking_flow(session):
            return False
        if _TOPIC_SHIFT_PHRASES.search(message) or scenario == "CANCELLATION_POLICY":
            return True

    if _TOPIC_SHIFT_PHRASES.search(message):
        if scenario in POLICY_RAG_SCENARIOS or scenario == "CUSTOMER_GREETING":
            if is_active_booking_flow(session):
                return False
            return True
        if re.search(r"\b(actually|instead|never\s+mind)\b", message, re.I):
            return True

    if scenario in POLICY_RAG_SCENARIOS and bool(intent_json.get("retrieval_needed")):
        if scenario == "SERVICE_INFORMATION" and is_active_booking_flow(session):
            return False
        if is_active_booking_flow(session):
            return False
        return True

    if scenario == "CUSTOMER_GREETING":
        extracted = extract_fields_from_message(message, list(getattr(session, "missing_fields", []) or []), session)
        if extracted:
            return False
        return True

    if pending == REPEAT_BOOKING_PENDING_ACTION and scenario in {
        "REPEAT_LAST_BOOKING",
        "CHECK_AVAILABILITY",
        "UNKNOWN",
    }:
        return False

    if pending == AWAIT_BOOKING_CONFIRMATION:
        if is_booking_confirmation_message(message):
            return False
        if is_service_package_price_question(message):
            return False
        if scenario in POLICY_RAG_SCENARIOS or scenario == "CUSTOMER_GREETING":
            return True
        if scenario in {
            "VIEW_BOOKING_STATUS",
            "CHECK_LOYALTY_POINTS",
            "CHECK_MEMBERSHIP_STATUS",
            "LOYALTY_ACCOUNT_INQUIRY",
        }:
            return True
        if _TOPIC_SHIFT_PHRASES.search(message):
            return True
        if scenario in {"CANCEL_BOOKING", "RESCHEDULE_BOOKING", "REDEEM_REWARD"}:
            return True
        if scenario in {
            "CHECK_AVAILABILITY",
            "MAKE_BOOKING",
            "REPEAT_LAST_BOOKING",
            "CONFIRM_BOOKING",
            "BOOKING_CONFIRMATION_ORPHAN",
            "UNKNOWN",
        }:
            return False
        return True

    if scenario in {"CANCEL_BOOKING", "RESCHEDULE_BOOKING", "REDEEM_REWARD", "MAKE_BOOKING"}:
        extracted = extract_fields_from_message(message, list(getattr(session, "missing_fields", []) or []), session)
        if extracted:
            return False
        if scenario == "MAKE_BOOKING" and is_service_package_price_question(message):
            return False
        return True

    if scenario == "SERVICE_INFORMATION" and is_active_booking_flow(session):
        return False

    pending_scenario = str(getattr(session, "last_scenario_intent", "") or "").strip()
    if not pending_scenario:
        return False

    if scenario == "UNKNOWN":
        return False

    if pending_scenario in LOYALTY_SCENARIOS and scenario in LOYALTY_SCENARIOS:
        return False

    if scenario != pending_scenario:
        return True

    return False


def is_likely_continuation(user_message: str, intent_json: dict, session) -> bool:
    """True when the message probably answers the prior missing-info prompt."""
    pending = str(getattr(session, "pending_action", "") or "").strip()
    missing_fields = list(getattr(session, "missing_fields", []) or [])
    if not pending or not missing_fields:
        return False

    extracted = extract_fields_from_message(user_message, missing_fields, session)
    if extracted:
        return True

    if is_topic_change(user_message, intent_json, session):
        return False

    scenario = str(intent_json.get("scenario_intent") or "UNKNOWN").strip()
    pending_scenario = str(getattr(session, "last_scenario_intent", "") or "").strip()

    if scenario in ("UNKNOWN", pending_scenario):
        return len(str(user_message or "").split()) <= 6

    confidence = float(intent_json.get("confidence") or 0.0)
    if confidence < 0.55 and len(str(user_message or "").split()) <= 6:
        return True

    return False


def is_booking_slot_request(user_message: str) -> bool:
    """True when the customer is booking (not merely asking if slots exist)."""
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    if not _extract_service_type(text):
        return False
    if not (_message_has_date_hint(text) or _message_has_time_hint(text)):
        return False
    return bool(
        re.search(
            r"\b(i\s+want|i'?d\s+like|i\s+need|book(?:ing)?|schedule|make\s+an?\s+appointment)\b",
            text,
            re.I,
        )
    )


def apply_session_booking_creation_flag(session, user_message: str, intent_json: dict, conv_ctx=None) -> None:
    del conv_ctx
    from booking_draft import is_explicit_booking_request

    should_book = bool(is_explicit_booking_request(user_message))
    scenario = str(intent_json.get("scenario_intent") or "").strip()
    if scenario == "MAKE_BOOKING":
        should_book = True
    if scenario == "CHECK_AVAILABILITY" and is_booking_slot_request(user_message):
        should_book = True
    if should_book:
        session.booking_creation_flow = True


def clear_booking_flow_state(session) -> None:
    """Clear booking slot-filling state without wiping customer identity."""
    session.write_projection_fields(
        pending_action="",
        missing_fields=[],
        collected_entities={},
        sub_flow="",
        booking_missing_snapshot=[],
        booking_creation_flow=False,
    )
    session.draft_booking_payload = {}
    session.selected_slot = ""
    session.selected_staff_id = None
    session.price_quote = None
    session.preferred_date = ""
    session.preferred_time = ""
    session.service_options = []
    session.service_options_for = ""
    session.service_package = ""
    session.selected_package = ""
    session.pet_id = None
    session.booking_scenario_snapshot = ""
    session.add_on_service = ""
    session.add_on_type = ""
    session.price_context = ""
    session.suspended_task = {}
    session.resume_after_response = False
    session.interruption_type = ""
    session.interruption_depth = 0
    session.current_step = ""
    session.completed_fields = []


def clear_stale_booking_session_state(session) -> None:
    """Clear stale booking flow but preserve active price enquiry memory."""
    from session_store import SERVICE_PRICE_PENDING_ACTION

    if str(getattr(session, "pending_action", "") or "").strip() == SERVICE_PRICE_PENDING_ACTION:
        session.draft_booking_payload = {}
        session.selected_slot = ""
        session.selected_staff_id = None
        session.preferred_date = ""
        session.preferred_time = ""
        session.write_projection_fields(booking_creation_flow=False)
        session.booking_missing_snapshot = []
        session.booking_scenario_snapshot = ""
        return

    clear_booking_flow_state(session)


def clear_pending_action(session) -> None:
    clear_booking_flow_state(session)


PRICE_SCENARIOS = frozenset({"SERVICE_INFORMATION", "POLICY_INTENT"})
BOOKING_SCENARIOS = frozenset({"MAKE_BOOKING", "CHECK_AVAILABILITY"})

STALE_BOOKING_SUB_FLOWS = frozenset(
    {
        "booking_service_info",
        "booking_price_pending_info",
    }
)

BOOKING_ONLY_MISSING_FIELDS = frozenset(
    {
        "preferred_date",
        "preferred_time",
        "pet_name",
        "service_package",
        "repeat_or_new_service_choice",
        "confirmation",
    }
)


def reset_flow_specific_state_on_intent_change(
    session,
    *,
    previous_scenario: str,
    new_scenario: str,
) -> None:
    """When switching from booking to service/pricing enquiry, drop booking-only state."""
    from pet_extraction import is_valid_stored_pet_name
    from session_store import BOOKING_PRICE_PENDING_SUB_FLOW, BOOKING_SERVICE_INFO_SUB_FLOW

    prev = str(previous_scenario or "").strip()
    new = str(new_scenario or "").strip()
    if new not in PRICE_SCENARIOS:
        return

    was_booking = (
        prev in BOOKING_SCENARIOS
        or bool(getattr(session, "booking_creation_flow", False))
        or str(getattr(session, "pending_action", "") or "").strip() == REPEAT_BOOKING_PENDING_ACTION
    )
    if not was_booking:
        return

    stale_sub_flows = STALE_BOOKING_SUB_FLOWS | {
        BOOKING_SERVICE_INFO_SUB_FLOW,
        BOOKING_PRICE_PENDING_SUB_FLOW,
    }

    if hasattr(session, "write_projection_fields"):
        session.write_projection_fields(
            booking_missing_snapshot=[],
            booking_creation_flow=False,
        )
    else:
        session.booking_missing_snapshot = []
        session.booking_creation_flow = False

    session.booking_scenario_snapshot = ""
    session.draft_booking_payload = {}
    session.selected_slot = ""
    session.selected_staff_id = None
    session.preferred_date = ""
    session.preferred_time = ""

    sub_flow = str(getattr(session, "sub_flow", "") or "").strip()
    if sub_flow in stale_sub_flows:
        if hasattr(session, "write_projection_fields"):
            session.write_projection_fields(sub_flow="")
        else:
            session.sub_flow = ""

    pending = str(getattr(session, "pending_action", "") or "").strip()
    if pending == REPEAT_BOOKING_PENDING_ACTION:
        if hasattr(session, "write_projection_fields"):
            session.write_projection_fields(pending_action="")
        else:
            session.pending_action = ""

    filtered_missing = [
        field
        for field in list(getattr(session, "missing_fields", []) or [])
        if field not in BOOKING_ONLY_MISSING_FIELDS
    ]
    if hasattr(session, "write_projection_fields"):
        session.write_projection_fields(missing_fields=filtered_missing)
    else:
        session.missing_fields = filtered_missing

    pet_name = str(getattr(session, "pet_name", "") or "").strip()
    if pet_name and not is_valid_stored_pet_name(pet_name):
        session.pet_name = ""
        merged = dict(getattr(session, "collected_entities", {}) or {})
        merged.pop("pet_name", None)
        if hasattr(session, "write_projection_fields"):
            session.write_projection_fields(collected_entities=merged)
        else:
            session.collected_entities = merged


def apply_session_identity(session, intent_json: dict) -> dict:
    """Inject resolved session identity without exposing customer_id to the customer."""
    updated = copy.deepcopy(intent_json)
    entities = dict(updated.get("entities") or {})

    if session.phone_number and not _entity_value(entities, "phone_number"):
        entities["phone_number"] = session.phone_number

    if session.customer_name and not _entity_value(entities, "customer_name"):
        entities["customer_name"] = session.customer_name

    if session.customer_id is not None and not _entity_value(entities, "customer_identifier"):
        entities["customer_identifier"] = str(session.customer_id)

    if session.pet_name and not _entity_value(entities, "pet_name"):
        entities["pet_name"] = session.pet_name
    if getattr(session, "pet_id", None) is not None and not _entity_value(entities, "pet_id"):
        entities["pet_id"] = str(session.pet_id)
    if session.pet_type and not _entity_value(entities, "pet_type"):
        entities["pet_type"] = session.pet_type
    if session.pet_size and not _entity_value(entities, "pet_size"):
        entities["pet_size"] = session.pet_size
    if session.pet_height and not _entity_value(entities, "pet_height"):
        entities["pet_height"] = session.pet_height

    pending = str(getattr(session, "pending_action", "") or "").strip()
    from booking_flow import is_repeat_booking_flow

    if is_repeat_booking_flow(session):
        if session.last_service_type and not _entity_value(entities, "service_type"):
            entities["service_type"] = session.last_service_type
        if session.pet_name and not _entity_value(entities, "pet_name"):
            entities["pet_name"] = session.pet_name
        if getattr(session, "pet_id", None) is not None and not _entity_value(entities, "pet_id"):
            entities["pet_id"] = str(session.pet_id)
        service_type = str(updated.get("service_type") or "UNKNOWN").strip()
        if service_type == "UNKNOWN" and session.last_service_type:
            updated["service_type"] = session.last_service_type

    missing = list(updated.get("missing_information") or [])
    if session.phone_number:
        missing = [field for field in missing if field != "phone_number"]
    if session.customer_id is not None:
        missing = [field for field in missing if field not in {"customer_id", "customer_identifier"}]
    if str(session.customer_name or "").strip():
        missing = [field for field in missing if field not in {"full_name", "customer_name"}]

    updated["entities"] = entities
    updated["missing_information"] = missing

    if session.existing_customer:
        updated["customer_status"] = "EXISTING_CUSTOMER"
    elif session.phone_number:
        updated["customer_status"] = "NEW_CUSTOMER"

    if session.pet_type and str(updated.get("pet_type") or "UNKNOWN").upper() in {"", "UNKNOWN"}:
        updated["pet_type"] = session.pet_type
    if session.pet_size and str(updated.get("pet_size") or "UNKNOWN").upper() in {"", "UNKNOWN"}:
        updated["pet_size"] = session.pet_size.upper()
    if session.pet_height and not str(updated.get("pet_height") or "").strip():
        updated["pet_height"] = session.pet_height

    return updated


def _build_intent_from_pending(session, entities: dict, missing: list[str]) -> dict:
    pending_action = str(getattr(session, "pending_action", "") or "").strip()
    scenario = str(session.last_scenario_intent or "CHECK_AVAILABILITY").strip()
    service_type = str(session.last_service_type or "UNKNOWN").strip()

    if pending_action == REPEAT_BOOKING_PENDING_ACTION or scenario == "REPEAT_LAST_BOOKING":
        is_repeat = (
            scenario == "REPEAT_LAST_BOOKING"
            or str(getattr(session, "last_scenario_intent", "") or "").strip() == "REPEAT_LAST_BOOKING"
        )
        if service_type == "UNKNOWN" and entities.get("service_type"):
            service_type = str(entities.get("service_type") or "UNKNOWN").strip()
        if is_repeat:
            return {
                "main_intent": "BOOKING_INTENT",
                "scenario_intent": "CHECK_AVAILABILITY",
                "service_type": service_type,
                "pet_type": "UNKNOWN",
                "pet_size": "UNKNOWN",
                "pet_height": "",
                "customer_status": "EXISTING_CUSTOMER" if session.existing_customer else "UNKNOWN",
                "entities": entities,
                "missing_information": missing,
                "retrieval_needed": False,
                "retrieval_source": [],
                "database_action_needed": not missing,
                "database_action": "check_availability",
                "next_action": "check_availability" if not missing else "ask_missing_information",
                "confidence": 0.95,
                "reason": "Session continuation after repeat last booking lookup",
            }
        if not missing:
            return {
                "main_intent": "BOOKING_INTENT",
                "scenario_intent": "CHECK_AVAILABILITY",
                "service_type": service_type,
                "pet_type": "UNKNOWN",
                "pet_size": "UNKNOWN",
                "pet_height": "",
                "customer_status": "EXISTING_CUSTOMER" if session.existing_customer else "UNKNOWN",
                "entities": entities,
                "missing_information": missing,
                "retrieval_needed": False,
                "retrieval_source": [],
                "database_action_needed": True,
                "database_action": "check_availability",
                "next_action": "check_availability",
                "confidence": 0.95,
                "reason": "Session continuation of pending booking collection",
            }
        return {
            "main_intent": "BOOKING_INTENT",
            "scenario_intent": "MAKE_BOOKING",
            "service_type": service_type,
            "pet_type": "UNKNOWN",
            "pet_size": "UNKNOWN",
            "pet_height": "",
            "customer_status": "EXISTING_CUSTOMER" if session.existing_customer else "UNKNOWN",
            "entities": entities,
            "missing_information": missing,
            "retrieval_needed": False,
            "retrieval_source": [],
            "database_action_needed": False,
            "database_action": "",
            "next_action": "ask_missing_information",
            "confidence": 0.95,
            "reason": "Session continuation of pending booking collection",
        }

    if scenario == "CHECK_AVAILABILITY":
        return {
            "main_intent": "BOOKING_INTENT",
            "scenario_intent": "CHECK_AVAILABILITY",
            "service_type": service_type,
            "pet_type": "UNKNOWN",
            "pet_size": "UNKNOWN",
            "pet_height": "",
            "customer_status": "EXISTING_CUSTOMER" if session.existing_customer else "UNKNOWN",
            "entities": entities,
            "missing_information": missing,
            "retrieval_needed": False,
            "retrieval_source": [],
            "database_action_needed": not missing,
            "database_action": "check_availability",
            "next_action": "check_availability" if not missing else "ask_missing_information",
            "confidence": 0.95,
            "reason": "Session continuation of pending availability check",
        }

    if scenario == "VIEW_BOOKING_STATUS":
        return {
            "main_intent": "BOOKING_INTENT",
            "scenario_intent": "VIEW_BOOKING_STATUS",
            "service_type": service_type,
            "pet_type": "UNKNOWN",
            "pet_size": "UNKNOWN",
            "pet_height": "",
            "customer_status": "EXISTING_CUSTOMER" if session.existing_customer else "UNKNOWN",
            "entities": entities,
            "missing_information": missing,
            "retrieval_needed": False,
            "retrieval_source": [],
            "database_action_needed": not missing,
            "database_action": "check_booking_status",
            "next_action": "check_booking_status" if not missing else "ask_missing_information",
            "confidence": 0.95,
            "reason": "Session continuation of pending booking status check",
        }

    loyalty_scenario = scenario if scenario in LOYALTY_SCENARIOS else "CHECK_LOYALTY_POINTS"
    return {
        "main_intent": "LOYALTY_INTENT",
        "scenario_intent": loyalty_scenario,
        "service_type": "UNKNOWN",
        "pet_type": "UNKNOWN",
        "pet_size": "UNKNOWN",
        "pet_height": "",
        "customer_status": "EXISTING_CUSTOMER" if session.existing_customer else "UNKNOWN",
        "entities": entities,
        "missing_information": missing,
        "retrieval_needed": False,
        "retrieval_source": [],
        "database_action_needed": not missing,
        "database_action": "check_loyalty_points",
        "next_action": "check_loyalty_points" if not missing else "ask_missing_information",
        "confidence": 0.95,
        "reason": "Session continuation of pending loyalty check",
    }


def apply_session_continuation(session, user_message: str, intent_json: dict, conv_ctx=None) -> dict:
    """Merge follow-up message into pending read-only action state."""
    del conv_ctx
    from booking_flow import is_repeat_booking_flow
    from booking_flow import merge_collected_entities, sync_session_booking_fields

    collected = dict(getattr(session, "collected_entities", {}) or {})
    entities = merge_collected_entities(session, dict(intent_json.get("entities") or {}))
    for key, value in collected.items():
        if value and not _entity_value(entities, key):
            entities[key] = value

    extracted = extract_fields_from_message(user_message, list(session.missing_fields or []), session)
    entities.update({key: value for key, value in extracted.items() if value})

    if is_repeat_booking_flow(session):
        if session.last_service_type and not _entity_value(entities, "service_type"):
            entities["service_type"] = session.last_service_type
        if session.pet_name and not _entity_value(entities, "pet_name"):
            entities["pet_name"] = session.pet_name
        if session.preferred_date and not _entity_value(entities, "preferred_date"):
            entities["preferred_date"] = session.preferred_date
        if session.preferred_time and not _entity_value(entities, "preferred_time"):
            entities["preferred_time"] = session.preferred_time
        if getattr(session, "pet_id", None) is not None and not _entity_value(entities, "pet_id"):
            entities["pet_id"] = str(session.pet_id)

    sync_session_booking_fields(session, entities)

    remaining = [
        field
        for field in (session.missing_fields or [])
        if not _entity_value(entities, field)
    ]

    merged = _build_intent_from_pending(session, entities, remaining)
    return apply_session_identity(session, merged)


def handle_session_before_routing(session, user_message: str, intent_json: dict, conv_ctx=None) -> tuple[dict, object]:
    """Apply topic reset, booking confirmation, or pending-action continuation before routing."""
    del conv_ctx
    if str(intent_json.get("scenario_intent") or "").strip() == "CUSTOMER_GREETING":
        # A fresh greeting starts a new conversational task. Do not let an
        # abandoned booking date/time leak into the next request.
        clear_stale_booking_session_state(session)
        return intent_json, session

    if intent_json.get("coupon_eligibility_with_booking"):
        return intent_json, session

    if intent_json.get("booking_interruption"):
        return intent_json, session

    if intent_json.get("standalone_service_info"):
        return intent_json, session

    pending = str(getattr(session, "pending_action", "") or "").strip()

    if pending == BOOKING_CHOOSE_REPEAT_OR_NEW:
        from booking_flow import (
            build_check_last_service_intent,
            handle_repeat_or_new_choice,
            is_last_service_inquiry,
        )

        if is_last_service_inquiry(user_message):
            return build_check_last_service_intent(session, intent_json), session

        handled = handle_repeat_or_new_choice(session, user_message, intent_json)
        return handled, session

    if pending == COLLECT_CUSTOMER_NAME:
        from booking_flow import handle_collect_customer_name

        handled = handle_collect_customer_name(session, user_message, intent_json)
        return handled, session

    from booking_service_info import apply_booking_service_info_rules

    if str(getattr(session, "sub_flow", "") or "").strip() == "booking_price_pending_info":
        service_info_intent = apply_booking_service_info_rules(session, user_message, intent_json)
        if service_info_intent.get("booking_service_info_handled"):
            return service_info_intent, session

    from booking_flow import (
        apply_booking_collection_rules,
        can_accept_booking_confirmation,
        is_safe_booking_confirmation_message,
    )
    from session_store import SLOT_AVAILABLE_PENDING, finalize_session_slot_selection

    if pending == SLOT_AVAILABLE_PENDING:
        import copy

        from booking_draft import is_slot_acceptance_message

        if is_slot_acceptance_message(user_message):
            session = finalize_session_slot_selection(session, intent_json)
            updated = copy.deepcopy(intent_json)
            updated["main_intent"] = "BOOKING_INTENT"
            updated["slot_just_accepted"] = True
            if getattr(session, "draft_booking_payload", None):
                updated["scenario_intent"] = "MAKE_BOOKING"
                updated["database_action_needed"] = False
                updated["database_action"] = ""
                updated["next_action"] = "await_confirmation"
                updated["missing_information"] = ["confirmation"]
            else:
                # Grooming slots do not depend on package choice. After a slot
                # is accepted, fetch verified package options before confirmation.
                updated["scenario_intent"] = "GET_BOOKING_SERVICE_OPTIONS"
                updated["database_action_needed"] = True
                updated["database_action"] = "get_booking_service_options"
                updated["retrieval_needed"] = True
                updated["retrieval_source"] = ["service_information"]
                updated["next_action"] = "get_booking_service_options"
                updated["deferred_booking_missing"] = ["service_package"]
                updated["missing_information"] = []
            updated["confidence"] = max(float(updated.get("confidence") or 0.0), 0.95)
            # Intent classifiers may label a bare "yes" as a generic returning
            # customer booking entry.  Once the active flow has consumed it as
            # slot acceptance, that stale entry flag must not trigger a
            # last-booking lookup later in main._process_chat().
            updated["returning_customer_booking_entry"] = False
            return updated, session
        alt_time = _extract_preferred_time(user_message)
        if alt_time:
            from booking_flow import apply_booking_collection_rules, sync_session_booking_fields

            entities = dict(getattr(session, "collected_entities", {}) or {})
            entities["preferred_time"] = alt_time
            sync_session_booking_fields(session, entities)
            session.selected_slot = ""
            updated = apply_booking_collection_rules(session, intent_json, user_message)
            return updated, session

    if pending == AWAIT_BOOKING_CONFIRMATION and can_accept_booking_confirmation(session, user_message):
        confirmed = build_confirm_booking_intent(session, intent_json)
        return confirmed, session

    if is_safe_booking_confirmation_message(user_message) and not can_accept_booking_confirmation(
        session, user_message
    ):
        return build_orphan_confirmation_intent(intent_json), session

    if (
        is_safe_booking_confirmation_message(user_message)
        and pending != AWAIT_BOOKING_CONFIRMATION
        and not getattr(session, "draft_booking_payload", None)
    ):
        return build_orphan_confirmation_intent(intent_json), session

    if pending and session.missing_fields:
        extracted = extract_fields_from_message(user_message, list(session.missing_fields or []), session)
        if extracted:
            continued = apply_session_continuation(session, user_message, intent_json)
            return continued, session

    if is_topic_change(user_message, intent_json, session):
        clear_pending_action(session)
        return intent_json, session

    if is_likely_continuation(user_message, intent_json, session):
        continued = apply_session_continuation(session, user_message, intent_json)
        return continued, session

    return intent_json, session


def update_session_after_turn(
    session,
    intent_json: dict,
    route_result: dict,
    database_result: dict,
    conv_ctx=None,
) -> object:
    """Persist non-progression session state after each turn."""
    del conv_ctx
    from booking_flow import merge_collected_entities, sync_session_booking_fields
    from session_store import (
        BOOKING_CHOOSE_REPEAT_OR_NEW,
        COLLECT_CUSTOMER_NAME,
        REPEAT_BOOKING_PENDING_ACTION,
        SERVICE_PRICE_PENDING_ACTION,
    )

    route = str(route_result.get("route") or "").strip()
    scenario = str(intent_json.get("scenario_intent") or "").strip()
    service_type = str(intent_json.get("service_type") or "UNKNOWN").strip()

    if intent_json.get("coupon_eligibility_with_booking"):
        missing = list(intent_json.get("deferred_booking_missing") or [])
        session.booking_creation_flow = True
        session.pending_action = REPEAT_BOOKING_PENDING_ACTION
        session.missing_fields = missing
        session.booking_missing_snapshot = missing
        entities = dict(intent_json.get("entities") or {})
        session.collected_entities = merge_collected_entities(session, entities)
        sync_session_booking_fields(session, session.collected_entities)
        session.last_intent = "MAKE_BOOKING"
        session.last_scenario_intent = "MAKE_BOOKING"
        return session

    if scenario == "GET_BOOKING_SERVICE_OPTIONS":
        data = dict(database_result.get("data") or {})
        options = list(data.get("service_options") or [])
        session.service_options = options
        session.service_options_for = str(data.get("service_type") or service_type or "").upper()
        missing = list(intent_json.get("deferred_booking_missing") or ["service_package"])
        session.booking_creation_flow = True
        session.pending_action = REPEAT_BOOKING_PENDING_ACTION
        session.missing_fields = missing
        session.booking_missing_snapshot = missing
        session.last_intent = "MAKE_BOOKING"
        session.last_scenario_intent = "MAKE_BOOKING"
        return session

    if intent_json.get("booking_supporting_service_info") or intent_json.get(
        "booking_supporting_info_needed"
    ):
        if not str(getattr(session, "booking_scenario_snapshot", "") or "").strip():
            session.booking_scenario_snapshot = str(
                getattr(session, "last_scenario_intent", "") or "MAKE_BOOKING"
            ).strip()
        if not str(getattr(session, "pending_action", "") or "").strip():
            session.pending_action = REPEAT_BOOKING_PENDING_ACTION
            session.booking_creation_flow = True
        entities = dict(intent_json.get("entities") or {})
        service_type_val = str(intent_json.get("service_type") or "").strip()
        if service_type_val and service_type_val != "UNKNOWN" and not entities.get("service_type"):
            entities["service_type"] = service_type_val
        snapshot = {key: str(value).strip() for key, value in entities.items() if str(value).strip()}
        session.collected_entities = merge_collected_entities(session, snapshot)
        sync_session_booking_fields(session, session.collected_entities)
        session.booking_creation_flow = True
        if intent_json.get("ask_package_after_info"):
            session.missing_fields = list(
                dict.fromkeys([*(session.missing_fields or []), "service_package"])
            )
        elif intent_json.get("missing_information"):
            session.missing_fields = list(intent_json.get("missing_information") or [])
        if str(intent_json.get("sub_flow") or "").strip():
            session.sub_flow = str(intent_json.get("sub_flow") or "").strip()
        elif route in {"CALL_KNOWLEDGE_RAG", "CALL_RAG_THEN_ASK_MISSING_INFO"}:
            session.sub_flow = ""
        session.last_scenario_intent = "MAKE_BOOKING"
        session.last_intent = "MAKE_BOOKING"
        return session

    if scenario and not intent_json.get("booking_supporting_service_info"):
        session.last_intent = scenario
        session.last_scenario_intent = scenario
    if service_type != "UNKNOWN":
        session.last_service_type = service_type

    if route == "ASK_MISSING_INFO" and scenario in {
        *READ_ONLY_PENDING_ACTIONS,
        "MAKE_BOOKING",
        "COLLECT_CUSTOMER_NAME",
        "SERVICE_INFORMATION",
    }:
        if intent_json.get("slot_just_accepted") and getattr(
            session, "draft_booking_payload", None
        ):
            from booking_draft import AWAIT_BOOKING_CONFIRMATION

            session.write_projection_fields(
                pending_action=AWAIT_BOOKING_CONFIRMATION,
                missing_fields=["confirmation"],
                booking_creation_flow=True,
            )
            session.current_step = "WAIT_FOR_CONFIRMATION"
            return session

        missing = list(intent_json.get("missing_information") or [])
        if intent_json.get("standalone_service_info") or intent_json.get("sub_flow") == SERVICE_PRICE_PENDING_ACTION:
            session.sub_flow = SERVICE_PRICE_PENDING_ACTION
            session.pending_action = SERVICE_PRICE_PENDING_ACTION
            session.missing_fields = missing
            session.booking_creation_flow = False
            entities = dict(intent_json.get("entities") or {})
            service_type_val = str(intent_json.get("service_type") or "").strip()
            if service_type_val and service_type_val != "UNKNOWN" and not entities.get("service_type"):
                entities["service_type"] = service_type_val
            snapshot = {key: str(value).strip() for key, value in entities.items() if str(value).strip()}
            session.collected_entities = merge_collected_entities(session, snapshot)
            sync_session_booking_fields(session, session.collected_entities)
            from booking_service_info import get_price_enquiry_context, sync_price_enquiry_session

            sync_price_enquiry_session(session, get_price_enquiry_context(session), write_session=True)
            return session
        if intent_json.get("sub_flow") == "booking_price_pending_info":
            session.sub_flow = "booking_price_pending_info"
            session.pending_action = REPEAT_BOOKING_PENDING_ACTION
            session.booking_creation_flow = True
        elif "repeat_or_new_service_choice" in missing:
            session.pending_action = BOOKING_CHOOSE_REPEAT_OR_NEW
        elif scenario == "COLLECT_CUSTOMER_NAME" or "full_name" in missing:
            if str(getattr(session, "customer_name", "") or "").strip():
                missing = [field for field in missing if field not in {"full_name", "customer_name"}]
                session.pending_action = REPEAT_BOOKING_PENDING_ACTION if session.booking_creation_flow else ""
            else:
                session.pending_action = COLLECT_CUSTOMER_NAME
        elif scenario == "MAKE_BOOKING":
            session.pending_action = REPEAT_BOOKING_PENDING_ACTION
        elif scenario in READ_ONLY_PENDING_ACTIONS:
            session.pending_action = READ_ONLY_PENDING_ACTIONS[scenario]
        else:
            session.pending_action = ""
        session.missing_fields = missing
        entities = dict(intent_json.get("entities") or {})
        service_type_val = str(intent_json.get("service_type") or "").strip()
        if service_type_val and service_type_val != "UNKNOWN" and not entities.get("service_type"):
            entities["service_type"] = service_type_val
        snapshot = {key: str(value).strip() for key, value in entities.items() if str(value).strip()}
        session.collected_entities = merge_collected_entities(session, snapshot)
        sync_session_booking_fields(session, session.collected_entities)
        if str(getattr(session, "customer_name", "") or "").strip():
            session.completed_fields = list(
                dict.fromkeys([*(session.completed_fields or []), "customer_name"])
            )
        return session

    if route == "CALL_DATABASE" and scenario == "CUSTOMER_GREETING":
        db_status = str(database_result.get("status") or "").strip()
        if db_status == "not_found":
            session.pending_action = COLLECT_CUSTOMER_NAME
            session.missing_fields = ["full_name"]
        return session

    if route == "CALL_DATABASE":
        db_status = str(database_result.get("status") or "").strip()
        db_action = str(database_result.get("action") or "").strip()
        if db_action == "create_booking" and db_status == "success" and database_result.get("data_found"):
            data = dict(database_result.get("data") or {})
            if data.get("verified"):
                session.previous_booking_id = data.get("booking_id")
                clear_booking_flow_state(session)
                session.pending_action = ""
                return session
        if db_action == "check_last_booking":
            return session
        if db_action == "check_available_slots" and db_status == "success":
            return session
        if db_status in {"success", "not_found"} and db_action in set(READ_ONLY_PENDING_ACTIONS.values()):
            clear_pending_action(session)
        return session

    return session


def _apply_non_progression_session_updates(session, intent_json: dict, route_result: dict, database_result: dict) -> None:
    """Phase 3: only non-derived, non-projection session fields."""
    if intent_json.get("booking_supporting_service_info") or intent_json.get("booking_supporting_info_needed"):
        if not str(getattr(session, "booking_scenario_snapshot", "") or "").strip():
            session.booking_scenario_snapshot = str(
                getattr(session, "last_scenario_intent", "") or "MAKE_BOOKING"
            ).strip()
