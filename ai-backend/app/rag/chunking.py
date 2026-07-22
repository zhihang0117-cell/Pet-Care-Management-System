"""Production chunking wrapper — reuses experiment chunking output, adds document ownership."""

from __future__ import annotations

import re

from app.config import PRODUCTION_DOCUMENT_TYPES
from app.services.chunking import chunk_from_pages


def validate_document_type(document_type: str) -> str:
    normalized = str(document_type or "").strip().lower()
    if normalized not in PRODUCTION_DOCUMENT_TYPES:
        allowed = ", ".join(sorted(PRODUCTION_DOCUMENT_TYPES))
        raise ValueError(
            f"Unsupported document_type '{document_type}'. "
            f"Allowed business categories: {allowed}"
        )
    return normalized


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower())
    return text.strip("_") or "section"


def build_production_chunk_id(
    document_id: str,
    *,
    section_id: str = "",
    part_number: int = 1,
) -> str:
    """Deterministic production chunk id: document + section + part.

    Must not include temporary download filenames (e.g. tmp8z84uvor).
    """
    doc_slug = _slug(document_id)
    section_key = _slug(section_id) if str(section_id or "").strip() else "misc"
    if int(part_number) <= 1:
        return f"{doc_slug}__{section_key}"
    return f"{doc_slug}__{section_key}_{int(part_number)}"


def chunk_docx_pages(
    pages,
    *,
    company_id: int,
    document_id: str,
    document_type: str,
    service_type: str = "general",
) -> list[dict]:
    """Run existing section-aware chunking, then attach production ownership fields.

    Note: the shared experiment chunker still accepts a parameter named tenant_id.
    We pass company_id into that parameter without modifying services/chunking.py.
    Production chunk_ids are rebuilt from document_id + section_id + part number so
    they stay stable across temp download paths.
    """
    document_type = validate_document_type(document_type)
    service_type = str(service_type or "general").strip().lower()
    if service_type not in {"grooming", "boarding", "daycare", "general"}:
        raise ValueError("Unsupported service_type")
    raw_chunks = chunk_from_pages(pages, str(company_id), merge_originals=False)
    if not raw_chunks:
        raise ValueError("Chunking produced zero chunks from the document.")

    part_counters: dict[str, int] = {}
    production_chunks: list[dict] = []
    for chunk in raw_chunks:
        section_id = str(chunk.get("section_id") or "").strip()
        counter_key = section_id or "_misc"
        part_counters[counter_key] = part_counters.get(counter_key, 0) + 1
        part_number = part_counters[counter_key]

        prod_chunk_id = build_production_chunk_id(
            document_id,
            section_id=section_id,
            part_number=part_number,
        )
        enriched = dict(chunk)
        enriched["chunk_id"] = prod_chunk_id
        enriched["company_id"] = company_id
        enriched["document_id"] = document_id
        enriched["document_type"] = document_type
        enriched["dataset_type"] = document_type
        enriched["service_type"] = service_type
        production_chunks.append(enriched)

    return production_chunks
