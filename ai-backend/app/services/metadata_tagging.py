"""Rule-based metadata tagging: dataset_type, pet_type, service_type, service_info."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.services.metadata_schema import (
    ALLOWED_PET_TYPES,
    ALLOWED_SERVICE_TYPES,
    CHUNK_DATASET_FIELDS,
    LEGACY_METADATA_KEYS,
    PET_TYPE_ALIASES,
    SERVICE_TYPE_ALIASES,
)

_RE_WHITESPACE = re.compile(r"\s+")
_DOG_TERMS = (
    "dog grooming",
    "dog service",
    "dog size",
    "dogs",
    "dog",
    "puppy",
    "puppies",
)

_CAT_TERMS = (
    "cat grooming",
    "cat service",
    "cats",
    "cat",
    "kitten",
    "kittens",
)

_GROOMING_TERMS = (
    "grooming",
    "bath",
    "haircut",
    "hair cut",
    "nail trim",
    "nail trimming",
    "fur trimming",
    "grooming package",
    "grooming price",
    "dog grooming",
    "cat grooming",
    "spa",
    "trimming",
)

_BOARDING_TERMS = (
    "boarding",
    "overnight stay",
    "overnight care",
    "overnight",
    "check-in",
    "check in",
    "checkin",
    "check-out",
    "check out",
    "checkout",
    "boarding price",
    "boarding rules",
    "boarding rule",
)

_DAYCARE_TERMS = (
    "daycare",
    "day care",
    "day-care",
    "playtime",
    "play time",
    "daily care",
    "daycare price",
    "daycare rules",
    "daycare rule",
)

_GENERAL_TERMS = (
    "cancellation",
    "cancel",
    "refund",
    "loyalty",
    "loyalty points",
    "rewards",
    "redemption",
    "payment policy",
    "business hours",
    "operating hours",
    "general terms",
    "terms and conditions",
    "all services",
    "all service",
)

# Loyalty / points — multi-word frames preferred over bare nouns.
_LOYALTY_FRAMES = (
    "earn points",
    "redeem points",
    "points balance",
    "points expiry",
    "points be used",
    "points for",
    "points from",
    "use points",
    "using points",
    "loyalty points",
    "loyalty program",
    "membership rewards",
    "membership reward",
    "membership package",
    "point package",
    "points package",
    "how many points",
    "points do i have",
    "eligible services",
    "non-refundable",
    "non refundable",
    "no cash refund",
    "no cash refunds",
    "topped-up",
    "topped up",
    "top up points",
)

_LOYALTY_ANCHORS = (
    "loyalty",
    "membership",
    "member",
    "redeem",
    "redemption",
    "rewards",
    "reward",
    "points",
)

# Booking cancel frames (not lone "refund", not reschedule).
_CANCEL_FRAMES = (
    "cancel my",
    "cancel the",
    "cancel after",
    "can i cancel",
    "cancellation",
    "cancel booking",
    "cancel my booking",
    "cancel my appointment",
    "cancel appointment",
    "to cancel",
    "need to cancel",
    "cancel last",
    "cancel because",
    "cancelled",
    "canceled",
    "after cancellation",
    "no-show",
    "no show",
    "refund if i cancel",
    "refund if cancel",
    "cash refund for cancelled",
    "cash refund for canceled",
    "late pickup",
    "late check-out",
    "late checkout",
    "pick up my pet on time",
    "cannot pick up",
    "fully booked",
    "advance booking",
)

_RESCHEDULE_FRAMES = (
    "reschedule",
    "change my booking",
    "change booking",
    "change my appointment",
    "change appointment",
    "change grooming date",
    "change the date",
    "move my booking",
    "move my appointment",
)

_SERVICE_PRIMARY_FRAMES = (
    "how much",
    "price",
    "prices",
    "pricing",
    "cost",
    "fee",
    "fees",
    "package",
    "packages",
    "what is your",
    "do you provide",
    "do you have",
    "services do you",
)

_BOOKING_OBJECT_CUES = (
    "appointment",
    "booking",
    "cancel my",
    "cancel the",
    "can i cancel",
    "cancel booking",
    "cancel appointment",
    "reschedule",
    "change my booking",
    "change booking",
    "change my appointment",
    "no-show",
    "no show",
    "refund if i cancel",
    "refund if cancel",
    "late pickup",
    "late check-out",
    "late checkout",
)

_LOYALTY_REFUND_CUES = (
    "no cash refund",
    "no cash refunds",
    "non-refundable",
    "non refundable",
    "points be refunded",
    "points refunded",
    "points refund",
    "point package",
    "points package",
)

_POINTS_REQUEST_CUES = (
    "how many points",
    "earn points",
    "redeem points",
    "points for",
    "points from",
    "points needed",
    "points do i have",
    "use points",
    "using points",
    "points balance",
    "points expiry",
    "points be used",
    "loyalty points",
)


def _norm(text: str) -> str:
    return _RE_WHITESPACE.sub(" ", (text or "").lower()).strip()


def _has_any(blob: str, phrases: tuple[str, ...]) -> bool:
    return any(p in blob for p in phrases)


def _source_file_name(chunk: dict[str, Any]) -> str:
    for field in CHUNK_DATASET_FIELDS:
        value = str(chunk.get(field, "")).strip()
        if value:
            return value
    return ""


def normalize_pet_type(value: str) -> str:
    key = _norm(value)
    if key in ALLOWED_PET_TYPES:
        return key
    return PET_TYPE_ALIASES.get(key, "all")


def normalize_service_type(value: str) -> str:
    key = _norm(value).replace(" ", "_")
    if key in ALLOWED_SERVICE_TYPES:
        return key
    return SERVICE_TYPE_ALIASES.get(key, "general")


def detect_pet_type(blob: str) -> str:
    has_dog = _has_any(blob, _DOG_TERMS)
    has_cat = _has_any(blob, _CAT_TERMS)
    if has_dog and not has_cat:
        return "dog"
    if has_cat and not has_dog:
        return "cat"
    if has_dog and has_cat:
        return "all"
    return "all"


def has_loyalty_intent(blob: str) -> bool:
    """True when the utterance's business object is loyalty/points/membership."""
    return classify_business_object(blob)["main_intent"] == "loyalty"


