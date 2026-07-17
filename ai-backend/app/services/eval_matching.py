"""Metadata matching for eval ground truth and retrieval post-filters.

Ground-truth helpers (is_chunk_relevant / find_relevant_chunk_ids) are eval-only:
they score Precision/Recall/Hit and must never drive live retrieval or responses.
Retrieval post-filter uses service_infos_match / service_type on embedding hits only.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from app.services.metadata_fields import (
    chunk_dataset_type,
    chunk_pet_type,
    chunk_service_info,
    chunk_service_type,
    dataset_names_match,
    eval_dataset_type,
    eval_expected_pet_type,
    eval_service_info,
    eval_service_type,
)

FilterMode = str

_SERVICE_INFO_STOPWORDS = frozenset(
    {"service", "information", "policy", "requirements", "the", "and", "for", "a", "an", "program"}
)
_GROOMING_HEALTH_SAFETY = (
    "health",
    "vaccination",
    "behavior",
    "safety",
    "flea",
    "tick",
    "matting",
    "sanitation",
    "eligibility",
)


def _normalize_service_info_label(text: str) -> str:
    normalized = (text or "").lower().replace("&", "and")
    return re.sub(r"\s+", " ", normalized).strip()


def _service_info_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _normalize_service_info_label(text))) - _SERVICE_INFO_STOPWORDS


def service_infos_match(expected: str, chunk: dict[str, Any]) -> bool:
    """Match eval expected_service_info against chunk section labels."""
    if not expected.strip():
        return True

    expected_norm = _normalize_service_info_label(expected)
    service_info = _normalize_service_info_label(chunk_service_info(chunk))
    main_header = _normalize_service_info_label(str(chunk.get("main_header", "")))
    sub_header = _normalize_service_info_label(str(chunk.get("sub_header", "")))
    chunk_id = str(chunk.get("chunk_id", ""))
    pet_type = str(chunk.get("pet_type", "all"))

    if expected_norm == service_info or expected_norm in service_info or service_info in expected_norm:
        return True

    if "grooming policy" in expected_norm:
        return main_header == "grooming service" or chunk_id.startswith("policies_3")

    if "grooming health" in expected_norm or ("health" in expected_norm and "safety" in expected_norm):
        if main_header == "grooming service" or chunk_id.startswith("policies_3"):
            return any(token in service_info for token in _GROOMING_HEALTH_SAFETY)
        return "pet health" in service_info

    if "cat grooming" in expected_norm:
        return "cat" in service_info or pet_type == "cat"

    if "dog grooming" in expected_norm:
        return "dog" in service_info or pet_type == "dog"

    if "cancellation" in expected_norm or "cancel" in expected_norm:
        labels = " ".join((service_info, main_header, sub_header))
        return any(
            token in labels
            for token in ("cancellation", "cancel", "refund", "reschedule", "no-show", "no show")
        )

    if any(token in expected_norm for token in ("loyalty", "membership", "points")):
        labels = " ".join((service_info, main_header, sub_header))
        if "loyalty" in expected_norm or "points" in expected_norm or "membership" in expected_norm:
            return any(token in labels for token in ("loyalty", "points", "membership"))

    core_tokens = _service_info_tokens(expected)
    if not core_tokens:
        return True
    label_tokens = _service_info_tokens(" ".join((service_info, main_header, sub_header)))
    return core_tokens <= label_tokens


def _dataset_type_match(chunk: dict[str, Any], eval_row: dict[str, Any]) -> bool:
    expected = eval_dataset_type(eval_row)
    if not expected:
        return True
    return dataset_names_match(expected, chunk_dataset_type(chunk))


def _service_type_match(chunk: dict[str, Any], eval_row: dict[str, Any]) -> bool:
    expected = eval_service_type(eval_row)
    if expected in ("", "unknown"):
        return True
    return chunk_service_type(chunk) == expected


def _pet_type_match(chunk: dict[str, Any], eval_row: dict[str, Any]) -> bool:
    expected = eval_expected_pet_type(eval_row)
    if expected == "all":
        return True
    chunk_pet = chunk_pet_type(chunk)
    if chunk_pet == "all":
        return True
    return chunk_pet == expected


def _service_info_match(chunk: dict[str, Any], eval_row: dict[str, Any]) -> bool:
    return service_infos_match(eval_service_info(eval_row), chunk)


def is_chunk_relevant(chunk: dict[str, Any], eval_row: dict[str, Any]) -> bool:
    """Eval-only ground-truth label (dataset + service + service_info + pet).

    Used only to score Precision/Recall/Hit — never to filter, rank, or generate
    live retrieval responses. Retrieval uses tenant → embed → metadata fallback.
    """
    return (
        _dataset_type_match(chunk, eval_row)
        and _service_type_match(chunk, eval_row)
        and _service_info_match(chunk, eval_row)
        and _pet_type_match(chunk, eval_row)
    )


def find_relevant_chunk_ids(chunks: list[dict[str, Any]], eval_row: dict[str, Any]) -> list[str]:
    return [chunk["chunk_id"] for chunk in chunks if is_chunk_relevant(chunk, eval_row)]

def _filter_mode_predicates(
    eval_row: dict[str, Any],
) -> list[tuple[FilterMode, Callable[[dict[str, Any]], bool]]]:
    apply_pet = eval_expected_pet_type(eval_row) in {"dog", "cat"}
    apply_service_info = bool(eval_service_info(eval_row).strip())

    modes: list[tuple[FilterMode, Callable[[dict[str, Any]], bool]]] = []

    if apply_service_info:
        modes.extend(
            [
                (
                    "dataset_type+service_type+service_info+expected_pet_type",
                    lambda c: _dataset_type_match(c, eval_row)
                    and _service_type_match(c, eval_row)
                    and _service_info_match(c, eval_row)
                    and _pet_type_match(c, eval_row),
                ),
                (
                    "dataset_type+service_type+service_info",
                    lambda c: _dataset_type_match(c, eval_row)
                    and _service_type_match(c, eval_row)
                    and _service_info_match(c, eval_row),
                ),
            ]
        )

    modes.extend(
        [
            (
                "dataset_type+service_type+expected_pet_type",
                lambda c: _dataset_type_match(c, eval_row)
                and _service_type_match(c, eval_row)
                and _pet_type_match(c, eval_row),
            ),
            (
                "dataset_type+service_type",
                lambda c: _dataset_type_match(c, eval_row) and _service_type_match(c, eval_row),
            ),
            ("dataset_type_only", lambda c: _dataset_type_match(c, eval_row)),
            (
                "service_type+expected_pet_type",
                lambda c: _service_type_match(c, eval_row) and _pet_type_match(c, eval_row),
            ),
            ("service_type_only", lambda c: _service_type_match(c, eval_row)),
            ("none", lambda c: True),
        ]
    )

    if not apply_pet:
        modes = [
            m
            for m in modes
            if m[0]
            not in (
                "dataset_type+service_type+service_info+expected_pet_type",
                "dataset_type+service_type+expected_pet_type",
                "service_type+expected_pet_type",
            )
        ]

    return modes


def filter_chunks_with_fallback(
    chunks: list[dict[str, Any]],
    eval_row: dict[str, Any],
    *,
    min_results: int = 3,
    final_top_k: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    total_chunks = len(chunks)
    after_dataset = [c for c in chunks if _dataset_type_match(c, eval_row)]
    after_service = [c for c in after_dataset if _service_type_match(c, eval_row)]
    after_service_info = [c for c in after_service if _service_info_match(c, eval_row)]
    after_pet = [c for c in after_service_info if _pet_type_match(c, eval_row)]

    selected_mode = "none"
    filtered = chunks

    for mode, predicate in _filter_mode_predicates(eval_row):
        candidate = [c for c in chunks if predicate(c)]
        if len(candidate) >= min_results or mode == "none":
            selected_mode = mode
            filtered = candidate
            break

    if final_top_k is not None:
        filtered = filtered[:final_top_k]

    return filtered, {
        "total_chunks": total_chunks,
        "after_dataset_filter": len(after_dataset),
        "after_service_filter": len(after_service),
        "after_service_info_filter": len(after_service_info),
        "after_pet_filter": len(after_pet),
        "filter_mode": selected_mode,
        "final_top_k": len(filtered),
        "eval_dataset_type": eval_dataset_type(eval_row),
        "eval_service_type": eval_service_type(eval_row),
        "eval_service_info": eval_service_info(eval_row),
        "eval_expected_pet_type": eval_expected_pet_type(eval_row),
    }

