"""
Shared pet name and pet size extraction for booking and price enquiry flows.
"""

from __future__ import annotations

import re

from booking_flow import entity_value

PET_NAME_BLOCKLIST = frozenset(
    {
        "how",
        "what",
        "when",
        "where",
        "why",
        "which",
        "who",
        "can",
        "could",
        "would",
        "do",
        "does",
        "is",
        "are",
        "my",
        "the",
        "a",
        "an",
        "it",
        "its",
        "i",
        "we",
        "you",
        "your",
        "please",
        "sure",
        "thanks",
        "hello",
        "hi",
    }
)

_EXPLICIT_PET_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:my\s+pet'?s?\s+name\s+is|pet\s+name\s*:)\s+([A-Za-z][A-Za-z'-]{0,19})\b",
        re.I,
    ),
    re.compile(r"(?:his|her)\s+name\s+is\s+([A-Za-z][A-Za-z'-]{0,19})\b", re.I),
    re.compile(
        r"(?:the\s+(?:dog|cat|pet)\s+is\s+called|(?:dog|cat)\s+is\s+called|called)\s+([A-Za-z][A-Za-z'-]{0,19})\b",
        re.I,
    ),
)

_PET_TYPE_EXTRACT = re.compile(r"\b(dog|cat|puppy|kitten)\b", re.I)
_HEIGHT_EXTRACT = re.compile(r"(\d+(?:\.\d+)?)\s*cm\b", re.I)
_SIZE_EXTRACT = re.compile(
    r"\b(xs|s|m|l|xl|xxl|small|medium|large|extra[\s-]?large|medium[\s-]?sized)\b",
    re.I,
)
_M_SIZE_EXTRACT = re.compile(r"\bM\s+size\b", re.I)


def is_valid_stored_pet_name(name: str) -> bool:
    token = str(name or "").strip()
    if not token or len(token) < 2:
        return False
    return token.lower() not in PET_NAME_BLOCKLIST


def extract_explicit_pet_name(message: str) -> str:
    """Return a pet name only when the message uses an explicit naming pattern."""
    text = str(message or "").strip()
    if not text:
        return ""
    for pattern in _EXPLICIT_PET_NAME_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = str(match.group(1) or "").strip()
        if is_valid_stored_pet_name(candidate):
            return candidate
    return ""


def extract_pet_name_from_message(message: str, pets: list[dict] | None = None) -> str:
    """Match known customer pets or explicit naming patterns — never question words."""
    explicit = extract_explicit_pet_name(message)
    if explicit:
        return explicit

    text = str(message or "").lower()
    for pet in list(pets or []):
        name = str(pet.get("pet_name") or "").strip()
        if name and re.search(rf"\b{re.escape(name.lower())}\b", text):
            return name
    return ""


def normalize_pet_size(value: str) -> str:
    token = str(value or "").strip().lower().replace(" ", "").replace("-", "")
    mapping = {
        "xs": "xs",
        "s": "small",
        "small": "small",
        "m": "medium",
        "medium": "medium",
        "mediumsized": "medium",
        "l": "large",
        "large": "large",
        "xl": "xl",
        "xxl": "xl",
        "extralarge": "xl",
    }
    return mapping.get(token, "")


def extract_pet_size_from_message(message: str) -> dict[str, str]:
    """Extract and normalize pet size / height from free text."""
    text = str(message or "").strip()
    extracted: dict[str, str] = {}

    height_match = _HEIGHT_EXTRACT.search(text)
    if height_match:
        extracted["pet_height"] = f"{height_match.group(1)}cm"
        extracted["pet_size_or_height"] = extracted["pet_height"]
        inferred = infer_pet_size_from_height(extracted["pet_height"])
        if inferred:
            extracted["pet_size"] = inferred

    if _M_SIZE_EXTRACT.search(text):
        extracted["pet_size"] = "medium"
        extracted["pet_size_or_height"] = "medium"
    else:
        size_match = _SIZE_EXTRACT.search(text)
        if size_match and "pet_size" not in extracted:
            normalized = normalize_pet_size(size_match.group(1))
            if normalized:
                extracted["pet_size"] = normalized
                extracted["pet_size_or_height"] = normalized

    return extracted


def extract_pet_type_from_message(message: str) -> str:
    match = _PET_TYPE_EXTRACT.search(str(message or ""))
    if not match:
        return ""
    token = match.group(1).lower()
    return "DOG" if token in {"dog", "puppy"} else "CAT"


def extract_pet_profile_from_message(message: str) -> dict[str, str]:
    """Combined pet type + size extraction for booking and pricing."""
    extracted: dict[str, str] = {}
    pet_type = extract_pet_type_from_message(message)
    if pet_type:
        extracted["pet_type"] = pet_type
    extracted.update(extract_pet_size_from_message(message))
    return {key: value for key, value in extracted.items() if str(value).strip()}


