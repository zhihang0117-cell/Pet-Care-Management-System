"""
Real RAG retrieval service for Pawfect backend.

Retrieves chunks from Supabase pgvector when configured.
Falls back to mock_rag.py if Supabase or embeddings are unavailable.
"""

import os
import re

from dotenv import load_dotenv

from llm_api_error import is_eval_strict_mode
from testing_mode import should_reraise_on_error
from mock_rag import mock_rag_retrieve

RAG_ROUTES = {"CALL_KNOWLEDGE_RAG", "CALL_RAG_AND_DATABASE", "CALL_RAG_THEN_ASK_MISSING_INFO"}

_embedding_model = None
_embedding_model_name = None

_HEADER_ONLY_PATTERNS = [
    r"^reservations?\s*&\s*cancellations?$",
    r"^cancellation policy$",
    r"^boarding rules$",
    r"^veterinary requirements$",
    r"^basic grooming add-ons$",
    r"^grooming price$",
    r"^daycare price$",
    r"^boarding price$",
    r"^service add-ons$",
]

_TABLE_HEADER_PATTERNS = [
    r"^[\w\s\-&]+ price table$",
    r"^[\w\s\-&]+ price$",
    r"^service add-ons$",
    r"^basic grooming add-ons$",
    r"^grooming price table$",
    r"^daycare price table$",
]


def reset_embedding_model_cache() -> None:
    """Clear the cached sentence-transformers model so env overrides can take effect."""
    global _embedding_model, _embedding_model_name
    _embedding_model = None
    _embedding_model_name = None


def get_embedding_model():
    """Load and cache the sentence-transformers embedding model."""
    global _embedding_model, _embedding_model_name
    model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3").strip() or "BAAI/bge-m3"
    if _embedding_model is None or _embedding_model_name != model_name:
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer(model_name)
        _embedding_model_name = model_name
    return _embedding_model


def _get_embedding_provider() -> str:
    return os.getenv("EMBEDDING_PROVIDER", "sentence_transformers").strip().lower()


def _embed_query_openai(query: str) -> list:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required for OpenAI embeddings")

    model_name = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.embeddings.create(input=query, model=model_name)
    return list(response.data[0].embedding)


def embed_query(query: str) -> list:
    """Encode a user query into an embedding vector for vector search."""
    if _get_embedding_provider() == "openai":
        return _embed_query_openai(query)

    model = get_embedding_model()
    model_name = os.getenv("EMBEDDING_MODEL", "").strip().lower()
    text = query
    if "e5" in model_name and not text.lower().startswith(("query:", "passage:")):
        text = f"query: {text}"
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def _safe_error_message(error: Exception) -> str:
    """Return a safe error message without exposing secrets."""
    message = str(error).strip() or error.__class__.__name__
    return message[:300]


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _word_count(text: str) -> int:
    return len(_normalize_text(text).split())


def _has_meaningful_body_content(text: str) -> bool:
    """True when text contains real answer body — prices, descriptions, lists, etc."""
    text_norm = _normalize_text(text)
    if not text_norm:
        return False

    text_lower = text_norm.lower()

    if re.search(r"\brm\s*\d", text_lower):
        return True

    if ";" in text_norm and _word_count(text_norm) >= 4:
        return True

    if "\n" in (text or "") and _word_count(text_norm) >= 4:
        return True

    if re.search(r"(?:^|\n)\s*[-•*]\s+\S", text or ""):
        return True

    if ":" in text_norm:
        after_colon = text_norm.split(":", 1)[1].strip()
        if _word_count(after_colon) >= 3 and not _is_section_number_title_label(after_colon):
            return True

    if _word_count(text_norm) >= 8:
        return True

    if re.search(
        r"\b(?:for|is|are|includes?|below|above|size|package|standard|all shave|keep head)\b",
        text_lower,
    ) and _word_count(text_norm) >= 5:
        return True

    return False


def _is_section_number_title_label(text: str) -> bool:
    text_norm = _normalize_text(text)
    if re.fullmatch(r"[\d\.]+\s+.+", text_norm):
        title_part = re.sub(r"^[\d\.]+\s+", "", text_norm).strip()
        return _word_count(title_part) <= 6
    if re.fullmatch(r".+:\s*[\d\.]+\s+.+", text_norm):
        return _is_duplicate_section_title_label(text_norm)
    return False


