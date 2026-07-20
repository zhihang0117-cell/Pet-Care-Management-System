"""Compute retrieval metrics and write results."""

import json
import shutil
import time
from pathlib import Path

import pandas as pd

from app.config import MAX_RETRIEVE_K, MODELS, RESULTS_DIR, TOP_K_VALUES
from app.services.chunking import load_chunks
from app.services.eval_matching import (
    find_relevant_chunk_ids,
    is_chunk_relevant,
)
from app.services.metadata_fields import (
    chunk_service_type,
    eval_dataset_type,
    eval_expected_pet_type,
    eval_query_text,
    eval_service_type,
)
from app.services.eval_prep import QUERIES_JSONL
from app.services.metadata_schema import ALLOWED_SERVICE_TYPES
from app.services.retrieval_service import metadata_aware_search, detect_retrieval_metadata
from app.services.supabase_store import SupabaseVectorStore, reset_supabase_client

_CSV_ENCODING = "utf-8"
_REFRESH_EVERY = 200
_SEARCH_RETRIES = 10


def _is_transient_rpc_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return any(
        token in name or token in text
        for token in (
            "remoteprotocolerror",
            "streamreset",
            "connecterror",
            "apiconnectionerror",
            "readtimeout",
            "writetimeout",
            "connecttimeout",
            "connectionterminated",
            "server disconnected",
            "connection reset",
            "forcibly closed",
            "winerror 10054",
            "temporarily unavailable",
            "broken pipe",
            "protocolerror",
            "connection error",
        )
    )


def _search_with_retry(
    store: SupabaseVectorStore,
    *,
    model_key: str,
    tenant_id: str,
    query_text: str,
    meta: dict,
    mode: str,
    k: int,
) -> tuple[SupabaseVectorStore, dict]:
    """Run metadata-aware search; refresh clients on transient transport failures."""
    from app.services.embeddings import clear_embedder_cache

    current = store
    last_exc: BaseException | None = None
    for attempt in range(_SEARCH_RETRIES):
        try:
            return current, metadata_aware_search(
                current,
                query_text,
                meta,
                mode=mode,
                k=k,
            )
        except Exception as exc:  # noqa: BLE001 — retry only transient transport failures
            last_exc = exc
            transient = _is_transient_rpc_error(exc)
            if not transient:
                raise
            wait = min(45.0, 2.0 * (2**attempt))
            print(
                f"[{MODELS[model_key]['display_name']}] transient RPC error "
                f"(attempt {attempt + 1}/{_SEARCH_RETRIES}): {exc!s}; sleep {wait:.1f}s",
                flush=True,
            )
            time.sleep(wait)
            reset_supabase_client()
            # OpenAI httpx clients can go stale after WinError 10054; rebuild embedder too.
            clear_embedder_cache(model_key)
            current = SupabaseVectorStore.from_model(model_key, tenant_id)
    assert last_exc is not None
    raise last_exc


def _checkpoint_path(display_name: str) -> Path:
    return RESULTS_DIR / display_name / "eval_checkpoint.jsonl"


def _load_checkpoint(path: Path) -> dict[str, dict]:
    done: dict[str, dict] = {}
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                print(
                    f"[checkpoint] skipping corrupt line {line_no} in {path.name}",
                    flush=True,
                )
                continue
            qid = str(row.get("query_id") or "")
            if qid:
                done[qid] = row
    return done


