"""Tenant-scoped embedding retrieval with post-search metadata fallback.

Flow (strict → fallback only when a level is empty/unusable):
  Query
  → classify_business_object() → main_intent, business_object, service_context
  → build retrieval metadata (service_type / intent / pet_type)
  → embed query · tenant-only vector search
  → Filter: service_type + service_info
  → Filter: service_info
  → Filter: service_type
  → tenant_only
  → Top-K / full ranked preview

Metadata purpose: drop less-relevant hits after embedding, then widen only if empty.
Do not special-case intents to skip service_type / service_info filters.
Ground truth is eval-only (scoring). It must never drive filtering, ranking, or
response context selection.
"""

from __future__ import annotations

from typing import Any, Literal

from app.config import (
    ACCEPTABLE_SIMILARITY,
    BENCHMARK_MODEL_KEYS,
    EVAL_CANDIDATE_K,
    GOOD_SIMILARITY,
    MAX_RETRIEVE_K,
    MODELS,
    PREVIEW_CANDIDATE_K,
)
from app.services.eval_matching import service_infos_match
from app.services.metadata_fields import (
    chunk_service_type,
    eval_dataset_type,
    eval_expected_pet_type,
    eval_query_text,
    eval_service_info,
    eval_service_type,
)
from app.services.metadata_tagging import (
    classify_business_object,
    infer_expected_pet_type_from_query,
)
from app.services.supabase_store import SupabaseVectorStore

RetrievalMode = Literal["threshold", "topk"]

# Post-embedding metadata fallback (strict → loose). Applied AFTER vector search.
# tenant_id is always applied by the vector store (never dropped).
FALLBACK_LEVELS: list[tuple[str, frozenset[str]]] = [
    ("service_type+service_info", frozenset({"service", "service_info"})),
    ("service_info", frozenset({"service_info"})),
    ("service_type", frozenset({"service"})),
    ("tenant_only", frozenset()),
]

LOW_CONFIDENCE_FOLLOW_UP = (
    "I couldn't find a confident match in the knowledge base. "
    "Could you clarify the pet type or service, or would you like me to connect you with staff?"
)


def slim_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Drop full chunk text for batch responses (keeps preview only)."""
    return {**hit, "text": ""}


def detect_retrieval_metadata(
    tenant_id: str,
    query_text: str = "",
    eval_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect metadata for post-search filtering / UI (not a pre-vector hard cut)."""
    row = dict(eval_row or {})
    text = (query_text or eval_query_text(row) or "").strip()

    dataset_type = eval_dataset_type(row) if row else ""
    classified = (
        classify_business_object(text)
        if text
        else {
            "main_intent": "other",
            "business_object": "other",
            "service_context": "unknown",
            "confidence": 0.0,
            "reason": "Empty query",
        }
    )
    main_intent = str(classified.get("main_intent") or "other")
    service_context = str(classified.get("service_context") or "unknown")

    if main_intent == "service" and service_context in ("grooming", "boarding", "daycare"):
        service_type = service_context
    elif main_intent in ("loyalty", "cancellation", "reschedule", "service"):
        service_type = "general"
    elif row:
        service_type = eval_service_type(row)
    else:
        service_type = "general"

    if row:
        pet_type = eval_expected_pet_type(row)
    else:
        pet_type = infer_expected_pet_type_from_query(text, service_type=service_type)

    service_info = eval_service_info(row) if row else ""

    return {
        "tenant_id": tenant_id,
        "dataset_type": dataset_type,
        "retrieval_source": dataset_type,
        "service_type": service_type,
        "service_context": service_context,
        "intent": main_intent,
        "main_intent": main_intent,
        "business_object": classified.get("business_object", "other"),
        "intent_confidence": classified.get("confidence", 0.0),
        "intent_reason": classified.get("reason", ""),
        "pet_type": pet_type,
        "service_info": service_info,
        "query": text,
    }


def _doc_similarity(doc: Any) -> float:
    return float((doc.metadata or {}).get("similarity") or 0.0)


def _doc_as_chunk(doc: Any) -> dict[str, Any]:
    meta = dict(doc.metadata or {})
    return {**meta, "chunk_id": str(meta.get("chunk_id", "")), "text": doc.page_content or ""}


