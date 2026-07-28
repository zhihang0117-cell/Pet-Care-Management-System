"""
MAKE_BOOKING scenario state machine and booking flow (single owner).

Starting booking, merging fields, missing fields, next question,
availability gate, booking summary, entry/collection/safety rules.
"""

from __future__ import annotations

import copy
import re

from customer_context import CustomerContext

from booking_draft import (
    is_booking_confirmation_message,
    is_explicit_booking_request,
    resolve_pet_id,
)
from intent_schema import has_greeting_prefix, strip_greeting_prefix
from session_store import (
    AWAIT_BOOKING_CONFIRMATION,
    BOOKING_CHOOSE_REPEAT_OR_NEW,
    CHECK_AVAILABLE_SLOTS_PENDING,
    COLLECT_CUSTOMER_NAME,
    REPEAT_BOOKING_PENDING_ACTION,
)

BOOKING_FIELD_ORDER = ("service_type", "pet_name", "preferred_date", "preferred_time")

NEW_CUSTOMER_BOOKING_FIELD_ORDER = (
    "customer_name",
    "service_type",
    "pet_name",
    "pet_type",
    "pet_size_or_height",
    "service_package",
    "preferred_date",
    "preferred_time",
)

BOOKING_STEP_BY_FIELD = {
    "customer_name": "ASK_CUSTOMER_NAME",
    "full_name": "ASK_CUSTOMER_NAME",
    "service_type": "ASK_SERVICE_TYPE",
    "pet_name": "ASK_PET_NAME",
    "pet_type": "ASK_PET_TYPE",
    "pet_size_or_height": "ASK_PET_SIZE_OR_HEIGHT",
    "service_package": "ASK_PACKAGE",
    "preferred_date": "ASK_DATE",
    "preferred_time": "ASK_TIME",
    "slot_acceptance": "CHECK_AVAILABILITY",
    "confirmation": "WAIT_FOR_CONFIRMATION",
}

PERSISTENT_BOOKING_FIELDS = (
    "customer_name",
    "phone_number",
    "customer_id",
    "existing_customer",
    "pet_name",
    "pet_type",
    "pet_size",
    "pet_height",
    "selected_service",
    "selected_package",
    "selected_addons",
    "preferred_date",
    "preferred_time",
)

_NAME_REJECT_TOKENS = frozenset(
    {
        "dog",
        "cat",
        "grooming",
        "boarding",
        "daycare",
        "today",
        "tomorrow",
        "yes",
        "no",
    }
)

_PET_TYPE_ONLY = re.compile(r"^\s*(?:a\s+)?(dog|cat|puppy|kitten|pet)\s*[!.?]*\s*$", re.I)

_REPEAT_CHOICE_PATTERNS = (
    r"same\s+service\s+as\s+last\s+time",
    r"same\s+as\s+last\s+time",
    r"same\s+service",
    r"repeat\s+(?:my\s+)?last\s+booking",
    r"book\s+the\s+same\s+(?:one|service)",
    r"same\s+one(?:\s+as\s+before)?",
    r"like\s+last\s+time",
    r"^same$",
    r"^repeat$",
)

_NEW_SERVICE_PATTERNS = (
    r"\bnew\s+service\b",
    r"\bnew\s+one\b",
    r"\bdifferent\s+service\b",
    r"\banother\s+service\b",
    r"\bchoose\s+(?:a\s+)?new\b",
)


def entity_value(entities: dict, key: str) -> str:
    return str(entities.get(key) or "").strip()


def is_empty_entity_value(value) -> bool:
    text = str(value or "").strip()
    return not text or text.upper() in {"UNKNOWN", "NULL", "NONE"}


def merge_persistent_entity(existing, new, *, explicitly_provided: bool = False) -> str:
    """Preserve existing session values when the current turn omits or nulls a field."""
    existing_text = str(existing or "").strip()
    if explicitly_provided and not is_empty_entity_value(new):
        return str(new).strip()
    if is_empty_entity_value(new):
        return existing_text
    return str(new).strip()


def _session_persistent_entity_snapshot(session) -> dict[str, str]:
    """Build booking state from durable session fields before merging current-turn entities."""
    snapshot: dict[str, str] = {}
    mappings = {
        "customer_name": str(getattr(session, "customer_name", "") or "").strip(),
        "phone_number": str(getattr(session, "phone_number", "") or "").strip(),
        "pet_name": str(getattr(session, "pet_name", "") or "").strip(),
        "pet_type": str(getattr(session, "pet_type", "") or "").strip(),
        "pet_size": str(getattr(session, "pet_size", "") or "").strip(),
        "pet_height": str(getattr(session, "pet_height", "") or "").strip(),
        "service_type": str(getattr(session, "last_service_type", "") or "").strip(),
        "selected_package": str(getattr(session, "selected_package", "") or "").strip(),
        "service_package": str(getattr(session, "service_package", "") or "").strip(),
        "preferred_date": str(getattr(session, "preferred_date", "") or "").strip(),
        "preferred_time": str(getattr(session, "preferred_time", "") or "").strip(),
    }
    customer_id = getattr(session, "customer_id", None)
    if customer_id is not None:
        mappings["customer_id"] = str(customer_id)
    if getattr(session, "existing_customer", False):
        mappings["existing_customer"] = "true"
    addons = list(getattr(session, "selected_addons", []) or [])
    if addons:
        snapshot["selected_addons"] = ",".join(str(item).strip() for item in addons if str(item).strip())
    for key, value in mappings.items():
        if value and value.upper() != "UNKNOWN":
            snapshot[key] = value
    return snapshot


def merge_collected_entities(
    session,
    entities: dict | None = None,
    *,
    explicitly_provided: set[str] | None = None,
    **updates: str,
) -> dict:
    """Merge session.collected_entities with entities/updates without dropping prior fields."""
    from pet_extraction import is_valid_stored_pet_name, normalize_pet_size

    explicitly_provided = explicitly_provided or set()
    merged = _session_persistent_entity_snapshot(session)
    merged.update(dict(getattr(session, "collected_entities", {}) or {}))

    def _apply(key: str, value, explicit: bool = False) -> None:
        if key == "selected_addons":
            return
        if isinstance(value, (list, dict)):
            return
        text = merge_persistent_entity(
            merged.get(key, ""),
            value,
            explicitly_provided=explicit or key in explicitly_provided,
        )
        if not text:
            return
        if key in {"customer_name", "full_name"} and text.lower() in _NAME_REJECT_TOKENS:
            return
        if key == "pet_name" and not is_valid_stored_pet_name(text):
            return
        if key == "pet_size":
            text = normalize_pet_size(text) or text
        if key == "preferred_time":
            from time_normalization import normalize_time

            text = normalize_time(text) or text
        merged[key] = text
        if key == "full_name" and text:
            merged["customer_name"] = text
        if key == "customer_name" and text:
            merged["full_name"] = text

    for key, value in (entities or {}).items():
        _apply(key, value, explicit=False)
    for key, value in updates.items():
        _apply(key, value, explicit=True)

    customer_name = merge_persistent_entity(
        merged.get("customer_name", ""),
        getattr(session, "customer_name", ""),
    )
    if customer_name:
        merged["customer_name"] = customer_name
        merged["full_name"] = customer_name
    return merged


