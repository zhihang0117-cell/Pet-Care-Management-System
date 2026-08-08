"""Resolve customer-owned pet references without model-selected identifiers."""

from __future__ import annotations

import re
from typing import Any


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
        if re.search(r"[\u3400-\u9fff]", normalized):
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
