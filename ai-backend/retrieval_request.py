"""Strict retrieval request builder to prevent RAG overlap (PART 11)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

INFORMATION_TYPES = frozenset(
    {
        "PACKAGE_DETAILS",
        "PACKAGE_PRICE",
        "ADDON_DETAILS",
        "ADDON_PRICE",
        "SERVICE_INFORMATION",
        "CANCELLATION_POLICY",
        "RESCHEDULE_POLICY",
        "SERVICE_POLICY",
        "GENERAL_POLICY",
    }
)


@dataclass
class RetrievalRequest:
    company_id: int | None = None
    query: str = ""
    service_type: str | None = None
    information_type: str | None = None
    selected_package: str | None = None
    selected_addon: str | None = None
    pet_type: str | None = None
    max_chunks: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "query": self.query,
            "service_type": self.service_type,
            "information_type": self.information_type,
            "selected_package": self.selected_package,
            "selected_addon": self.selected_addon,
            "pet_type": self.pet_type,
            "max_chunks": self.max_chunks,
        }


def classify_information_type(intent_json: dict, user_message: str = "") -> str:
    scenario = str(intent_json.get("scenario_intent") or "").strip()
    text = str(user_message or "").lower()
    if scenario == "CANCELLATION_POLICY" or "cancellation" in text and "policy" in text:
        return "CANCELLATION_POLICY"
    if "reschedule" in text and "policy" in text:
        return "RESCHEDULE_POLICY"
    if any(k in text for k in ("how much", "price", "cost", "fee")):
        return "PACKAGE_PRICE"
    if any(k in text for k in ("included", "include", "what is in", "details")):
        return "PACKAGE_DETAILS"
    if scenario in {"GROOMING_POLICY", "DAYCARE_POLICY", "BOARDING_POLICY", "VET_REQUIREMENT"}:
        return "SERVICE_POLICY"
    if scenario == "SERVICE_INFORMATION":
        return "SERVICE_INFORMATION"
    if scenario.endswith("_POLICY") or scenario == "GENERAL_POLICY":
        return "GENERAL_POLICY"
    return "SERVICE_INFORMATION"


def build_retrieval_request(
    user_message: str,
    intent_json: dict,
    *,
    company_id: int | None = None,
    max_chunks: int = 5,
) -> RetrievalRequest:
    entities = dict(intent_json.get("entities") or {})
    service = str(
        intent_json.get("service_type") or entities.get("service_type") or ""
    ).strip().upper() or None
    if service == "UNKNOWN":
        service = None
    return RetrievalRequest(
        company_id=company_id,
        query=str(user_message or "").strip(),
        service_type=service,
        information_type=classify_information_type(intent_json, user_message),
        selected_package=str(
            entities.get("service_package") or entities.get("selected_package") or ""
        ).strip()
        or None,
        selected_addon=str(entities.get("selected_addon") or "").strip() or None,
        pet_type=str(entities.get("pet_type") or intent_json.get("pet_type") or "").strip().upper() or None,
        max_chunks=max_chunks,
    )


def chunk_matches_retrieval_request(chunk: dict, request: RetrievalRequest) -> bool:
    """Filter retrieved chunks by service and information type."""
    text = str(chunk.get("content") or chunk.get("text") or "").lower()
    meta = chunk.get("metadata") or chunk.get("meta") or {}
    chunk_service = str(meta.get("service_type") or meta.get("service") or "").upper()
    if request.service_type and chunk_service and chunk_service not in {request.service_type, "GENERAL"}:
        return False
    info = str(request.information_type or "")
    if info == "CANCELLATION_POLICY":
        return "cancellation" in text or "refund" in text
    if info == "PACKAGE_PRICE":
        return any(k in text for k in ("price", "rm", "cost", "fee"))
    if info == "PACKAGE_DETAILS":
        return "include" in text or "grooming" in text or "package" in text
    if info == "SERVICE_POLICY":
        return "rule" in text or "requirement" in text or "policy" in text
    return True


def filter_rag_chunks(chunks: list, request: RetrievalRequest) -> list:
    filtered = [c for c in list(chunks or []) if chunk_matches_retrieval_request(c, request)]
    limit = request.max_chunks or 5
    return filtered[:limit]


# --- price item intent (grooming packages vs add-ons) ---

from booking_flow import entity_value as _entity_value

DOG_TRIMMING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\btrimming\b", re.I),
    re.compile(r"\bdog\s+trimming\b", re.I),
    re.compile(r"\btrimming\s+package\b", re.I),
    re.compile(r"\ball\s+shave\b", re.I),
    re.compile(r"\bkeep\s+head\s+and\s+tail\b", re.I),
    re.compile(r"\bkeep\s+head,\s*tail\s+and\s+legs\b", re.I),
    re.compile(r"\bkeep\s+head\s*&\s*tail\b", re.I),
    re.compile(r"\bfull\s+scissor(s)?\b", re.I),
)

EXPLICIT_ADDON_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\bshaving\s+belly\b", re.I), "shaving_belly", "Shaving Belly"),
    (re.compile(r"\bshaving\s+paw(s)?\b", re.I), "shaving_paw", "Shaving Paw"),
    (re.compile(r"\bshaving\s+sanitary\b", re.I), "shaving_sanitary", "Shaving Sanitary"),
    (re.compile(r"\btrimming\s+head\b", re.I), "trimming_head", "Trimming Head"),
    (re.compile(r"\btrimming\s+eyes\b", re.I), "trimming_eyes", "Trimming Eyes"),
    (re.compile(r"\btrimming\s+sanitary\b", re.I), "trimming_sanitary", "Trimming Sanitary"),
)

PACKAGE_SLUG_TO_LABEL = {"dog_trimming": "Dog Trimming Package"}

ADDON_SLUG_TO_LABEL = {
    "shaving_belly": "Shaving Belly",
    "shaving_paw": "Shaving Paw",
    "shaving_sanitary": "Shaving Sanitary",
    "trimming_head": "Trimming Head",
    "trimming_eyes": "Trimming Eyes",
    "trimming_sanitary": "Trimming Sanitary",
}

_SIZE_HINTS: dict[str, re.Pattern[str]] = {
    "xs": re.compile(r"\b(xs|below\s*25|under\s*25|<\s*25)\b", re.I),
    "s": re.compile(r"\b(s\s+size|small|25\s*-\s*35|26\s*cm|below\s*35)\b", re.I),
    "m": re.compile(r"\b(m\s+size|medium|36\s*-\s*45|36\s*cm|45\s*cm)\b", re.I),
    "l": re.compile(r"\b(l\s+size|large|46\s*-\s*55|46\s*cm|55\s*cm)\b", re.I),
    "xl": re.compile(r"\b(xl|extra\s+large|above\s*55|56\s*cm)\b", re.I),
}


def extract_price_item_intent(message: str) -> dict[str, str]:
    text = str(message or "").strip()
    result = {"requested_package": "", "requested_price_item_type": "", "requested_addon": ""}
    if not text:
        return result
    for pattern, slug, _label in EXPLICIT_ADDON_PATTERNS:
        if pattern.search(text):
            result["requested_price_item_type"] = "addon"
            result["requested_addon"] = slug
            return result
    for pattern in DOG_TRIMMING_PATTERNS:
        if pattern.search(text):
            result["requested_package"] = "dog_trimming"
            result["requested_price_item_type"] = "base_package"
            return result
    return result


def is_dog_trimming_package_intent(message: str) -> bool:
    return extract_price_item_intent(message).get("requested_package") == "dog_trimming"


def is_explicit_addon_intent(message: str) -> bool:
    return extract_price_item_intent(message).get("requested_price_item_type") == "addon"


def resolve_price_context(user_message: str, ctx: dict | None = None) -> dict[str, str]:
    merged = extract_price_item_intent(user_message)
    source = dict(ctx or {})
    for key in ("requested_package", "requested_price_item_type", "requested_addon"):
        val = str(source.get(key) or "").strip()
        if val:
            merged[key] = val
    package = _entity_value(source, "requested_package") or _entity_value(source, "selected_package")
    if package == "dog_trimming" and not merged.get("requested_package"):
        merged["requested_package"] = "dog_trimming"
        merged["requested_price_item_type"] = "base_package"
    addon = _entity_value(source, "requested_addon")
    if addon and not merged.get("requested_addon"):
        merged["requested_addon"] = addon
        merged["requested_price_item_type"] = "addon"
    return merged


def chunk_is_dog_trimming_package(chunk: dict) -> bool:
    text = str(chunk.get("text") or "").lower()
    metadata = dict(chunk.get("metadata") or {})
    for key in ("main_header", "sub_header", "section_title", "service_info"):
        header = str(metadata.get(key) or "").lower()
        if "dog trimming" in header:
            return True
    return "dog trimming package" in text


def chunk_is_shaving_addon(chunk: dict) -> bool:
    text = str(chunk.get("text") or "").lower()
    metadata = dict(chunk.get("metadata") or {})
    for key in ("main_header", "sub_header", "section_title", "service_info"):
        header = str(metadata.get(key) or "").lower()
        if any(token in header for token in ("basic grooming add-on", "shaving add-on", "grooming add-on price")):
            return True
    if re.search(r"shaving\s+(belly|paw|sanitary)", text):
        return True
    labels = ("shaving belly", "shaving paw", "shaving sanitary")
    return sum(1 for label in labels if label in text) >= 2


def prioritize_chunks_for_price_intent(chunks: list[dict], price_context: dict) -> list[dict]:
    if not chunks:
        return []
    item_type = str(price_context.get("requested_price_item_type") or "").strip()
    package = str(price_context.get("requested_package") or "").strip()
    if item_type == "base_package" and package == "dog_trimming":
        trimming = [chunk for chunk in chunks if chunk_is_dog_trimming_package(chunk)]
        neutral = [chunk for chunk in chunks if chunk not in trimming and not chunk_is_shaving_addon(chunk)]
        addons = [chunk for chunk in chunks if chunk_is_shaving_addon(chunk)]
        return trimming + neutral + addons
    if item_type == "addon":
        addon_chunks = [chunk for chunk in chunks if chunk_is_shaving_addon(chunk)]
        other = [chunk for chunk in chunks if chunk not in addon_chunks]
        return addon_chunks + other
    return list(chunks)


def _normalize_price(raw: str) -> str:
    price = re.sub(r"\s+", "", str(raw or ""))
    if not price.upper().startswith("RM"):
        price = f"RM{price.replace('RM', '').replace('rm', '')}"
    return price


def extract_dog_trimming_prices(
    text: str,
    chunks: list[dict],
    *,
    pet_size: str = "",
    pet_height: str = "",
) -> list[str]:
    sections: list[str] = []
    for chunk in chunks:
        if chunk_is_dog_trimming_package(chunk):
            sections.append(str(chunk.get("text") or ""))
    combined = "\n".join(sections) if sections else str(text or "")
    if not re.search(r"dog trimming", combined, re.I):
        return []
    size_key = str(pet_size or "").strip().lower()
    if not size_key and pet_height:
        from booking_service_info import infer_pet_size_from_height

        size_key = infer_pet_size_from_height(pet_height) or ""
    scoped = combined
    if size_key and size_key in _SIZE_HINTS:
        segments = re.split(r"(?=(?:for\s+)?[xlsm]{1,2}\s+size|\bfor\s+[xlsm]{1,2}\b)", combined, flags=re.I)
        matched = [segment for segment in segments if _SIZE_HINTS[size_key].search(segment)]
        if matched:
            scoped = "\n".join(matched)
    variant_patterns = (
        (r"all\s+shave[^.\n]{0,40}?(RM\s*\d+(?:\.\d+)?|\d+\s*RM)", "All Shave"),
        (r"keep\s+head\s*(?:&|and)\s*tail[^.\n]{0,40}?(RM\s*\d+(?:\.\d+)?|\d+\s*RM)", "Keep Head & Tail"),
        (r"keep\s+head,\s*tail\s+and\s+legs[^.\n]{0,40}?(RM\s*\d+(?:\.\d+)?|\d+\s*RM)", "Keep Head, Tail and Legs"),
        (r"full\s+scissor(s)?[^.\n]{0,40}?(RM\s*\d+(?:\.\d+)?|\d+\s*RM)", "Full Scissor"),
    )
    lines: list[str] = []
    seen: set[str] = set()
    for pattern, label in variant_patterns:
        match = re.search(pattern, scoped, re.I)
        if not match:
            continue
        price = _normalize_price(match.group(1))
        if label not in seen:
            seen.add(label)
            lines.append(f"- {label}: {price}")
    if not lines and scoped != combined:
        return extract_dog_trimming_prices(combined, [], pet_size=pet_size, pet_height=pet_height)
    return lines


def rag_has_dog_trimming_package_price(text: str, chunks: list[dict]) -> bool:
    if extract_dog_trimming_prices(text, chunks):
        return True
    combined = (text + "\n").lower()
    for chunk in chunks:
        combined += str(chunk.get("text") or "").lower() + "\n"
    return bool(re.search(r"dog trimming[^.\n]{0,120}(RM\s*\d+|\d+\s*rm)", combined, re.I))