def sync_session_booking_fields(session, entities: dict) -> None:
    """
    Persist resolved booking slots on the session object.

    Phase 4: active /chat uses canonical merge — prefer legacy_adapters for scripts.
    """
    customer_name = entity_value(entities, "customer_name") or entity_value(entities, "full_name")
    if customer_name:
        session.customer_name = customer_name
        entities["customer_name"] = customer_name
        entities["full_name"] = customer_name

    service = entity_value(entities, "service_type")
    if service and service.upper() != "UNKNOWN":
        session.last_service_type = service.upper()
        entities["service_type"] = service.upper()

    pet_name = entity_value(entities, "pet_name")
    if pet_name:
        from pet_extraction import is_valid_stored_pet_name

        if is_valid_stored_pet_name(pet_name):
            session.pet_name = pet_name

    pet_id_raw = entity_value(entities, "pet_id")
    if pet_id_raw:
        try:
            session.pet_id = int(pet_id_raw)
        except (TypeError, ValueError):
            pass
    elif pet_name and getattr(session, "pet_id", None) is not None:
        entities["pet_id"] = str(session.pet_id)

    preferred_date = entity_value(entities, "preferred_date")
    if preferred_date:
        session.preferred_date = preferred_date

    preferred_time = entity_value(entities, "preferred_time")
    if preferred_time:
        from time_normalization import normalize_time

        normalized = normalize_time(preferred_time) or preferred_time
        previous = str(getattr(session, "preferred_time", "") or "").strip()
        if previous and previous != normalized:
            from session_store import clear_availability_and_confirmation_state

            clear_availability_and_confirmation_state(session)
        session.preferred_time = normalized
        entities["preferred_time"] = normalized

    from package_selection import package_slug_to_label, sync_package_fields_to_session
    from pet_extraction import normalize_pet_size

    selected_package = entity_value(entities, "selected_package") or str(
        getattr(session, "selected_package", "") or ""
    ).strip()
    if selected_package:
        session.selected_package = selected_package
        label = package_slug_to_label(selected_package) or entity_value(entities, "service_package")
        if label:
            session.service_package = label

    service_package = entity_value(entities, "service_package")
    if service_package and not selected_package:
        session.service_package = service_package

    pet_type = entity_value(entities, "pet_type")
    if pet_type:
        session.pet_type = pet_type.upper()

    pet_size = entity_value(entities, "pet_size")
    if pet_size:
        normalized = normalize_pet_size(pet_size) or str(pet_size).strip().lower()
        if normalized:
            session.pet_size = normalized
            entities["pet_size"] = normalized

    pet_height = entity_value(entities, "pet_height") or entity_value(entities, "pet_size_or_height")
    if pet_height:
        session.pet_height = pet_height

    sync_package_fields_to_session(session, entities)
    session.collected_entities = merge_collected_entities(session, entities)


def apply_session_entities_to_intent(session, intent_json: dict) -> dict:
    """Inject accumulated booking fields from collected_entities only."""
    entities = dict(intent_json.get("entities") or {})
    merged = merge_collected_entities(session, entities)

    service = entity_value(merged, "service_type")
    if service and service.upper() != "UNKNOWN":
        intent_json["service_type"] = service.upper()
    intent_json["entities"] = merged
    return intent_json


def is_pet_type_only_message(message: str) -> bool:
    return bool(_PET_TYPE_ONLY.match(str(message or "").strip()))


def parse_repeat_or_new_choice(user_message: str) -> str:
    """Return 'repeat', 'new_service', GROOMING/DAYCARE/BOARDING, or empty."""
    from booking_draft import is_repeat_last_booking_message
    from session_continuation import _extract_service_type

    text = str(user_message or "").strip()
    lowered = text.lower()

    if is_repeat_last_booking_message(text) or any(
        re.search(p, lowered, re.I) for p in _REPEAT_CHOICE_PATTERNS
    ):
        return "repeat"

    if any(re.search(p, lowered, re.I) for p in _NEW_SERVICE_PATTERNS):
        return "new_service"

    service = _extract_service_type(text)
    if service:
        return service

    if lowered in {"last time", "same one"}:
        return "repeat"

    return ""


def ordered_missing_fields(missing: list[str], *, new_customer: bool = False) -> list[str]:
    order_name = NEW_CUSTOMER_BOOKING_FIELD_ORDER if new_customer else BOOKING_FIELD_ORDER
    order = {name: idx for idx, name in enumerate(order_name)}
    alias = {"full_name": "customer_name", "pet_size": "pet_size_or_height", "pet_height": "pet_size_or_height"}
    normalized: list[str] = []
    for field in missing:
        canonical = alias.get(field, field)
        if canonical not in normalized:
            normalized.append(canonical)
    return sorted(normalized, key=lambda name: order.get(name, len(order_name)))


def derive_completed_fields(session, entities: dict) -> list[str]:
    completed: list[str] = []
    if str(getattr(session, "customer_name", "") or "").strip() or entity_value(entities, "customer_name"):
        completed.append("customer_name")
    checks = {
        "service_type": entity_value(entities, "service_type") or str(getattr(session, "last_service_type", "") or ""),
        "pet_name": entity_value(entities, "pet_name") or str(getattr(session, "pet_name", "") or ""),
        "pet_type": entity_value(entities, "pet_type") or str(getattr(session, "pet_type", "") or ""),
        "pet_size_or_height": entity_value(entities, "pet_size_or_height")
        or entity_value(entities, "pet_size")
        or entity_value(entities, "pet_height")
        or str(getattr(session, "pet_size", "") or getattr(session, "pet_height", "") or ""),
        "service_package": entity_value(entities, "service_package")
        or entity_value(entities, "selected_package")
        or str(getattr(session, "service_package", "") or getattr(session, "selected_package", "") or ""),
        "preferred_date": entity_value(entities, "preferred_date") or str(getattr(session, "preferred_date", "") or ""),
        "preferred_time": entity_value(entities, "preferred_time") or str(getattr(session, "preferred_time", "") or ""),
    }
    for field, value in checks.items():
        if value and str(value).upper() != "UNKNOWN":
            completed.append(field)
    return list(dict.fromkeys(completed))


def recalculate_booking_step(session, missing: list[str]) -> str:
    ordered = ordered_missing_fields(
        missing,
        new_customer=not bool(getattr(session, "existing_customer", False)),
    )
    if not ordered:
        pending = str(getattr(session, "pending_action", "") or "").strip()
        if pending == AWAIT_BOOKING_CONFIRMATION:
            return "WAIT_FOR_CONFIRMATION"
        if pending == CHECK_AVAILABLE_SLOTS_PENDING:
            return "CHECK_AVAILABILITY"
        return "SHOW_BOOKING_SUMMARY"
    return BOOKING_STEP_BY_FIELD.get(ordered[0], f"ASK_{ordered[0].upper()}")


def update_booking_progress(session, entities: dict, missing: list[str]) -> None:
    session.completed_fields = derive_completed_fields(session, entities)
    session.current_step = recalculate_booking_step(session, missing)
    session.missing_fields = list(missing)


