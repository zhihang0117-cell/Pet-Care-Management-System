"""
Package / price / service-information questions during an active booking flow.

When the customer asks about packages or pricing mid-booking, answer via RAG but
preserve booking session memory and continue slot-filling afterward.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from booking_flow import (
    entity_value,
    merge_collected_entities,
    ordered_missing_fields,
    sync_session_booking_fields,
)
from session_store import (
    AWAIT_BOOKING_CONFIRMATION,
    BOOKING_CHOOSE_REPEAT_OR_NEW,
    BOOKING_PRICE_PENDING_SUB_FLOW,
    BOOKING_SERVICE_INFO_SUB_FLOW,
    REPEAT_BOOKING_PENDING_ACTION,
    SERVICE_PRICE_PENDING_ACTION,
)

ACTIVE_BOOKING_PENDING_ACTIONS = {
    BOOKING_CHOOSE_REPEAT_OR_NEW,
    REPEAT_BOOKING_PENDING_ACTION,
    AWAIT_BOOKING_CONFIRMATION,
}


@dataclass
class ServiceInfoObservation:
    intent_patch: dict = field(default_factory=dict)
    service_info_requested: bool = False
    requested_service: str = ""
    requested_package: str | None = None
    required_fact_types: list[str] = field(default_factory=list)
    resume_booking_after_answer: bool = False
    info_type: str = ""
    standalone: bool = False
    price_missing_fields: list[str] = field(default_factory=list)
    ask_package_after_info: bool = False
    deferred_booking_missing: list[str] = field(default_factory=list)
    clear_stale_booking: bool = False
    clear_enquiry_flow: bool = False
    entity_fields: dict = field(default_factory=dict)


def _apply_service_info_observation(session, obs: ServiceInfoObservation) -> None:
    if obs.clear_stale_booking:
        from session_continuation import clear_stale_booking_session_state

        clear_stale_booking_session_state(session)
    if obs.clear_enquiry_flow:
        from session_store import SERVICE_PRICE_PENDING_ACTION

        session.pending_action = ""
        session.sub_flow = ""
        session.missing_fields = []
    if obs.entity_fields:
        sync_session_booking_fields(session, obs.entity_fields)
    if obs.price_missing_fields:
        session.missing_fields = list(obs.price_missing_fields)
    if obs.standalone and obs.price_missing_fields:
        from session_store import SERVICE_PRICE_PENDING_ACTION

        session.pending_action = SERVICE_PRICE_PENDING_ACTION
        session.sub_flow = SERVICE_PRICE_PENDING_ACTION
    if obs.resume_booking_after_answer:
        session.booking_creation_flow = True
        if obs.deferred_booking_missing:
            session.missing_fields = list(obs.deferred_booking_missing)

_SERVICE_INFO_SIGNAL = re.compile(
    r"\b("
    r"package(s)?|how\s+much|price|pricing|cost|fee|"
    r"what\s+.+\s+(include|included)|what\s+is\s+included|"
    r"what\s+.+\s+(do\s+you\s+)?(have|offer|provide)|"
    r"what\s+.+\s+packages?\s+(do\s+you\s+)?(have|offer)|"
    r"service\s+option(s)?|"
    r"what\s+grooming\s+(service|package|packages)|"
    r"grooming\s+packages?|"
    r"basic\s+grooming|full\s+grooming|nano\s+spa"
    r")\b",
    re.I,
)

_PRICE_SIGNAL = re.compile(r"\b(how\s+much|price|pricing|cost|fee)\b", re.I)

_PACKAGE_QUESTION_SIGNAL = re.compile(
    r"\b(what|which|tell\s+me|do\s+you\s+have|offer|include|included)\b",
    re.I,
)

_UNRELATED_POLICY_SIGNAL = re.compile(
    r"\b(cancellation|cancel\s+policy|refund\s+policy|reschedule\s+policy)\b",
    re.I,
)


def is_unrelated_policy_topic(message: str, scenario: str = "") -> bool:
    text = str(message or "")
    if str(scenario or "").strip() == "CANCELLATION_POLICY":
        return True
    return bool(_UNRELATED_POLICY_SIGNAL.search(text))


def is_active_booking_flow(session) -> bool:
    pending = str(getattr(session, "pending_action", "") or "").strip()
    if pending in ACTIVE_BOOKING_PENDING_ACTIONS:
        return True
    return bool(getattr(session, "booking_creation_flow", False)) and pending in {
        REPEAT_BOOKING_PENDING_ACTION,
        AWAIT_BOOKING_CONFIRMATION,
        BOOKING_CHOOSE_REPEAT_OR_NEW,
    }


def is_service_package_price_question(message: str) -> bool:
    text = str(message or "").strip()
    if not text or _UNRELATED_POLICY_SIGNAL.search(text):
        return False
    if re.search(r"\bwhat\s+package(s)?\s+(?:do\s+you\s+)?(?:have|offer)\b", text, re.I):
        return True
    if not _SERVICE_INFO_SIGNAL.search(text):
        return False
    package = extract_service_package(text)
    if package and not _PACKAGE_QUESTION_SIGNAL.search(text) and not _PRICE_SIGNAL.search(text):
        return False
    return True


def is_price_question(message: str) -> bool:
    return bool(_PRICE_SIGNAL.search(str(message or "")))


def is_package_list_question(message: str) -> bool:
    text = str(message or "")
    return bool(re.search(r"\bpackage(s)?\b", text, re.I)) and bool(
        _PACKAGE_QUESTION_SIGNAL.search(text)
    )


def grooming_package_list_fallback(service_type: str | None = None) -> str:
    """Customer-facing fallback when RAG has no grooming package details."""
    service_key = str(service_type or "grooming").strip().lower()
    if service_key == "grooming":
        return (
            "For grooming, we offer Basic Grooming and Full Grooming packages. "
            "Basic Grooming covers essential wash and tidy, while Full Grooming "
            "includes a more complete groom and styling."
        )
    return (
        f"I don't have the exact {service_key} package details here yet, "
        "but I can help check with the team."
    )


def extract_service_package(message: str) -> str:
    from package_selection import extract_service_package as _extract_base_package

    return _extract_base_package(message)


def _session_entities(session) -> dict:
    return merge_collected_entities(session, {})


def _has_pet_type(entities: dict, session) -> bool:
    pet_type = entity_value(entities, "pet_type") or str(getattr(session, "pet_type", "") or "").strip()
    return bool(pet_type) and pet_type.upper() not in {"", "UNKNOWN"}


def _has_pet_size_or_height(entities: dict, session) -> bool:
    for key in ("pet_size_or_height", "pet_height", "pet_size"):
        value = entity_value(entities, key) or str(getattr(session, key, "") or "").strip()
        if value and value.upper() != "UNKNOWN":
            return True
    return False


def price_info_missing_fields(session, service_type: str = "") -> list[str]:
    service = str(
        service_type or getattr(session, "last_service_type", "") or _session_entities(session).get("service_type") or ""
    ).strip().upper()
    if service != "GROOMING":
        return []

    entities = _session_entities(session)
    missing: list[str] = []
    if not _has_pet_type(entities, session):
        missing.append("pet_type")
    if not _has_pet_size_or_height(entities, session):
        missing.append("pet_size_or_height")
    return missing


def extract_pet_price_info(message: str) -> dict[str, str]:
    from pet_extraction import extract_pet_profile_from_message

    return extract_pet_profile_from_message(message)


def infer_pet_size_from_height(height_text: str) -> str:
    from pet_extraction import infer_pet_size_from_height as _infer

    return _infer(height_text)


def _booking_missing_snapshot(session) -> list[str]:
    stored = list(getattr(session, "missing_fields", []) or [])
    booking_only = [
        field
        for field in stored
        if field not in {"pet_type", "pet_size_or_height", "confirmation"}
    ]
    return booking_only


def _merge_booking_and_price_missing(session, price_missing: list[str]) -> list[str]:
    combined = list(price_missing) + _booking_missing_snapshot(session)
    return list(dict.fromkeys(combined))


_CLEAR_BOOKING_SIGNAL = re.compile(
    r"\b("
    r"book(?:ing)?|"
    r"appointment|"
    r"reserve|"
    r"make\s+(?:a\s+)?(?:booking|appointment)|"
    r"schedule\s+(?:a\s+)?(?:booking|appointment)|"
    r"check\s+availability|"
    r"available\s+slot|"
    r"\bslot\b"
    r")\b",
    re.I,
)


def has_package_selection_intent(message: str) -> bool:
    """True when the customer selects or requests a specific package."""
    text = " ".join(str(message or "").strip().split()).lower()
    if not text:
        return False
    patterns = (
        r"\b(want|would like)\s+(?:the\s+)?(?:dog\s+|cat\s+)?(?:trimming|grooming|daycare|boarding)\s+package\b",
        r"\b(want|would like)\s+(?:the\s+)?\w+\s+package\b",
        r"\bbook\s+this\s+(?:package|service)\b",
        r"\b(want|would like)\s+this\s+package\b",
        r"\b(want|would like)\s+(?:the\s+)?(?:all shave|keep head|full scissor)\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def has_clear_booking_signal(message: str) -> bool:
    """True only for explicit booking words — 'want' alone is not booking."""
    text = str(message or "").strip()
    if not text:
        return False
    if _CLEAR_BOOKING_SIGNAL.search(text):
        return True
    if re.search(r"\b(want|would\s+like)\s+to\s+book\b", text, re.I):
        return True
    if re.search(r"\bbook\s+(?:a\s+)?(?:grooming|daycare|boarding)\b", text, re.I):
        return True
    if has_package_selection_intent(text):
        return True
    from booking_draft import is_explicit_booking_request

    return is_explicit_booking_request(text)


def is_standalone_service_info_enquiry(message: str, intent_json: dict | None = None) -> bool:
    """Package/price/service question without explicit booking intent."""
    intent_json = intent_json or {}
    if not is_service_package_price_question(message):
        return False
    if has_clear_booking_signal(message):
        return False
    if intent_json.get("booking_supporting_info_needed"):
        return False
    return True


def is_standalone_non_booking_enquiry(message: str, intent_json: dict | None = None) -> bool:
    """Standalone info/status/policy/loyalty that must not continue a stale booking."""
    from intent_schema import (
        _is_booking_status_query,
        _is_loyalty_account_query,
        POLICY_RAG_SCENARIOS,
    )

    text = str(message or "").strip()
    if not text:
        return False
    if is_standalone_service_info_enquiry(text, intent_json):
        return True
    if _is_booking_status_query(text):
        return True
    if _is_loyalty_account_query(text):
        return True
    if is_unrelated_policy_topic(text):
        return True
    scenario = str((intent_json or {}).get("scenario_intent") or "").strip()
    if scenario in POLICY_RAG_SCENARIOS and scenario != "SERVICE_INFORMATION":
        return True
    return False


def has_stale_booking_session(session) -> bool:
    pending = str(getattr(session, "pending_action", "") or "").strip()
    if pending:
        return True
    if getattr(session, "booking_creation_flow", False):
        return True
    if getattr(session, "collected_entities", None):
        return True
    if getattr(session, "draft_booking_payload", None):
        return True
    return False


def _resolve_service_type_for_enquiry(session, intent_json: dict, user_message: str) -> str:
    from session_continuation import _extract_service_type

    entities = merge_collected_entities(session, dict(intent_json.get("entities") or {}))
    service = str(
        _extract_service_type(user_message)
        or entities.get("service_type")
        or intent_json.get("service_type")
        or getattr(session, "last_service_type", "")
        or ""
    ).strip().upper()
    if service and service != "UNKNOWN":
        return service
    return "GROOMING" if re.search(r"\bgroom", user_message, re.I) else "UNKNOWN"


def build_standalone_service_information_intent(
    session, intent_json: dict, user_message: str
) -> dict:
    """Route standalone package/service questions to RAG only (no booking)."""
    updated = build_service_information_intent(session, intent_json, user_message)
    service = _resolve_service_type_for_enquiry(session, updated, user_message)
    if service != "UNKNOWN":
        updated["service_type"] = service
        entities = dict(updated.get("entities") or {})
        entities["service_type"] = service
        updated["entities"] = entities

    package = extract_service_package(user_message)
    if package:
        updated["entities"] = merge_collected_entities(
            session, dict(updated.get("entities") or {}), service_package=package
        )

    updated["booking_supporting_service_info"] = False
    updated["booking_supporting_info_needed"] = False
    updated["standalone_service_info"] = True
    updated["database_action_needed"] = False
    updated["retrieval_needed"] = True
    updated["retrieval_source"] = ["service_information"]
    updated["next_action"] = "retrieve_service_info"
    updated["missing_information"] = []
    return updated


def observe_standalone_price_size_request(
    session, intent_json: dict, user_message: str
) -> ServiceInfoObservation:
    """Price enquiry needing pet size/height — standalone, not a booking flow."""
    from session_store import SERVICE_PRICE_PENDING_ACTION

    updated = build_standalone_service_information_intent(session, intent_json, user_message)
    service_type = str(updated.get("service_type") or "GROOMING").strip().upper()
    price_missing = price_info_missing_fields(session, service_type)
    package = extract_service_package(user_message) or entity_value(
        dict(updated.get("entities") or {}), "service_package"
    )

    entity_fields = dict(updated.get("entities") or {})
    if package:
        entity_fields = merge_collected_entities(session, entity_fields, service_package=package)

    intent_patch = {
        **updated,
        "sub_flow": SERVICE_PRICE_PENDING_ACTION,
        "database_action_needed": False,
        "retrieval_needed": False,
        "retrieval_source": [],
        "next_action": "ask_missing_information",
        "missing_information": price_missing,
        "standalone_service_info": True,
        "booking_supporting_service_info": False,
        "booking_supporting_info_needed": False,
        "entities": entity_fields,
    }

    return ServiceInfoObservation(
        service_info_requested=True,
        requested_service=service_type,
        requested_package=package or None,
        required_fact_types=["pet_type", "pet_size"] if price_missing else [],
        resume_booking_after_answer=False,
        info_type="PRICE_ENQUIRY",
        standalone=True,
        price_missing_fields=price_missing,
        entity_fields=entity_fields,
        intent_patch=intent_patch,
    )


def build_standalone_price_size_request_intent(
    session, intent_json: dict, user_message: str
) -> dict:
    """Legacy wrapper — prefer observe_standalone_price_size_request + conv_ctx."""
    obs = observe_standalone_price_size_request(session, intent_json, user_message)
    return obs.intent_patch


def observe_standalone_price_info_continuation(
    session, user_message: str, intent_json: dict
) -> ServiceInfoObservation | None:
    """Continue standalone price enquiry after pet type/size collection."""
    from session_store import SERVICE_PRICE_PENDING_ACTION

    pending = str(getattr(session, "pending_action", "") or "").strip()
    sub_flow = str(getattr(session, "sub_flow", "") or "").strip()
    if pending != SERVICE_PRICE_PENDING_ACTION and sub_flow != SERVICE_PRICE_PENDING_ACTION:
        return None

    extracted = extract_pet_price_info(user_message)
    if not extracted:
        return None

    entities = merge_collected_entities(session, dict(intent_json.get("entities") or {}), **extracted)
    if entity_value(entities, "pet_height") and not entity_value(entities, "pet_size"):
        inferred = infer_pet_size_from_height(entity_value(entities, "pet_height"))
        if inferred:
            entities["pet_size"] = inferred

    service_type = str(
        entities.get("service_type") or getattr(session, "last_service_type", "") or intent_json.get("service_type") or "GROOMING"
    ).strip().upper()
    price_missing = price_info_missing_fields(session, service_type)
    explicit = set(extracted.keys())

    if price_missing:
        intent_patch = {
            **copy.deepcopy(intent_json),
            "main_intent": "POLICY_INTENT",
            "scenario_intent": "SERVICE_INFORMATION",
            "service_type": service_type,
            "sub_flow": SERVICE_PRICE_PENDING_ACTION,
            "standalone_service_info": True,
            "entities": entities,
            "missing_information": price_missing,
            "database_action_needed": False,
            "database_action": "",
            "next_action": "ask_missing_information",
        }
        return ServiceInfoObservation(
            service_info_requested=True,
            requested_service=service_type,
            info_type="PRICE_ENQUIRY",
            standalone=True,
            price_missing_fields=price_missing,
            entity_fields=entities,
            intent_patch=intent_patch,
        )

    result = build_standalone_service_information_intent(session, intent_json, user_message)
    result["entities"] = entities
    if entity_value(entities, "pet_type"):
        result["pet_type"] = entity_value(entities, "pet_type").upper()
    if entity_value(entities, "pet_size"):
        result["pet_size"] = entity_value(entities, "pet_size").upper()
    if entity_value(entities, "pet_height"):
        result["pet_height"] = entity_value(entities, "pet_height")

    return ServiceInfoObservation(
        service_info_requested=True,
        requested_service=service_type,
        info_type="PRICE_ENQUIRY",
        standalone=True,
        clear_enquiry_flow=True,
        resume_booking_after_answer=False,
        entity_fields=entities,
        intent_patch=result,
    )


def handle_standalone_price_info_continuation(
    session, user_message: str, intent_json: dict
) -> dict | None:
    obs = observe_standalone_price_info_continuation(session, user_message, intent_json)
    return obs.intent_patch if obs else None


def apply_standalone_service_info_rules(
    session, intent_json: dict, user_message: str, conv_ctx=None
) -> dict:
    """
    Standalone package/price/service questions and stale-session breakout.

    Runs before booking continuation so polluted sessions do not hijack info queries.
    """
    del conv_ctx
    from session_continuation import clear_stale_booking_session_state

    updated = copy.deepcopy(intent_json)

    if is_price_enquiry_active(session) or (
        is_grooming_price_related_message(user_message) and not has_clear_booking_signal(user_message)
    ):
        return handle_price_enquiry_turn(session, updated, user_message)

    if is_standalone_non_booking_enquiry(user_message, updated) and has_stale_booking_session(session):
        if not is_mixed_booking_service_info(user_message, updated):
            clear_stale_booking_session_state(session)

    if not is_standalone_service_info_enquiry(user_message, updated):
        return updated

    clear_stale_booking_session_state(session)

    service_type = _resolve_service_type_for_enquiry(session, updated, user_message)
    if is_price_question(user_message) and price_info_missing_fields(session, service_type):
        obs = observe_standalone_price_size_request(session, updated, user_message)
        _apply_service_info_observation(session, obs)
        return obs.intent_patch

    intent_patch = build_standalone_service_information_intent(session, updated, user_message)
    obs = ServiceInfoObservation(
        service_info_requested=True,
        requested_service=service_type,
        standalone=True,
        info_type=classify_supporting_info_type(user_message),
        intent_patch=intent_patch,
    )
    _apply_service_info_observation(session, obs)
    return intent_patch


def build_standalone_price_size_reply(intent_json: dict) -> str:
    """Ask pet type/size for standalone grooming price enquiry."""
    entities = dict(intent_json.get("entities") or {})
    package = entity_value(entities, "service_package") or str(
        intent_json.get("service_package") or ""
    ).strip()
    label = package or "Grooming"
    return (
        f"{label} price depends on your pet's size or height. "
        "May I know your pet is a dog or cat, and its height or size?"
    )


def has_booking_signal(message: str, intent_json: dict) -> bool:
    """True when the message carries an explicit booking request."""
    return has_clear_booking_signal(message)


def is_mixed_booking_service_info(message: str, intent_json: dict) -> bool:
    """Booking request and package/price/service question in the same turn."""
    if not is_service_package_price_question(message):
        return False
    return has_booking_signal(message, intent_json)


def classify_supporting_info_type(message: str) -> str:
    if is_price_question(message) and not is_package_list_question(message):
        return "PRICE_ENQUIRY"
    return "PACKAGE_ENQUIRY"


def observe_mixed_booking_service_info(
    session, intent_json: dict, user_message: str
) -> ServiceInfoObservation:
    """Keep MAKE_BOOKING primary intent; attach supporting service-info RAG lookup."""
    from booking_flow import compute_deferred_booking_missing
    from session_continuation import _extract_service_type

    updated = copy.deepcopy(intent_json)
    entities = merge_collected_entities(session, dict(updated.get("entities") or {}))
    service = str(
        _extract_service_type(user_message)
        or entities.get("service_type")
        or getattr(session, "last_service_type", "")
        or updated.get("service_type")
        or ""
    ).strip().upper()
    entity_fields: dict = {}
    if service and service != "UNKNOWN":
        entities["service_type"] = service
        updated["service_type"] = service
        entity_fields["service_type"] = service

    info_type = classify_supporting_info_type(user_message)
    ask_package = info_type == "PACKAGE_ENQUIRY" and not is_package_list_question(user_message)

    if info_type == "PRICE_ENQUIRY" and price_info_missing_fields(session, service):
        price_obs = observe_booking_price_size_request(session, updated, user_message)
        price_obs.intent_patch.update(
            {
                "main_intent": "BOOKING_INTENT",
                "scenario_intent": "MAKE_BOOKING",
                "booking_supporting_info_needed": True,
                "supporting_info_type": info_type,
                "booking_supporting_service_info": True,
            }
        )
        return price_obs

    missing = compute_deferred_booking_missing(session, updated, user_message)
    if ask_package and not entity_value(entities, "service_package"):
        if "service_package" not in missing:
            missing = ["service_package", *missing]

    deferred = [field for field in missing if field not in {"pet_type", "pet_size_or_height"}]
    intent_patch = {
        **updated,
        "main_intent": "BOOKING_INTENT",
        "scenario_intent": "MAKE_BOOKING",
        "booking_supporting_info_needed": True,
        "booking_supporting_service_info": True,
        "supporting_info_type": info_type,
        "entities": entities,
        "confidence": max(float(updated.get("confidence") or 0.0), 0.95),
        "ask_package_after_info": ask_package,
        "retrieval_needed": True,
        "retrieval_source": ["service_information"],
        "database_action_needed": False,
        "database_action": "",
        "next_action": "retrieve_service_info",
        "sub_flow": BOOKING_SERVICE_INFO_SUB_FLOW,
        "missing_information": ordered_missing_fields(missing),
    }

    return ServiceInfoObservation(
        service_info_requested=True,
        requested_service=service,
        info_type=info_type,
        ask_package_after_info=ask_package,
        resume_booking_after_answer=True,
        required_fact_types=["service_information"],
        entity_fields=entity_fields,
        deferred_booking_missing=deferred,
        intent_patch=intent_patch,
    )


def observe_booking_price_size_request(
    session, intent_json: dict, user_message: str
) -> ServiceInfoObservation:
    updated = build_service_information_intent(session, intent_json, user_message)
    service_type = str(updated.get("service_type") or "UNKNOWN").strip().upper()
    price_missing = price_info_missing_fields(session, service_type)
    combined_missing = _merge_booking_and_price_missing(session, price_missing)
    deferred = _booking_missing_snapshot(session)

    intent_patch = {
        **updated,
        "sub_flow": BOOKING_PRICE_PENDING_SUB_FLOW,
        "database_action_needed": False,
        "retrieval_needed": False,
        "retrieval_source": [],
        "next_action": "ask_missing_information",
        "missing_information": combined_missing,
        "main_intent": "BOOKING_INTENT",
        "scenario_intent": "MAKE_BOOKING",
        "booking_supporting_service_info": True,
        "booking_service_info_handled": True,
    }

    return ServiceInfoObservation(
        service_info_requested=True,
        requested_service=service_type,
        info_type="PRICE_ENQUIRY",
        resume_booking_after_answer=True,
        price_missing_fields=combined_missing,
        deferred_booking_missing=deferred,
        intent_patch=intent_patch,
    )


def build_mixed_booking_supporting_intent(
    session, intent_json: dict, user_message: str
) -> dict:
    """Legacy wrapper — prefer observe_mixed_booking_service_info + conv_ctx."""
    return observe_mixed_booking_service_info(session, intent_json, user_message).intent_patch


def build_price_size_request_intent(session, intent_json: dict, user_message: str) -> dict:
    return observe_booking_price_size_request(session, intent_json, user_message).intent_patch


def apply_mixed_booking_service_info_rules(
    session, intent_json: dict, user_message: str, conv_ctx=None
) -> dict:
    """
    Detect booking + package/price in one message (or mid-booking service questions).
    Keeps MAKE_BOOKING; routes to RAG before continuing slot-filling.
    """
    del conv_ctx
    if intent_json.get("booking_service_info_handled"):
        return intent_json

    if intent_json.get("standalone_service_info"):
        return intent_json

    if is_standalone_service_info_enquiry(user_message, intent_json):
        return intent_json

    if str(getattr(session, "sub_flow", "") or "").strip() == BOOKING_PRICE_PENDING_SUB_FLOW:
        obs = observe_booking_price_info_continuation(session, user_message, intent_json)
        if obs is not None:
            _apply_service_info_observation(session, obs)
            result = obs.intent_patch
            result["booking_service_info_handled"] = True
            return result

    package_result = handle_booking_package_selection(session, user_message, intent_json)
    if package_result is not None:
        package_result["booking_service_info_handled"] = True
        return package_result

    mixed = is_mixed_booking_service_info(user_message, intent_json)
    if not mixed:
        return intent_json

    if _UNRELATED_POLICY_SIGNAL.search(user_message):
        return intent_json

    obs = observe_mixed_booking_service_info(session, intent_json, user_message)
    _apply_service_info_observation(session, obs)
    result = obs.intent_patch
    result["booking_service_info_handled"] = True
    return result


def build_mixed_booking_rag_reply(rag_text: str, session, intent_json: dict) -> str:
    """Combine optional greeting, RAG answer, and booking continuation prompt."""
    from booking_flow import mark_greeted

    sections: list[str] = []
    name = str(getattr(session, "customer_name", "") or "").strip()
    if name and not getattr(session, "greeted_this_session", False):
        mark_greeted(session)
        sections.append(f"Hi {name}, welcome back to Pawfect! 😊")

    body = str(rag_text or "").strip()
    if (
        body
        and str(intent_json.get("supporting_info_type") or "").strip()
        == "SERVICE_PACKAGE"
    ):
        package_rows = re.findall(
            r"(?:^|\n)\s*The\s+([^\n]+?)\s+package\s+is\s+priced\s+at\s+"
            r"(RM\s*\d+(?:\.\d{1,2})?)",
            body,
            re.I | re.M,
        )
        if package_rows:
            normalized_rows = [
                (label.strip(" .:-"), re.sub(r"\s+", "", price.upper()))
                for label, price in package_rows
            ]
            option_lines = [
                f"• {label}: {price}" for label, price in normalized_rows
            ]
            first_label, first_price = normalized_rows[0]
            other_labels = [label for label, _price in normalized_rows[1:]]
            guidance = (
                f"{first_label} at {first_price} is the lowest-cost starting point."
            )
            if other_labels:
                guidance += (
                    f" Choose {' or '.join(other_labels)} if you prefer one of those "
                    "bath product options."
                )
            saved_date = str(getattr(session, "preferred_date", "") or "").strip()
            saved_time = str(getattr(session, "preferred_time", "") or "").strip()
            pet_name = str(getattr(session, "pet_name", "") or "").strip()
            next_step = (
                f"Reply with {', '.join(label for label, _price in normalized_rows)}. "
                f"I'll then check the saved {saved_date or 'preferred date'}"
                f"{f' {saved_time}' if saved_time else ''} availability."
            )
            body = "\n".join(
                [
                    (
                        f"For {pet_name}, these verified options are available:"
                        if pet_name
                        else "These verified options are available:"
                    ),
                    *option_lines,
                    "",
                    guidance,
                    "",
                    next_step,
                ]
            )
    if body:
        sections.append(body)

    continuation = (
        ""
        if str(intent_json.get("supporting_info_type") or "").strip()
        == "SERVICE_PACKAGE"
        and body
        else build_booking_continuation_prompt(session, intent_json).strip()
    )
    if continuation:
        sections.append(continuation)
    return "\n\n".join(sections).strip()


def build_service_information_intent(session, intent_json: dict, user_message: str) -> dict:
    updated = copy.deepcopy(intent_json)
    entities = merge_collected_entities(session, dict(updated.get("entities") or {}))
    service_type = str(
        entities.get("service_type")
        or getattr(session, "last_service_type", "")
        or updated.get("service_type")
        or "UNKNOWN"
    ).strip().upper()

    updated["main_intent"] = "POLICY_INTENT"
    updated["scenario_intent"] = "SERVICE_INFORMATION"
    updated["service_type"] = service_type if service_type != "UNKNOWN" else updated.get("service_type", "UNKNOWN")
    updated["retrieval_needed"] = True
    updated["retrieval_source"] = ["service_information"]
    updated["database_action_needed"] = False
    updated["database_action"] = ""
    updated["next_action"] = "retrieve_service_info"
    updated["booking_supporting_service_info"] = True
    updated["entities"] = entities
    updated["confidence"] = max(float(updated.get("confidence") or 0.0), 0.95)

    if entity_value(entities, "pet_type"):
        updated["pet_type"] = entity_value(entities, "pet_type").upper()
    if entity_value(entities, "pet_size"):
        updated["pet_size"] = entity_value(entities, "pet_size").upper()
    if entity_value(entities, "pet_height"):
        updated["pet_height"] = entity_value(entities, "pet_height")

    return updated


def observe_booking_price_info_continuation(
    session, user_message: str, intent_json: dict
) -> ServiceInfoObservation | None:
    if str(getattr(session, "sub_flow", "") or "").strip() != BOOKING_PRICE_PENDING_SUB_FLOW:
        return None

    extracted = extract_pet_price_info(user_message)
    if not extracted:
        return None

    entities = merge_collected_entities(session, dict(intent_json.get("entities") or {}), **extracted)
    if entity_value(entities, "pet_height") and not entity_value(entities, "pet_size"):
        inferred = infer_pet_size_from_height(entity_value(entities, "pet_height"))
        if inferred:
            entities["pet_size"] = inferred

    explicit = set(extracted.keys())
    service = str(getattr(session, "last_service_type", "") or intent_json.get("service_type") or "GROOMING").strip().upper()
    price_missing = price_info_missing_fields(session, service)

    if price_missing:
        intent_patch = {
            **copy.deepcopy(intent_json),
            "main_intent": "BOOKING_INTENT",
            "scenario_intent": "MAKE_BOOKING",
            "sub_flow": BOOKING_PRICE_PENDING_SUB_FLOW,
            "booking_supporting_service_info": True,
            "booking_service_info_handled": True,
            "entities": entities,
            "missing_information": _merge_booking_and_price_missing(session, price_missing),
            "database_action_needed": False,
            "database_action": "",
            "next_action": "ask_missing_information",
            "service_type": service,
        }
        return ServiceInfoObservation(
            service_info_requested=True,
            requested_service=service,
            info_type="PRICE_ENQUIRY",
            resume_booking_after_answer=True,
            price_missing_fields=intent_patch["missing_information"],
            deferred_booking_missing=_booking_missing_snapshot(session),
            entity_fields=entities,
            intent_patch=intent_patch,
        )

    result = build_service_information_intent(session, intent_json, user_message)
    result["booking_service_info_handled"] = True
    return ServiceInfoObservation(
        service_info_requested=True,
        requested_service=service,
        info_type="PRICE_ENQUIRY",
        resume_booking_after_answer=True,
        clear_enquiry_flow=True,
        entity_fields=entities,
        intent_patch=result,
    )


def handle_booking_price_info_continuation(
    session, user_message: str, intent_json: dict
) -> dict | None:
    obs = observe_booking_price_info_continuation(session, user_message, intent_json)
    return obs.intent_patch if obs else None


def handle_booking_package_selection(
    session, user_message: str, intent_json: dict, conv_ctx=None
) -> dict | None:
    del conv_ctx
    if not is_active_booking_flow(session):
        return None
    if str(getattr(session, "sub_flow", "") or "").strip() == BOOKING_PRICE_PENDING_SUB_FLOW:
        return None

    from package_selection import apply_package_and_addon_extraction, extract_base_package_slug

    if not extract_base_package_slug(user_message) and not extract_service_package(user_message):
        return None
    if is_service_package_price_question(user_message) and is_package_list_question(user_message):
        return None

    entities = merge_collected_entities(session, dict(intent_json.get("entities") or {}))
    entities = apply_package_and_addon_extraction(session, entities, user_message, write_session=False)

    from booking_flow import apply_booking_collection_rules

    probe = copy.deepcopy(intent_json)
    probe["entities"] = entities
    probe["scenario_intent"] = "MAKE_BOOKING"
    probe["main_intent"] = "BOOKING_INTENT"
    if getattr(session, "last_service_type", ""):
        probe["service_type"] = session.last_service_type
    return apply_booking_collection_rules(session, probe, user_message)


def apply_booking_service_info_rules(
    session, user_message: str, intent_json: dict, conv_ctx=None
) -> dict:
    """Mid-flow price continuation inside handle_session_before_routing."""
    del conv_ctx
    if str(getattr(session, "sub_flow", "") or "").strip() == BOOKING_PRICE_PENDING_SUB_FLOW:
        obs = observe_booking_price_info_continuation(session, user_message, intent_json)
        if obs is not None:
            _apply_service_info_observation(session, obs)
            result = obs.intent_patch
            result["booking_service_info_handled"] = True
            return result
    return intent_json


def build_booking_continuation_prompt(session, intent_json: dict | None = None) -> str:
    """Ask for the next missing booking field after answering service info."""
    intent_json = intent_json or {}

    if intent_json.get("ask_package_after_info"):
        entities = _session_entities(session)
        if not entity_value(entities, "service_package"):
            return "Which package would you like for this booking — Basic Grooming or Full Grooming?"

    from booking_flow import compute_booking_missing_fields

    probe = {
        "main_intent": "BOOKING_INTENT",
        "scenario_intent": "MAKE_BOOKING",
        "service_type": str(getattr(session, "last_service_type", "") or "UNKNOWN"),
        "entities": merge_collected_entities(session, {}),
        "missing_information": [],
    }
    missing = [
        field
        for field in compute_booking_missing_fields(session, probe, skip_session_sync=True)
        if field not in {"pet_type", "pet_size_or_height"}
    ]
    if not missing:
        return ""

    from booking_flow import build_missing_field_reply

    return build_missing_field_reply(missing, session, probe)


def append_service_info_follow_up(
    reply: str,
    session,
    intent_json: dict,
    user_message: str = "",
) -> str:
    """Append booking or price follow-up questions to a service-info reply."""
    del user_message
    if isinstance(reply, dict):
        sections = [str(part).strip() for part in reply.get("sections", []) if str(part).strip()]
        next_question = str(reply.get("next_question") or "").strip()
        if next_question:
            sections.append(next_question)
        return "\n\n".join(sections).strip()

    text = str(reply or "").strip()
    if intent_json.get("price_enquiry_incomplete"):
        missing = list(intent_json.get("missing_information") or [])
        follow_up = build_price_missing_field_reply(session, missing).strip()
        if follow_up and follow_up.lower() not in text.lower():
            text = f"{text}\n\n{follow_up}".strip() if text else follow_up
        return text

    if intent_json.get("booking_supporting_info_needed") and not intent_json.get("booking_interruption"):
        continuation = build_booking_continuation_prompt(session, intent_json).strip()
        if continuation and continuation.lower() not in text.lower():
            text = f"{text}\n\n{continuation}".strip() if text else continuation
    return text


# --- grooming price enquiry ---

PRICE_FIELD_ORDER = (
    "service_type",
    "pet_size_or_height",
    "service_package",
    "add_on_type",
)

_SHAVING_SIGNAL = re.compile(r"\b(shav(e|ing|er)|trim\s+hair|haircut)\b", re.I)
_TRIMMING_PRICE_SIGNAL = re.compile(
    r"\b("
    r"trimming|dog\s+trimming|trimming\s+package|all\s+shave|"
    r"keep\s+head\s+and\s+tail|keep\s+head,\s*tail\s+and\s+legs|"
    r"keep\s+head\s*&\s*tail|full\s+scissor"
    r")\b",
    re.I,
)
_EXPLICIT_SHAVING_ADDON_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bshaving\s+belly\b", re.I), "Shaving Belly"),
    (re.compile(r"\bshaving\s+paw(s)?\b", re.I), "Shaving Paw"),
    (re.compile(r"\bshaving\s+sanitary\b", re.I), "Shaving Sanitary"),
    (re.compile(r"\btrimming\s+head\b", re.I), "Trimming Head"),
    (re.compile(r"\btrimming\s+eyes\b", re.I), "Trimming Eyes"),
    (re.compile(r"\btrimming\s+sanitary\b", re.I), "Trimming Sanitary"),
)
_ADDON_SIGNAL = re.compile(r"\b(add.?on|addon|nail|teeth|ear\s+clean)\b", re.I)
_EXACT_PRICE_SIGNAL = re.compile(r"\b(exact\s+price|how\s+much|price|pricing|cost|fee|total)\b", re.I)
_GROOMING_SIGNAL = re.compile(r"\b(groom|grooming|basic\s+grooming|full\s+grooming)\b", re.I)


def is_price_enquiry_active(session) -> bool:
    pending = str(getattr(session, "pending_action", "") or "").strip()
    sub_flow = str(getattr(session, "sub_flow", "") or "").strip()
    return pending == SERVICE_PRICE_PENDING_ACTION or sub_flow == SERVICE_PRICE_PENDING_ACTION


def is_grooming_price_related_message(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    from retrieval_request import is_dog_trimming_package_intent, is_explicit_addon_intent

    if has_clear_booking_signal(text):
        return False
    if is_dog_trimming_package_intent(text) and is_price_question(text):
        return True
    if is_explicit_addon_intent(text) and is_price_question(text):
        return True
    if is_dog_trimming_package_intent(text) and _EXACT_PRICE_SIGNAL.search(text):
        return True
    if is_package_list_question(text) and not is_price_question(text) and not _SHAVING_SIGNAL.search(text):
        return False
    if is_service_package_price_question(text) and (is_price_question(text) or _SHAVING_SIGNAL.search(text)):
        return True
    if _GROOMING_SIGNAL.search(text) and (_EXACT_PRICE_SIGNAL.search(text) or _SHAVING_SIGNAL.search(text)):
        return True
    if _SHAVING_SIGNAL.search(text) and _GROOMING_SIGNAL.search(text):
        return True
    if is_price_question(text) and _GROOMING_SIGNAL.search(text):
        return True
    if _SHAVING_SIGNAL.search(text) and re.search(r"\bcan\b|\?", text, re.I):
        return True
    return False


def get_price_enquiry_context(session) -> dict:
    ctx = merge_collected_entities(session, {})
    for key, attr in (
        ("service_type", "last_service_type"),
        ("selected_package", "selected_package"),
        ("service_package", "service_package"),
        ("add_on_service", "add_on_service"),
        ("add_on_type", "add_on_type"),
        ("pet_name", "pet_name"),
        ("pet_type", "pet_type"),
        ("pet_height", "pet_height"),
        ("pet_size", "pet_size"),
        ("price_context", "price_context"),
        ("requested_package", "requested_package"),
        ("requested_price_item_type", "requested_price_item_type"),
        ("requested_addon", "requested_addon"),
    ):
        val = str(getattr(session, attr, "") or "").strip()
        if val and not entity_value(ctx, key):
            ctx[key] = val
    from package_selection import get_session_selected_addons

    addons = get_session_selected_addons(session)
    if addons and not ctx.get("selected_addons"):
        ctx["selected_addons"] = addons
    if entity_value(ctx, "pet_height") and not entity_value(ctx, "pet_size_or_height"):
        ctx["pet_size_or_height"] = entity_value(ctx, "pet_height")
    if entity_value(ctx, "pet_size") and not entity_value(ctx, "pet_size_or_height"):
        ctx["pet_size_or_height"] = entity_value(ctx, "pet_size")
    return ctx


def sync_price_enquiry_session(session, ctx: dict, *, write_session: bool = True) -> None:
    if not write_session:
        return
    from package_selection import sync_package_fields_to_session
    from pet_extraction import is_valid_stored_pet_name, normalize_pet_size

    merged = merge_collected_entities(session, ctx)
    service = entity_value(merged, "service_type")
    projection = {
        "collected_entities": merged,
        "pending_action": SERVICE_PRICE_PENDING_ACTION,
        "sub_flow": SERVICE_PRICE_PENDING_ACTION,
        "booking_creation_flow": False,
        "booking_missing_snapshot": [],
    }
    if service:
        projection["last_service_type"] = service.upper()
    if hasattr(session, "write_projection_fields"):
        session.write_projection_fields(**projection)
    else:
        session.collected_entities = merged
        session.pending_action = SERVICE_PRICE_PENDING_ACTION
        session.sub_flow = SERVICE_PRICE_PENDING_ACTION
        session.booking_creation_flow = False
        session.booking_missing_snapshot = []
        if service:
            session.last_service_type = service.upper()
    sync_package_fields_to_session(session, merged, write_session=True)
    for key in ("add_on_type", "pet_type", "pet_height", "price_context", "requested_package", "requested_price_item_type", "requested_addon"):
        val = entity_value(merged, key)
        if val:
            setattr(session, key, val.upper() if key == "pet_type" else val)
    pet_size = entity_value(merged, "pet_size")
    if pet_size:
        normalized = normalize_pet_size(pet_size) or pet_size
        session.pet_size = normalized
        merged["pet_size"] = normalized
    pet_name = entity_value(merged, "pet_name")
    if pet_name and is_valid_stored_pet_name(pet_name):
        session.pet_name = pet_name


def extract_pet_name_for_price(message: str, session) -> str:
    from booking_flow import get_customer_pets_for_session
    from pet_extraction import extract_pet_name_from_message

    pets = get_customer_pets_for_session(session) if session is not None else []
    return extract_pet_name_from_message(message, pets)


def extract_add_on_service(message: str) -> str:
    from retrieval_request import extract_price_item_intent, is_dog_trimming_package_intent

    text = str(message or "").lower()
    price_intent = extract_price_item_intent(message)
    if price_intent.get("requested_price_item_type") == "addon":
        addon = price_intent.get("requested_addon", "")
        return "shaving" if addon.startswith("shaving") else "add-on"
    if is_dog_trimming_package_intent(message):
        return ""
    if re.search(r"\bshaving\b", text) and not _TRIMMING_PRICE_SIGNAL.search(text):
        return "shaving"
    if re.search(r"\bnail\b", text):
        return "nail"
    if _ADDON_SIGNAL.search(text):
        return "add-on"
    return ""


def extract_add_on_type(message: str) -> str:
    from retrieval_request import ADDON_SLUG_TO_LABEL, extract_price_item_intent

    price_intent = extract_price_item_intent(message)
    addon_slug = price_intent.get("requested_addon", "")
    if addon_slug:
        return ADDON_SLUG_TO_LABEL.get(addon_slug, "")
    for pattern, label in _EXPLICIT_SHAVING_ADDON_PATTERNS:
        if pattern.search(str(message or "")):
            return label
    return ""


def extract_price_enquiry_from_message(message: str, session) -> dict[str, str]:
    from package_selection import apply_package_and_addon_extraction, extract_addon_slug
    from retrieval_request import extract_price_item_intent
    from session_continuation import _extract_service_type

    extracted: dict[str, str] = {}
    service = _extract_service_type(message)
    if service:
        extracted["service_type"] = service.upper()
    elif _GROOMING_SIGNAL.search(message or "") or _TRIMMING_PRICE_SIGNAL.search(message or ""):
        extracted["service_type"] = "GROOMING"
    extracted.update(extract_pet_price_info(message))
    pet_name = extract_pet_name_for_price(message, session)
    if pet_name:
        extracted["pet_name"] = pet_name
        if session is not None:
            from pet_profile import apply_pet_profile_to_session, get_pet_by_customer_and_name

            matched_pet = get_pet_by_customer_and_name(session, pet_name)
            if matched_pet:
                profile = apply_pet_profile_to_session(session, matched_pet)
                extracted.update({k: v for k, v in profile.items() if v})
    extracted = apply_package_and_addon_extraction(session, extracted, message)
    price_intent = extract_price_item_intent(message)
    if price_intent.get("requested_package") == "dog_trimming":
        extracted.update(
            {
                "requested_package": "dog_trimming",
                "requested_price_item_type": "base_package",
                "selected_package": "dog_trimming",
                "service_package": "Dog Trimming Package",
            }
        )
        extracted.pop("add_on_service", None)
        extracted.pop("add_on_type", None)
    elif price_intent.get("requested_price_item_type") == "addon":
        extracted["requested_price_item_type"] = "addon"
        extracted["requested_addon"] = price_intent.get("requested_addon", "")
        if session is not None:
            session.requested_price_item_type = "addon"
            session.requested_addon = price_intent.get("requested_addon", "")
    add_on = extract_add_on_service(message)
    if add_on and not extract_addon_slug(message):
        extracted["add_on_service"] = add_on
        if add_on == "shaving" and not entity_value(extracted, "service_type"):
            extracted["service_type"] = "GROOMING"
    add_on_type = extract_add_on_type(message)
    if add_on_type:
        extracted["add_on_type"] = add_on_type
    if entity_value(extracted, "pet_height") and not entity_value(extracted, "pet_size"):
        inferred = infer_pet_size_from_height(entity_value(extracted, "pet_height"))
        if inferred:
            extracted["pet_size"] = inferred
    if _EXACT_PRICE_SIGNAL.search(message or ""):
        extracted["price_context"] = "exact_price_requested"
    return {k: v for k, v in extracted.items() if str(v).strip()}


def _has_size_indicator(ctx: dict) -> bool:
    return any(entity_value(ctx, key) for key in ("pet_height", "pet_size", "pet_size_or_height"))


def compute_grooming_price_missing_fields(session, *, exact_price: bool = False) -> list[str]:
    ctx = get_price_enquiry_context(session)
    missing: list[str] = []
    from pet_profile import fetch_customer_pets

    pets = fetch_customer_pets(session)
    pet_name = entity_value(ctx, "pet_name") or str(getattr(session, "pet_name", "") or "").strip()
    if len(pets) > 1 and not pet_name:
        return ["pet_name"]
    service = str(ctx.get("service_type") or getattr(session, "last_service_type", "") or "").strip().upper()
    if not service or service == "UNKNOWN":
        if entity_value(ctx, "add_on_service").lower() == "shaving":
            ctx["service_type"] = "GROOMING"
        else:
            missing.append("service_type")
    elif service != "GROOMING":
        return missing
    if not _has_size_indicator(ctx):
        missing.append("pet_size_or_height")
    wants_exact = exact_price or entity_value(ctx, "price_context") == "exact_price_requested"
    add_on = entity_value(ctx, "add_on_service").lower()
    selected_package = entity_value(ctx, "selected_package") or entity_value(ctx, "service_package")
    requested_package = entity_value(ctx, "requested_package") or selected_package
    if wants_exact or add_on:
        if not selected_package and requested_package != "dog_trimming":
            missing.append("service_package")
    if add_on == "shaving" and not entity_value(ctx, "add_on_type") and wants_exact:
        if entity_value(ctx, "requested_price_item_type") == "addon":
            missing.append("add_on_type")
    order = {name: idx for idx, name in enumerate(PRICE_FIELD_ORDER)}
    return sorted(dict.fromkeys(missing), key=lambda n: order.get(n, len(PRICE_FIELD_ORDER)))


def build_price_missing_field_reply(session, missing: list[str]) -> str:
    from pet_profile import build_pet_choice_reply, fetch_customer_pets

    ctx = get_price_enquiry_context(session)
    pet_name = entity_value(ctx, "pet_name")
    pet_label = f"{pet_name}'s" if pet_name else "your pet's"
    if not missing:
        return ""
    first = missing[0]
    if first == "pet_size_or_height":
        if entity_value(ctx, "pet_height") or entity_value(ctx, "pet_size"):
            return ""
        if entity_value(ctx, "pet_type"):
            return f"Grooming price depends on {pet_label} size or height. May I know your pet's height or size?"
        return f"Grooming price depends on {pet_label} size or height. May I know your pet is a dog or cat, and its height or size?"
    if first == "pet_name":
        pets = fetch_customer_pets(session)
        return build_pet_choice_reply(pets) if len(pets) > 1 else "Which pet would this be for?"
    if first == "service_package":
        requested = entity_value(ctx, "requested_package") or entity_value(ctx, "selected_package")
        return "Which trimming option would you like?" if requested == "dog_trimming" else "Which grooming package would you like — Basic Grooming or Full Grooming?"
    if first == "add_on_type":
        return "Which shaving add-on do you mean — belly, paw, or sanitary?"
    if first == "service_type":
        return "Which service would you like pricing for — grooming, daycare, or boarding?"
    return "May I have a few more details so I can confirm the price?"


def build_price_enquiry_intent(session, intent_json: dict, user_message: str, *, route_to_rag: bool) -> dict:
    ctx = get_price_enquiry_context(session)
    updated = build_service_information_intent(session, intent_json, user_message)
    updated.update(
        {
            "standalone_service_info": True,
            "booking_supporting_service_info": False,
            "booking_supporting_info_needed": False,
            "service_type": str(ctx.get("service_type") or "GROOMING").upper(),
        }
    )
    updated["entities"] = merge_collected_entities(session, dict(updated.get("entities") or {}), **ctx)
    exact = entity_value(ctx, "price_context") == "exact_price_requested" or bool(
        _EXACT_PRICE_SIGNAL.search(user_message or "")
    )
    missing = compute_grooming_price_missing_fields(session, exact_price=exact)
    updated["missing_information"] = missing
    updated["price_enquiry_incomplete"] = bool(missing)
    updated["price_enquiry_complete"] = not missing
    if route_to_rag:
        updated.update(
            {
                "retrieval_needed": True,
                "retrieval_source": ["service_information"],
                "database_action_needed": False,
                "database_action": "",
                "next_action": "retrieve_service_info",
            }
        )
    else:
        updated.update(
            {
                "retrieval_needed": False,
                "retrieval_source": [],
                "database_action_needed": False,
                "database_action": "",
                "next_action": "ask_missing_information",
            }
        )
    if hasattr(session, "write_projection_fields"):
        session.write_projection_fields(missing_fields=missing)
    else:
        session.missing_fields = missing
    sync_price_enquiry_session(session, ctx)
    return updated


def handle_price_enquiry_turn(session, intent_json: dict, user_message: str) -> dict:
    from session_continuation import reset_flow_specific_state_on_intent_change
    from pet_profile import enrich_session_pet_profile

    reset_flow_specific_state_on_intent_change(
        session,
        previous_scenario=str(getattr(session, "last_scenario_intent", "") or "").strip(),
        new_scenario="SERVICE_INFORMATION",
    )
    enrich_session_pet_profile(session, user_message)
    extracted = extract_price_enquiry_from_message(user_message, session)
    ctx = merge_collected_entities(session, get_price_enquiry_context(session), **extracted)
    sync_price_enquiry_session(session, ctx)
    exact = entity_value(ctx, "price_context") == "exact_price_requested" or bool(
        _EXACT_PRICE_SIGNAL.search(user_message or "")
    )
    missing = compute_grooming_price_missing_fields(session, exact_price=exact)
    route_to_rag = missing != ["pet_size_or_height"]
    return build_price_enquiry_intent(session, intent_json, user_message, route_to_rag=route_to_rag)


def build_enriched_grooming_rag_query(user_message: str, session, intent_json: dict | None = None) -> str:
    from package_selection import addon_slug_to_label, get_session_selected_addons, package_slug_to_label
    from pet_extraction import is_valid_stored_pet_name, normalize_pet_size
    from retrieval_request import ADDON_SLUG_TO_LABEL, resolve_price_context

    ctx = get_price_enquiry_context(session)
    if intent_json:
        entities = {
            k: v
            for k, v in dict(intent_json.get("entities") or {}).items()
            if v and k not in {"pet_name", "phone_number", "customer_identifier", "preferred_date", "preferred_time"}
        }
        if entity_value(entities, "pet_name") and not is_valid_stored_pet_name(entity_value(entities, "pet_name")):
            entities.pop("pet_name", None)
        ctx = merge_collected_entities(session, ctx, **entities)
    price_context = resolve_price_context(user_message, ctx)
    requested_package = price_context.get("requested_package", "")
    item_type = price_context.get("requested_price_item_type", "")
    requested_addon = price_context.get("requested_addon", "")
    pet_size = normalize_pet_size(entity_value(ctx, "pet_size")) or entity_value(ctx, "pet_size").lower()
    pet_height = entity_value(ctx, "pet_height")
    pet_type = entity_value(ctx, "pet_type").lower()
    if requested_package == "dog_trimming" or (
        item_type == "base_package" and entity_value(ctx, "selected_package") == "dog_trimming"
    ):
        parts = ["Dog Trimming Packages prices by dog size"]
        if pet_size:
            parts.append(f"{pet_size} size dog")
        if pet_height:
            parts.append(pet_height)
        if pet_type == "dog":
            parts.append("dog")
        parts.append(str(user_message or "").strip())
        return " ".join(p for p in parts if p)
    if item_type == "addon" and requested_addon:
        addon_label = ADDON_SLUG_TO_LABEL.get(requested_addon, requested_addon.replace("_", " "))
        parts = [f"grooming {addon_label.lower()} add-on price"]
        if pet_size:
            parts.append(f"{pet_size} dog")
        parts.append(str(user_message or "").strip())
        return " ".join(p for p in parts if p)
    parts = ["grooming basic grooming full grooming price service information"]
    if pet_type:
        parts.append(f"{pet_type} grooming price")
    if pet_height:
        parts.append(f"{pet_height} dog grooming price")
    elif pet_size:
        parts.append(f"{pet_size} dog grooming price")
    package_slug = entity_value(ctx, "selected_package")
    package_label = package_slug_to_label(package_slug) or entity_value(ctx, "service_package")
    if package_label:
        parts.append(f"grooming {package_label.lower()} price")
    for addon_slug in get_session_selected_addons(session):
        addon_label = addon_slug_to_label(addon_slug) or addon_slug.replace("_", " ")
        parts.append(f"grooming {addon_label.lower()} add-on price")
    add_on = entity_value(ctx, "add_on_service").lower()
    if add_on == "shaving" and item_type == "addon":
        add_on_type = entity_value(ctx, "add_on_type")
        parts.append((add_on_type or requested_addon.replace("_", " ")).lower() + " price")
    parts.append(str(user_message or "").strip())
    return " ".join(p for p in parts if p)


def build_service_info_retrieval_query(user_message: str, intent_json: dict, session=None) -> str:
    scenario = str(intent_json.get("scenario_intent") or "").strip()
    if scenario == "GET_BOOKING_SERVICE_OPTIONS":
        entities = dict(intent_json.get("entities") or {})
        service = str(
            intent_json.get("service_type") or entities.get("service_type") or ""
        ).strip().upper()
        pet_type = str(
            entities.get("pet_type")
            or getattr(session, "pet_type", "")
            or ""
        ).strip().upper()
        if service == "DAYCARE":
            return (
                "service information daycare Hourly Care Daycare Above 3 Hours "
                "Splash Pool Session CCA Enrichment Class prices"
            )
        if service == "BOARDING":
            species = "Cat Hotel Price room types capacity price" if pet_type == "CAT" else "Dog Hotel Price room types capacity price"
            return f"service information boarding {species}"
        species = (
            "cat"
            if pet_type == "CAT"
            else ("dog" if pet_type == "DOG" else "cat and dog")
        )
        return (
            f"service information {species} grooming bathing packages trimming "
            "Standard Bath Premium Bath Luxury Bath prices"
        )
    if scenario != "SERVICE_INFORMATION":
        return user_message
    service = str(intent_json.get("service_type") or "").strip().lower()
    if service in {"grooming", "unknown", ""} or re.search(r"\b(groom|package|price|shav|trim)\b", user_message, re.I):
        if session is not None:
            return build_enriched_grooming_rag_query(user_message, session, intent_json)
    entities = dict(intent_json.get("entities") or {})
    package = extract_service_package(user_message) or entity_value(entities, "service_package")
    parts = ["grooming package basic grooming full grooming price service information"]
    if package:
        parts.append(f"grooming {package.lower()} price pet size height")
    parts.append(user_message.strip())
    return " ".join(part for part in parts if part)


def _policy_size_code(pet_size: str) -> str:
    """Translate session size labels back to the codes used by policy tables."""
    token = str(pet_size or "").strip().lower().replace("-", "").replace(" ", "")
    return {
        "xs": "XS",
        "s": "S",
        "small": "S",
        "m": "M",
        "medium": "M",
        "l": "L",
        "large": "L",
        "xl": "XL",
        "extralarge": "XL",
        "xxl": "XXL",
    }.get(token, "")


def _height_matches_policy_row(row: str, height_cm: float) -> bool:
    below = re.search(r"below\s*(\d+(?:\.\d+)?)\s*cm", row, re.I)
    if below:
        return height_cm < float(below.group(1))
    above = re.search(r"above\s*(\d+(?:\.\d+)?)\s*cm", row, re.I)
    if above:
        return height_cm > float(above.group(1))
    between = re.search(
        r"(\d+(?:\.\d+)?)\s*cm\s*[-–]\s*(\d+(?:\.\d+)?)\s*cm",
        row,
        re.I,
    )
    if between:
        return float(between.group(1)) <= height_cm <= float(between.group(2))
    return False


def extract_matching_grooming_price_rows(
    chunks: list[dict],
    *,
    pet_size: str = "",
    pet_height: str = "",
) -> list[tuple[str, str]]:
    """Return exact price rows whose policy band matches this pet."""
    height_match = re.search(r"(\d+(?:\.\d+)?)", str(pet_height or ""))
    height_cm = float(height_match.group(1)) if height_match else None
    size_code = _policy_size_code(pet_size)
    matches: list[tuple[str, str]] = []

    for chunk in chunks:
        metadata = dict(chunk.get("metadata") or {})
        title = str(metadata.get("section_title") or metadata.get("sub_header") or "Grooming")
        for raw_row in re.split(r"\n\s*\n", str(chunk.get("text") or "")):
            row = " ".join(raw_row.split()).strip()
            if not row.lower().startswith("for ") or "RM" not in row:
                continue
            matched = (
                _height_matches_policy_row(row, height_cm)
                if height_cm is not None
                else bool(
                    size_code
                    and re.match(
                        rf"for\s+{re.escape(size_code)}(?:\s+size|\s+\d)",
                        row,
                        re.I,
                    )
                )
            )
            if matched:
                matches.append((title, row))
    return matches


def build_grooming_price_response_plan(
    rag_text: str,
    session,
    intent_json: dict,
    chunks: list[dict] | None = None,
) -> dict:
    from retrieval_request import (
        ADDON_SLUG_TO_LABEL,
        extract_dog_trimming_prices,
        rag_has_dog_trimming_package_price,
        resolve_price_context,
    )

    ctx = get_price_enquiry_context(session)
    pet_name = entity_value(ctx, "pet_name")
    pet_height = entity_value(ctx, "pet_height")
    pet_size = entity_value(ctx, "pet_size")
    pet_type = entity_value(ctx, "pet_type").upper()
    missing = list(intent_json.get("missing_information") or [])
    price_context = resolve_price_context("", ctx)
    item_type = price_context.get("requested_price_item_type", "")
    requested_package = price_context.get("requested_package", "")
    requested_addon = price_context.get("requested_addon", "")
    sections: list[str] = []
    next_question: str | None = None
    body = str(rag_text or "").strip()
    chunk_list = chunks or []
    has_base_package = _rag_has_base_package_price(body, chunk_list)
    if requested_package == "dog_trimming" or (
        item_type == "base_package" and entity_value(ctx, "selected_package") == "dog_trimming"
    ):
        trimming_lines = extract_dog_trimming_prices(body, chunk_list, pet_size=pet_size, pet_height=pet_height)
        if trimming_lines:
            bullets = [line.replace("- ", "• ", 1) if line.startswith("- ") else f"• {line}" for line in trimming_lines]
            type_word = "dog" if pet_type == "DOG" else "cat" if pet_type == "CAT" else "pet"
            intro = (
                f"For your {pet_size} {type_word}, the trimming prices are:"
                if pet_size
                else (f"For {pet_name}, the trimming prices are:" if pet_name else "The trimming prices are:")
            )
            sections = [intro, "\n".join(bullets)]
            if len(bullets) > 1:
                next_question = "Which trimming option would you prefer?"
        elif rag_has_dog_trimming_package_price(body, chunk_list):
            sections.append(body)
        else:
            name_bit = f"{pet_name}'s" if pet_name else "your pet's"
            sections.append(
                f"I couldn't confirm the Dog Trimming Package prices for {name_bit} size yet. "
                "I'll check with the team."
            )
    elif item_type == "addon":
        addon_lines = _extract_shaving_addon_prices(body, chunk_list, requested_addon=requested_addon)
        if addon_lines:
            addon_label = ADDON_SLUG_TO_LABEL.get(requested_addon, "add-on")
            sections.extend([f"The {addon_label} price I can confirm is:", *addon_lines])
        elif body and not _looks_like_payment_consent_only(body):
            sections.append(body)
        else:
            sections.append("I don't have that add-on price here yet, but I can help check with the team.")
    else:
        matched_rows = extract_matching_grooming_price_rows(
            chunk_list,
            pet_size=pet_size,
            pet_height=pet_height,
        )
        if matched_rows:
            type_word = "dog" if pet_type == "DOG" else "cat" if pet_type == "CAT" else "pet"
            measured = f" at {pet_height}" if pet_height else ""
            sections.append(
                f"Based on your {type_word}'s size{measured}, these are the matching policy prices:"
            )
            for title, row in matched_rows:
                sections.append(f"{title}:\n{row}")
            next_question = (
                "Which option would you like? Once you choose it, I can continue with the booking."
            )
            return {"sections": sections, "next_question": next_question}
        if entity_value(ctx, "add_on_service") == "shaving":
            sections.append("Shaving add-ons are available for grooming.")
        addon_lines = _extract_shaving_addon_prices(body, chunk_list)
        if addon_lines and entity_value(ctx, "add_on_service") == "shaving":
            sections.extend(["The shaving add-on prices I can confirm are:", *addon_lines])
        elif body and not _looks_like_payment_consent_only(body) and not re.search(r"shaving belly", body, re.I):
            sections.append(body)
        if entity_value(ctx, "price_context") == "exact_price_requested" or "service_package" in missing:
            if not has_base_package and entity_value(ctx, "add_on_service"):
                name_bit = f"{pet_name}'s" if pet_name else "your pet's"
                sections.append(
                    f"I can confirm the shaving add-on price, but I don't have the full base grooming "
                    f"package price for {name_bit} size here."
                )
    return {"sections": sections, "next_question": next_question}


def format_grooming_price_rag_reply(
    rag_text: str,
    session,
    intent_json: dict,
    chunks: list[dict] | None = None,
) -> str:
    plan = build_grooming_price_response_plan(rag_text, session, intent_json, chunks)
    sections = [str(part).strip() for part in plan.get("sections", []) if str(part).strip()]
    next_question = str(plan.get("next_question") or "").strip()
    if next_question:
        sections.append(next_question)
    return "\n\n".join(sections).strip()


def _extract_shaving_addon_prices(
    text: str,
    chunks: list[dict],
    *,
    requested_addon: str = "",
) -> list[str]:
    combined = text + "".join("\n" + str(chunk.get("text") or "") for chunk in chunks)
    slug_to_label = {"shaving_belly": "Shaving Belly", "shaving_paw": "Shaving Paw", "shaving_sanitary": "Shaving Sanitary"}
    patterns = (
        (
            rf"{slug_to_label[requested_addon].replace(' ', r'\s+')}[^.\n]{{0,40}}?(?:priced at\s+)?(RM\s*\d+(?:\.\d+)?|\d+\s*RM)",
            slug_to_label[requested_addon],
        ),
    ) if requested_addon in slug_to_label else (
        (r"shaving\s+belly[^.\n]{0,40}?(?:priced at\s+)?(RM\s*\d+(?:\.\d+)?|\d+\s*RM)", "Shaving Belly"),
        (r"shaving\s+paw[^.\n]{0,40}?(?:priced at\s+)?(RM\s*\d+(?:\.\d+)?|\d+\s*RM)", "Shaving Paw"),
        (r"shaving\s+sanitary[^.\n]{0,40}?(?:priced at\s+)?(RM\s*\d+(?:\.\d+)?|\d+\s*RM)", "Shaving Sanitary"),
    )
    lines: list[str] = []
    seen: set[str] = set()
    for pattern, label in patterns:
        match = re.search(pattern, combined, re.I)
        if not match:
            continue
        price = match.group(2) if match.lastindex and match.lastindex >= 2 else match.group(1)
        price = re.sub(r"\s+", "", str(price))
        if not price.upper().startswith("RM"):
            price = f"RM{price.replace('RM', '')}"
        if label not in seen:
            seen.add(label)
            lines.append(f"- {label}: {price}")
    return lines


def _rag_has_base_package_price(text: str, chunks: list[dict]) -> bool:
    from retrieval_request import rag_has_dog_trimming_package_price

    combined = (text + "\n").lower()
    for chunk in chunks:
        combined += str(chunk.get("text") or "").lower() + "\n"
    if rag_has_dog_trimming_package_price(text, chunks):
        return True
    return bool(
        re.search(r"basic grooming[^.\n]{0,80}(RM\s*\d+|\d+\s*rm)", combined, re.I)
        or re.search(r"full grooming[^.\n]{0,80}(RM\s*\d+|\d+\s*rm)", combined, re.I)
        or re.search(r"grooming price table", combined, re.I)
    )


def _looks_like_payment_consent_only(text: str) -> bool:
    lowered = str(text or "").lower()
    if "payment term" in lowered or "consent to service" in lowered:
        return "basic grooming" not in lowered and "full grooming" not in lowered and "shaving" not in lowered
    return False
