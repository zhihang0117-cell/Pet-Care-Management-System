from typing import Any

from pydantic import BaseModel, Field


class IngestResponse(BaseModel):
    tenant_id: str
    files_saved: list[str]
    files_in_corpus: list[str] = Field(default_factory=list)
    chunk_count: int
    rule_chunk_count: int
    sample_chunks: list[dict[str, Any]]
    message: str = ""


class IndexResponse(BaseModel):
    model_key: str
    display_name: str
    tenant_id: str
    chunks_indexed: int


class EvaluateResponse(BaseModel):
    model_key: str
    display_name: str
    metrics: dict[str, Any]


class BenchmarkResponse(BaseModel):
    tenant_id: str
    models_run: list[str]
    models_skipped: list[dict[str, str]]
    comparison: list[dict[str, Any]]


class PipelineStatusResponse(BaseModel):
    steps: list[dict[str, Any]]
    logs: list[dict[str, str]]
    tenant_id: str
    chunk_count: int
    indexed_models: list[str]
    evaluated_models: list[str]
    current_job: str | None = None


class ModelInfo(BaseModel):
    key: str
    display_name: str
    dimensions: int
    type: str


class ModelsResponse(BaseModel):
    models: list[ModelInfo]


class ChunksPreviewResponse(BaseModel):
    total: int
    rule_count: int
    samples: list[dict[str, Any]] = Field(default_factory=list)


class EvalQueryRow(BaseModel):
    eval_id: str = ""
    seed_id: str = ""
    user_message: str = ""
    dataset_type: str = ""
    service_type: str = ""
    expected_pet_type: str = "all"
    expected_dataset_type: str = ""
    expected_service_type: str = ""
    pet_type: str = ""
    file_name: str = ""
    source_pdf: str = ""
    actual_scenario: str = ""


class EvalQueriesRequest(BaseModel):
    rows: list[EvalQueryRow] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    source_pdf: str = ""
    dataset_type: str = ""
    file_name: str = ""


class MatchedChunkPreview(BaseModel):
    chunk_id: str
    dataset_type: str = ""
    document_file_name: str = ""
    section_path: str = ""
    section_id: str = ""
    section_title: str = ""
    main_header: str = ""
    pet_type: str = "all"
    service_type: str = "general"
    service_info: str = ""
    text_preview: str = ""
    text: str = ""
    char_count: int = 0


class PreparedQueryRow(BaseModel):
    query_id: str
    query: str
    dataset_type: str = ""
    service_type: str = ""
    expected_pet_type: str = "all"
    match_method: str = "none"
    match_score: float = 0.0
    relevant_chunk_ids: list[str] = Field(default_factory=list)
    matched_chunks: list[MatchedChunkPreview] = Field(default_factory=list)


class EvalQueriesResponse(BaseModel):
    query_count: int
    prepared: bool
    message: str
    queries: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    prepared_rows: list[PreparedQueryRow] = Field(default_factory=list)
