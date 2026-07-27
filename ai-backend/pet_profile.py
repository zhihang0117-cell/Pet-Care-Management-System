"""
Load and merge customer pet records from the relational database into session state.

After customer identity resolution, pet profiles (name, type, height, size) are
fetched by customer_id and applied before missing-field calculation.
"""

from __future__ import annotations

import os

from customer_context import CustomerContext
from booking_flow import entity_value, merge_collected_entities


def _normalize_pet_type(value: str) -> str:
    token = str(value or "").strip().upper()
    if token in {"DOG", "PUPPY"}:
        return "DOG"
    if token in {"CAT", "KITTEN"}:
        return "CAT"
    return token


def _normalize_height_cm(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.lower().endswith("cm"):
        return text
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    if not digits:
        return text
    return f"{digits}cm"


def _is_active_pet_row(pet: dict) -> bool:
    if not isinstance(pet, dict):
        return False
    for key in ("status", "pet_status", "is_active"):
        if key not in pet:
            continue
        raw = pet.get(key)
        if raw is None:
            continue
        text = str(raw).strip().lower()
        if text in {"inactive", "deleted", "disabled", "false", "0", "no"}:
            return False
        if text in {"active", "true", "1", "yes"}:
            return True
    return True


def map_pet_row_to_profile(pet: dict) -> dict:
    """Map a relational pet row to normalized session entity fields."""
    from pet_extraction import infer_pet_size_from_height, normalize_pet_size

    pet_name = str(pet.get("pet_name") or "").strip()
    pet_type = _normalize_pet_type(pet.get("pet_type") or "")
    height = _normalize_height_cm(pet.get("height_cm"))
    if not height:
        height = _normalize_height_cm(pet.get("pet_height_cm"))
    if not height:
        height = _normalize_height_cm(pet.get("pet_height"))

    size_raw = str(pet.get("size") or pet.get("pet_size") or "").strip()
    pet_size = normalize_pet_size(size_raw) or size_raw.lower()

    if height and not pet_size:
        inferred = infer_pet_size_from_height(height)
        if inferred:
            pet_size = inferred

    profile: dict[str, str] = {}
    pet_id = pet.get("pet_id")
    if pet_id is not None:
        profile["pet_id"] = str(pet_id)
    if pet_name:
        profile["pet_name"] = pet_name
    if pet_type and pet_type != "UNKNOWN":
        profile["pet_type"] = pet_type
    if height:
        profile["pet_height"] = height
        profile["pet_size_or_height"] = height
    if pet_size:
        profile["pet_size"] = pet_size
        if not profile.get("pet_size_or_height"):
            profile["pet_size_or_height"] = pet_size
    return profile


def fetch_customer_pets(session) -> list[dict]:
    """Return active pet rows for the session customer (cached on session when possible)."""
    cached = list(getattr(session, "customer_pets", None) or [])
    if cached:
        return cached

    if session.customer_id is None:
        return []

    provider = os.getenv("DATABASE_PROVIDER", "mock").strip().lower() or "mock"

    pets: list[dict] = []
    if provider == "supabase":
        from relational_actions import get_pets_by_customer_id

        context = CustomerContext(
            phone_number=str(getattr(session, "phone_number", "") or "").strip(),
            request_customer_id=str(session.customer_id),
        )
        context.resolved_customer_id = session.customer_id
        result = get_pets_by_customer_id(context)
        pets = list(result.get("data", {}).get("pets") or [])
    else:
        from mock_database import mock_get_customer_pets

        result = mock_get_customer_pets(
            customer_id=session.customer_id,
            phone_number=str(getattr(session, "phone_number", "") or "").strip(),
        )
        pets = list(result.get("data", {}).get("pets") or [])

    active_pets = [pet for pet in pets if _is_active_pet_row(pet)]
    session.customer_pets = active_pets
    return active_pets


def get_pet_by_customer_and_name(session, pet_name: str) -> dict | None:
    name = str(pet_name or "").strip().lower()
    if not name:
        return None
    for pet in fetch_customer_pets(session):
        if str(pet.get("pet_name") or "").strip().lower() == name:
            return pet
    return None


def match_pet_from_message(session, user_message: str) -> dict | None:
    from pet_extraction import extract_pet_name_from_message

    pets = fetch_customer_pets(session)
    if not pets:
        return None
    matched_name = extract_pet_name_from_message(user_message, pets)
    if not matched_name:
        return None
    return get_pet_by_customer_and_name(session, matched_name)


def build_pet_profile_entity_update(pet: dict) -> dict:
    """Return pet profile fields for session merge."""
    return map_pet_row_to_profile(pet)


def apply_pet_profile_to_session(session, pet: dict, *, write_session: bool = True) -> dict:
    """Merge a relational pet row into session canonical scalar mirrors only."""
    profile = map_pet_row_to_profile(pet)
    if not profile or not write_session:
        return profile

    if profile.get("pet_name"):
        session.pet_name = profile["pet_name"]
    if profile.get("pet_type"):
        session.pet_type = profile["pet_type"]
    if profile.get("pet_size"):
        session.pet_size = profile["pet_size"]
    if profile.get("pet_height"):
        session.pet_height = profile["pet_height"]
    if profile.get("pet_id"):
        try:
            session.pet_id = int(profile["pet_id"])
        except (TypeError, ValueError):
            pass
    return profile


def build_pet_choice_reply(pets: list[dict]) -> str:
    entries: list[tuple[str, str]] = []
    for pet in pets:
        name = str(pet.get("pet_name") or "").strip()
        if not name:
            continue
        pet_type = str(pet.get("pet_type") or "").strip().lower()
        if pet_type in {"dog", "puppy"}:
            pet_type = "dog"
        elif pet_type in {"cat", "kitten"}:
            pet_type = "cat"
        entries.append((name, pet_type))

    seen: list[tuple[str, str]] = []
    for entry in entries:
        if entry not in seen:
            seen.append(entry)

    if len(seen) == 2:
        type_words = {t for _, t in seen if t in {"dog", "cat"}}
        if type_words == {"dog"}:
            group = "dogs"
        elif type_words == {"cat"}:
            group = "cats"
        else:
            group = "pets"
        return (
            f"I found two {group} under your profile. "
            f"Is this enquiry for {seen[0][0]} or {seen[1][0]}?"
        )
    if len(seen) > 2:
        names = [name for name, _ in seen]
        joined = ", ".join(names[:-1])
        return (
            f"I found several pets under your profile. "
            f"Is this enquiry for {joined}, or {names[-1]}?"
        )
    if len(seen) == 1:
        return f"Is this for {seen[0][0]}?"
    return "Which pet would this be for?"


def enrich_session_pet_profile(session, user_message: str = "") -> dict:
    """
    Load customer pets and apply a matched pet profile to the session.

    Returns:
        {
          "status": "matched" | "ambiguous" | "none" | "no_customer" | "no_pets",
          "matched_pet": {...} | None,
          "pet_names": [...],
        }
    """
    if session.customer_id is None:
        return {"status": "no_customer", "matched_pet": None, "pet_names": []}

    collected = dict(getattr(session, "collected_entities", {}) or {})
    if str(collected.get("new_pet_requested") or "").strip().lower() in {"true", "yes", "1"}:
        name = str(collected.get("pet_name") or getattr(session, "pet_name", "") or "").strip()
        if name:
            session.pet_name = name
        return {"status": "new_pet", "matched_pet": None, "pet_names": []}

    pets = fetch_customer_pets(session)
    pet_names = [
        str(pet.get("pet_name") or "").strip()
        for pet in pets
        if str(pet.get("pet_name") or "").strip()
    ]
    if not pets:
        return {"status": "no_pets", "matched_pet": None, "pet_names": []}

    matched = match_pet_from_message(session, user_message)
    if matched:
        apply_pet_profile_to_session(session, matched)
        return {"status": "matched", "matched_pet": matched, "pet_names": pet_names}

    if len(pets) == 1:
        mentioned = match_pet_from_message(session, user_message)
        explicit_name = str(getattr(session, "pet_name", "") or "").strip()
        if not mentioned and not explicit_name:
            apply_pet_profile_to_session(session, pets[0])
            return {"status": "matched", "matched_pet": pets[0], "pet_names": pet_names}

    if len(pets) > 1 and not str(getattr(session, "pet_name", "") or "").strip():
        return {"status": "ambiguous", "matched_pet": None, "pet_names": pet_names}

    if getattr(session, "pet_id", None) is not None:
        for pet in pets:
            if pet.get("pet_id") == session.pet_id:
                apply_pet_profile_to_session(session, pet)
                return {"status": "matched", "matched_pet": pet, "pet_names": pet_names}

    stored_name = str(getattr(session, "pet_name", "") or "").strip()
    if stored_name:
        stored_pet = get_pet_by_customer_and_name(session, stored_name)
        if stored_pet:
            apply_pet_profile_to_session(session, stored_pet)
            return {"status": "matched", "matched_pet": stored_pet, "pet_names": pet_names}

    return {"status": "none", "matched_pet": None, "pet_names": pet_names}


def session_has_pet_size_profile(session) -> bool:
    ctx = merge_collected_entities(session, {})
    for key in ("pet_height", "pet_size", "pet_size_or_height"):
        if entity_value(ctx, key):
            return True
    for key in ("pet_height", "pet_size"):
        if str(getattr(session, key, "") or "").strip():
            return True
    return False


def session_has_pet_type_profile(session) -> bool:
    ctx = merge_collected_entities(session, {})
    if entity_value(ctx, "pet_type"):
        return True
    return bool(str(getattr(session, "pet_type", "") or "").strip())