def _is_duplicate_section_title_label(text: str) -> bool:
    text_norm = _normalize_text(text)
    match = re.fullmatch(r"(.+?):\s*([\d\.]+)\s+(.+)", text_norm)
    if not match:
        return False
    left = match.group(1).strip().lower()
    right_title = match.group(3).strip().lower()
    return left == right_title


def _strip_metadata_headings(text: str, metadata: dict) -> str:
    body = _normalize_text(text)
    header_fields = [
        metadata.get("main_header"),
        metadata.get("sub_header"),
        metadata.get("section_title"),
        metadata.get("service_info"),
        metadata.get("section_path"),
    ]

    for header_value in header_fields:
        if not header_value:
            continue
        header_norm = _normalize_text(str(header_value))
        if not header_norm:
            continue
        header_lower = header_norm.lower()
        body_lower = body.lower()

        duplicate_label = f"{header_norm}: "
        section_prefix_pattern = rf"^{re.escape(header_norm)}:\s*[\d\.]+\s+{re.escape(header_norm)}\s*$"
        section_only_pattern = rf"^[\d\.]+\s+{re.escape(header_norm)}\s*$"

        if re.fullmatch(section_prefix_pattern, body, flags=re.I):
            return ""
        if re.fullmatch(section_only_pattern, body, flags=re.I):
            return ""
        if body_lower == header_lower:
            return ""
        if body_lower == f"{header_lower}: {header_lower}":
            return ""

        if body_lower.startswith(duplicate_label.lower()):
            candidate = body[len(header_norm) + 2 :].strip()
            if _is_section_number_title_label(f"{header_norm}: {candidate}") or _is_section_number_title_label(
                candidate
            ):
                return ""
            body = candidate
            body_lower = body.lower()

        numbered_prefix = rf"^[\d\.]+\s+{re.escape(header_norm)}\s*"
        if re.match(numbered_prefix, body, flags=re.I):
            body = re.sub(numbered_prefix, "", body, count=1, flags=re.I).strip()

    return body.strip()


def is_header_only_chunk(text: str, metadata: dict | None = None) -> bool:
    """
    True only for duplicated section labels / title-only chunks with no body content.

    Metadata headings are used to detect duplicated titles, not to reject unfamiliar formats.
    """
    metadata = dict(metadata or {})
    text_norm = _normalize_text(text)
    if not text_norm:
        return True

    if _is_duplicate_section_title_label(text_norm):
        return True

    if re.fullmatch(r"[\d\.]+\s+.+", text_norm) and not _has_meaningful_body_content(text_norm):
        return True

    if _has_meaningful_body_content(text_norm):
        remaining = _strip_metadata_headings(text_norm, metadata)
        if remaining and _has_meaningful_body_content(remaining):
            return False
        if not remaining:
            return True
        return not _has_meaningful_body_content(remaining)

    remaining = _strip_metadata_headings(text_norm, metadata)
    if remaining and _has_meaningful_body_content(remaining):
        return False

    if any(re.fullmatch(pattern, text_norm.lower()) for pattern in _HEADER_ONLY_PATTERNS):
        return True
    if any(re.fullmatch(pattern, text_norm.lower()) for pattern in _TABLE_HEADER_PATTERNS):
        return True

    return not remaining


def is_useful_chunk(chunk: dict) -> tuple[bool, str]:
    """
    Decide whether a retrieved chunk contains real answer content.

    Returns:
        (True, "useful") or (False, reason)
    """
    text = str(chunk.get("text") or "")
    metadata = chunk.get("metadata") or {}

    if not _normalize_text(text):
        return False, "empty"

    if is_header_only_chunk(text, metadata):
        if len(_normalize_text(text)) < 80 and _word_count(text) < 12:
            return False, "too_short_header_only"
        return False, "header_only"

    return True, "useful"


def filter_useful_chunks(chunks: list[dict]) -> tuple[list[dict], list[dict], bool]:
    """
    Filter out bad/header-only chunks while preserving ranking order.

    Returns:
        useful_chunks, removed_chunks, all_chunks_filtered_using_original
    """
    useful_chunks: list[dict] = []
    removed_chunks: list[dict] = []

    for chunk in chunks:
        is_useful, reason = is_useful_chunk(chunk)
        if is_useful:
            useful_chunks.append(chunk)
        else:
            removed_chunks.append(
                {
                    "chunk_id": chunk.get("chunk_id"),
                    "reason": reason,
                    "text_preview": _normalize_text(chunk.get("text", ""))[:120],
                }
            )

    all_chunks_filtered_using_original = False
    if chunks and not useful_chunks:
        all_chunks_filtered_using_original = True
        useful_chunks = chunks

    return useful_chunks, removed_chunks, all_chunks_filtered_using_original


