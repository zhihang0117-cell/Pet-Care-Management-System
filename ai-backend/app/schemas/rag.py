"""Production retrieval schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

PetType = Literal["dog", "cat", "all"]
ServiceType = Literal["grooming", "boarding", "daycare", "general"]


class RetrieveRequest(BaseModel):
    company_id: int = Field(..., ge=1)
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    service_type: ServiceType | None = None
    pet_type: PetType | None = None


class RetrievedChunk(BaseModel):
    rank: int
    chunk_id: str
    document_id: str
    score: float
    content: str
    metadata: dict[str, Any]


class RetrieveResponse(BaseModel):
    company_id: int
    query: str
    top_k: int
    filter_mode: str
    low_confidence: bool
    chunks: list[RetrievedChunk]
