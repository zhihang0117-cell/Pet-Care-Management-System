"""Shared metadata field accessors for chunks and eval rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.metadata_schema import (
    CHUNK_DATASET_FIELDS,
    EVAL_DATASET_FIELDS,
    EVAL_PET_FIELDS,
    EVAL_QUERY_TEXT_FIELDS,
    EVAL_SERVICE_FIELDS,
    EVAL_SERVICE_INFO_FIELDS,
)
from app.services.metadata_tagging import (
    infer_expected_pet_type_from_query,
    normalize_pet_type,
    normalize_service_type,
)


def _record(record: dict[str, Any]) -> dict[str, Any]:
    nested = record.get("metadata")
    if isinstance(nested, dict):
        return nested
    return record


def _first_nonempty(record: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = str(record.get(field, "")).strip()
        if value:
            return value
    return ""


def chunk_dataset_type(chunk: dict[str, Any]) -> str:
    return _first_nonempty(_record(chunk), CHUNK_DATASET_FIELDS)


def chunk_pet_type(chunk: dict[str, Any]) -> str:
    return normalize_pet_type(str(_record(chunk).get("pet_type", "all")))


def chunk_service_type(chunk: dict[str, Any]) -> str:
    return normalize_service_type(str(_record(chunk).get("service_type", "general")))


def chunk_service_info(chunk: dict[str, Any]) -> str:
    return str(_record(chunk).get("service_info", "")).strip()


def eval_query_text(eval_row: dict[str, Any]) -> str:
    return _first_nonempty(eval_row, EVAL_QUERY_TEXT_FIELDS)


def eval_dataset_type(eval_row: dict[str, Any]) -> str:
    return _first_nonempty(eval_row, EVAL_DATASET_FIELDS)


def eval_service_type(eval_row: dict[str, Any]) -> str:
    for field in EVAL_SERVICE_FIELDS:
        value = str(eval_row.get(field, "")).strip()
        if value:
            return normalize_service_type(value)
    return "general"


def eval_expected_pet_type(eval_row: dict[str, Any]) -> str:
    for field in EVAL_PET_FIELDS:
        value = str(eval_row.get(field, "")).strip()
        if value:
            return normalize_pet_type(value)
    return infer_expected_pet_type_from_query(
        eval_query_text(eval_row),
        service_type=eval_service_type(eval_row),
    )


def eval_service_info(eval_row: dict[str, Any]) -> str:
    return _first_nonempty(eval_row, EVAL_SERVICE_INFO_FIELDS)


def _normalize_dataset_stem(name: str) -> str:
    """Normalize file stems so 'Service Information' matches 'service_information'."""
    stem = Path(name).stem.lower()
    for char in (" ", "-", "_"):
        stem = stem.replace(char, "_")
    while "__" in stem:
        stem = stem.replace("__", "_")
    return stem.strip("_")


def dataset_names_match(expected: str, actual: str) -> bool:
    if not expected or not actual:
        return not expected
    if expected == actual or expected.lower() == actual.lower():
        return True
    return _normalize_dataset_stem(expected) == _normalize_dataset_stem(actual)


def scope_chunks_by_dataset(chunks: list[dict[str, Any]], dataset_type: str) -> list[dict[str, Any]]:
    if not dataset_type:
        return chunks
    return [c for c in chunks if dataset_names_match(dataset_type, chunk_dataset_type(c))]