_BROAD_QUERY_PATTERNS = [
    r"\btell me about\b",
    r"\bwhat are the\b.+\brequirements?\b",
    r"\bexplain\b.+\b(?:policy|rules?|requirements?)\b",
    r"\bwhat are the\b.+\brules?\b",
]

_PRICE_QUERY_PATTERNS = [
    r"\bprice\b",
    r"\bhow much\b",
    r"\bcost\b",
    r"\bfee\b",
]

_SPECIFIC_SERVICES = {"grooming", "daycare", "boarding"}


def detect_required_service_type(
    intent_json: dict, user_message: str
) -> tuple[str | None, str | None]:
    """
    Detect required service_type for chunk filtering.

    Returns:
        (detected_service_type, required_service_type)
    """
    intent_service = str(intent_json.get("service_type", "UNKNOWN")).upper()

    if intent_service in ("GROOMING", "DAYCARE", "BOARDING"):
        required = intent_service.lower()
        return required, required

    message_lower = user_message.lower()
    if "grooming" in message_lower or re.search(r"\bgroom\b", message_lower):
        return "grooming", "grooming"
    if "daycare" in message_lower or "day care" in message_lower:
        return "daycare", "daycare"
    if "boarding" in message_lower:
        return "boarding", "boarding"

    return None, None


def _chunk_service_type(chunk: dict) -> str:
    metadata = chunk.get("metadata") or {}
    return str(metadata.get("service_type", "")).strip().lower()


def filter_by_required_service(
    chunks: list[dict], required_service_type: str | None
) -> tuple[list[dict], list[dict], bool]:
    """
    Keep only chunks matching the required service_type.

    Returns:
        matching_chunks, removed_wrong_service_chunks, service_filter_fallback_used
    """
    if not required_service_type:
        return chunks, [], False

    matching_chunks: list[dict] = []
    removed_chunks: list[dict] = []

    for chunk in chunks:
        chunk_service = _chunk_service_type(chunk)
        if chunk_service == required_service_type:
            matching_chunks.append(chunk)
        else:
            removed_chunks.append(
                {
                    "chunk_id": chunk.get("chunk_id"),
                    "chunk_service_type": chunk_service or "unknown",
                    "required_service_type": required_service_type,
                    "reason": "wrong_service_type",
                    "text_preview": _normalize_text(chunk.get("text", ""))[:120],
                }
            )

    service_filter_fallback_used = False
    if chunks and not matching_chunks:
        service_filter_fallback_used = True
        matching_chunks = chunks

    return matching_chunks, removed_chunks, service_filter_fallback_used


def _is_broad_query(user_message: str) -> bool:
    """Detect broad questions that may need up to 3 context chunks."""
    message = user_message.lower().strip()
    return any(re.search(pattern, message) for pattern in _BROAD_QUERY_PATTERNS)


def _is_price_query(user_message: str) -> bool:
    """Detect price-focused questions."""
    message = user_message.lower().strip()
    return any(re.search(pattern, message) for pattern in _PRICE_QUERY_PATTERNS)


def select_final_context_chunks(
    filtered_chunks: list[dict],
    user_message: str,
    price_context: dict | None = None,
) -> list[dict]:
    """
    Select the most relevant useful chunks for the final answer.

    When price_context indicates a base package or add-on, prioritize matching sections.
    """
    from retrieval_request import prioritize_chunks_for_price_intent, resolve_price_context

    load_dotenv(override=not os.getenv("_EVAL_OVERRIDE_ACTIVE"))

    default_count = int(os.getenv("FINAL_CONTEXT_CHUNKS", "2"))
    max_count = int(os.getenv("MAX_FINAL_CONTEXT_CHUNKS", "3"))

    if not filtered_chunks:
        return []

    ranked_chunks = prioritize_chunks_for_price_intent(
        filtered_chunks,
        price_context or resolve_price_context(user_message),
    )

    if _is_price_query(user_message):
        limit = min(2, len(ranked_chunks))
    elif _is_broad_query(user_message):
        limit = min(max_count, len(ranked_chunks))
    else:
        limit = min(default_count, len(ranked_chunks))

    return ranked_chunks[:limit]


