"""Build eval queries from structured CSV rows and metadata-aligned ground truth."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from app.config import DATA_EVAL, DATA_PROCESSED
from app.services.eval_matching import find_relevant_chunk_ids
from app.services.metadata_fields import (
    chunk_dataset_type,
    eval_query_text,
    scope_chunks_by_dataset,
)
from app.services.metadata_schema import EVAL_CSV_COLUMN_ALIASES, EVAL_ROW_FIELDS
from app.services.metadata_tagging import (
    infer_expected_pet_type_from_query,
    normalize_pet_type,
    normalize_service_type,
    validate_metadata,
)

QUERIES_INPUT = DATA_EVAL / "queries.json"
QUERIES_JSONL = DATA_EVAL / "queries.jsonl"
QUERIES_CSV = DATA_EVAL / "queries.csv"


def _coerce_csv_row(row: dict[str, Any]) -> dict[str, Any]:
    coerced = dict(row)
    for src, dest in EVAL_CSV_COLUMN_ALIASES.items():
        value = str(coerced.get(src, "")).strip()
        if value and not str(coerced.get(dest, "")).strip():
            coerced[dest] = value

    eval_id = str(coerced.get("eval_id", "")).strip()
    if eval_id and not str(coerced.get("query_id", "")).strip():
        coerced["query_id"] = eval_id
    return coerced


def eval_queries_ready() -> bool:
    return QUERIES_JSONL.exists()


def _normalize_eval_row(row: dict[str, Any], index: int) -> dict[str, Any]:
    row = _coerce_csv_row(row)
    normalized = {key: str(row.get(key, "")).strip() for key in EVAL_ROW_FIELDS}
    query = eval_query_text(row) or eval_query_text(normalized)
    if not query:
        raise ValueError(f"Row {index}: missing user_message/query text.")

    eval_id = normalized.get("eval_id") or str(row.get("query_id", "")).strip()
    normalized["eval_id"] = eval_id
    normalized["query_id"] = eval_id or f"Q{index:03d}"
    normalized["user_message"] = query
    normalized["query"] = query
    normalized["language"] = str(row.get("language", "unknown")).strip() or "unknown"
    normalized["category"] = str(row.get("category", "")).strip()
    normalized["dataset_type"] = normalized.get("dataset_type", "")
    normalized["service_type"] = normalize_service_type(normalized.get("service_type", ""))

    explicit_pet = str(row.get("expected_pet_type", "") or row.get("pet_type", "")).strip()
    normalized["expected_pet_type"] = (
        normalize_pet_type(explicit_pet)
        if explicit_pet
        else infer_expected_pet_type_from_query(query, service_type=normalized["service_type"])
    )
    service_info = str(
        row.get("service_info") or row.get("expected_service_info") or normalized.get("service_info", "")
    ).strip()
    normalized["service_info"] = validate_metadata({"service_info": service_info})["service_info"] if service_info else ""
    return normalized


def save_eval_rows(rows: list[dict[str, Any]], source_pdf: str = "") -> list[dict[str, Any]]:
    if not rows:
        raise ValueError("At least one eval row is required.")

    cleaned = [_normalize_eval_row(row, i) for i, row in enumerate(rows, start=1)]
    DATA_EVAL.mkdir(parents=True, exist_ok=True)
    QUERIES_INPUT.write_text(
        json.dumps({"rows": cleaned, "dataset_type": source_pdf or ""}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return cleaned


def save_text_queries(queries: list[str], source_pdf: str = "") -> list[str]:
    rows = [{"user_message": q.strip()} for q in queries if q.strip()]
    if not rows:
        raise ValueError("At least one non-empty query is required.")
    saved_rows = save_eval_rows(rows, source_pdf=source_pdf)
    return [row["query"] for row in saved_rows]


def parse_eval_csv_text(csv_text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise ValueError("CSV is missing a header row.")

    rows: list[dict[str, Any]] = []
    for row in reader:
        if not any(str(v).strip() for v in row.values()):
            continue
        rows.append(dict(row))
    if not rows:
        raise ValueError("No eval rows found in CSV.")
    return rows


def save_eval_csv(csv_text: str, source_pdf: str = "") -> list[dict[str, Any]]:
    if QUERIES_JSONL.exists():
        QUERIES_JSONL.unlink()
    rows = parse_eval_csv_text(csv_text)
    saved = save_eval_rows(rows, source_pdf=source_pdf)
    QUERIES_CSV.write_text(csv_text, encoding="utf-8")
    return saved


def _merge_saved_rows_with_csv(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-apply CSV column aliases when reloading saved rows."""
    if not QUERIES_CSV.exists():
        return rows

    csv_by_id: dict[str, dict[str, Any]] = {}
    for raw in parse_eval_csv_text(QUERIES_CSV.read_text(encoding="utf-8")):
        coerced = _coerce_csv_row(raw)
        eval_id = str(coerced.get("eval_id", "")).strip()
        if eval_id:
            csv_by_id[eval_id] = coerced

    merged: list[dict[str, Any]] = []
    for row in rows:
        eval_id = str(row.get("eval_id") or row.get("query_id", "")).strip()
        combined = {**row, **csv_by_id.get(eval_id, {})}
        merged.append(combined)
    return merged