def build_pet_name_question(session=None, intent_json: dict | None = None) -> str:
    """New customers get a name prompt; returning customers may get pet disambiguation."""
    intent_json = intent_json or {}
    if session is not None and not bool(getattr(session, "existing_customer", False)):
        return "What is your pet's name?"
    if session is not None:
        from pet_profile import build_pet_choice_reply

        pets = get_customer_pets_for_session(session)
        if len(pets) > 1:
            names = [
                str(pet.get("pet_name") or "").strip()
                for pet in pets
                if str(pet.get("pet_name") or "").strip()
            ]
            if len(names) >= 2:
                return f"Which pet is this booking for: {' or '.join(names[:2])}?"
            from pet_profile import build_pet_choice_reply

            return build_pet_choice_reply(pets)
        if len(pets) == 1:
            name = str(pets[0].get("pet_name") or "").strip()
            if name:
                return f"Is this booking for {name}?"
    return "Which pet would this booking be for?"


def build_missing_field_reply(
    missing: list[str],
    session=None,
    intent_json: dict | None = None,
) -> str:
    """Recommend an easy booking next step and collect related fields together."""
    intent_json = intent_json or {}
    missing = ordered_missing_fields(
        missing,
        new_customer=not bool(getattr(session, "existing_customer", False)),
    )
    if not missing:
        return "May I have a few more details so I can help you with the booking?"

    first = missing[0]

    if first in {"customer_name", "full_name"}:
        return "May I have your name first so we can create your customer profile before booking?"

    if first == "repeat_or_new_service_choice":
        return build_repeat_or_new_reply(session, intent_json)

    if first == "service_type":
        if intent_json.get("new_customer_booking_entry") and not intent_json.get(
            "new_customer_booking_collection"
        ):
            return build_new_customer_booking_entry_reply(session, intent_json)
        known_pet = str(getattr(session, "pet_name", "") or "").strip()
        if known_pet:
            return (
                f"I can arrange grooming, daycare, or boarding for {known_pet} 😊 "
                "Send the service you need and your preferred date together, "
                "and I'll help you choose an available time."
            )
        if intent_json.get("new_service_choice"):
            return (
                "We can arrange grooming, daycare, or boarding 😊 "
                "For the quickest next step, send your pet's name and what care you need."
            )
        return (
            "We can arrange grooming, daycare, or boarding 😊 "
            "Send your pet's name, the service you need, and your preferred date together, "
            "and I'll help move the booking forward."
        )

    if len(missing) > 1:
        labels = {
            "customer_name": "your name",
            "full_name": "your name",
            "pet_name": "which pet this is for",
            "pet_type": "whether your pet is a dog or cat",
            "pet_size_or_height": "your pet's size or height",
            "preferred_date": "your preferred date",
            "preferred_time": "a preferred time, if you have one",
            "service_package": "the package you prefer",
        }
        requested = [labels.get(field, field.replace("_", " ")) for field in missing]
        joined = (
            " and ".join(requested)
            if len(requested) == 2
            else ", ".join(requested[:-1]) + f", and {requested[-1]}"
        )
        return (
            f"I can help arrange that 😊 To move the booking forward, please send these "
            f"booking details together in one message: {joined}. "
            "For example: “Milo, grooming, 3 August, morning.”"
        )

    if first == "pet_name":
        return build_pet_name_question(session, intent_json)

    if first == "preferred_date":
        return (
            "Let's find a suitable slot 😊 Send your preferred date and I'll check the "
            "available times for you."
        )

    if first == "preferred_time":
        return (
            "If you share your preferred time, I'll match it with the nearest available slot."
        )

    if first == "service_package":
        service = str(
            (intent_json or {}).get("service_type")
            or getattr(session, "last_service_type", "")
            or ""
        ).strip().upper()
        options = list(getattr(session, "service_options", []) or [])
        if options and str(getattr(session, "service_options_for", "") or "").upper() == service:
            pet_kind = str(getattr(session, "pet_type", "") or "").strip().upper()
            if pet_kind in {"CAT", "DOG"}:
                opposite = "dog " if pet_kind == "CAT" else "cat "
                options = [
                    item
                    for item in options
                    if not str(item.get("service_name") or "")
                    .strip()
                    .lower()
                    .startswith(opposite)
                ]
            lines = [
                f"• {item.get('service_name')}"
                + (f" — {item.get('price_display')}" if item.get("price_display") else "")
                for item in options[:6]
            ]
            return (
                f"For {service.lower()}, these are the available services:\n\n"
                + "\n".join(lines)
                + "\n\nWhich one would you like for the booking?"
            )
        return f"Which {service.lower() or 'service'} option would you like for this booking?"

    if first == "pet_type" and "pet_size_or_height" in missing[1:]:
        return (
            "Grooming price depends on pet size. "
            "May I know your pet is a dog or cat, and its height or size?"
        )

    if first == "pet_type":
        return "May I know your pet is a dog or cat?"

    if first == "pet_size_or_height":
        return "May I know your pet's height or size?"

    return (
        "May I have a few more details?\n"
        + "\n".join(f"• {field.replace('_', ' ')}" for field in missing)
    )


def build_single_pet_hint(pet_name: str, missing: list[str]) -> str | None:
    """Optional natural mention when a single pet was auto-selected."""
    name = str(pet_name or "").strip()
    if not name:
        return None
    if "preferred_date" in missing and "preferred_time" in missing:
        return f"I'll use {name} for this booking. What date and time do you prefer?"
    if "preferred_date" in missing:
        return f"I'll use {name} for this booking. What date would you prefer?"
    if "preferred_time" in missing:
        return f"I'll use {name} for this booking. What time do you prefer?"
    return None


def is_booking_pending(session) -> bool:
    pending = str(getattr(session, "pending_action", "") or "").strip()
    return pending in {
        BOOKING_CHOOSE_REPEAT_OR_NEW,
        REPEAT_BOOKING_PENDING_ACTION,
        CHECK_AVAILABLE_SLOTS_PENDING,
        AWAIT_BOOKING_CONFIRMATION,
        COLLECT_CUSTOMER_NAME,
    }


def mark_greeted(session) -> None:
    session.greeted_this_session = True


# --- booking entry ---


def is_generic_booking_request(user_message: str, intent_json: dict | None = None) -> bool:
    """True when the customer wants to book but has not named a service yet."""
    from session_continuation import _extract_service_type
    from booking_draft import is_repeat_last_booking_message

    intent_json = intent_json or {}
    entities = dict(intent_json.get("entities") or {})
    service = str(
        intent_json.get("service_type") or entities.get("service_type") or ""
    ).strip().upper()
    if service and service != "UNKNOWN":
        return False
    if _extract_service_type(user_message):
        return False
    if is_repeat_last_booking_message(user_message):
        return False
    return is_explicit_booking_request(user_message) or str(
        intent_json.get("scenario_intent") or ""
    ).strip() == "MAKE_BOOKING"


def _customer_first_name(full_name: str) -> str:
    parts = [part.strip() for part in str(full_name or "").split() if part.strip()]
    return parts[0] if parts else ""


def _should_greet_this_turn(session, intent_json: dict) -> bool:
    if bool(getattr(session, "greeted_this_session", False)):
        return False
    if bool(intent_json.get("greeting_prefix_detected")):
        return True
    return not bool(getattr(session, "greeted_this_session", False))


def format_last_booking_sentence(data: dict) -> str:
    """Natural sentence for a verified previous booking."""
    label = str(
        data.get("display_label")
        or data.get("package_name")
        or data.get("service_name")
        or ""
    ).strip()
    service_type = str(data.get("last_service_type") or data.get("service_type") or "").upper()
    if not label:
        if service_type == "DAYCARE":
            label = "daycare"
        elif service_type == "BOARDING":
            label = "boarding"
        else:
            return ""

    pet = str(data.get("pet_name") or "").strip()
    if pet:
        return f"Your last booking was {label} for {pet}."
    return f"Your last booking was {label}."


