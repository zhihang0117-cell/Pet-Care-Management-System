"""
Atomic per-document chunk replacement in the BGE-Large production index
(chunks_bge_large). Ported from the retired ai-backend/app/database/supabase.py
+ app/rag/pipeline.py (pre-restructure commit 2d5ecb4^), adapted to call the
already-existing app/db/supabase_client.py and app/rag/embeddings.py instead
of a second, parallel Supabase-client/embedder implementation.

Requires the replace_document_chunks_bge_large RPC and the chunks_bge_large
.document_id column — see backend/sql/002_add_document_id_to_chunks_bge_large.sql.
Until that migration is applied in Supabase, replace_document_chunks() raises
a clear RuntimeError instead of silently deleting/skipping anything.
"""

from __future__ import annotations

from typing import Any

from app.db.supabase_client import get_supabase_client
from app.documents.chunking import chunk_docx_pages
from app.documents.ingestion import download_storage_docx, load_docx_pages, validate_document_type
from app.documents.metadata_tagging import validate_metadata
from app.rag.embeddings import embed_documents

TABLE = "chunks_bge_large"
REPLACE_DOCUMENT_RPC = "replace_document_chunks_bge_large"


def _chunk_to_metadata(chunk: dict[str, Any], company_id: int) -> dict[str, Any]:
    validated = validate_metadata(
        {
            "chunk_id": chunk["chunk_id"],
            "dataset_type": chunk.get("dataset_type", ""),
            "main_header": chunk.get("main_header", ""),
            "sub_header": chunk.get("sub_header", ""),
            "section_path": chunk.get("section_path", ""),
            "pet_type": chunk.get("pet_type", "all"),
            "service_type": chunk.get("service_type", "general"),
            "service_info": chunk.get("service_info", ""),
        }
    )
    return {
        **validated,
        "company_id": int(chunk.get("company_id", company_id)),
        "document_id": str(chunk.get("document_id", "")),
        "document_type": str(chunk.get("document_type", "")),
        "document_file_name": chunk.get("document_file_name", ""),
        "language": chunk.get("language", ""),
        "section_id": chunk.get("section_id", ""),
        "section_title": chunk.get("section_title", ""),
        "chapter": chunk.get("chapter", ""),
        "category": chunk.get("category", ""),
        "is_rule": chunk.get("is_rule", True),
        "page": chunk.get("page", 0),
    }


def _prepare_rows(chunks: list[dict], company_id: int, *, batch_size: int = 32) -> list[dict[str, Any]]:
    indexable = [c for c in chunks if str(c.get("text", "")).strip()]
    if not indexable:
        raise ValueError("No indexable chunks were produced for this document.")

    prepared: list[dict[str, Any]] = []
    for i in range(0, len(indexable), batch_size):
        batch = indexable[i : i + batch_size]
        embeddings = embed_documents([c["text"] for c in batch])
        if len(embeddings) != len(batch):
            raise RuntimeError("Embedding count does not match chunk count.")

        for chunk, embedding in zip(batch, embeddings):
            if not embedding:
                raise RuntimeError(f"Empty embedding generated for chunk_id={chunk.get('chunk_id')}")
            prepared.append(
                {
                    "chunk_id": str(chunk["chunk_id"]),
                    "content": chunk["text"],
                    "metadata": _chunk_to_metadata(chunk, company_id),
                    "embedding": embedding,
                }
            )
    return prepared


def replace_document_chunks(company_id: int, document_id: str, chunks: list[dict]) -> int:
    """Prepare embeddings first, then atomically replace one document's chunks via RPC.

    Delete is scoped to (company_id, document_id) inside the SQL RPC itself —
    if the RPC is missing this raises before any delete happens.
    """
    document_id = str(document_id).strip()
    if not document_id:
        raise ValueError("document_id is required")

    prepared_rows = _prepare_rows(chunks, company_id)
    client = get_supabase_client()

    try:
        response = client.rpc(
            REPLACE_DOCUMENT_RPC,
            {"p_company_id": int(company_id), "p_document_id": document_id, "p_chunks": prepared_rows},
        ).execute()
    except Exception as exc:
        message = str(exc)
        if REPLACE_DOCUMENT_RPC in message or "PGRST202" in message or (
            "document_id" in message and "column" in message.lower()
        ):
            raise RuntimeError(
                "Production document replacement RPC/column is not available. Apply "
                "backend/sql/002_add_document_id_to_chunks_bge_large.sql manually in the "
                "Supabase SQL editor before using document indexing."
            ) from exc
        raise

    inserted = response.data
    if isinstance(inserted, int):
        return inserted
    if inserted is None:
        return len(prepared_rows)
    return int(inserted)


def process_document(
    local_file_path,
    *,
    company_id: int,
    document_id: str,
    document_type: str,
    service_type: str = "general",
) -> dict[str, Any]:
    """DOCX -> section-aware chunking -> tagging -> BGE-Large embeddings -> atomic replace."""
    company_id = int(company_id)
    document_id = str(document_id).strip()
    document_type = validate_document_type(document_type)
    if company_id <= 0:
        raise ValueError("company_id is required")
    if not document_id:
        raise ValueError("document_id is required")

    pages = load_docx_pages(local_file_path)
    chunks = chunk_docx_pages(
        pages, company_id=company_id, document_id=document_id, document_type=document_type, service_type=service_type
    )
    indexed_count = replace_document_chunks(company_id, document_id, chunks)

    return {
        "company_id": company_id,
        "document_id": document_id,
        "document_type": document_type,
        "service_type": service_type,
        "chunks_indexed": indexed_count,
        "chunk_ids": [str(c["chunk_id"]) for c in chunks],
        "table": TABLE,
        "status": "indexed",
    }


def process_document_from_storage(
    *, company_id: int, document_id: str, document_type: str, storage_bucket: str, storage_path: str,
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
