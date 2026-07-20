"""Supabase pgvector store with tenant-filtered similarity search."""

from __future__ import annotations

import json
import threading
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from supabase import Client, create_client

from app.config import MODELS, settings
from app.services.embeddings import get_embedder
from app.services.metadata_fields import chunk_dataset_type
from app.services.metadata_tagging import validate_metadata

_supabase_client: Client | None = None
_supabase_lock = threading.Lock()

_store_cache: dict[tuple[str, str], SupabaseVectorStore] = {}
_store_lock = threading.Lock()


def get_supabase_client() -> Client:
    """Return a process-wide cached Supabase client."""
    global _supabase_client
    if not settings.supabase_url or not settings.supabase_service_key:
        raise EnvironmentError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY are required. "
            "Copy backend/.env.example to backend/.env and fill in your Supabase credentials."
        )
    if _supabase_client is not None:
        return _supabase_client
    with _supabase_lock:
        if _supabase_client is None:
            _supabase_client = create_client(settings.supabase_url, settings.supabase_service_key)
        return _supabase_client


def reset_supabase_client() -> Client:
    """Drop cached client/stores so long eval runs can open a fresh HTTP/2 connection."""
    global _supabase_client
    with _supabase_lock:
        _supabase_client = None
    with _store_lock:
        _store_cache.clear()
    return get_supabase_client()


class SupabaseVectorStore:
    """LangChain-compatible vector store backed by Supabase pgvector."""

    def __init__(
        self,
        client: Client,
        embedder: Embeddings,
        model_key: str,
        tenant_id: str,
    ):
        self.client = client
        self.embedder = embedder
        self.model_key = model_key
        self.tenant_id = tenant_id
        cfg = MODELS[model_key]
        self.table = cfg["table"]
        self.rpc = cfg["rpc"]
        self._cached_count: int | None = None

    @classmethod
    def from_model(cls, model_key: str, tenant_id: str) -> SupabaseVectorStore:
        """Reuse one store (and its cached embedder) per model+tenant."""
        cache_key = (model_key, tenant_id)
        cached = _store_cache.get(cache_key)
        if cached is not None:
            return cached
        with _store_lock:
            cached = _store_cache.get(cache_key)
            if cached is not None:
                return cached
            store = cls(
                client=get_supabase_client(),
                embedder=get_embedder(model_key),
                model_key=model_key,
                tenant_id=tenant_id,
            )
            _store_cache[cache_key] = store
            return store

    def _chunk_to_metadata(self, chunk: dict) -> dict[str, Any]:
        validated = validate_metadata(
            {
                "chunk_id": chunk["chunk_id"],
                "tenant_id": chunk["tenant_id"],
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
            "document_file_name": chunk.get("document_file_name", chunk.get("source_file", "")),
            "language": chunk.get("language", ""),
            "section_id": chunk.get("section_id", ""),
            "section_title": chunk.get("section_title", ""),
            "chapter": chunk.get("chapter", ""),
            "category": chunk.get("category", ""),
            "is_rule": chunk.get("is_rule", True),
            "page": chunk.get("page", 0),
        }

    def delete_tenant_chunks(self) -> None:
        self.client.table(self.table).delete().eq("tenant_id", self.tenant_id).execute()
        self._cached_count = 0

    def upsert_chunks(self, chunks: list[dict], batch_size: int = 32) -> int:
        indexable = [c for c in chunks if c.get("text", "").strip()]
        if not indexable:
            raise ValueError(
                "No indexable chunks found. Re-upload your Word document — chunks may have been filtered incorrectly."
            )

        self.delete_tenant_chunks()
        total = 0

        for i in range(0, len(indexable), batch_size):
            batch = indexable[i : i + batch_size]
            texts = [c["text"] for c in batch]
            embeddings = self.embedder.embed_documents(texts)
            rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
            for chunk, embedding in zip(batch, embeddings):
                key = (self.tenant_id, chunk["chunk_id"])
                rows_by_key[key] = {
                    "tenant_id": self.tenant_id,
                    "chunk_id": chunk["chunk_id"],
                    "content": chunk["text"],
                    "metadata": self._chunk_to_metadata(chunk),
                    "embedding": embedding,
                }
            rows = list(rows_by_key.values())
            self.client.table(self.table).upsert(rows, on_conflict="tenant_id,chunk_id").execute()
            total += len(rows)

        self._cached_count = total
        return total

    def similarity_search(
        self,
        query: str,
        k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[Document]:
        filt = dict(filter or {})
        filt.pop("tenant_id", None)
        metadata_filter = {key: value for key, value in filt.items()}

        query_embedding = self.embedder.embed_query(query)
        response = self.client.rpc(
            self.rpc,
            {
                "query_embedding": query_embedding,
                "match_count": k,
                "filter_tenant": self.tenant_id,
                "filter_metadata": metadata_filter,
            },
        ).execute()

        documents: list[Document] = []
        for row in response.data or []:
            metadata = row.get("metadata") or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            metadata["similarity"] = row.get("similarity")
            documents.append(
                Document(
                    page_content=row.get("content", ""),
                    metadata=metadata,
                )
            )
        return documents

    def count(self, *, refresh: bool = False) -> int:
        if not refresh and self._cached_count is not None:
            return self._cached_count
        response = (
            self.client.table(self.table)
            .select("id", count="exact")
            .eq("tenant_id", self.tenant_id)
            .execute()
        )
        self._cached_count = response.count or 0
        return self._cached_count