def detect_service_context(blob: str) -> str:
    """Secondary service mention (grooming / boarding / daycare / general / unknown)."""
    text = _norm(blob)
    if _has_any(text, _GROOMING_TERMS):
        return "grooming"
    if _has_any(text, _BOARDING_TERMS):
        return "boarding"
    if _has_any(text, _DAYCARE_TERMS):
        return "daycare"
    if _has_any(text, _GENERAL_TERMS):
        return "general"
    return "unknown"


def _has_loyalty_business_object(text: str) -> bool:
    """Whole-utterance loyalty object — not a bare service noun."""
    if _has_any(text, _LOYALTY_FRAMES):
        return True
    # Anchor + intent verb/context (avoid classifying on the word alone in isolation).
    has_anchor = _has_any(text, _LOYALTY_ANCHORS)
    if not has_anchor:
        return False
    loyalty_verbs = (
        "earn",
        "redeem",
        "use",
        "used",
        "using",
        "count for",
        "counts for",
        "valid",
        "expiry",
        "expire",
        "balance",
        "how many",
        "do i have",
        "program",
        "package",
        "eligible",
        "membership",
        "loyalty",
        "points",
        "reward",
        "rewards",
    )
    return _has_any(text, loyalty_verbs)


def _has_reschedule_signal(text: str) -> bool:
    return _has_any(text, _RESCHEDULE_FRAMES)


def _has_cancellation_signal(text: str) -> bool:
    """Booking cancel signal — excludes loyalty non-refundable / points-refund rules."""
    if _is_loyalty_refund_rule(text):
        return False
    return _has_any(text, _CANCEL_FRAMES)


def _is_loyalty_refund_rule(text: str) -> bool:
    """Refund language about points/packages, not booking cancellation."""
    if not _has_any(text, ("refund", "refunded", "non-refundable", "non refundable")):
        return False
    if _has_any(text, ("if i cancel", "if cancel", "cancel my", "cancel booking", "cancel appointment")):
        return False
    return _has_any(text, _LOYALTY_REFUND_CUES) or (
        _has_any(text, ("points", "point", "loyalty", "membership"))
        and not _has_any(text, _BOOKING_OBJECT_CUES)
    )


def _has_service_mention(text: str) -> bool:
    return detect_service_context(text) in ("grooming", "boarding", "daycare")


def _has_service_primary_request(text: str) -> bool:
    return _has_service_mention(text) and _has_any(text, _SERVICE_PRIMARY_FRAMES)