def _get_rag_company_id() -> int:
    """Resolve the company_id used for Supabase chunk filtering."""
    company_id = os.getenv("RAG_COMPANY_ID", "").strip()
    if company_id.isdigit():
        return int(company_id)

    legacy_tenant = os.getenv("RAG_TENANT_ID", "").strip()
    if legacy_tenant.isdigit():
        return int(legacy_tenant)

    return 1


def _parse_embedding(value) -> list[float]:
    """Parse embedding vectors returned by Supabase/PostgREST."""
    if value is None:
        return []
    if isinstance(value, list):
        return [float(item) for item in value]
    if isinstance(value, str):
        import json

        text = value.strip()
        if text.startswith("["):
            return [float(item) for item in json.loads(text)]
    return []


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0

    dot_product = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot_product / (left_norm * right_norm)


def _retrieve_rows_via_company_id(
    supabase,
    table_name: str,
    company_id: int,
    query_embedding: list[float],
    top_k: int,
) -> list[dict]:
    """
    Fallback retrieval when the RPC still references the old tenant_id column.

    Filters by company_id and ranks chunks locally by cosine similarity.
    """
    response = (
        supabase.table(table_name)
        .select("id, company_id, chunk_id, content, metadata, embedding")
        .eq("company_id", company_id)
        .execute()
    )
    rows = response.data or []

    scored_rows: list[tuple[float, dict]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        embedding = _parse_embedding(row.get("embedding"))
        if not embedding:
            continue

        scored_row = dict(row)
        scored_row["similarity"] = _cosine_similarity(query_embedding, embedding)
        scored_rows.append((scored_row["similarity"], scored_row))

    scored_rows.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in scored_rows[:top_k]]


def _retrieve_booking_policy_rows(
    supabase,
    table_name: str,
    company_id: int,
    intent_json: dict,
) -> list[dict]:
    """Fetch the canonical service-information family for booking choices.

    Some older boarding price chunks are tagged ``general`` in metadata, so a
    pure service_type filter cannot discover them reliably.
    """
    scenario = str(intent_json.get("scenario_intent") or "").strip()
    if scenario not in {"GET_BOOKING_SERVICE_OPTIONS", "SERVICE_INFORMATION"}:
        return []

    entities = dict(intent_json.get("entities") or {})
    service = str(
        intent_json.get("service_type") or entities.get("service_type") or ""
    ).strip().upper()
    pet_type = str(entities.get("pet_type") or "").strip().upper()

    if scenario == "SERVICE_INFORMATION" and service != "GROOMING":
        return []

    if scenario == "SERVICE_INFORMATION" and service == "GROOMING":
        # Pull the actual species price matrices instead of trusting a generic
        # vector top-k that often ranks add-on or heading chunks first.
        chunk_pattern = (
            "service_information_2_1_%"
            if pet_type == "CAT"
            else "service_information_2_2_%"
        )
    else:
        chunk_pattern = {
        "GROOMING": "service_information_2_%",
        "DAYCARE": "service_information_3_%",
        "BOARDING": (
            "service_information_1_1"
            if pet_type == "CAT"
            else "service_information_1_2"
        ),
        }.get(service)
    if not chunk_pattern:
        return []

    query = (
        supabase.table(table_name)
        .select("id, company_id, chunk_id, content, metadata")
        .eq("company_id", company_id)
    )
    if "%" in chunk_pattern:
        query = query.like("chunk_id", chunk_pattern)
    else:
        query = query.eq("chunk_id", chunk_pattern)
    rows = query.execute().data or []

    normalized_rows: list[dict] = []
    for row in rows:
        normalized = dict(row)
        metadata = dict(normalized.get("metadata") or {})
        metadata["service_type"] = service.lower()
        normalized["metadata"] = metadata
        normalized.setdefault("similarity", 1.0)
        normalized_rows.append(normalized)
    return normalized_rows