def build_repeat_or_new_reply(session, intent_json: dict) -> str:
    last_booking_result = intent_json.get("booking_entry_last_booking")
    if not isinstance(last_booking_result, dict):
        snapshot = dict(getattr(session, "last_booking_snapshot", {}) or {})
        if snapshot:
            last_booking_result = {"status": "success", "data": snapshot}
        else:
            last_booking_result = {"status": "not_found", "data": {}}
    status = str(last_booking_result.get("status") or "").strip()
    data = dict(last_booking_result.get("data") or {})
    has_valid = status == "success" and bool(data)
    sections: list[str] = []
    if _should_greet_this_turn(session, intent_json):
        if session.existing_customer:
            name = _customer_first_name(str(session.customer_name or ""))
            sections.append(f"Hi {name}, welcome back 😊" if name else "Hi, welcome back 😊")
        mark_greeted(session)
    if has_valid:
        sentence = format_last_booking_sentence(data)
        if sentence:
            sections.append(sentence)
        sections.append(
            "For the quickest booking, you can repeat that service. "
            "Just send your preferred date and I'll check the available times. "
            "If you'd like something different, you can choose grooming, daycare, or boarding."
        )
    else:
        sections.append(
            "We can arrange grooming, daycare, or boarding. Send the service, pet name, "
            "and preferred date together, and I'll help you choose an available slot."
        )
    return "\n\n".join(sections).strip()


def build_new_customer_booking_entry_reply(session, intent_json: dict) -> str:
    sections: list[str] = []
    if _should_greet_this_turn(session, intent_json):
        sections.append("Hi, welcome to Pawfect 😊")
        mark_greeted(session)
    sections.extend(
        [
            "I can help you arrange a booking.",
            "Send your pet's name, whether they're a dog or cat, the service you need "
            "(grooming, daycare, or boarding), and your preferred date together. "
            "I'll then help you choose an available slot.",
        ]
    )
    return "\n\n".join(sections).strip()


def build_new_customer_booking_collection_reply(session, intent_json: dict, user_message: str = "") -> str:
    entities = dict(intent_json.get("entities") or {})
    missing = ordered_missing_fields(list(intent_json.get("missing_information") or []), new_customer=True)
    next_question = build_missing_field_reply(missing, session, intent_json)
    sections: list[str] = []
    greeting_required = _should_greet_this_turn(session, intent_json)
    turn_extracted = dict(intent_json.get("turn_extracted_entities") or {})
    known_name = str(getattr(session, "customer_name", "") or entity_value(entities, "customer_name")).strip()
    if greeting_required and not known_name:
        sections.append("Hi, welcome to Pawfect 😊")
        mark_greeted(session)
    elif turn_extracted.get("pet_type"):
        sections.append("Got it.")
    elif intent_json.get("customer_name_collected_this_turn") or turn_extracted.get("customer_name"):
        if known_name:
            sections.append(f"Thanks, {known_name}.")
    if next_question:
        sections.append(next_question)
    return "\n\n".join(sections).strip()


def build_repeat_or_new_intent(session, intent_json: dict, user_message: str) -> dict:
    updated = copy.deepcopy(intent_json)
    updated["main_intent"] = "BOOKING_INTENT"
    updated["scenario_intent"] = "MAKE_BOOKING"
    updated["database_action_needed"] = False
    updated["retrieval_needed"] = False
    updated["retrieval_source"] = []
    updated["database_action"] = ""
    updated["next_action"] = "ask_missing_information"
    updated["missing_information"] = ["repeat_or_new_service_choice"]
    updated["greeting_prefix_detected"] = has_greeting_prefix(user_message)
    updated["returning_customer_booking_entry"] = True
    updated["entities"] = merge_collected_entities(session, dict(updated.get("entities") or {}))
    return updated


def build_new_customer_booking_entry_intent(session, intent_json: dict, user_message: str) -> dict:
    updated = copy.deepcopy(intent_json)
    updated["main_intent"] = "BOOKING_INTENT"
    updated["scenario_intent"] = "MAKE_BOOKING"
    updated["database_action_needed"] = False
    updated["retrieval_needed"] = False
    updated["retrieval_source"] = []
    updated["database_action"] = ""
    updated["next_action"] = "ask_missing_information"
    updated["missing_information"] = ["service_type"]
    updated["greeting_prefix_detected"] = has_greeting_prefix(user_message)
    updated["new_customer_booking_entry"] = True
    updated["entities"] = merge_collected_entities(session, dict(updated.get("entities") or {}))
    return updated


def build_collect_customer_name_intent(
    session, intent_json: dict, user_message: str, *, for_booking: bool
) -> dict:
    updated = copy.deepcopy(intent_json)
    updated["main_intent"] = "BOOKING_INTENT" if for_booking else "GREETING_INTENT"
    updated["scenario_intent"] = "COLLECT_CUSTOMER_NAME"
    updated["database_action_needed"] = False
    updated["retrieval_needed"] = False
    updated["retrieval_source"] = []
    updated["database_action"] = ""
    updated["next_action"] = "ask_missing_information"
    updated["missing_information"] = ["customer_name"]
    updated["collect_name_for_booking"] = for_booking
    updated["greeting_prefix_detected"] = has_greeting_prefix(user_message)
    return updated


def build_new_service_booking_intent(session, intent_json: dict, service_type: str) -> dict:
    updated = copy.deepcopy(intent_json)
    entities = merge_collected_entities(session, dict(updated.get("entities") or {}), service_type=service_type)
    updated["entities"] = entities
    updated["service_type"] = service_type
    updated["main_intent"] = "BOOKING_INTENT"
    updated["scenario_intent"] = "MAKE_BOOKING"
    updated["database_action_needed"] = False
    updated["retrieval_needed"] = False
    updated["retrieval_source"] = []
    updated["database_action"] = ""
    updated["next_action"] = "ask_missing_information"
    updated["missing_information"] = []
    sync_session_booking_fields(session, entities)
    return updated


def build_check_last_service_intent(session, intent_json: dict) -> dict:
    """Read-only last booking lookup while staying in repeat-or-new choice."""
    updated = copy.deepcopy(intent_json)
    updated["main_intent"] = "BOOKING_INTENT"
    updated["scenario_intent"] = "REPEAT_LAST_BOOKING"
    updated["database_action_needed"] = True
    updated["retrieval_needed"] = False
    updated["retrieval_source"] = []
    updated["database_action"] = "check_last_booking"
    updated["next_action"] = "check_last_booking"
    updated["missing_information"] = []
    updated["last_service_inquiry_only"] = True
    updated["confidence"] = max(float(updated.get("confidence") or 0.0), 0.95)
    return updated