def load_eval_rows() -> tuple[list[dict[str, Any]], str]:
    if not QUERIES_INPUT.exists():
        raise FileNotFoundError("No eval queries saved. Upload an eval CSV first.")

    data = json.loads(QUERIES_INPUT.read_text(encoding="utf-8"))
    default_dataset = str(data.get("dataset_type") or data.get("source_pdf", ""))

    if isinstance(data.get("rows"), list) and data["rows"]:
        rows = _merge_saved_rows_with_csv(data["rows"])
        rows = [_normalize_eval_row(row, i) for i, row in enumerate(rows, start=1)]
        return rows, default_dataset

    legacy_queries = [q.strip() for q in data.get("queries", []) if str(q).strip()]
    if not legacy_queries:
        raise ValueError("No eval rows found in saved eval set.")
    rows = [_normalize_eval_row({"user_message": q}, i) for i, q in enumerate(legacy_queries, start=1)]
    return rows, default_dataset


def load_text_queries() -> tuple[list[str], str]:
    rows, dataset_type = load_eval_rows()
    return [row["query"] for row in rows], dataset_type


def list_source_files() -> list[str]:
    chunks_path = DATA_PROCESSED / "chunks.json"
    if not chunks_path.exists():
        return []
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    names = {chunk_dataset_type(c) for c in chunks if chunk_dataset_type(c)}
    return sorted(names)


def prepare_eval_set(
    chunks_path: Path | None = None,
    output_path: Path | None = None,
) -> list[dict]:
    chunks_path = chunks_path or DATA_PROCESSED / "chunks.json"
    output_path = output_path or QUERIES_JSONL

    if not chunks_path.exists():
        raise FileNotFoundError("No chunks found. Upload Word documents first.")

    eval_rows, default_dataset = load_eval_rows()
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))

    prepared_rows: list[dict[str, Any]] = []
    no_matches: list[str] = []

    for row in eval_rows:
        row_dataset = row.get("dataset_type") or default_dataset
        scoped_chunks = scope_chunks_by_dataset(chunks, row_dataset)
        relevant_chunk_ids = find_relevant_chunk_ids(scoped_chunks, row)
        prepared = {
            **row,
            "dataset_type": row_dataset,
            "relevant_chunk_ids": relevant_chunk_ids,
            "match_method": "metadata" if relevant_chunk_ids else "none",
            "match_score": 1.0 if relevant_chunk_ids else 0.0,
        }
        prepared_rows.append(prepared)
        if not relevant_chunk_ids:
            no_matches.append(row["query"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in prepared_rows) + "\n",
        encoding="utf-8",
    )
    print(f"Prepared {len(prepared_rows)} eval queries -> {output_path}")
    if no_matches:
        print(f"Warning: no metadata-matched chunks for {len(no_matches)} query(s)")
    return prepared_rows


def _chunk_preview(chunk: dict) -> dict:
    text = chunk.get("text", "")
    metadata = validate_metadata(
        {
            "chunk_id": chunk.get("chunk_id", ""),
            "tenant_id": chunk.get("tenant_id", ""),
            "dataset_type": chunk_dataset_type(chunk),
            "main_header": chunk.get("main_header", ""),
            "sub_header": chunk.get("sub_header", ""),
            "section_path": chunk.get("section_path", ""),
            "pet_type": chunk.get("pet_type", "all"),
            "service_type": chunk.get("service_type", "general"),
            "service_info": chunk.get("service_info", ""),
        }
    )
    return {
        "chunk_id": metadata["chunk_id"],
        "dataset_type": metadata["dataset_type"],
        "document_file_name": metadata["dataset_type"],
        "section_path": metadata["section_path"],
        "section_id": chunk.get("section_id", ""),
        "section_title": chunk.get("section_title", ""),
        "main_header": metadata["main_header"],
        "pet_type": metadata["pet_type"],
        "service_type": metadata["service_type"],
        "service_info": metadata["service_info"],
        "text_preview": chunk.get("text_preview") or text[:180].replace("\n", " "),
        "text": text,
        "char_count": len(text),
    }


def load_eval_query_by_id(query_id: str) -> dict[str, Any] | None:
    """Load a single eval query row from queries.jsonl by query_id / eval_id."""
    if not QUERIES_JSONL.exists():
        return None
    target = query_id.strip()
    for line in QUERIES_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rid = str(row.get("query_id") or row.get("eval_id") or "").strip()
        if rid == target:
            return row
    return None


def load_prepared_eval(chunks_path: Path | None = None) -> list[dict]:
    """Load prepared eval rows for UI / API.

    Streams queries.jsonl and omits heavy matched chunk payloads so 10k-row
    sets fit in memory. Retrieval still uses relevant_chunk_ids on disk.
    """
    if not QUERIES_JSONL.exists():
        return []

    rows: list[dict] = []
    with QUERIES_JSONL.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            chunk_ids = row.get("relevant_chunk_ids") or []
            rows.append(
                {
                    "query_id": row.get("query_id", ""),
                    "query": row.get("query", row.get("user_message", "")),
                    "dataset_type": row.get("dataset_type", ""),
                    "service_type": row.get("service_type", ""),
                    "service_info": row.get("service_info", ""),
                    "expected_pet_type": row.get("expected_pet_type", ""),
                    "match_method": row.get("match_method", "none"),
                    "match_score": float(row.get("match_score", 0.0)),
                    "relevant_chunk_ids": chunk_ids,
                    "matched_chunks": [],
                }
            )
    return rows
