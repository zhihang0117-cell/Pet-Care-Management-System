"""Embedding model factory with query/document prefixes and global cache."""

from __future__ import annotations

import os
import threading

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from app.config import MODELS, settings

_embedder_cache: dict[str, Embeddings] = {}
_embedder_lock = threading.Lock()


class PrefixedEmbeddings(Embeddings):
    def __init__(self, base: Embeddings, query_prefix: str = "", document_prefix: str = ""):
        self.base = base
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        prefixed = [f"{self.document_prefix}{t}" if self.document_prefix else t for t in texts]
        return self.base.embed_documents(prefixed)

    def embed_query(self, text: str) -> list[float]:
        prefixed = f"{self.query_prefix}{text}" if self.query_prefix else text
        return self.base.embed_query(prefixed)


def _build_embedder(model_key: str) -> Embeddings:
    cfg = MODELS[model_key]

    if cfg["type"] == "openai":
        if not settings.openai_api_key and not os.getenv("OPENAI_API_KEY"):
            raise EnvironmentError("OPENAI_API_KEY is required for text-embedding-3-small")
        base: Embeddings = OpenAIEmbeddings(
            model=cfg["model_name"],
            api_key=settings.openai_api_key or None,
        )
    else:
        from langchain_huggingface import HuggingFaceEmbeddings

        # Loads SentenceTransformer once; callers must reuse via get_embedder cache.
        base = HuggingFaceEmbeddings(
            model_name=cfg["model_name"],
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )

    if cfg.get("query_prefix") or cfg.get("document_prefix"):
        return PrefixedEmbeddings(
            base,
            query_prefix=cfg.get("query_prefix", ""),
            document_prefix=cfg.get("document_prefix", ""),
        )
    return base


def get_embedder(model_key: str) -> Embeddings:
    """Return a process-wide cached embedder for ``model_key``.

    HuggingFace / SentenceTransformer weights are loaded on first use only.
    Evaluation and Part 4 batch retrieval reuse the same instance for all queries.
    """
    if model_key not in MODELS:
        raise ValueError(f"Unknown model key: {model_key}. Choose from: {list(MODELS)}")

    cached = _embedder_cache.get(model_key)
    if cached is not None:
        return cached

    with _embedder_lock:
        cached = _embedder_cache.get(model_key)
        if cached is not None:
            return cached
        embedder = _build_embedder(model_key)
        _embedder_cache[model_key] = embedder
        return embedder


def clear_embedder_cache(model_key: str | None = None) -> None:
    """Drop cached embedders (all, or one key). Useful in tests."""
    with _embedder_lock:
        if model_key is None:
            _embedder_cache.clear()
        else:
            _embedder_cache.pop(model_key, None)