def handle_repeat_or_new_choice(session, user_message: str, intent_json: dict, conv_ctx=None) -> dict:
    del conv_ctx
    choice = parse_repeat_or_new_choice(user_message)
    if choice == "repeat":
        updated = copy.deepcopy(intent_json)
        updated["main_intent"] = "BOOKING_INTENT"
        updated["scenario_intent"] = "REPEAT_LAST_BOOKING"
        updated["database_action_needed"] = True
        updated["retrieval_needed"] = False
        updated["retrieval_source"] = []
        updated["database_action"] = "check_last_booking"
        updated["next_action"] = "check_last_booking"
        updated["missing_information"] = []
        updated["confidence"] = max(float(updated.get("confidence") or 0.0), 0.95)
        return updated

    if choice == "new_service":
        updated = copy.deepcopy(intent_json)
        updated["main_intent"] = "BOOKING_INTENT"
        updated["scenario_intent"] = "MAKE_BOOKING"
        updated["database_action_needed"] = False
        updated["database_action"] = ""
        updated["next_action"] = "ask_missing_information"
        updated["missing_information"] = ["service_type"]
        updated["new_service_choice"] = True
        updated["confidence"] = max(float(updated.get("confidence") or 0.0), 0.95)
        return updated

    if choice in {"GROOMING", "DAYCARE", "BOARDING"}:
        result = build_new_service_booking_intent(session, intent_json, choice)
        result["confidence"] = max(float(result.get("confidence") or 0.0), 0.95)
        return result

    updated = copy.deepcopy(intent_json)
    updated["main_intent"] = "BOOKING_INTENT"
    updated["scenario_intent"] = "MAKE_BOOKING"
    updated["missing_information"] = ["repeat_or_new_service_choice"]
    updated["database_action_needed"] = False
    updated["database_action"] = ""
    updated["next_action"] = "ask_missing_information"
    updated["confidence"] = max(float(updated.get("confidence") or 0.0), 0.95)
    return updated


def extract_customer_name_from_message(message: str, *, expect_name: bool = False) -> str:
    from booking_draft import is_repeat_last_booking_message
    from session_continuation import _extract_service_type

    text = " ".join(str(message or "").strip().split())
    if not text:
        return ""
    lowered = text.lower().strip(" .,!😊")
    if lowered in _NAME_REJECT_TOKENS:
        return ""
    if not expect_name and len(text.split()) > 6:
        return ""
    if re.search(r"\b(my\s+pet|pet\s+is|dog|cat|puppy|kitten)\b", lowered):
        return ""
    if is_explicit_booking_request(text) or _extract_service_type(text):
        return ""
    if is_repeat_last_booking_message(text):
        return ""
    for prefix in ("my name is ", "i am ", "i'm ", "this is ", "name is ", "call me "):
        if lowered.startswith(prefix):
            candidate = text[len(prefix) :].strip(" .,!😊")
            if candidate and candidate.lower() not in _NAME_REJECT_TOKENS:
                return candidate
    if expect_name or len(text.split()) <= 3:
        if re.match(r"^[A-Za-z][A-Za-z\s'.-]{0,40}$", text):
            candidate = text.strip(" .,!😊")
            if candidate.lower() not in _NAME_REJECT_TOKENS:
                return candidate
    return ""


def handle_collect_customer_name(session, user_message: str, intent_json: dict, conv_ctx=None) -> dict:
    del conv_ctx
    expect_name = (
        str(getattr(session, "pending_action", "") or "").strip() == COLLECT_CUSTOMER_NAME
        or str(getattr(session, "current_step", "") or "").strip() == "ASK_CUSTOMER_NAME"
    )
    name = extract_customer_name_from_message(user_message, expect_name=expect_name)
    for_booking = (
        bool(intent_json.get("collect_name_for_booking"))
        or getattr(session, "booking_creation_flow", False)
        or (
            str(getattr(session, "pending_action", "") or "").strip() == COLLECT_CUSTOMER_NAME
            and str(getattr(session, "current_step", "") or "").strip() == "ASK_CUSTOMER_NAME"
            and str(getattr(session, "last_scenario_intent", "") or "").strip() != "CUSTOMER_GREETING"
        )
    )

    if not name:
        return build_collect_customer_name_intent(session, intent_json, user_message, for_booking=for_booking)

    entities = merge_collected_entities(
        session,
        dict(intent_json.get("entities") or {}),
        explicitly_provided={"customer_name"},
        customer_name=name,
    )
    session.customer_name = name
    sync_session_booking_fields(session, entities)

    if for_booking:
        return {
            **copy.deepcopy(intent_json),
            "main_intent": "BOOKING_INTENT",
            "scenario_intent": "MAKE_BOOKING",
            "entities": entities,
            "missing_information": [],
            "database_action_needed": False,
            "database_action": "",
            "next_action": "ask_missing_information",
            "new_customer_booking_collection": True,
            "customer_name_collected_this_turn": True,
        }

    return {
        **copy.deepcopy(intent_json),
        # The active collect-name state is authoritative. A short personal
        # name is often classified as UNKNOWN by the semantic model, but it is
        # still a valid answer to the question the assistant just asked.
        "main_intent": "GREETING_INTENT",
        "scenario_intent": "COLLECT_CUSTOMER_NAME",
        "missing_information": [],
        "collect_name_for_booking": False,
        "database_action_needed": False,
        "database_action": "",
        "retrieval_needed": False,
        "retrieval_source": [],
        "next_action": "ask_missing_information",
        "confidence": max(float(intent_json.get("confidence") or 0.0), 0.99),
        "reason": "Customer supplied the requested name in the active onboarding flow",
        "entities": entities,
    }


def apply_booking_entry_rules(session, intent_json: dict, user_message: str, conv_ctx=None) -> dict:
    """Gate generic booking requests before slot-filling for new/existing customers."""
    del conv_ctx
    updated = copy.deepcopy(intent_json)
    if updated.get("slot_just_accepted"):
        return updated
    if updated.get("coupon_eligibility_with_booking"):
        # Answer the live loyalty/coupon question first. Booking collection
        # resumes from verified fields after this read-only response.
        from session_continuation import _extract_service_type, _message_has_date_hint, _message_has_time_hint

        entities = dict(updated.get("entities") or {})
        if not _message_has_date_hint(user_message):
            entities.pop("preferred_date", None)
            session.preferred_date = ""
            session.collected_entities.pop("preferred_date", None)
            session.selected_slot = ""
            session.draft_booking_payload = {}
        if not _message_has_time_hint(user_message):
            entities.pop("preferred_time", None)
            session.preferred_time = ""
            session.collected_entities.pop("preferred_time", None)
        updated["entities"] = entities
        missing = compute_deferred_booking_missing(session, updated, user_message)
        if not _extract_service_type(user_message) and "service_type" not in missing:
            missing = ["service_type", *missing]
        updated["deferred_booking_missing"] = missing
        return updated
    if updated.get("booking_supporting_service_info") or updated.get("booking_service_info_handled"):
        return updated
    if updated.get("booking_supporting_info_needed"):
        return updated

    scenario = str(updated.get("scenario_intent") or "").strip()
    pending = str(getattr(session, "pending_action", "") or "").strip()

    if pending in {BOOKING_CHOOSE_REPEAT_OR_NEW, COLLECT_CUSTOMER_NAME}:
        return updated
    if scenario in {
        "REPEAT_LAST_BOOKING",
        "CONFIRM_BOOKING",
        "BOOKING_CONFIRMATION_ORPHAN",
        "CHECK_AVAILABILITY",
        "COLLECT_CUSTOMER_NAME",
    }:
        return updated
    if scenario != "MAKE_BOOKING":
        return updated

    effective_message = user_message
    if has_greeting_prefix(user_message):
        _, remainder = strip_greeting_prefix(user_message)
        if remainder:
            effective_message = remainder

    if not session.existing_customer and is_generic_booking_request(effective_message, updated):
        if not str(session.customer_name or "").strip():
            return build_collect_customer_name_intent(session, updated, user_message, for_booking=True)
        return build_new_customer_booking_entry_intent(session, updated, user_message)

    if not session.existing_customer and not str(session.customer_name or "").strip():
        return build_collect_customer_name_intent(session, updated, user_message, for_booking=False)

    if session.existing_customer and is_generic_booking_request(effective_message, updated):
        return build_repeat_or_new_intent(session, updated, user_message)

    return updated