def _map_row_to_chunk(row: dict) -> dict:
    """Map Supabase RPC row to the standard RAG chunk format."""
    row_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}

    return {
        "chunk_id": row_metadata.get("chunk_id") or row.get("chunk_id") or row.get("id") or "unknown",
        "text": row.get("content") or row.get("text") or row.get("chunk_text") or "",
        "source": (
            row_metadata.get("document_file_name")
            or row.get("source_file")
            or row.get("source")
            or row.get("file_name")
            or "Unknown"
        ),
        "score": float(row.get("similarity") or row.get("score") or 0.0),
        "metadata": {
            "company_id": row.get("company_id") or row_metadata.get("company_id"),
            "tenant_id": row_metadata.get("tenant_id") or row.get("tenant_id"),
            "service_type": row_metadata.get("service_type") or row.get("service_type"),
            "pet_type": row_metadata.get("pet_type") or row.get("pet_type"),
            "service_info": row_metadata.get("service_info") or row.get("service_info"),
            "section_title": row_metadata.get("section_title") or row.get("section_title"),
            "sub_header": row_metadata.get("sub_header") or row.get("sub_header"),
            "main_header": row_metadata.get("main_header") or row.get("main_header"),
            "section_path": row_metadata.get("section_path") or row.get("section_path"),
        },
    }


def _extract_row_metadata(row: dict) -> dict:
    """Extract metadata from an RPC row for debug output."""
    if isinstance(row.get("metadata"), dict):
        return row["metadata"]

    return {
        key: row.get(key)
        for key in [
            "company_id",
            "tenant_id",
            "service_type",
            "pet_type",
            "service_info",
            "section_title",
            "sub_header",
            "main_header",
            "section_path",
        ]
        if row.get(key) is not None
    }


def _build_rag_debug(
    rpc_name: str,
    table_name: str,
    filter_tenant: str | None,
    filter_company_id: int | None,
    filter_metadata: dict,
    retrieval_mode: str,
    rows: list,
    raw_chunks: list[dict],
    bad_filtered_chunks: list[dict],
    service_filtered_chunks: list[dict],
    final_context_chunks: list[dict],
    removed_bad_chunks: list[dict],
    removed_wrong_service_chunks: list[dict],
    detected_service_type: str | None,
    required_service_type: str | None,
    all_bad_chunks_filtered_using_original: bool,
    service_filter_fallback_used: bool,
    retrieval_query: str = "",
    rewritten_query: str = "",
    query_rewrite_used: bool = False,
    query_rewrite_skipped_reason: str = "",
) -> dict:
    """Build safe RAG debug info without exposing secrets."""
    first_row = rows[0] if rows and isinstance(rows[0], dict) else {}

    final_context_policy = "top_2_default_max_3_broad_price_max_2"
    if service_filter_fallback_used:
        final_context_policy = (
            "service_filter_removed_all_using_bad_filtered_fallback_"
            + final_context_policy
        )

    raw_chunk_ids = [chunk.get("chunk_id") for chunk in raw_chunks]
    top_similarity_scores = [float(chunk.get("score") or 0.0) for chunk in raw_chunks]
    max_similarity = max(top_similarity_scores) if top_similarity_scores else 0.0
    avg_top_similarity = (
        sum(top_similarity_scores) / len(top_similarity_scores)
        if top_similarity_scores
        else 0.0
    )

    return {
        "rpc_name": rpc_name,
        "table_name": table_name,
        "retrieval_mode": retrieval_mode,
        "retrieval_query": retrieval_query,
        "rewritten_query": rewritten_query,
        "query_rewrite_used": query_rewrite_used,
        "query_rewrite_skipped_reason": query_rewrite_skipped_reason,
        "filter_tenant_sent": filter_tenant,
        "filter_company_id_sent": filter_company_id,
        "filter_metadata_sent": filter_metadata,
        "detected_service_type": detected_service_type,
        "required_service_type": required_service_type,
        "raw_result_count": len(raw_chunks),
        "raw_chunk_ids": raw_chunk_ids,
        "top_similarity_scores": top_similarity_scores,
        "max_similarity": max_similarity,
        "avg_top_similarity": avg_top_similarity,
        "bad_filtered_result_count": len(bad_filtered_chunks),
        "service_filtered_result_count": len(service_filtered_chunks),
        "removed_bad_chunks": removed_bad_chunks,
        "removed_wrong_service_chunks": removed_wrong_service_chunks,
        "final_context_chunk_count": len(final_context_chunks),
        "final_chunk_ids_used": [chunk.get("chunk_id") for chunk in final_context_chunks],
        "final_context_policy": final_context_policy,
        "all_bad_chunks_filtered_using_original": all_bad_chunks_filtered_using_original,
        "service_filter_fallback_used": service_filter_fallback_used,
        "raw_similarity_scores": [
            round(float(chunk.get("score") or 0.0), 4) for chunk in raw_chunks
        ],
        "raw_chunk_ids": [chunk.get("chunk_id") for chunk in raw_chunks],
        "first_result_keys": list(first_row.keys()) if first_row else [],
        "first_result_metadata": _extract_row_metadata(first_row) if first_row else None,
    }