def infer_pet_size_from_height(height_text: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)?)", str(height_text or ""))
    if not match:
        return ""
    try:
        cm = float(match.group(1))
    except ValueError:
        return ""
    if cm < 30:
        return "small"
    if cm < 45:
        return "medium"
    if cm < 60:
        return "large"
    return "xl"


def merge_pet_profile_without_erasing(session, entities: dict, message: str) -> dict:
    """Merge extracted pet profile; never overwrite valid stored values with empty/unknown."""
    merged = dict(entities or {})
    profile = extract_pet_profile_from_message(message)
    for key, value in profile.items():
        if not str(value or "").strip():
            continue
        if key == "pet_size":
            existing = normalize_pet_size(
                entity_value(merged, "pet_size")
                or str(getattr(session, "pet_size", "") or "")
            )
            if existing and not value:
                continue
        merged[key] = value
    return merged


_PET_NAME_REJECTED_TOKENS = frozenset(
    {
        *PET_NAME_BLOCKLIST,
        "dog",
        "cat",
        "puppy",
        "kitten",
        "yes",
        "no",
        "yeah",
        "yep",
        "yup",
        "nope",
        "nah",
        "ok",
        "okay",
        "sure",
        "confirm",
        "confirmed",
        "proceed",
        "small",
        "medium",
        "large",
        "xs",
        "s",
        "m",
        "l",
        "xl",
        "xxl",
        "tomorrow",
        "today",
        "grooming",
        "daycare",
        "boarding",
        "basic",
        "full",
        "trimming",
        "morning",
        "afternoon",
        "evening",
        "night",
        "am",
        "pm",
    }
)

_NEW_PET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:my\s+)?new\s+pet\s+(?:is\s+(?:called\s+)?)?([A-Za-z][A-Za-z'-]{0,19})\b", re.I),
    re.compile(r"\bnew\s+pet\s+(?:named\s+)?([A-Za-z][A-Za-z'-]{0,19})\b", re.I),
)

_FOR_PET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:for|with)\s+(?:my\s+)?(?:pet\s+)?([A-Za-z][A-Za-z'-]{0,19})\b", re.I),
    re.compile(r"\b(?:book|booking)\s+(?:grooming|daycare|boarding)\s+for\s+([A-Za-z][A-Za-z'-]{0,19})\b", re.I),
)


def is_rejected_pet_name_token(token: str) -> bool:
    return str(token or "").strip().lower() in _PET_NAME_REJECTED_TOKENS


def resolve_bare_pet_name(message: str) -> str:
    """Accept bare plausible pet names when message is a short name token."""
    text = str(message or "").strip().strip(".!?")
    if not text:
        return ""
    explicit = extract_explicit_pet_name(text)
    if explicit:
        return explicit
    tokens = text.split()
    if len(tokens) != 1:
        return ""
    candidate = tokens[0]
    if not is_valid_stored_pet_name(candidate):
        return ""
    if is_rejected_pet_name_token(candidate):
        return ""
    if candidate.isupper() and len(candidate) <= 3:
        return ""
    return candidate


def extract_new_pet_name_from_message(message: str) -> str:
    text = str(message or "").strip()
    for pattern in _NEW_PET_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = str(match.group(1) or "").strip()
        if is_valid_stored_pet_name(candidate) and not is_rejected_pet_name_token(candidate):
            return candidate
    return ""


def extract_pet_name_from_natural_request(message: str, pets: list[dict] | None = None) -> str:
    """Extract pet name from natural booking phrasing or bare token."""
    text = str(message or "").strip()
    new_pet = extract_new_pet_name_from_message(text)
    if new_pet:
        return new_pet
    known = extract_pet_name_from_message(text, pets)
    if known:
        return known
    for pattern in _FOR_PET_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = str(match.group(1) or "").strip()
        if is_valid_stored_pet_name(candidate) and not is_rejected_pet_name_token(candidate):
            return candidate
    return resolve_bare_pet_name(text)


def message_requests_new_pet(message: str) -> bool:
    return bool(re.search(r"\bnew\s+pet\b", str(message or ""), re.I))


def extract_natural_booking_entities(message: str, pets: list[dict] | None = None) -> dict[str, str]:
    """Extract service + pet from natural booking requests."""
    from session_continuation import _extract_service_type

    entities: dict[str, str] = {}
    service = _extract_service_type(message)
    if service:
        entities["service_type"] = service
    pet_name = extract_pet_name_from_natural_request(message, pets)
    if pet_name:
        entities["pet_name"] = pet_name
    if message_requests_new_pet(message) and pet_name:
        entities["new_pet_requested"] = "true"
    return entities