def build_collect_customer_name_reply(intent_json: dict, session=None) -> str:
    for_booking = bool(intent_json.get("collect_name_for_booking"))
    with_greeting = bool(intent_json.get("greeting_prefix_detected"))

    if for_booking:
        if with_greeting:
            if session is not None:
                mark_greeted(session)
            return (
                "Hi, welcome to Pawfect 😊 "
                "May I have your name first so we can create your customer profile before booking?"
            )
        return (
            "May I have your name first so we can create your customer profile before booking?"
        )

    if with_greeting:
        return (
            "Hi, welcome to Pawfect 😊 "
            "May I have your name first so we can assist you better?"
        )
    return "May I have your name first so we can assist you better?"


# --- booking collection ---


def ensure_session_customer(session) -> None:
    """Resolve customer_id from phone when available (internal only)."""
    if session.customer_id is not None or not str(session.phone_number or "").strip():
        return

    from database_service import lookup_customer_by_phone

    result = lookup_customer_by_phone(str(session.phone_number or "").strip())
    if str(result.get("status") or "").strip() != "success":
        return

    data = result.get("data") or {}
    session.customer_id = data.get("customer_id")
    session.customer_name = str(data.get("full_name") or "").strip()
    session.existing_customer = True


def get_customer_pets_for_session(session) -> list[dict]:
    ensure_session_customer(session)
    if session.customer_id is None:
        return []

    from pet_profile import fetch_customer_pets

    return fetch_customer_pets(session)


def extract_pet_name_from_message(message: str, pets: list[dict]) -> str:
    from pet_extraction import extract_pet_name_from_message as _extract

    return _extract(message, pets)


def is_repeat_booking_flow(session) -> bool:
    return (
        str(getattr(session, "last_scenario_intent", "") or "").strip() == "REPEAT_LAST_BOOKING"
        or (
            str(getattr(session, "pending_action", "") or "").strip() == REPEAT_BOOKING_PENDING_ACTION
            and bool(getattr(session, "last_service_type", ""))
            and str(getattr(session, "last_scenario_intent", "") or "").strip() == "REPEAT_LAST_BOOKING"
        )
    )


def is_booking_collection_active(session, intent_json: dict) -> bool:
    scenario = str(intent_json.get("scenario_intent") or "").strip()
    pending = str(getattr(session, "pending_action", "") or "").strip()
    if pending == BOOKING_CHOOSE_REPEAT_OR_NEW:
        return False
    if scenario in {"REPEAT_LAST_BOOKING", "CONFIRM_BOOKING", "BOOKING_CONFIRMATION_ORPHAN", "COLLECT_CUSTOMER_NAME"}:
        return False
    if pending == COLLECT_CUSTOMER_NAME and scenario != "MAKE_BOOKING":
        return False
    missing = list(intent_json.get("missing_information") or [])
    if "repeat_or_new_service_choice" in missing or "customer_name" in missing:
        return False
    if getattr(session, "booking_creation_flow", False):
        return True
    if scenario == "MAKE_BOOKING":
        return True
    if pending == REPEAT_BOOKING_PENDING_ACTION:
        return True
    if is_repeat_booking_flow(session):
        return True
    return False


def _apply_message_extractions(entities: dict, user_message: str, pets: list[dict], session=None) -> dict:
    from pet_extraction import extract_natural_booking_entities, merge_pet_profile_without_erasing
    from pet_profile import apply_pet_profile_to_session, get_pet_by_customer_and_name
    from session_store import clear_availability_and_confirmation_state
    from time_normalization import extract_time_from_message, normalize_time
    from session_continuation import (
        _extract_preferred_date,
        _extract_preferred_time,
        _extract_service_type,
        _message_has_date_hint,
        _message_has_time_hint,
    )

    natural = extract_natural_booking_entities(user_message, pets)
    if natural:
        entities = {**entities, **natural}

    updated = merge_pet_profile_without_erasing(session, dict(entities), user_message)
    if not entity_value(updated, "service_type"):
        extracted_service = _extract_service_type(user_message)
        if extracted_service:
            updated["service_type"] = extracted_service
    if not entity_value(updated, "preferred_date") and _message_has_date_hint(user_message):
        updated["preferred_date"] = _extract_preferred_date(user_message)
    if _message_has_time_hint(user_message):
        extracted_time = extract_time_from_message(user_message)
        normalized = normalize_time(extracted_time) or extracted_time
        if normalized:
            previous = str(getattr(session, "preferred_time", "") or "").strip()
            if session is not None and previous and previous != normalized:
                clear_availability_and_confirmation_state(session)
            updated["preferred_time"] = normalized
    elif not entity_value(updated, "preferred_time"):
        updated["preferred_time"] = _extract_preferred_time(user_message)
    if not entity_value(updated, "pet_name") and user_message:
        from pet_extraction import extract_pet_name_from_natural_request

        pet_name = extract_pet_name_from_natural_request(user_message, pets)
        if pet_name:
            updated["pet_name"] = pet_name
            if session is not None:
                matched_pet = get_pet_by_customer_and_name(session, pet_name)
                if matched_pet:
                    profile = apply_pet_profile_to_session(session, matched_pet)
                    updated = merge_collected_entities(session, updated, **profile)
    return updated


def _merged_booking_entities(session, intent_json: dict, user_message: str = "") -> dict:
    """Return fully merged persistent booking state for missing-field calculation."""
    from pet_extraction import merge_pet_profile_without_erasing

    raw_entities = dict(intent_json.get("entities") or {})
    if not session.existing_customer:
        raw_entities.pop("customer_name", None)
        raw_entities.pop("full_name", None)
    entities = merge_collected_entities(session, raw_entities)
    entities = merge_pet_profile_without_erasing(session, entities, user_message)
    entities = _apply_message_extractions(
        entities, user_message, get_customer_pets_for_session(session), session=session
    )
    if str(getattr(session, "customer_name", "") or "").strip():
        entities["customer_name"] = str(session.customer_name).strip()
        entities["full_name"] = str(session.customer_name).strip()
    if session.last_service_type and is_empty_entity_value(entities.get("service_type")):
        entities["service_type"] = session.last_service_type
    for key in ("pet_name", "preferred_date", "preferred_time", "pet_type", "pet_size", "pet_height", "pet_id"):
        session_value = getattr(session, key, "")
        if key == "pet_id":
            if getattr(session, "pet_id", None) is not None and not entity_value(entities, "pet_id"):
                entities["pet_id"] = str(session.pet_id)
            continue
        if str(session_value or "").strip() and is_empty_entity_value(entities.get(key)):
            entities[key] = str(session_value).strip()
    return entities


