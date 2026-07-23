"""
Base grooming package vs add-on separation for session memory.
"""

from __future__ import annotations

import re

from booking_flow import entity_value, merge_collected_entities

BASE_PACKAGE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bbasic\s+grooming\b", re.I), "basic_grooming"),
    (re.compile(r"\bfull\s+grooming\b", re.I), "full_grooming"),
    (re.compile(r"^\s*basic\s*[!.?]*\s*$", re.I), "basic_grooming"),
    (re.compile(r"^\s*full\s*[!.?]*\s*$", re.I), "full_grooming"),
    (re.compile(r"\bi\s+want\s+basic\s+grooming\b", re.I), "basic_grooming"),
    (re.compile(r"\bi\s+want\s+full\s+grooming\b", re.I), "full_grooming"),
    (re.compile(r"\bdog\s+trimming\s+package\b", re.I), "dog_trimming"),
    (re.compile(r"\btrimming\s+package\b", re.I), "dog_trimming"),
    (re.compile(r"\ball\s+shave\b", re.I), "dog_trimming"),
    (re.compile(r"\bkeep\s+head\s+and\s+tail\b", re.I), "dog_trimming"),
    (re.compile(r"\bkeep\s+head,\s*tail\s+and\s+legs\b", re.I), "dog_trimming"),
    (re.compile(r"\bkeep\s+head\s*&\s*tail\b", re.I), "dog_trimming"),
    (re.compile(r"\bfull\s+scissor(s)?\b", re.I), "dog_trimming"),
)

ADDON_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bnano\s+spa\b", re.I), "nano_spa"),
    (re.compile(r"\bde[\s-]?shedding\b", re.I), "de_shedding"),
)

PACKAGE_SLUG_TO_LABEL = {
    "basic_grooming": "Basic Grooming",
    "full_grooming": "Full Grooming",
    "dog_trimming": "Dog Trimming Package",
}

ADDON_SLUG_TO_LABEL = {
    "nano_spa": "Nano Spa",
    "de_shedding": "De-shedding",
}


def extract_base_package_slug(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    for pattern, slug in BASE_PACKAGE_PATTERNS:
        if pattern.search(text):
            return slug
    return ""


def extract_addon_slug(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    for pattern, slug in ADDON_PATTERNS:
        if pattern.search(text):
            return slug
    return ""


def package_slug_to_label(slug: str) -> str:
    return PACKAGE_SLUG_TO_LABEL.get(str(slug or "").strip(), "")


def addon_slug_to_label(slug: str) -> str:
    return ADDON_SLUG_TO_LABEL.get(str(slug or "").strip(), "")


def extract_service_package(message: str) -> str:
    """Legacy display label for base packages only — never add-ons."""
    slug = extract_base_package_slug(message)
    return package_slug_to_label(slug)


def get_session_selected_addons(session) -> list[str]:
    raw = getattr(session, "selected_addons", None)
    if not isinstance(raw, list):
        return []
    cleaned: list[str] = []
    for item in raw:
        token = str(item or "").strip()
        if not token or token.startswith("["):
            continue
        if token not in cleaned:
            cleaned.append(token)
    return cleaned


def extract_package_entity_updates(session, entities: dict, message: str) -> dict:
    """Return package/addon field updates without writing session."""
    merged = dict(entities or {})
    base = extract_base_package_slug(message)
    addon = extract_addon_slug(message)
    existing_base = str(getattr(session, "selected_package", "") or "").strip() if session else ""
    fields: dict = {}

    if base:
        fields["selected_package"] = base
        label = package_slug_to_label(base)
        if label:
            fields["service_package"] = label

    if addon:
        addons = list(getattr(session, "selected_addons", []) or []) if session else []
        if addon not in addons:
            addons.append(addon)
        fields["selected_addons"] = addons
        addon_label = addon_slug_to_label(addon)
        if addon_label:
            fields["add_on_service"] = addon_label

    if addon and not base and existing_base:
        fields["selected_package"] = existing_base
        label = package_slug_to_label(existing_base)
        if label:
            fields["service_package"] = label

    merged.update({k: v for k, v in fields.items() if k != "selected_addons"})
    return {"fields": fields, "merged": merged}


def apply_package_and_addon_extraction(session, entities: dict, message: str, *, write_session: bool = True) -> dict:
    """Merge base package and add-ons without letting add-ons overwrite the base package."""
    update = extract_package_entity_updates(session, entities, message)
    merged = dict(update.get("merged") or entities or {})
    merged.update({k: v for k, v in (update.get("fields") or {}).items() if k != "selected_addons"})

    if write_session and session is not None:
        base = extract_base_package_slug(message)
        addon = extract_addon_slug(message)
        if base:
            session.selected_package = base
            label = package_slug_to_label(base)
            if label and hasattr(session, "write_projection_fields"):
                session.write_projection_fields(service_package=label)
            elif label:
                session.service_package = label
        if addon:
            addons = get_session_selected_addons(session)
            if addon not in addons:
                addons.append(addon)
            session.selected_addons = addons

    return merged


def sync_package_fields_to_session(session, entities: dict, *, write_session: bool = True) -> None:
    """Legacy adapter — prefer extract_package_entity_updates + canonical merge."""
    if not write_session:
        return
    base = entity_value(entities, "selected_package") or str(
        getattr(session, "selected_package", "") or ""
    ).strip()
    if base:
        session.selected_package = base
        label = package_slug_to_label(base)
        if label:
            if hasattr(session, "write_projection_fields"):
                session.write_projection_fields(service_package=label)
            else:
                session.service_package = label

    merged = merge_collected_entities(
        session,
        {k: v for k, v in dict(entities or {}).items() if k != "selected_addons"},
    )
    if base:
        merged["selected_package"] = base
        label = package_slug_to_label(base)
        if label:
            merged["service_package"] = label
    merged.pop("selected_addons", None)
    if hasattr(session, "write_projection_fields"):
        session.write_projection_fields(collected_entities=merged)
    else:
        session.collected_entities = merged