def _effective_fields(fields: frozenset[str], meta: dict[str, Any]) -> frozenset[str]:
    """Keep the requested filter fields; drop only when query metadata is missing."""
    active = set(fields)
    expected_svc = str(meta.get("service_type") or "").strip()

    if "service" in active and expected_svc in ("", "unknown", "general"):
        active.discard("service")
    if "service_info" in active and not str(meta.get("service_info") or "").strip():
        active.discard("service_info")
    return frozenset(active)


def _post_filter_docs(docs: list[Any], meta: dict[str, Any], fields: frozenset[str]) -> list[Any]:
    """Apply selected metadata constraints on already-retrieved embedding hits."""
    out = list(docs)
    if "service" in fields:
        expected_svc = meta["service_type"]
        out = [d for d in out if chunk_service_type(_doc_as_chunk(d)) == expected_svc]
    if "service_info" in fields:
        expected_info = str(meta.get("service_info") or "")
        out = [d for d in out if service_infos_match(expected_info, _doc_as_chunk(d))]
    return out


def _best_score(docs: list[Any]) -> float:
    if not docs:
        return 0.0
    return max(_doc_similarity(d) for d in docs)


def _level_usable(docs: list[Any], *, mode: RetrievalMode, top_k: int) -> bool:
    """Accept a fallback level when it still has useful hits (prefer non-empty pool)."""
    if not docs:
        return False
    if mode == "topk":
        return len(docs) >= min(1, top_k)
    return True


def _should_accept_results(docs: list[Any]) -> bool:
    """Confidence band on best cosine similarity (UX / low_confidence flag)."""
    if not docs:
        return False
    return _best_score(docs) >= ACCEPTABLE_SIMILARITY


def _score_band(best: float) -> str:
    if best >= GOOD_SIMILARITY:
        return "good"
    if best >= ACCEPTABLE_SIMILARITY:
        return "acceptable"
    return "weak"


def _hit_from_doc(doc: Any, rank: int) -> dict[str, Any]:
    metadata = dict(doc.metadata or {})
    text = doc.page_content or ""
    return {
        "rank": rank,
        "chunk_id": str(metadata.get("chunk_id", "")),
        "document_id": str(metadata.get("document_id", "")),
        "similarity": float(metadata.get("similarity") or 0.0),
        "text": text,
        "text_preview": text,
        "dataset_type": str(metadata.get("dataset_type", "")),
        "pet_type": str(metadata.get("pet_type", "")),
        "service_type": str(metadata.get("service_type", "")),
        "service_info": str(metadata.get("service_info", "")),
        "section_path": str(metadata.get("section_path", "")),
        "main_header": str(metadata.get("main_header", "")),
        "sub_header": str(metadata.get("sub_header", "")),
    }


def metadata_aware_search(
    store: SupabaseVectorStore,
    query_text: str,
    meta: dict[str, Any],
    *,
    mode: RetrievalMode = "topk",
    k: int | None = None,
) -> dict[str, Any]:
    """tenant → embed (no metadata RPC) → metadata fallback post-filter → Top-K."""
    top_k = k or MAX_RETRIEVE_K
    candidate_k = (
        PREVIEW_CANDIDATE_K
        if mode == "threshold"
        else max(EVAL_CANDIDATE_K, top_k, PREVIEW_CANDIDATE_K)
    )

    # 1) Embedding search within tenant only (empty metadata filter).
    candidates = store.similarity_search(query_text, k=candidate_k, filter={})
    candidates = sorted(candidates, key=_doc_similarity, reverse=True)

    attempts: list[dict[str, Any]] = [
        {
            "filter_mode": "tenant_embed_pool",
            "fields": [],
            "rpc_filter": {},
            "result_count": len(candidates),
            "best_score": round(_best_score(candidates), 6),
            "score_band": _score_band(_best_score(candidates)),
            "accepted": False,
        }
    ]

    selected: list[Any] = list(candidates)
    used_level = "tenant_only"
    accepted_level = False

    # 2) Metadata post-filter with fallback on the same embedding-ranked pool.
    seen: set[tuple[str, ...]] = set()
    for level_name, base_fields in FALLBACK_LEVELS:
        fields = _effective_fields(base_fields, meta)
        signature = (level_name, tuple(sorted(fields)))
        if signature in seen:
            continue
        seen.add(signature)

        docs = _post_filter_docs(candidates, meta, fields)
        docs = sorted(docs, key=_doc_similarity, reverse=True)
        best = _best_score(docs)
        usable = _level_usable(docs, mode=mode, top_k=top_k)
        attempt = {
            "filter_mode": level_name,
            "fields": sorted(fields),
            "rpc_filter": {},
            "result_count": len(docs),
            "best_score": round(best, 6),
            "score_band": _score_band(best),
            "accepted": False,
        }
        attempts.append(attempt)

        if usable:
            attempt["accepted"] = True
            selected = docs
            used_level = level_name if fields else "tenant_only"
            accepted_level = True
            break

        # Keep last non-empty attempt if stricter levels were empty; do not skip levels by intent.
        selected = docs
        used_level = level_name if fields else "tenant_only"

    if not accepted_level and not selected:
        selected = candidates
        used_level = "tenant_only"

    low_confidence = not _should_accept_results(selected)
    follow_up = LOW_CONFIDENCE_FOLLOW_UP if low_confidence else ""

    if mode == "threshold":
        selected = sorted(selected, key=_doc_similarity, reverse=True)
    else:
        selected = selected[:top_k]

    hits = [_hit_from_doc(doc, rank=i) for i, doc in enumerate(selected, start=1)]
    best_selected = _best_score(selected) if selected else 0.0

    return {
        "hits": hits,
        "low_confidence": low_confidence,
        "follow_up": follow_up,
        "filter_mode": used_level,
        "best_score": round(best_selected, 6),
        "score_band": _score_band(best_selected),
        "result_count": len(hits),
        "metadata": {
            "tenant_id": meta.get("tenant_id", ""),
            "dataset_type": meta.get("dataset_type", ""),
            "service_type": meta.get("service_type", ""),
            "service_context": meta.get("service_context", ""),
            "intent": meta.get("intent", ""),
            "pet_type": meta.get("pet_type", ""),
            "service_info": meta.get("service_info", ""),
        },
        "fallback_attempts": attempts,
    }


