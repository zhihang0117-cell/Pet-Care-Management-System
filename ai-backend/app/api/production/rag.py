"""Production retrieval API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.rag.pipeline import retrieve_chunks
from app.schemas.rag import RetrieveRequest, RetrieveResponse, RetrievedChunk

router = APIRouter(tags=["production-rag"])


@router.post("/rag/retrieve", response_model=RetrieveResponse)
def retrieve_endpoint(body: RetrieveRequest) -> RetrieveResponse:
    try:
        result = retrieve_chunks(
            query=body.query,
            company_id=body.company_id,
            top_k=body.top_k,
            service_type=body.service_type,
            pet_type=body.pet_type,
        )
        return RetrieveResponse(
            company_id=result["company_id"],
            query=result["query"],
            top_k=result["top_k"],
            filter_mode=result["filter_mode"],
            low_confidence=result["low_confidence"],
            chunks=[RetrievedChunk(**chunk) for chunk in result["chunks"]],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