def real_rag_retrieve(
    user_message: str,
    intent_json: dict,
    retrieval_query: str | None = None,
    rewrite_debug: dict | None = None,
) -> dict:
    """
    Retrieve RAG chunks from Supabase pgvector via RPC.

    Flow:
    1. Embed retrieval query (rewritten when enabled, otherwise original message)
    2. Call Supabase RPC
    3. Map raw chunks
    4. Filter bad/header-only chunks
    5. Filter wrong service_type chunks
    6. Select top matching chunks for final answer using the original user message
    """
    load_dotenv(override=not os.getenv("_EVAL_OVERRIDE_ACTIVE"))

    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    rpc_name = (
        os.getenv("RAG_RPC", "match_chunks_bge_large").strip()
        or "match_chunks_bge_large"
    )
    table_name = (
        os.getenv("RAG_TABLE", "chunks_bge_large").strip()
        or "chunks_bge_large"
    )
    top_k = int(os.getenv("RAG_TOP_K", "5"))
    # Booking option lookup must first collect a wider policy candidate set before
    # filtering by service metadata.  A generic top-5 can otherwise omit the room
    # policy chunks even when they exist, then incorrectly fall back to another
    # service's high-scoring chunk.
    retrieval_top_k = (
        max(top_k, 20)
        if str(intent_json.get("scenario_intent") or "").strip()
        == "GET_BOOKING_SERVICE_OPTIONS"
        else top_k
    )

    if not supabase_url or not supabase_key:
        raise ValueError("Supabase credentials are not configured")

    from supabase import create_client

    supabase = create_client(supabase_url, supabase_key)
    embed_text = (retrieval_query or user_message).strip() or user_message
    rewrite_meta = rewrite_debug or {}
    query_embedding = embed_query(embed_text)

    company_id = _get_rag_company_id()
    filter_tenant = os.getenv("RAG_TENANT_ID", "pawfect-demo").strip() or "pawfect-demo"
    filter_metadata: dict = {}
    retrieval_mode = "rpc"
    rows: list = []

    try:
        response = supabase.rpc(
            rpc_name,
            {
                "query_embedding": query_embedding,
                "match_count": retrieval_top_k,
                "filter_tenant": filter_tenant,
                "filter_metadata": filter_metadata,
            },
        ).execute()
        rows = response.data or []
    except Exception as error:
        error_text = str(error).lower()
        if (
            "tenant_id does not exist" in error_text
            or "company_id" in error_text
            or "42703" in error_text
        ):
            retrieval_mode = "company_id_local_vector"
            rows = _retrieve_rows_via_company_id(
                supabase,
                table_name,
                company_id,
                query_embedding,
                retrieval_top_k,
            )
        else:
            raise

    policy_rows = _retrieve_booking_policy_rows(
        supabase, table_name, company_id, intent_json
    )
    # When the canonical family exists, keep the final context policy-only.
    # This prevents operational/veterinary chunks from leaking into a simple
    # service or room selection response.
    combined_rows = policy_rows if policy_rows else rows
    unique_rows: list[dict] = []
    seen_chunk_ids: set[str] = set()
    for row in combined_rows:
        if not isinstance(row, dict):
            continue
        chunk_id = str(row.get("chunk_id") or row.get("id") or "")
        if chunk_id and chunk_id in seen_chunk_ids:
            continue
        if chunk_id:
            seen_chunk_ids.add(chunk_id)
        unique_rows.append(row)

    raw_chunks = [_map_row_to_chunk(row) for row in unique_rows]
    raw_chunks = [chunk for chunk in raw_chunks if chunk["text"]]

    bad_filtered_chunks, removed_bad_chunks, all_bad_chunks_filtered_using_original = (
        filter_useful_chunks(raw_chunks)
    )

    detected_service_type, required_service_type = detect_required_service_type(
        intent_json, user_message
    )
    service_filtered_chunks, removed_wrong_service_chunks, service_filter_fallback_used = (
        filter_by_required_service(bad_filtered_chunks, required_service_type)
    )
    from retrieval_request import resolve_price_context

    price_context = resolve_price_context(user_message, dict(intent_json.get("entities") or {}))
    final_context_chunks = select_final_context_chunks(
        service_filtered_chunks,
        user_message,
        price_context=price_context,
    )

    return {
        "chunks": final_context_chunks,
        "rag_debug": _build_rag_debug(
            rpc_name,
            table_name,
            filter_tenant,
            company_id,
            filter_metadata,
            retrieval_mode,
            rows,
            raw_chunks,
            bad_filtered_chunks,
            service_filtered_chunks,
            final_context_chunks,
            removed_bad_chunks,
            removed_wrong_service_chunks,
            detected_service_type,
            required_service_type,
            all_bad_chunks_filtered_using_original,
            service_filter_fallback_used,
            retrieval_query=embed_text,
            rewritten_query=rewrite_meta.get("rewritten_query", ""),
            query_rewrite_used=bool(rewrite_meta.get("query_rewrite_used", False)),
            query_rewrite_skipped_reason=rewrite_meta.get("query_rewrite_skipped_reason", ""),
        ),
    }