def _filter_completed_missing(session, entities: dict, missing: list[str]) -> list[str]:
    completed = set(derive_completed_fields(session, entities))
    alias_completed = completed | {
        "full_name" if "customer_name" in completed else "",
        "customer_name" if "full_name" in completed else "",
        "pet_size" if "pet_size_or_height" in completed else "",
        "pet_height" if "pet_size_or_height" in completed else "",
    }
    alias_completed.discard("")
    filtered = []
    for field in missing:
        canonical = field
        if field == "full_name":
            canonical = "customer_name"
        if canonical in completed or field in alias_completed:
            continue
        if field not in filtered:
            filtered.append(field)
    return filtered


def compute_booking_missing_fields(
    session,
    intent_json: dict,
    user_message: str = "",
    *,
    defer_pet_autoselect: bool = True,
    skip_session_sync: bool = False,
) -> list[str]:
    from pet_profile import enrich_session_pet_profile

    del defer_pet_autoselect
    enrich_session_pet_profile(session, user_message)

    entities = _merged_booking_entities(session, intent_json, user_message)
    new_customer = not bool(getattr(session, "existing_customer", False))

    missing: list[str] = []

    if is_repeat_booking_flow(session):
        service = str(
            intent_json.get("service_type")
            or entities.get("service_type")
            or getattr(session, "last_service_type", "")
            or ""
        ).strip().upper()
        if service and service != "UNKNOWN":
            intent_json["service_type"] = service
            entities["service_type"] = service
        if getattr(session, "pet_name", ""):
            entities["pet_name"] = session.pet_name
        if not entity_value(entities, "preferred_date"):
            missing.append("preferred_date")
        if not entity_value(entities, "preferred_time") and not entity_value(entities, "preferred_date"):
            missing.append("preferred_time")
        intent_json["entities"] = entities
        if not skip_session_sync:
            sync_session_booking_fields(session, entities)
        missing = _filter_completed_missing(session, entities, missing)
        if not skip_session_sync:
            update_booking_progress(session, entities, missing)
        else:
            session.completed_fields = derive_completed_fields(session, entities)
            intent_json["current_step"] = recalculate_booking_step(session, missing)
        return ordered_missing_fields(missing, new_customer=new_customer)

    if new_customer and bool(getattr(session, "booking_creation_flow", False)):
        if not entity_value(entities, "customer_name"):
            missing.append("customer_name")

    service = str(
        intent_json.get("service_type") or entities.get("service_type") or ""
    ).strip().upper()
    if not service or service == "UNKNOWN":
        missing.append("service_type")
    else:
        intent_json["service_type"] = service
        entities["service_type"] = service

    pets = get_customer_pets_for_session(session)
    pet_name = entity_value(entities, "pet_name")

    if not pet_name or not entity_value(entities, "pet_id"):
        if pet_name and session.customer_id is not None:
            resolved_id = resolve_pet_id(session.customer_id, pet_name, session.phone_number)
            if resolved_id is not None:
                session.pet_id = resolved_id
                entities["pet_id"] = str(resolved_id)
            else:
                missing.append("pet_name")
        else:
            missing.append("pet_name")
    elif session.customer_id is not None:
        resolved_id = resolve_pet_id(session.customer_id, pet_name, session.phone_number)
        if resolved_id is None:
            missing.append("pet_name")
        else:
            session.pet_id = resolved_id
            entities["pet_id"] = str(resolved_id)

    service_upper = str(service or entities.get("service_type") or "").strip().upper()
    if service_upper == "GROOMING":
        if not entity_value(entities, "pet_type"):
            missing.append("pet_type")
        elif not (
            entity_value(entities, "pet_size_or_height")
            or entity_value(entities, "pet_size")
            or entity_value(entities, "pet_height")
        ):
            missing.append("pet_size_or_height")

    if (
        service_upper in {"GROOMING", "DAYCARE", "BOARDING"}
        and not entity_value(entities, "service_package")
        and not entity_value(entities, "selected_package")
    ):
        missing.append("service_package")

    collected = dict(getattr(session, "collected_entities", {}) or {})
    if (
        service_upper == "GROOMING"
        and ("service_package" in collected or entity_value(entities, "service_package"))
        and not entity_value(entities, "service_package")
        and not entity_value(entities, "selected_package")
    ):
        missing.append("service_package")

    if not entity_value(entities, "preferred_date"):
        missing.append("preferred_date")
    # A known date is sufficient to query the whole day. The customer selects
    # preferred_time from the returned real slots on the next turn.
    if not entity_value(entities, "preferred_time") and not entity_value(entities, "preferred_date"):
        missing.append("preferred_time")

    missing = _filter_completed_missing(session, entities, missing)
    intent_json["entities"] = entities
    if not skip_session_sync:
        sync_session_booking_fields(session, entities)
    ordered = ordered_missing_fields(missing, new_customer=new_customer)
    if not skip_session_sync:
        update_booking_progress(session, entities, ordered)
    else:
        session.completed_fields = derive_completed_fields(session, entities)
        intent_json["current_step"] = recalculate_booking_step(session, ordered)
    intent_json["completed_fields"] = list(session.completed_fields)
    return ordered


def compute_deferred_booking_missing(session, intent_json: dict, user_message: str = "") -> list[str]:
    """Missing booking fields without auto-selecting a single pet (mixed info + booking)."""
    return compute_booking_missing_fields(
        session, intent_json, user_message, defer_pet_autoselect=True, skip_session_sync=True
    )


def has_complete_booking_fields(session, intent_json: dict, user_message: str = "") -> bool:
    probe = copy.deepcopy(intent_json)
    return not compute_booking_missing_fields(session, probe, user_message, skip_session_sync=True)


def apply_booking_collection_rules(
    session,
    intent_json: dict,
    user_message: str,
    conv_ctx=None,
) -> dict:
    """Gate slot lookup until required booking fields are collected."""
    del conv_ctx
    updated = copy.deepcopy(intent_json)
    if updated.get("slot_just_accepted"):
        return updated
    if updated.get("coupon_eligibility_with_booking"):
        return updated
    if updated.get("standalone_service_info"):
        return updated
    if str(updated.get("scenario_intent") or "").strip() == "SERVICE_INFORMATION":
        return updated
    if updated.get("booking_supporting_service_info") or updated.get("booking_service_info_handled"):
        return updated
    if updated.get("booking_supporting_info_needed"):
        return updated
    if str(updated.get("sub_flow") or "").strip() == "booking_price_pending_info":
        return updated

    updated = apply_session_entities_to_intent(session, updated)
    scenario = str(updated.get("scenario_intent") or "").strip()
    if scenario in {"REPEAT_LAST_BOOKING", "CONFIRM_BOOKING", "BOOKING_CONFIRMATION_ORPHAN"}:
        return updated
    if not is_booking_collection_active(session, updated):
        return updated

    missing = compute_booking_missing_fields(session, updated, user_message)
    updated["missing_information"] = missing
    if not session.existing_customer and session.booking_creation_flow:
        updated["new_customer_booking_collection"] = True
        updated["current_step"] = getattr(session, "current_step", "")
        updated["completed_fields"] = list(getattr(session, "completed_fields", []) or [])

    if missing:
        if (
            "service_package" in missing
            and "pet_type" not in missing
            and str(getattr(session, "service_options_for", "") or "").upper()
            != str(updated.get("service_type") or (updated.get("entities") or {}).get("service_type") or "").upper()
        ):
            updated["main_intent"] = "BOOKING_INTENT"
            updated["scenario_intent"] = "GET_BOOKING_SERVICE_OPTIONS"
            updated["database_action_needed"] = True
            updated["retrieval_needed"] = True
            updated["retrieval_source"] = ["service_information"]
            updated["database_action"] = "get_booking_service_options"
            updated["next_action"] = "get_booking_service_options"
            updated["deferred_booking_missing"] = missing
            updated["missing_information"] = []
            updated["confidence"] = max(float(updated.get("confidence") or 0.0), 0.95)
            return updated
        updated["main_intent"] = "BOOKING_INTENT"
        updated["scenario_intent"] = "MAKE_BOOKING"
        updated["database_action_needed"] = False
        updated["retrieval_needed"] = False
        updated["retrieval_source"] = []
        updated["database_action"] = ""
        updated["next_action"] = "ask_missing_information"
        updated["confidence"] = max(float(updated.get("confidence") or 0.0), 0.95)
        return updated

    updated["main_intent"] = "BOOKING_INTENT"
    updated["scenario_intent"] = "CHECK_AVAILABILITY"
    updated["database_action_needed"] = True
    updated["retrieval_needed"] = False
    updated["retrieval_source"] = []
    updated["database_action"] = "check_availability"
    updated["next_action"] = "check_availability"
    updated["confidence"] = max(float(updated.get("confidence") or 0.0), 0.95)
    return updated


