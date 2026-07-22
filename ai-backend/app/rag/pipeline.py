"""Production orchestration entrypoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import MODELS, PRODUCTION_MODEL_KEY
from app.database.supabase import ProductionVectorStore
from app.rag.chunking import chunk_docx_pages, validate_document_type
from app.rag.ingestion import load_docx_pages
from app.rag.retrieval import retrieve_production_chunks
from app.rag.storage import download_storage_docx


def process_document(
    local_file_path: str | Path,
    company_id: int,
    document_id: str,
    document_type: str,
    service_type: str = "general",
) -> dict[str, Any]:
    """DOCX → chunking → metadata → BGE-Large embeddings → document-scoped Supabase replace."""
    path = Path(local_file_path)
    company_id = int(company_id)
    document_id = str(document_id).strip()
    document_type = validate_document_type(document_type)

    if company_id <= 0:
        raise ValueError("company_id is required")
    if not document_id:
        raise ValueError("document_id is required")

    pages = load_docx_pages(path)
    chunks = chunk_docx_pages(
        pages,
        company_id=company_id,
        document_id=document_id,
        document_type=document_type,
        service_type=service_type,
    )

    store = ProductionVectorStore.for_company(company_id)
    indexed_count = store.replace_document_chunks(document_id, chunks)

    return {
        "company_id": company_id,
        "document_id": document_id,
        "document_type": document_type,
        "service_type": service_type,
        "chunks_indexed": indexed_count,
        "chunk_ids": [str(c["chunk_id"]) for c in chunks],
        "model_key": PRODUCTION_MODEL_KEY,
        "table": MODELS[PRODUCTION_MODEL_KEY]["table"],
        "status": "indexed",
    }


def process_document_from_storage(
    *,
    company_id: int,
    document_id: str,
    document_type: str,
    storage_bucket: str,
    storage_path: str,
    service_type: str = "general",
) -> dict[str, Any]:
    temp_path = download_storage_docx(storage_bucket, storage_path)
    try:
        return process_document(
            temp_path,
            company_id=company_id,
            document_id=document_id,
            document_type=document_type,
            service_type=service_type,
        )
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def retrieve_chunks(
    query: str,
    company_id: int,
    top_k: int = 5,
    service_type: str | None = None,
    pet_type: str | None = None,
) -> dict[str, Any]:
    result = retrieve_production_chunks(
        query,
        company_id,
        top_k=top_k,
        service_type=service_type,
        pet_type=pet_type,
    )

    chunks = []
    for hit in result.get("hits", []):
        metadata = {
            key: value
            for key, value in hit.items()
            if key
            not in {
                "rank",
                "chunk_id",
                "document_id",
                "similarity",
                "text",
                "text_preview",
            }
        }
        # Prefer table-backed document_id on the hit (from production RPC column).
        document_id = str(hit.get("document_id") or metadata.get("document_id") or "")
        chunks.append(
            {
                "rank": int(hit.get("rank", 0)),
                "chunk_id": str(hit.get("chunk_id", "")),
                "document_id": document_id,
                "score": float(hit.get("similarity") or 0.0),
                "content": str(hit.get("text", "")),
                "metadata": metadata,
            }
        )

    return {
        "company_id": company_id,
        "query": query,
        "top_k": top_k,
        "filter_mode": str(result.get("filter_mode", "tenant_only")),
        "low_confidence": bool(result.get("low_confidence")),
        "chunks": chunks,
    }