def _append_checkpoint(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    top_k = retrieved[:k]
    if k == 0:
        return 0.0
    hits = sum(1 for cid in top_k if cid in relevant)
    return hits / k


def _recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    top_k = retrieved[:k]
    hits = sum(1 for cid in top_k if cid in relevant)
    return hits / len(relevant)


def _f1_at_k(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _reciprocal_rank_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    for rank, cid in enumerate(retrieved[:k], start=1):
        if cid in relevant:
            return 1.0 / rank
    return 0.0


def _retrieval_filter(query: dict, tenant_id: str) -> dict:
    """Legacy helper — tenant is always applied by SupabaseVectorStore."""
    return {"tenant_id": tenant_id}


def _relevant_chunk_ids(eval_row: dict, chunk_map: dict[str, dict]) -> set[str]:
    precomputed = set(eval_row.get("relevant_chunk_ids") or [])
    if precomputed:
        return precomputed
    return set(find_relevant_chunk_ids(list(chunk_map.values()), eval_row))


def _is_retrieved_relevant(chunk: dict | None, eval_row: dict) -> bool:
    if not chunk:
        return False
    return is_chunk_relevant(chunk, eval_row)


def evaluate_retrieval(
    vectorstore: SupabaseVectorStore,
    model_key: str,
    tenant_id: str,
    chunking_version: str = "section-aware",
    *,
    rerank_enabled: bool = False,
    rerank_candidate_n: int = 0,
    rerank_model: str = "",
) -> dict:
    display_name = MODELS[model_key]["display_name"]
    queries_path = QUERIES_JSONL
    if not queries_path.exists():
        raise FileNotFoundError(
            "Eval queries not found. Upload an eval CSV first, then prepare queries."
        )

    queries = [
        json.loads(line)
        for line in queries_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    retrieval_rows: list[dict] = []
    per_query_metrics: list[dict] = []
    scenario_rows: list[dict] = []
    chunk_map = {c["chunk_id"]: c for c in load_chunks()}
    store = vectorstore
    total = len(queries)
    eval_t0 = time.perf_counter()
    ckpt_path = _checkpoint_path(display_name)
    checkpointed = _load_checkpoint(ckpt_path)
    done_ids = set(checkpointed.keys())
    if done_ids:
        print(
            f"[{display_name}] resuming from checkpoint: {len(done_ids)}/{total} done",
            flush=True,
        )
        for payload in checkpointed.values():
            per_query_metrics.append(payload["row_metrics"])
            scenario_rows.append(payload["scenario_row"])
            retrieval_rows.append(payload["retrieval_row"])

    processed = 0
    for idx, q in enumerate(queries):
        qid = str(q.get("query_id") or "")
        if qid and qid in done_ids:
            continue

        done_count = len(per_query_metrics)
        if processed > 0 and processed % _REFRESH_EVERY == 0:
            reset_supabase_client()
            store = SupabaseVectorStore.from_model(model_key, tenant_id)
            elapsed = time.perf_counter() - eval_t0
            rate = processed / elapsed if elapsed > 0 else 0.0
            print(
                f"[{display_name}] {done_count}/{total} queries "
                f"({rate:.2f} q/s, refreshed Supabase client)",
                flush=True,
            )
        elif processed > 0 and processed % 50 == 0:
            elapsed = time.perf_counter() - eval_t0
            rate = processed / elapsed if elapsed > 0 else 0.0
            remaining = total - done_count
            eta = remaining / rate if rate > 0 else 0.0
            print(
                f"[{display_name}] {done_count}/{total} queries "
                f"({rate:.2f} q/s, ETA {eta/60:.1f} min)",
                flush=True,
            )

        query_text = eval_query_text(q)
        relevant = _relevant_chunk_ids(q, chunk_map)
        meta = detect_retrieval_metadata(tenant_id, query_text=query_text, eval_row=q)
        start = time.perf_counter()
        embed_k = MAX_RETRIEVE_K
        store, search = _search_with_retry(
            store,
            model_key=model_key,
            tenant_id=tenant_id,
            query_text=query_text,
            meta=meta,
            mode="topk",
            k=embed_k,
        )
        latency_ms = (time.perf_counter() - start) * 1000

        final_docs_meta = search["hits"]
        # Rebuild Document-like access from hits for logging compatibility
        retrieved_ids = [h["chunk_id"] for h in final_docs_meta][: max(TOP_K_VALUES)]
        retrieved_texts = [h.get("text", "") for h in final_docs_meta][: max(TOP_K_VALUES)]
        embed_retrieved_ids = retrieved_ids
        embed_retrieved_texts = retrieved_texts
        embed_scores = [float(h.get("similarity") or 0.0) for h in final_docs_meta]

        filter_log = {
            "total_chunks": store.count(),
            "after_dataset_filter": 0,
            "after_service_filter": 0,
            "after_pet_filter": 0,
            "filter_mode": search.get("filter_mode", ""),
            "final_top_k": len(retrieved_ids),
            "best_score": search.get("best_score", 0.0),
            "score_band": search.get("score_band", ""),
            "low_confidence": search.get("low_confidence", False),
            "fallback_attempts": search.get("fallback_attempts", []),
        }
        for attempt in search.get("fallback_attempts") or []:
            fields = set(attempt.get("fields") or [])
            if "dataset" in fields:
                filter_log["after_dataset_filter"] = attempt.get("result_count", 0)
            if "service" in fields:
                filter_log["after_service_filter"] = attempt.get("result_count", 0)
            if "pet" in fields:
                filter_log["after_pet_filter"] = attempt.get("result_count", 0)

        filtered_docs_ids = retrieved_ids
        # Score against precomputed GT ids only (keeps recall/precision in [0, 1]).
        retrieved_relevant_flags = [cid in relevant for cid in retrieved_ids]

        row_metrics = {
            "query_id": q.get("query_id", ""),
            "query": query_text,
            "language": q.get("language", "unknown"),
            "category": q.get("category", ""),
            "dataset_type": eval_dataset_type(q),
            "service_type": eval_service_type(q),
            "expected_pet_type": eval_expected_pet_type(q),
        }
        for k in TOP_K_VALUES:
            top_ids = retrieved_ids[:k]
            top_flags = retrieved_relevant_flags[:k]
            top_scores = embed_scores[:k]
            hits = sum(1 for flag in top_flags if flag)
            precision = hits / k if k else 0.0
            recall = hits / len(relevant) if relevant else 0.0
            row_metrics[f"precision@{k}"] = precision
            row_metrics[f"recall@{k}"] = recall
            row_metrics[f"f1@{k}"] = _f1_at_k(precision, recall)
            # No-GT signal: mean cosine similarity of the model's own Top-K hits
            row_metrics[f"similarity@{k}"] = (
                sum(top_scores) / len(top_scores) if top_scores else 0.0
            )
            row_metrics[f"hit@{k}"] = float(any(top_flags))
            row_metrics[f"mrr@{k}"] = _reciprocal_rank_at_k(retrieved_ids, relevant, k)

        row_metrics["top1_similarity"] = float(embed_scores[0]) if embed_scores else 0.0
        row_metrics["latency_ms"] = latency_ms
        per_query_metrics.append(row_metrics)

        top_chunk = chunk_map.get(retrieved_ids[0]) if retrieved_ids else None
        actual_service = eval_service_type(q)
        predicted_service = chunk_service_type(top_chunk) if top_chunk else "unknown"
        scenario_row = {
            "query_id": q.get("query_id", ""),
            "actual_scenario": actual_service,
            "predicted_scenario": predicted_service,
        }
        scenario_rows.append(scenario_row)

        retrieval_row = {
            "query_id": q.get("query_id", ""),
            "language": q.get("language", "unknown"),
            "dataset_type": eval_dataset_type(q),
            "query": query_text,
            "service_type": eval_service_type(q),
            "expected_pet_type": eval_expected_pet_type(q),
            "relevant_chunk_ids": "|".join(sorted(relevant)),
            "embedding_candidate_chunk_ids": "|".join(embed_retrieved_ids),
            "embedding_candidate_scores": "|".join(str(s) for s in embed_scores),
            "total_chunks": filter_log["total_chunks"],
            "after_dataset_filter": filter_log["after_dataset_filter"],
            "after_service_filter": filter_log["after_service_filter"],
            "after_pet_filter": filter_log["after_pet_filter"],
            "filter_mode": filter_log["filter_mode"],
            "best_score": filter_log.get("best_score", 0.0),
            "score_band": filter_log.get("score_band", ""),
            "low_confidence": int(bool(filter_log.get("low_confidence"))),
            "metadata_filtered_chunk_ids": "|".join(filtered_docs_ids),
            "retrieved_chunk_ids": "|".join(retrieved_ids),
            "final_top_k": len(retrieved_ids),
            "retrieved_embedding_scores": "|".join(
                str(float(h.get("similarity") or 0.0))
                for h in final_docs_meta[: max(TOP_K_VALUES)]
            ),
            "actual_scenario": actual_service,
            "predicted_scenario": predicted_service,
            "recall@1": row_metrics["recall@1"],
            "recall@3": row_metrics["recall@3"],
            "recall@5": row_metrics["recall@5"],
            "precision@1": row_metrics["precision@1"],
            "precision@3": row_metrics["precision@3"],
            "precision@5": row_metrics["precision@5"],
            "f1@3": row_metrics["f1@3"],
            "hit_at_1": int(bool(retrieved_relevant_flags[:1] and retrieved_relevant_flags[0])),
            "hit_at_3": int(any(retrieved_relevant_flags[:3])),
            "hit_at_5": int(any(retrieved_relevant_flags[:5])),
            "mrr_at_5": round(row_metrics["mrr@5"], 6),
            "latency_ms": round(latency_ms, 2),
            "relevant_texts": " ||| ".join(
                chunk_map.get(cid, {}).get("text", "") for cid in relevant if cid in chunk_map
            ),
            "embedding_candidate_texts": " ||| ".join(embed_retrieved_texts[:embed_k]),
            "retrieved_texts": " ||| ".join(retrieved_texts),
        }
        retrieval_rows.append(retrieval_row)

        if qid:
            _append_checkpoint(
                ckpt_path,
                {
                    "query_id": qid,
                    "row_metrics": row_metrics,
                    "scenario_row": scenario_row,
                    "retrieval_row": retrieval_row,
                },
            )
            done_ids.add(qid)
        processed += 1

    metrics_df = pd.DataFrame(per_query_metrics)

    summary = {
        "model": display_name,
        "model_key": model_key,
        "tenant_id": tenant_id,
        "eval_dataset": "structured_eval_csv",
        "chunking_version": chunking_version,
        "metadata_filter": (
            "tenant_id → embed (no metadata RPC) → post-filter fallback "
            "(service_type+service_info → service_info → service_type → tenant_only) → Top-K; "
            "GT labels are eval-only (scoring) and never used to filter/rank/respond"
        ),
        "num_queries": len(queries),
        "rerank_enabled": False,
        "variant": "baseline",
    }
    for k in TOP_K_VALUES:
        summary[f"precision@{k}"] = round(metrics_df[f"precision@{k}"].mean(), 4)
        summary[f"recall@{k}"] = round(metrics_df[f"recall@{k}"].mean(), 4)
        summary[f"f1@{k}"] = round(metrics_df[f"f1@{k}"].mean(), 4)
        summary[f"similarity@{k}"] = round(metrics_df[f"similarity@{k}"].mean(), 4)
        summary[f"hit@{k}"] = round(metrics_df[f"hit@{k}"].mean(), 4)
        summary[f"mrr@{k}"] = round(metrics_df[f"mrr@{k}"].mean(), 4)
    summary["avg_top1_similarity"] = round(metrics_df["top1_similarity"].mean(), 4)
    summary["avg_latency_ms"] = round(metrics_df["latency_ms"].mean(), 2)
    summary["scenario_confusion_matrix"] = compute_scenario_confusion_matrix(
        scenario_rows,
        model_key=model_key,
    )

    by_language = {}
    for lang, group in metrics_df.groupby("language"):
        by_language[lang] = {f"f1@{k}": round(group[f"f1@{k}"].mean(), 4) for k in TOP_K_VALUES}
    summary["by_language"] = by_language

    out_dir = RESULTS_DIR / display_name
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "metrics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    pd.DataFrame(retrieval_rows).to_csv(
        out_dir / "retrieval_log.csv", index=False, encoding=_CSV_ENCODING
    )
    metrics_df.to_csv(out_dir / "per_query_metrics.csv", index=False, encoding=_CSV_ENCODING)
    pd.DataFrame(scenario_rows).to_csv(
        out_dir / "scenario_predictions.csv", index=False, encoding=_CSV_ENCODING
    )

    if ckpt_path.exists():
        ckpt_path.unlink()
        print(f"[{display_name}] checkpoint cleared after successful run", flush=True)

    _append_comparison(summary)
    return summary


def _canonical_model_key(model_key: str) -> str:
    return str(model_key).split(":")[0].strip()


def _comparison_rows_from_summary(summary: dict) -> list[dict]:
    model_key = _canonical_model_key(summary["model_key"])
    variant = summary.get("variant") or "baseline"
    rows = []
    for k in TOP_K_VALUES:
        rows.append(
            {
                "model": summary["model"],
                "model_key": model_key,
                "variant": variant,
                "k": k,
                "precision": summary[f"precision@{k}"],
                "recall": summary[f"recall@{k}"],
                "f1": summary[f"f1@{k}"],
                "hit": summary.get(f"hit@{k}", 0.0),
                "mrr": summary.get(f"mrr@{k}", 0.0),
                "similarity": summary.get(f"similarity@{k}", 0.0),
                "avg_top1_similarity": summary.get("avg_top1_similarity", 0.0),
                "avg_latency_ms": summary["avg_latency_ms"],
            }
        )
    return rows


def _dedupe_comparison_df(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the latest row-set per model (canonical model_key, last in file order)."""
    if df.empty:
        return df

    work = df.copy()
    if "variant" not in work.columns:
        work["variant"] = "baseline"

    last_variant: dict[str, str] = {}
    for model_key in work["model_key"]:
        canonical = _canonical_model_key(str(model_key))
        last_variant[canonical] = str(model_key)

    keep = set(last_variant.values())
    deduped = work[work["model_key"].isin(keep)].copy()
    deduped["model_key"] = deduped["model_key"].map(_canonical_model_key)
    return deduped.reset_index(drop=True)


def reset_comparison() -> None:
    """Clear comparison results before a full benchmark run."""
    comparison_path = RESULTS_DIR / "comparison.csv"
    if comparison_path.exists():
        comparison_path.unlink()


def _append_comparison(summary: dict) -> None:
    comparison_path = RESULTS_DIR / "comparison.csv"
    canonical = _canonical_model_key(summary["model_key"])
    new_df = pd.DataFrame(_comparison_rows_from_summary(summary))

    if comparison_path.exists():
        existing = pd.read_csv(comparison_path, encoding=_CSV_ENCODING)
        existing = _dedupe_comparison_df(existing)
        existing["canonical_key"] = existing["model_key"].map(_canonical_model_key)
        existing = existing[existing["canonical_key"] != canonical].drop(columns=["canonical_key"])
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = _dedupe_comparison_df(combined)
    combined.to_csv(comparison_path, index=False, encoding=_CSV_ENCODING)


def backup_baseline_comparison() -> None:
    src = RESULTS_DIR / "comparison.csv"
    dst = RESULTS_DIR / "comparison_baseline.csv"
    if src.exists() and not dst.exists():
        shutil.copy(src, dst)


def load_comparison() -> list[dict]:
    path = RESULTS_DIR / "comparison.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path, encoding=_CSV_ENCODING)
    df = _dedupe_comparison_df(df)
    return df.to_dict(orient="records")


def load_per_query_metrics(model_key: str) -> list[dict]:
    display_name = MODELS[model_key]["display_name"]
    path = RESULTS_DIR / display_name / "per_query_metrics.csv"
    if not path.exists():
        return []
    return pd.read_csv(path, encoding=_CSV_ENCODING).to_dict(orient="records")


def load_retrieval_log(model_key: str) -> list[dict]:
    display_name = MODELS[model_key]["display_name"]
    path = RESULTS_DIR / display_name / "retrieval_log.csv"
    if not path.exists():
        return []
    return pd.read_csv(path, encoding=_CSV_ENCODING).to_dict(orient="records")


def _split_ids(value: object) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []
    return [part for part in text.split("|") if part.strip()]


def compute_scenario_confusion_matrix(
    scenario_rows: list[dict[str, str]],
    *,
    model_key: str,
) -> dict:
    from sklearn.metrics import classification_report, confusion_matrix

    labels = sorted(ALLOWED_SERVICE_TYPES)
    actual = [row.get("actual_scenario", "unknown") for row in scenario_rows]
    predicted = [row.get("predicted_scenario", "unknown") for row in scenario_rows]

    if not scenario_rows:
        return {
            "model_key": model_key,
            "labels": labels,
            "matrix": [],
            "classification_report": {},
            "num_queries": 0,
        }

    matrix = confusion_matrix(actual, predicted, labels=labels)
    report = classification_report(
        actual,
        predicted,
        labels=labels,
        output_dict=True,
        zero_division=0,
    )
    return {
        "model_key": model_key,
        "labels": labels,
        "matrix": matrix.tolist(),
        "classification_report": report,
        "num_queries": len(scenario_rows),
    }


def compute_confusion_matrix(model_key: str, k: int = 3) -> dict:
    """Service-type confusion matrix for retrieved top-1 predictions."""
    from sklearn.metrics import classification_report, confusion_matrix

    if model_key not in MODELS:
        raise KeyError(model_key)

    display_name = MODELS[model_key]["display_name"]
    scenario_path = RESULTS_DIR / display_name / "scenario_predictions.csv"
    if not scenario_path.exists():
        rows = load_retrieval_log(model_key)
        scenario_rows = [
            {
                "actual_scenario": row.get("actual_scenario", "UNKNOWN"),
                "predicted_scenario": row.get("predicted_scenario", "UNKNOWN"),
            }
            for row in rows
        ]
    else:
        scenario_rows = pd.read_csv(scenario_path, encoding=_CSV_ENCODING).to_dict(orient="records")

    labels = sorted(ALLOWED_SERVICE_TYPES)
    actual = [row.get("actual_scenario", "unknown") for row in scenario_rows]
    predicted = [row.get("predicted_scenario", "unknown") for row in scenario_rows]

    if not scenario_rows:
        return {
            "model_key": model_key,
            "model": display_name,
            "k": k,
            "matrix": [],
            "labels": labels,
            "classification_report": {},
            "num_queries": 0,
        }

    matrix = confusion_matrix(actual, predicted, labels=labels)
    report = classification_report(
        actual,
        predicted,
        labels=labels,
        output_dict=True,
        zero_division=0,
    )

    return {
        "model_key": model_key,
        "model": display_name,
        "k": k,
        "matrix": matrix.tolist(),
        "labels": labels,
        "classification_report": report,
        "num_queries": len(scenario_rows),
    }



def load_all_confusion_matrices(k: int = 3) -> list[dict]:
    results = []
    for model_key in MODELS:
        display = MODELS[model_key]["display_name"]
        path = RESULTS_DIR / display / "retrieval_log.csv"
        if path.exists():
            results.append(compute_confusion_matrix(model_key, k=k))
    return results

