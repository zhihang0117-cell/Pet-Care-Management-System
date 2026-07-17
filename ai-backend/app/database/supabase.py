"""Production Supabase vector store with document-scoped atomic replacement."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.documents import Document
from supabase import Client

from app.config import MODELS, PRODUCTION_MODEL_KEY
from app.rag.embeddings import get_production_embedder
from app.services.metadata_fields import chunk_dataset_type
from app.services.metadata_tagging import validate_metadata
from app.services.supabase_store import get_supabase_client

REPLACE_DOCUMENT_RPC = "replace_document_chunks_bge_large"
PRODUCTION_MATCH_RPC = "match_chunks_bge_large_production"


class ProductionVectorStore:
    """BGE-Large production store. Never deletes an entire company."""

    def __init__(self, client: Client, company_id: int):
        self.client = client
        self.company_id = int(company_id)
        self.embedder = get_production_embedder()
        cfg = MODELS[PRODUCTION_MODEL_KEY]
        self.model_key = PRODUCTION_MODEL_KEY
        self.table = cfg["table"]
        self.rpc = PRODUCTION_MATCH_RPC

    @classmethod
    def for_company(cls, company_id: int) -> ProductionVectorStore:
        return cls(client=get_supabase_client(), company_id=company_id)

    def _chunk_to_metadata(self, chunk: dict[str, Any]) -> dict[str, Any]:
        validated = validate_metadata(
            {
                "chunk_id": chunk["chunk_id"],
                "company_id": chunk.get("company_id", self.company_id),
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
            **validated,
            "company_id": int(chunk.get("company_id", self.company_id)),
            "document_id": str(chunk.get("document_id", "")),
            "document_type": str(chunk.get("document_type", "")),
            "document_file_name": chunk.get("document_file_name", chunk.get("source_file", "")),
            "language": chunk.get("language", ""),
            "section_id": chunk.get("section_id", ""),
            "section_title": chunk.get("section_title", ""),
            "chapter": chunk.get("chapter", ""),
            "category": chunk.get("category", ""),
            "is_rule": chunk.get("is_rule", True),
            "page": chunk.get("page", 0),
        }

    def _prepare_rows(self, chunks: list[dict], *, batch_size: int = 32) -> list[dict[str, Any]]:
        indexable = [c for c in chunks if str(c.get("text", "")).strip()]
        if not indexable:
            raise ValueError("No indexable chunks were produced for this document.")

        prepared: list[dict[str, Any]] = []
        for i in range(0, len(indexable), batch_size):
            batch = indexable[i : i + batch_size]
            texts = [c["text"] for c in batch]
            embeddings = self.embedder.embed_documents(texts)
            if len(embeddings) != len(batch):
                raise RuntimeError("Embedding count does not match chunk count.")

            for chunk, embedding in zip(batch, embeddings):
                if not embedding:
                    raise RuntimeError(
                        f"Empty embedding generated for chunk_id={chunk.get('chunk_id')}"
                    )
                metadata = self._chunk_to_metadata(chunk)
                prepared.append(
                    {
                        "company_id": self.company_id,
                        "document_id": str(chunk["document_id"]),
                        "chunk_id": str(chunk["chunk_id"]),
                        "content": chunk["text"],
                        "metadata": metadata,
                        "embedding": embedding,
                    }
                )
        return prepared

    def replace_document_chunks(self, document_id: str, chunks: list[dict]) -> int:
        """Prepare embeddings first, then atomically replace one document via RPC.

        Delete is always scoped to (company_id, document_id) inside the SQL RPC.
        If the RPC is missing, this method fails before any delete occurs.
        """
        document_id = str(document_id).strip()
        if not document_id:
            raise ValueError("document_id is required")

        prepared_rows = self._prepare_rows(chunks)
        payload = [
            {
                "chunk_id": row["chunk_id"],
                "content": row["content"],
                "metadata": row["metadata"],
                "embedding": row["embedding"],
            }
            for row in prepared_rows
        ]

        try:
            response = self.client.rpc(
                REPLACE_DOCUMENT_RPC,
                {
                    "p_company_id": self.company_id,
                    "p_document_id": document_id,
                    "p_chunks": payload,
                },
            ).execute()
        except Exception as exc:
            message = str(exc)
            if "replace_document_chunks_bge_large" in message or "PGRST202" in message:
                raise RuntimeError(
                    "Production document replacement RPC is not available. "
                    "Apply supabase/migrations/002_add_document_id_to_chunks_bge_large.sql "
                    "manually in Supabase before using production indexing. "
                    "Until then, the service will not delete existing document chunks."
                ) from exc
            if "document_id" in message and "column" in message.lower():
                raise RuntimeError(
                    "The chunks_bge_large.document_id column is missing. "
                    "Apply supabase/migrations/002_add_document_id_to_chunks_bge_large.sql "
                    "manually before using production indexing."
                ) from exc
            if "company_id" in message and "column" in message.lower():
                raise RuntimeError(
                    "The chunks_bge_large.company_id column is missing or the replacement "
                    "RPC is out of date. Re-apply the reviewed migration SQL."
                ) from exc
            raise

        inserted = response.data
        if isinstance(inserted, int):
            return inserted
        if inserted is None:
            return len(prepared_rows)
        return int(inserted)

    def similarity_search(
        self,
        query: str,
        k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[Document]:
        """Company-scoped retrieval.

        Production uses a bigint RPC (`match_chunks_bge_large_production`).
        The legacy `match_chunks_bge_large(filter_tenant text, ...)` remains
        available for experiment callers.
        """
        filt = dict(filter or {})
        filt.pop("company_id", None)
        filt.pop("tenant_id", None)
        metadata_filter = {key: value for key, value in filt.items()}

        query_embedding = self.embedder.embed_query(query)
        response = self.client.rpc(
            self.rpc,
            {
                "query_embedding": query_embedding,
                "match_count": k,
                "p_company_id": self.company_id,
                "filter_metadata": metadata_filter,
            },
        ).execute()

        documents: list[Document] = []
        for row in response.data or []:
            metadata = row.get("metadata") or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            metadata = dict(metadata)
            metadata["similarity"] = row.get("similarity")
            metadata.setdefault("chunk_id", row.get("chunk_id", ""))
            # Prefer the dedicated table column over metadata jsonb.
            column_document_id = str(row.get("document_id") or "").strip()
            if column_document_id:
                metadata["document_id"] = column_document_id
            else:
                metadata.setdefault("document_id", "")
            documents.append(
                Document(
                    page_content=row.get("content", ""),
                    metadata=metadata,
                )
            )
        return documents
