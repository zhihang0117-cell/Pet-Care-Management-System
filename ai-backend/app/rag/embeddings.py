"""Production BGE-Large embedder (single model, unchanged settings)."""

from __future__ import annotations

from app.config import MODELS, PRODUCTION_MODEL_KEY
from app.services.embeddings import get_embedder

PRODUCTION_MODEL_CONFIG = MODELS[PRODUCTION_MODEL_KEY]


def get_production_embedder():
    return get_embedder(PRODUCTION_MODEL_KEY)
