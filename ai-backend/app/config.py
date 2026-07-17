from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_EVAL = PROJECT_ROOT / "data" / "eval"
RESULTS_DIR = PROJECT_ROOT / "results"

TOP_K_VALUES = [1, 3, 5, 7]
MAX_RETRIEVE_K = 7  # fixed Top-K for final evaluation (Recall@K)
# Part 4 preview: no similarity cut / no result cap — return ranked chunks with scores.
# Kept for API response fields / legacy docs only (not applied in retrieval).
SIMILARITY_THRESHOLD = 0.0
MAX_THRESHOLD_RESULTS = 0
# Candidate pool for Part 4 (large enough to cover full corpus ~193)
PREVIEW_CANDIDATE_K = 250
EVAL_CANDIDATE_K = 50
# Fallback acceptance on true similarity (not distance)
GOOD_SIMILARITY = 0.60
ACCEPTABLE_SIMILARITY = 0.50
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100
MIN_CHUNK_SIZE = 80
SECTION_SUB_SPLIT = 2000

MODELS = {
    "minilm": {
        "display_name": "all-MiniLM-L6-v2",
        "table": "chunks_minilm",
        "rpc": "match_chunks_minilm",
        "dimensions": 384,
        "type": "huggingface",
        "model_name": "sentence-transformers/all-MiniLM-L6-v2",
        "query_prefix": "",
        "document_prefix": "",
    },
    "bge-large-en-v1.5": {
        "display_name": "bge-large-en-v1.5",
        "table": "chunks_bge_large",
        "rpc": "match_chunks_bge_large",
        "dimensions": 1024,
        "type": "huggingface",
        "model_name": "BAAI/bge-large-en-v1.5",
        "query_prefix": "Represent this sentence for searching relevant passages: ",
        "document_prefix": "",
    },
    "multilingual-e5-large": {
        "display_name": "multilingual-e5-large",
        "table": "chunks_e5_large",
        "rpc": "match_chunks_e5_large",
        "dimensions": 1024,
        "type": "huggingface",
        "model_name": "intfloat/multilingual-e5-large",
        "query_prefix": "query: ",
        "document_prefix": "passage: ",
    },
    "bge-m3": {
        "display_name": "BGE-M3",
        "table": "chunks_bge_m3",
        "rpc": "match_chunks_bge_m3",
        "dimensions": 1024,
        "type": "huggingface",
        "model_name": "BAAI/bge-m3",
        "query_prefix": "",
        "document_prefix": "",
    },
    "text-embedding-3-small": {
        "display_name": "text-embedding-3-small",
        "table": "chunks_openai_small",
        "rpc": "match_chunks_openai_small",
        "dimensions": 1536,
        "type": "openai",
        "model_name": "text-embedding-3-small",
        "query_prefix": "",
        "document_prefix": "",
    },
}

BENCHMARK_MODEL_KEYS = [
    "minilm",
    "bge-large-en-v1.5",
    "multilingual-e5-large",
    "bge-m3",
    "text-embedding-3-small",
]

# Production RAG (BGE-Large only — do not change model settings here)
PRODUCTION_MODEL_KEY = "bge-large-en-v1.5"
PRODUCTION_TOP_K = 5

# Business document categories (not file formats)
PRODUCTION_DOCUMENT_TYPES = frozenset(
    {
        "policies",
        "service_information",
        "business_flow_booking",
        "veterinary",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_url: str = ""
    supabase_service_key: str = ""
    openai_api_key: str = ""
    default_tenant_id: str = "pawfect-demo"


settings = Settings()
