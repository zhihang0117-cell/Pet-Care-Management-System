"""Production document processing schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProductionDocumentType = Literal[
    "policies",
    "service_information",
    "business_flow_booking",
    "veterinary",
]


class ProcessDocumentRequest(BaseModel):
    company_id: int = Field(..., ge=1)
    document_id: str = Field(..., min_length=1)
    document_type: ProductionDocumentType
    storage_bucket: str = Field(..., min_length=1)
    storage_path: str = Field(..., min_length=1)


class ProcessDocumentResponse(BaseModel):
    company_id: int
    document_id: str
    document_type: ProductionDocumentType
    chunks_indexed: int
    chunk_ids: list[str]
    model_key: str
    table: str
    status: str = "indexed"