def _infer_business_object(text: str) -> str:
    """Identify the main business object from full utterance meaning."""
    if _has_any(text, ("point package", "points package")):
        return "point_package"

    if _is_loyalty_refund_rule(text):
        if _has_any(text, ("point package", "points package", "package")):
            return "point_package"
        if _has_any(text, ("membership", "member")):
            return "membership"
        return "points"

    points_as_object = _has_any(text, _POINTS_REQUEST_CUES) or (
        _has_loyalty_business_object(text)
        and _has_any(text, ("points", "point", "loyalty", "redeem", "redemption", "rewards", "reward"))
    )
    booking_as_object = _has_any(text, _BOOKING_OBJECT_CUES) or _has_cancellation_signal(text) or _has_reschedule_signal(
        text
    )

    # Competing signals: choose by what is acted on / requested, not keyword order.
    if points_as_object and booking_as_object:
        if _has_any(text, _POINTS_REQUEST_CUES) or _is_loyalty_refund_rule(text):
            return "points"
        if _has_any(text, ("appointment", "booking")) or _has_any(
            text, ("if i cancel", "if cancel", "cancel my", "can i cancel")
        ):
            return "booking"
        return "points"

    if points_as_object:
        if _has_any(text, ("membership", "member")) and "point" not in text and "points" not in text:
            return "membership"
        return "points"

    if _has_any(text, ("membership package", "membership reward", "membership rewards", "membership")):
        if _has_loyalty_business_object(text):
            return "membership"

    if booking_as_object:
        return "booking"

    if _has_service_primary_request(text) or (
        _has_service_mention(text)
        and not _has_loyalty_business_object(text)
        and not _has_cancellation_signal(text)
        and not _has_reschedule_signal(text)
    ):
        return "service"

    if _has_any(text, ("payment policy", "payment", "pay with", "cash payment")):
        return "payment"

    return "other"


def _intent_from_object_and_action(
    *,
    text: str,
    business_object: str,
    service_context: str,
) -> tuple[str, float, str]:
    """Map object + action + requested info to main_intent (no global priority)."""
    loyalty_hit = _has_loyalty_business_object(text) or business_object in (
        "points",
        "membership",
        "point_package",
    )
    cancel_hit = _has_cancellation_signal(text)
    reschedule_hit = _has_reschedule_signal(text)
    service_hit = _has_service_mention(text)
    service_primary = _has_service_primary_request(text)

    active = []
    if loyalty_hit:
        active.append("loyalty")
    if cancel_hit:
        active.append("cancellation")
    if reschedule_hit:
        active.append("reschedule")
    if service_hit or service_primary:
        active.append("service")

    # Single clear topic — keyword detection is enough.
    if len(active) <= 1:
        if business_object in ("points", "membership", "point_package") or active == ["loyalty"]:
            return (
                "loyalty",
                0.92,
                f"Single loyalty topic; business object is {business_object}",
            )
        if active == ["reschedule"] or (business_object == "booking" and reschedule_hit and not cancel_hit):
            return (
                "reschedule",
                0.92,
                "Single reschedule topic; action applies to a booking",
            )
        if active == ["cancellation"] or (business_object == "booking" and cancel_hit):
            return (
                "cancellation",
                0.92,
                "Single cancellation topic; action applies to a booking",
            )
        if active == ["service"] or business_object == "service":
            return (
                "service",
                0.9,
                f"Single service topic; requesting info about {service_context}",
            )
        if business_object == "payment":
            return ("other", 0.55, "Payment mentioned without a clearer loyalty/cancel/service object")
        return ("other", 0.35, "No clear loyalty, cancellation, reschedule, or service topic")

    # Multiple signals — resolve from object / action / requested information.
    if business_object in ("points", "membership", "point_package"):
        svc_note = (
            f"; {service_context} is service context only"
            if service_context in ("grooming", "boarding", "daycare")
            else ""
        )
        return (
            "loyalty",
            0.88,
            f"Main object is {business_object}{svc_note}; classify by object not co-occurring keywords",
        )

    if business_object == "booking":
        if reschedule_hit and not cancel_hit:
            return (
                "reschedule",
                0.88,
                f"Main object is booking; action is reschedule"
                + (
                    f"; {service_context} is booking context"
                    if service_context in ("grooming", "boarding", "daycare")
                    else ""
                ),
            )
        if cancel_hit:
            return (
                "cancellation",
                0.88,
                f"Main object is booking; action is cancellation"
                + (
                    f"; {service_context} is booking context"
                    if service_context in ("grooming", "boarding", "daycare")
                    else ""
                ),
            )
        if reschedule_hit:
            return (
                "reschedule",
                0.85,
                "Main object is booking; action is reschedule",
            )

    if business_object == "service" or (service_primary and not loyalty_hit and not cancel_hit and not reschedule_hit):
        return (
            "service",
            0.86,
            f"Main object is the {service_context} service itself",
        )

    if business_object == "payment":
        return ("other", 0.5, "Payment-related request without a dominant loyalty/cancel object")

    # Ambiguous leftover: prefer the object that matches the strongest action frame.
    if cancel_hit and not _is_loyalty_refund_rule(text):
        return ("cancellation", 0.6, "Cancellation action present; object ambiguous so action used")
    if reschedule_hit:
        return ("reschedule", 0.6, "Reschedule action present; object ambiguous so action used")
    if loyalty_hit:
        return ("loyalty", 0.6, "Loyalty signals present; object ambiguous so loyalty object preferred")
    if service_hit:
        return ("service", 0.55, "Service mention present without a clearer competing object")
    return ("other", 0.3, "Multiple weak signals; no reliable main business object")


