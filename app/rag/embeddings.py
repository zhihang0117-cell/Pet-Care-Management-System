"""
Query embedding for RAG retrieval.

Extracted from ai-backend/rag_service.py: this keeps only the embedding
step (sentence-transformers/BGE by default, OpenAI embeddings as an
alternate provider). The chunk-filtering/header-detection/service-type
heuristics in the original file were coupled to the retired intent-schema
pipeline and are not needed — the LLM judges relevance itself per the
system prompt's RAG rules.
"""

from __future__ import annotations

import os
import threading

_embedding_model = None
_embedding_model_name = None

# The orchestrator runs independent tool calls concurrently in a
# ThreadPoolExecutor (e.g. get_booking_service_options and retrieve_policy
# both calling RAG in the same turn). sentence-transformers/torch is not
# safe to call from two threads at once here — it segfaulted the whole
# server process under exactly that pattern. Serialize all embedding calls
# through one lock so concurrent tool calls never overlap inside the model.
_embedding_lock = threading.Lock()


def reset_embedding_model_cache() -> None:
    """Clear the cached sentence-transformers model so env overrides can take effect."""
    global _embedding_model, _embedding_model_name
    _embedding_model = None
    _embedding_model_name = None


def get_embedding_model():
    """Load and cache the sentence-transformers embedding model."""
    global _embedding_model, _embedding_model_name
    # Must match whatever model actually produced the vectors already stored
    # in chunks_bge_large (BGE-Large — BAAI/bge-large-en-v1.5, per the real
    # .env this project runs with) — silently falling back to a DIFFERENT
    # model here if EMBEDDING_MODEL is ever unset would degrade RAG
    # retrieval with no error at all (same vector dimension, wrong semantic
    # space, so pgvector similarity search still "succeeds", just badly).
    model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-en-v1.5").strip() or "BAAI/bge-large-en-v1.5"
    if _embedding_model is None or _embedding_model_name != model_name:
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer(model_name)
        _embedding_model_name = model_name
    return _embedding_model


def _get_embedding_provider() -> str:
    return os.getenv("EMBEDDING_PROVIDER", "sentence_transformers").strip().lower()


def _embed_query_openai(query: str) -> list:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required for OpenAI embeddings")

    model_name = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.embeddings.create(input=query, model=model_name)
    return list(response.data[0].embedding)


def embed_query(query: str) -> list:
    """Encode a user query into an embedding vector for pgvector search."""
    if _get_embedding_provider() == "openai":
        return _embed_query_openai(query)

    model_name = os.getenv("EMBEDDING_MODEL", "").strip().lower()
    text = query
    if "e5" in model_name and not text.lower().startswith(("query:", "passage:")):
        text = f"query: {text}"

    with _embedding_lock:
        model = get_embedding_model()
        embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def _embed_documents_openai(texts: list[str]) -> list[list[float]]:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required for OpenAI embeddings")

    model_name = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.embeddings.create(input=texts, model=model_name)
    return [list(item.embedding) for item in response.data]


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Encode a batch of document chunks for indexing (see embed_query for the
    matching query-side encoding — document side never gets the e5/BGE query
    prefix, matching how the retired production config defined document_prefix
    as empty for every model still in use)."""
    if not texts:
        return []
    if _get_embedding_provider() == "openai":
        return _embed_documents_openai(texts)

    with _embedding_lock:
        model = get_embedding_model()
        embeddings = model.encode(texts, normalize_embeddings=True)
    return [e.tolist() for e in embeddings]