def retrieve_rag_context(user_message: str, intent_json: dict, route: str, session=None) -> dict:
    """
    Safely retrieve RAG context using Supabase or mock fallback.
    """
    load_dotenv(override=not os.getenv("_EVAL_OVERRIDE_ACTIVE"))

    rag_provider_config = os.getenv("RAG_PROVIDER", "mock").strip().lower() or "mock"
    rewrite_result = {
        "retrieval_query": user_message,
        "rewritten_query": "",
        "query_rewrite_used": False,
        "query_rewrite_skipped_reason": "route_not_rag",
    }

    if route not in RAG_ROUTES:
        return {
            "rag_used": False,
            "rag_reliable": False,
            "rag_provider_config": rag_provider_config,
            "rag_provider_used": "none",
            "rag_error": None,
            "rag_context": [],
            "rag_debug": None,
            "retrieval_query": user_message,
            "rewritten_query": "",
            "query_rewrite_used": False,
            "query_rewrite_skipped_reason": rewrite_result["query_rewrite_skipped_reason"],
        }

    from booking_service_info import build_service_info_retrieval_query

    retrieval_query = build_service_info_retrieval_query(
        user_message, intent_json, session=session
    )
    rewrite_result = {
        "retrieval_query": retrieval_query,
        "rewritten_query": "",
        "query_rewrite_used": False,
        "query_rewrite_skipped_reason": "disabled",
    }

    if rag_provider_config == "supabase":
        supabase_url = os.getenv("SUPABASE_URL", "").strip()
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

        if not supabase_url or not supabase_key:
            if should_reraise_on_error():
                raise RuntimeError("Supabase credentials are not configured")
            if is_eval_strict_mode():
                return {
                    "rag_used": False,
                    "rag_provider_config": rag_provider_config,
                    "rag_provider_used": "supabase",
                    "rag_error": "Supabase credentials are not configured",
                    "rag_context": [],
                    "rag_debug": None,
                    **rewrite_result,
                }
            mock_chunks = mock_rag_retrieve(user_message, intent_json, route)
            return {
                "rag_used": len(mock_chunks) > 0,
                "rag_provider_config": rag_provider_config,
                "rag_provider_used": "mock_fallback",
                "rag_error": "Supabase credentials are not configured",
                "rag_context": mock_chunks,
                "rag_debug": None,
                **rewrite_result,
            }

        try:
            result = real_rag_retrieve(
                user_message,
                intent_json,
                retrieval_query=retrieval_query,
                rewrite_debug=rewrite_result,
            )
            raw_count = result["rag_debug"].get("raw_result_count", 0)

            if raw_count > 0:
                return _finalize_rag_response(
                    result["chunks"],
                    rag_provider_config=rag_provider_config,
                    rag_provider_used="supabase",
                    rag_error=None,
                    rewrite_result=rewrite_result,
                    user_message=user_message,
                    intent_json=intent_json,
                    rag_debug=result["rag_debug"],
                )

            if is_eval_strict_mode():
                return {
                    "rag_used": False,
                    "rag_reliable": False,
                    "rag_provider_used": "supabase",
                    "rag_error": "Supabase returned empty chunks",
                    "rag_context": [],
                    "rag_debug": result["rag_debug"],
                    **rewrite_result,
                }
            mock_chunks = mock_rag_retrieve(user_message, intent_json, route)
            return _finalize_rag_response(
                mock_chunks,
                rag_provider_config=rag_provider_config,
                rag_provider_used="mock_fallback",
                rag_error="Supabase returned empty chunks",
                rewrite_result=rewrite_result,
                user_message=user_message,
                intent_json=intent_json,
                rag_debug=result["rag_debug"],
            )
        except Exception as error:
            if should_reraise_on_error():
                raise
            if is_eval_strict_mode():
                return {
                    "rag_used": False,
                    "rag_reliable": False,
                    "rag_provider_used": "supabase",
                    "rag_error": _safe_error_message(error),
                    "rag_context": [],
                    "rag_debug": None,
                    **rewrite_result,
                }
            mock_chunks = mock_rag_retrieve(user_message, intent_json, route)
            return _finalize_rag_response(
                mock_chunks,
                rag_provider_config=rag_provider_config,
                rag_provider_used="mock_fallback",
                rag_error=_safe_error_message(error),
                rewrite_result=rewrite_result,
                user_message=user_message,
                intent_json=intent_json,
                rag_debug=None,
            )

    if is_eval_strict_mode():
        return {
            "rag_used": False,
            "rag_reliable": False,
            "rag_provider_used": "supabase",
            "rag_error": f"RAG_PROVIDER must be supabase during evaluation (got {rag_provider_config})",
            "rag_context": [],
            "rag_debug": None,
            **rewrite_result,
        }

    mock_chunks = mock_rag_retrieve(user_message, intent_json, route)
    return _finalize_rag_response(
        mock_chunks,
        rag_provider_config=rag_provider_config,
        rag_provider_used="mock",
        rag_error=None,
        rewrite_result=rewrite_result,
        user_message=user_message,
        intent_json=intent_json,
    )