def build_booking_missing_info_reply(intent_json: dict, session=None) -> str:
    new_customer = not bool(getattr(session, "existing_customer", False)) if session is not None else False
    missing = ordered_missing_fields(
        list(intent_json.get("missing_information") or []),
        new_customer=new_customer,
    )
    if str(intent_json.get("sub_flow") or "").strip() == "booking_price_pending_info":
        price_fields = [field for field in missing if field in {"pet_type", "pet_size_or_height"}]
        booking_fields = ordered_missing_fields(
            [field for field in missing if field not in {"pet_type", "pet_size_or_height"}]
        )
        missing = price_fields + booking_fields

    if not missing:
        return "May I have a few more details so I can help you with the booking?"

    return build_missing_field_reply(missing, session, intent_json)


# --- booking safety ---

_TIME_ONLY = re.compile(
    r"^\s*("
    r"\d{1,2}(:\d{2})?\s*(am|pm)|"
    r"\d{1,2}\s*(am|pm)|"
    r"morning|afternoon|evening|night|noon"
    r")\s*[!.?]*\s*$",
    re.I,
)

_LAST_SERVICE_INQUIRY = re.compile(
    r"\b("
    r"check\s+my\s+last\s+(?:service|booking)|"
    r"what\s+(?:was|is)\s+my\s+last\s+(?:service|booking)|"
    r"what\s+did\s+i\s+book\s+last\s+time|"
    r"help\s+(?:me\s+)?check\s+my\s+last\s+(?:service|booking)|"
    r"my\s+last\s+service|"
    r"last\s+booking\s+details"
    r")\b",
    re.I,
)

_PET_CONFIRM_YES = re.compile(
    r"^\s*(yes|yeah|yep|yup|correct|that'?s?\s+(?:right|correct)|ok(?:ay)?)\s*[!.?]*\s*$",
    re.I,
)


def is_time_only_message(message: str) -> bool:
    return bool(_TIME_ONLY.match(str(message or "").strip()))


def is_last_service_inquiry(message: str) -> bool:
    return bool(_LAST_SERVICE_INQUIRY.search(str(message or "")))


def is_safe_booking_confirmation_message(message: str) -> bool:
    """Strict YES/confirm detection; never treat time-only input as confirmation."""
    text = str(message or "").strip()
    if not text or is_time_only_message(text):
        return False
    return is_booking_confirmation_message(text)


def can_accept_booking_confirmation(session, message: str) -> bool:
    if not is_safe_booking_confirmation_message(message):
        return False
    pending = str(getattr(session, "pending_action", "") or "").strip()
    if pending != AWAIT_BOOKING_CONFIRMATION:
        return False
    draft = getattr(session, "draft_booking_payload", None) or {}
    return bool(draft)


def extract_pet_confirmation(message: str, pets: list[dict]) -> str:
    """Accept explicit pet name or YES when confirming a single known pet."""
    name = extract_pet_name_from_message(message, pets)
    if name:
        return name
    if len(pets) == 1 and _PET_CONFIRM_YES.match(str(message or "").strip()):
        return str(pets[0].get("pet_name") or "").strip()
    return ""


def apply_booking_confirmation_safety(session, intent_json: dict, user_message: str) -> dict:
    """Block CONFIRM_BOOKING unless draft + await_booking_confirmation + explicit YES."""
    # A single affirmative turn may accept the offered slot OR confirm the
    # resulting booking summary, never both.  handle_session_before_routing()
    # marks the slot-acceptance turn after it creates the draft.  Promoting
    # that same "yes" to CONFIRM_BOOKING here skips the summary and leaves the
    # next turn liable to fall back into availability again.
    if intent_json.get("slot_just_accepted"):
        return intent_json

    scenario = str(intent_json.get("scenario_intent") or "").strip()
    if scenario != "CONFIRM_BOOKING" and not is_safe_booking_confirmation_message(user_message):
        return intent_json

    def _confirmation_ready() -> bool:
        pending = str(getattr(session, "pending_action", "") or "").strip()
        step = str(getattr(session, "current_step", "") or "").strip()
        draft = dict(getattr(session, "draft_booking_payload", {}) or {})
        if can_accept_booking_confirmation(session, user_message):
            return True
        if pending == AWAIT_BOOKING_CONFIRMATION and draft:
            return True
        if step in {"SHOW_BOOKING_SUMMARY", "WAIT_FOR_CONFIRMATION"}:
            return True
        if has_complete_booking_fields(session, intent_json, user_message) and (
            draft or pending == "make_booking_pending_info"
        ):
            return True
        return False

    if _confirmation_ready():
        updated = copy.deepcopy(intent_json)
        updated["scenario_intent"] = "CONFIRM_BOOKING"
        updated["main_intent"] = "BOOKING_INTENT"
        updated["database_action_needed"] = True
        updated["database_action"] = "create_booking"
        updated["next_action"] = "create_booking"
        updated["missing_information"] = []
        return updated

    if is_safe_booking_confirmation_message(user_message):
        if compute_booking_missing_fields(session, intent_json, user_message, skip_session_sync=True):
            updated = copy.deepcopy(intent_json)
            updated["scenario_intent"] = "MAKE_BOOKING"
            updated["main_intent"] = "BOOKING_INTENT"
            updated["database_action_needed"] = False
            updated["database_action"] = ""
            updated["next_action"] = "ask_missing_information"
            return updated
        updated = copy.deepcopy(intent_json)
        updated["scenario_intent"] = "BOOKING_CONFIRMATION_ORPHAN"
        updated["main_intent"] = "BOOKING_INTENT"
        updated["database_action_needed"] = False
        updated["database_action"] = ""
        updated["next_action"] = "ask_missing_information"
        return updated

    if scenario == "CONFIRM_BOOKING":
        updated = copy.deepcopy(intent_json)
        updated["scenario_intent"] = "MAKE_BOOKING"
        updated["main_intent"] = "BOOKING_INTENT"
        updated["database_action_needed"] = False
        updated["database_action"] = ""
        return updated

    return intent_json
