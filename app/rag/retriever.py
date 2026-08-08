from __future__ import annotations

import os
import re

from app.db.supabase_client import get_supabase_client
from app.rag.embeddings import embed_query

# Pinned intentionally — not read from RAG_TOP_K or any other env var, and
# not a caller-overridable parameter. Change this constant if the number of
# retrieved chunks ever needs to change; nothing else should control it.
TOP_K = 5

_PRICE_PATTERN = re.compile(r"\bRM\s*\d", re.I)
_LOW_VALUE_WORD_COUNT = 10

# match_chunks_bge_large's `similarity` is `1 - cosine_distance`, so it
# ranges roughly [-1, 1]. Because the RPC has no WHERE-clause cutoff, it
# always returns match_count rows ordered by distance even when nothing in
# the knowledge base is actually about the query — e.g. an off-topic
# question can still surface the closest (but unrelated) chunk at
# similarity ~0.2-0.3, and callers that only look at "did we get any rows
# back" then hand that chunk to the model as if it were real evidence.
# 0.35 is a conservative floor picked to sit below genuinely relevant
# BGE-large matches on this knowledge base's content (~0.5+) while still
# cutting the near-random tail; re-tune against real queries if it proves
# too strict/loose in practice.
MIN_SIMILARITY = 0.35

# Grooming price chunks bundle every size tier of a package into one
# paragraph at the document-ingestion level (e.g. "For S size cats (...) -
# ...; For M size cats (...) - ...; ..."), so retrieval always returns the
# whole table — there's no way to fetch "just size M" from the source. The
# model has been observed pasting the entire table back to the customer
# instead of quoting only their pet's row, so this extracts just the
# matching block deterministically rather than relying on the model to.
_SIZE_BLOCK_RE = re.compile(
    r"For\s+(?P<size>X{0,2}[SML])\s+size\s+\w+s?\s*\([^)]*\)\s*-\s*(?P<body>.+?)\."
    r"(?=\s*(?:For\s+X{0,2}[SML]\s+size|\Z))",
    re.IGNORECASE | re.DOTALL,
)


def _narrow_to_pet_size(content: str | None, pet_size: str | None) -> str | None:
    """
    If `content` is a by-size price table (multiple "For X size ... - ..."
    blocks), return just the block matching pet_size. Falls back to the
    original content unchanged if it isn't a size table, or the pet's size
    isn't one of the sizes present — never drop information the model
    might still need.
    """
    if not content or not pet_size:
        return content
    blocks = list(_SIZE_BLOCK_RE.finditer(content))
    if len(blocks) < 2:
        return content
    wanted = str(pet_size).strip().upper()
    for match in blocks:
        if match.group("size").strip().upper() == wanted:
            header = content.split("\n", 1)[0].strip()
            body = " ".join(match.group("body").split())
            return f"{header}\nFor {wanted} size - {body}."
    return content


def _is_low_value_chunk(content: str | None) -> bool:
    """
    Some chunks are just a bare section header re-stated with its number
    (e.g. "Dog Grooming Price: 2.2 Dog Grooming Price") — no actual price or
    policy content. These can still score highly on similarity (the header
    text matches the query well) and crowd out the real pricing/policy
    chunks underneath if the caller only looks at the top result. Drop them
    here rather than counting on every caller to notice they're empty.
    """
    text = " ".join((content or "").split())
    if not text:
        return True
    if _PRICE_PATTERN.search(text):
        return False
    return len(text.split()) < _LOW_VALUE_WORD_COUNT


class CompanyRAGRetriever:
    """
    Company-scoped RAG retrieval.

    BGE-large embeddings are precomputed and stored in Supabase
    (chunks_bge_large.embedding, pgvector). The query is embedded at
    request time (app/rag/embeddings.py) and matched via the RPC named by
    RAG_RPC (default match_chunks_bge_large — the deployed compatibility
    RPC that takes filter_tenant as text and casts to bigint company_id
    when numeric; see ai-backend's crud migration notes).

    NOTE: filter_tenant below is the actual deployed RPC's parameter name
    (a pre-existing SQL function signature) — do not rename that key, only
    the Python-side company_id it's populated from.
    """

    def search(
        self,
        company_id: str,
        query: str,
        service_type: str | None = None,
        pet_type: str | None = None,
        pet_size: str | None = None,
    ) -> list[dict]:
        """
        service_type (when given) narrows results via metadata containment —
        chunks are tagged metadata.service_type in {"grooming", "daycare",
        "boarding", "general"} (lowercase). Confirmed working against the
        deployed RPC. Leave it unset for a general/ambiguous enquiry — plain
        semantic search across all service types is the right default then.

        pet_type ("cat"/"dog") is filtered client-side, not via the RPC: the
        RPC's jsonb containment match can't express "cat OR all", so passing
        pet_type into filter_metadata would wrongly drop species-agnostic
        chunks (payment terms, add-ons, general info tagged pet_type=all).
        Instead, chunks tagged for the OTHER species are dropped here after
        retrieval — this is what actually stops dog pricing from leaking
        into a cat's grooming quote (and vice versa) when a species-neutral
        query pulls back a mix of cat/dog/all chunks in the same top-5.

        pet_size ("S"/"M"/"L"/"XL"/etc, from the pet's own record) narrows a
        by-size price table chunk down to just that pet's row — see
        _narrow_to_pet_size. Chunks that aren't a size table are returned
        unchanged.
        """
        rpc_name = os.getenv("RAG_RPC", "match_chunks_bge_large").strip() or "match_chunks_bge_large"
        query_embedding = embed_query(query)
        wanted_pet_type = pet_type.strip().lower() if pet_type else None

        # pet_type filtering happens client-side (see docstring), so if we
        # only ever pulled TOP_K raw rows, wrong-species chunks can occupy
        # slots in that raw pool (cat/dog pricing chunks are near-identical
        # in wording — "Bathing Packages", "Standard Bath - Groomers
        # Choice" — so they score similarly) and get filtered out, leaving
        # fewer than TOP_K results and starving the real answer even though
        # a correct chunk existed further down the ranking. Over-fetch a
        # larger raw pool when a species filter will apply, then trim back
        # to exactly TOP_K after filtering — the delivered result count
        # stays pinned at TOP_K either way.
        match_count = TOP_K * 4 if wanted_pet_type else TOP_K
        filter_metadata = {"service_type": service_type.strip().lower()} if service_type else {}
        params = {
            "query_embedding": query_embedding,
            "match_count": match_count,
            "filter_tenant": str(company_id),
            "filter_metadata": filter_metadata,
        }

        rows = get_supabase_client().rpc(rpc_name, params).execute().data or []

        results = []
        for row in rows:
            similarity = row.get("similarity")
            if similarity is not None and similarity < MIN_SIMILARITY:
                continue  # semantically unrelated to the query — not real evidence
            if _is_low_value_chunk(row.get("content")):
                continue
            chunk_pet_type = str((row.get("metadata") or {}).get("pet_type") or "").strip().lower()
            if wanted_pet_type and chunk_pet_type and chunk_pet_type not in {"all", wanted_pet_type}:
                continue  # wrong species for this pet — e.g. dog content while asking about a cat
            results.append(
                {
                    "content": _narrow_to_pet_size(row.get("content"), pet_size),
                    "metadata": row.get("metadata"),
                    "score": row.get("similarity"),
                }
            )
            if len(results) >= TOP_K:
                break
        return results