def _get_rag_min_similarity_threshold() -> float:
    load_dotenv(override=not os.getenv("_EVAL_OVERRIDE_ACTIVE"))
    raw = os.getenv("RAG_MIN_SIMILARITY_THRESHOLD", "0.20").strip()
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.20


def _evaluate_rag_reliability(chunks: list, rag_error: str | None) -> bool:
    if rag_error:
        return False
    if not chunks:
        return False
    threshold = _get_rag_min_similarity_threshold()
    scores = [float(chunk.get("score") or 0.0) for chunk in chunks]
    return max(scores) >= threshold if scores else False


def _finalize_rag_response(
    chunks: list,
    *,
    rag_provider_config: str,
    rag_provider_used: str,
    rag_error: str | None,
    rewrite_result: dict,
    user_message: str,
    intent_json: dict,
    rag_debug=None,
) -> dict:
    from retrieval_request import build_retrieval_request, filter_rag_chunks

    request = build_retrieval_request(user_message, intent_json)
    filtered = filter_rag_chunks(chunks, request)
    intent_json["retrieval_request"] = request.to_dict()
    rag_reliable = _evaluate_rag_reliability(filtered, rag_error)
    data_found = bool(rag_reliable and filtered)
    handoff_required = False
    handoff_reason = None
    if rag_error:
        handoff_required = True
        handoff_reason = "RAG_RETRIEVAL_ERROR"
    elif not rag_reliable:
        handoff_required = True
        handoff_reason = "RAG_CONTEXT_NOT_FOUND"
    return {
        "rag_used": len(filtered) > 0,
        "rag_reliable": rag_reliable,
        "rag_provider_config": rag_provider_config,
        "rag_provider_used": rag_provider_used,
        "rag_error": rag_error,
        "rag_context": filtered,
        "rag_debug": rag_debug,
        "success": rag_error is None,
        "data_found": data_found,
        "action": "retrieve_rag_context",
        "data": {"chunk_count": len(filtered), "chunks": filtered},
        "error": rag_error,
        "handoff_required": handoff_required,
        "handoff_reason": handoff_reason,
        **rewrite_result,
    }
