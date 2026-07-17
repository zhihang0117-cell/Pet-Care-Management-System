"""Production retrieval without eval-row coupling."""

from __future__ import annotations

from typing import Any

from app.config import PRODUCTION_TOP_K
from app.database.supabase import ProductionVectorStore
from app.services.metadata_tagging import (
    classify_business_object,
    infer_expected_pet_type_from_query,
)
from app.services.retrieval_service import metadata_aware_search


def build_production_metadata(
    company_id: int,
    query_text: str,
    *,
    service_type: str | None = None,
    pet_type: str | None = None,
) -> dict[str, Any]:
    text = str(query_text or "").strip()
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

    resolved_service_type = str(service_type or "").strip().lower()
    if not resolved_service_type:
        if main_intent == "service" and service_context in ("grooming", "boarding", "daycare"):
            resolved_service_type = service_context
        elif main_intent in ("loyalty", "cancellation", "reschedule", "service"):
            resolved_service_type = "general"
        else:
            resolved_service_type = "general"

    resolved_pet_type = str(pet_type or "").strip().lower()
    if not resolved_pet_type:
        resolved_pet_type = infer_expected_pet_type_from_query(
            text,
            service_type=resolved_service_type,
        )

    return {
        "company_id": company_id,
        "dataset_type": "",
        "retrieval_source": "",
        "service_type": resolved_service_type,
        "service_context": service_context,
        "intent": main_intent,
        "main_intent": main_intent,
        "business_object": classified.get("business_object", "other"),
        "intent_confidence": classified.get("confidence", 0.0),
        "intent_reason": classified.get("reason", ""),
        "pet_type": resolved_pet_type,
        "service_info": "",
        "query": text,
    }


def retrieve_production_chunks(
    query: str,
    company_id: int,
    *,
    top_k: int = PRODUCTION_TOP_K,
    service_type: str | None = None,
    pet_type: str | None = None,
) -> dict[str, Any]:
    store = ProductionVectorStore.for_company(company_id)
    meta = build_production_metadata(
        company_id,
        query,
        service_type=service_type,
        pet_type=pet_type,
    )
    return metadata_aware_search(
        store,
        query,
        meta,
        mode="topk",
        k=top_k,
    )
