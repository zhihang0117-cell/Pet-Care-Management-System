"""Production document processing API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.rag.pipeline import process_document_from_storage
from app.schemas.documents import ProcessDocumentRequest, ProcessDocumentResponse

router = APIRouter(tags=["production-documents"])


@router.post("/documents/process", response_model=ProcessDocumentResponse)
def process_document_endpoint(body: ProcessDocumentRequest) -> ProcessDocumentResponse:
    try:
        result = process_document_from_storage(
            company_id=body.company_id,
            document_id=body.document_id,
            document_type=body.document_type,
            storage_bucket=body.storage_bucket,
            storage_path=body.storage_path,
        )
        return ProcessDocumentResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