def retrieve_one_model(
    model_key: str,
    query_text: str,
    tenant_id: str,
    k: int | None = None,
    *,
    mode: RetrievalMode = "threshold",
    eval_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Retrieval for one embedding model: embed first, then metadata fallback."""
    if model_key not in MODELS:
        return {
            "model_key": model_key,
            "display_name": model_key,
            "skipped": True,
            "reason": f"Unknown model: {model_key}",
            "low_confidence": False,
            "follow_up": "",
            "filter_mode": "",
            "hits": [],
        }

    display = MODELS[model_key]["display_name"]
    meta = detect_retrieval_metadata(tenant_id, query_text=query_text, eval_row=eval_row)

    try:
        store = SupabaseVectorStore.from_model(model_key, tenant_id)
        if store.count() == 0:
            return {
                "model_key": model_key,
                "display_name": display,
                "skipped": True,
                "reason": "not indexed",
                "low_confidence": False,
                "follow_up": "",
                "filter_mode": "",
                "metadata": meta,
                "hits": [],
            }

        search = metadata_aware_search(
            store,
            query_text,
            meta,
            mode=mode,
            k=k or MAX_RETRIEVE_K,
        )
        return {
            "model_key": model_key,
            "display_name": display,
            "skipped": False,
            "reason": "low_confidence" if search["low_confidence"] else "",
            "low_confidence": search["low_confidence"],
            "follow_up": search["follow_up"],
            "filter_mode": search["filter_mode"],
            "best_score": search["best_score"],
            "score_band": search["score_band"],
            "metadata": search["metadata"],
            "fallback_attempts": search["fallback_attempts"],
            "hits": search["hits"],
        }
    except EnvironmentError as exc:
        return {
            "model_key": model_key,
            "display_name": display,
            "skipped": True,
            "reason": str(exc),
            "low_confidence": False,
            "follow_up": "",
            "filter_mode": "",
            "metadata": meta,
            "hits": [],
        }
    except Exception as exc:  # noqa: BLE001 — surface per-model failure in UI
        return {
            "model_key": model_key,
            "display_name": display,
            "skipped": True,
            "reason": str(exc),
            "low_confidence": False,
            "follow_up": "",
            "filter_mode": "",
            "metadata": meta,
            "hits": [],
        }

def retrieve_all_models(
    query_text: str,
    tenant_id: str,
    k: int | None = None,
    model_keys: list[str] | None = None,
    *,
    mode: RetrievalMode = "threshold",
    eval_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run retrieval for each model sequentially."""
    keys = model_keys or list(BENCHMARK_MODEL_KEYS)
    return {
        key: retrieve_one_model(
            key, query_text, tenant_id, k, mode=mode, eval_row=eval_row
        )
        for key in keys
    }