def classify_business_object(
    text: str,
    *,
    main_header: str = "",
    sub_header: str = "",
    service_info: str = "",
    section_context: str = "",
) -> dict[str, Any]:
    """Classify main intent from full semantic meaning.

    Does **not** use a fixed loyalty → cancellation → service priority.
    Keywords are enough for a single clear topic; when multiple intent signals
    co-occur, resolve from business object, action, and requested information.
    """
    blob = _norm(
        " ".join(
            part
            for part in (text, main_header, sub_header, service_info, section_context)
            if str(part or "").strip()
        )
    )
    service_context = detect_service_context(blob)
    business_object = _infer_business_object(blob)
    main_intent, confidence, reason = _intent_from_object_and_action(
        text=blob,
        business_object=business_object,
        service_context=service_context,
    )

    return {
        "main_intent": main_intent,
        "business_object": business_object,
        "service_context": service_context,
        "confidence": float(confidence),
        "reason": reason,
    }


def detect_service_type(blob: str) -> str:
    """Map whole-utterance business object to storage service_type."""
    result = classify_business_object(blob)
    if result["main_intent"] == "service":
        ctx = result["service_context"]
        if ctx in ("grooming", "boarding", "daycare"):
            return ctx
    return "general"


def _normalize_header_label(text: str) -> str:
    """Collapse whitespace; keep header casing and wording."""
    return _RE_WHITESPACE.sub(" ", (text or "").strip()).strip(".,;:")


def _short_topic_label(
    *,
    blob: str,
    pet_type: str,
    service_type: str,
    text: str = "",
) -> str:
    """5–10 word topic label when no sub/header is available."""
    source = (text or blob).strip()
    if source:
        line = source.split("\n", 1)[0].strip()
        if line.endswith(":"):
            line = line[:-1].strip()
        words = line.split()
        if len(words) >= 5:
            return _normalize_header_label(" ".join(words[:10]))

    pet = {"dog": "Dog", "cat": "Cat", "all": ""}[pet_type]
    svc_label = {
        "grooming": "grooming services",
        "boarding": "boarding services",
        "daycare": "daycare services",
        "general": "policy information",
        "all": "pet care information",
    }[service_type]
    label = " ".join(part for part in (pet, svc_label) if part).strip()
    return label or "Pet care information"


def generate_service_info(
    *,
    sub_header: str = "",
    main_header: str = "",
    section_title: str = "",
    blob: str = "",
    pet_type: str = "all",
    service_type: str = "general",
    text: str = "",
) -> str:
    if sub_header and sub_header.strip():
        return _normalize_header_label(sub_header)

    for header in (main_header, section_title):
        if header and header.strip():
            return _normalize_header_label(header)

    return _short_topic_label(
        blob=blob,
        pet_type=pet_type,
        service_type=service_type,
        text=text,
    )


def validate_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    validated = dict(metadata)

    dataset_type = str(
        validated.get("dataset_type")
        or validated.get("file_name")
        or validated.get("source_file")
        or validated.get("document_file_name")
        or ""
    ).strip()
    if dataset_type:
        dataset_type = Path(dataset_type).name
    validated["dataset_type"] = dataset_type

    pet_type = normalize_pet_type(str(validated.get("pet_type", "all")))
    if pet_type not in ALLOWED_PET_TYPES:
        pet_type = "all"
    validated["pet_type"] = pet_type

    service_type = normalize_service_type(str(validated.get("service_type", "general")))
    if service_type not in ALLOWED_SERVICE_TYPES:
        service_type = "general"
    validated["service_type"] = service_type

    service_info = str(validated.get("service_info", "")).strip()
    if not service_info:
        service_info = "Pet care information"
    validated["service_info"] = _normalize_header_label(service_info)

    for key in LEGACY_METADATA_KEYS:
        validated.pop(key, None)

    return validated


def tag_chunk_metadata(chunk: dict[str, Any]) -> dict[str, Any]:
    text = chunk.get("text", "")
    main_header = chunk.get("main_header", "")
    sub_header = chunk.get("sub_header", "")
    section_path = chunk.get("section_path", "")
    section_title = chunk.get("section_title", sub_header)
    dataset_type = _source_file_name(chunk)

    blob = _norm(" ".join([section_path, main_header, sub_header, section_title, text]))
    pet_type = detect_pet_type(blob)
    classified = classify_business_object(
        text,
        main_header=main_header,
        sub_header=sub_header,
        section_context=" ".join(part for part in (section_path, section_title) if part),
    )
    if classified["main_intent"] == "service" and classified["service_context"] in (
        "grooming",
        "boarding",
        "daycare",
    ):
        service_type = classified["service_context"]
    else:
        service_type = "general"

    # Point / membership / loyalty policy sections are general — never grooming/boarding/daycare
    header_blob = _norm(" ".join([main_header, sub_header, section_path, section_title]))
    if _has_any(
        header_blob,
        (
            "point package",
            "membership package",
            "loyalty",
            "eligible services",
            "point deduction",
            "point conversion",
            "point package validity",
            "membership validity",
            "misuse policy",
        ),
    ) or (
        "points" in header_blob
        and _has_any(header_blob, ("package", "redeem", "redemption", "membership"))
    ):
        service_type = "general"
        pet_type = "all"

    service_info = generate_service_info(
        sub_header=sub_header,
        main_header=main_header,
        section_title=section_title,
        blob=blob,
        pet_type=pet_type,
        service_type=service_type,
        text=text,
    )

    metadata = {
        "chunk_id": chunk.get("chunk_id", ""),
        "tenant_id": chunk.get("tenant_id", ""),
        "main_header": main_header,
        "sub_header": sub_header,
        "section_path": section_path,
        "dataset_type": dataset_type,
        "pet_type": pet_type,
        "service_type": service_type,
        "service_info": service_info,
    }
    return validate_metadata(metadata)


def auto_tag_fields(
    *,
    text: str = "",
    main_header: str = "",
    sub_header: str = "",
    section_title: str = "",
    file_name: str = "",
    source_file: str = "",
    chunk_id: str = "",
    tenant_id: str = "",
) -> dict[str, Any]:
    return tag_chunk_metadata(
        {
            "chunk_id": chunk_id,
            "tenant_id": tenant_id,
            "file_name": file_name or source_file,
            "main_header": main_header,
            "sub_header": sub_header or section_title,
            "section_path": "",
            "text": text,
        }
    )


def infer_expected_pet_type_from_query(query: str, *, service_type: str = "") -> str:
    """Infer eval expected_pet_type: dog | cat | all."""
    blob = _norm(query)
    svc = normalize_service_type(service_type)

    if svc == "general" and _has_any(
        blob,
        (
            "cancellation",
            "cancel",
            *_LOYALTY_FRAMES,
            *_LOYALTY_ANCHORS,
            "payment policy",
        ),
    ):
        return "all"

    dog_grooming = _has_any(
        blob,
        ("dog grooming", "grooming dog", "groom my dog", "puppy grooming", "dog groom"),
    )
    cat_grooming = _has_any(
        blob,
        ("cat grooming", "grooming cat", "groom my cat", "kitten grooming", "cat groom"),
    )
    has_grooming = _has_any(blob, _GROOMING_TERMS)
    has_dog = _has_any(blob, _DOG_TERMS)
    has_cat = _has_any(blob, _CAT_TERMS)

    if dog_grooming and cat_grooming:
        return "all"
    if dog_grooming or (has_dog and has_grooming and not has_cat):
        return "dog"
    if cat_grooming or (has_cat and has_grooming and not has_dog):
        return "cat"
    if has_dog and has_cat:
        return "all"
    return "all"
