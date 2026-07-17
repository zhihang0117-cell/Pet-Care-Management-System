from pathlib import Path
import json
import sys
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    BENCHMARK_MODEL_KEYS,
    DATA_EVAL,
    DATA_RAW,
    MAX_RETRIEVE_K,
    MAX_THRESHOLD_RESULTS,
    MODELS,
    SIMILARITY_THRESHOLD,
    settings,
)
from app.pipeline_state import StepStatus, pipeline_state
from app.schemas import (
    BenchmarkResponse,
    ChunksPreviewResponse,
    EvalQueriesRequest,
    EvalQueriesResponse,
    EvaluateResponse,
    IndexResponse,
    IngestResponse,
    ModelInfo,
    ModelsResponse,
    PipelineStatusResponse,
)
from app.services.chunking import (
    chunk_documents,
    get_chunking_breakdown,
    list_documents,
    load_chunks,
    auto_tag_chunks,
    relabel_chunks,
    remove_document,
)
from app.services.dm_test import load_dm_test_payload
from app.services.evaluation import (
    compute_confusion_matrix,
    evaluate_retrieval,
    load_all_confusion_matrices,
    load_comparison,
    load_per_query_metrics,
    load_retrieval_log,
    reset_comparison,
)
from app.services.eval_prep import (
    QUERIES_INPUT,
    QUERIES_CSV,
    QUERIES_JSONL,
    eval_queries_ready,
    list_source_files,
    load_eval_query_by_id,
    load_eval_rows,
    load_prepared_eval,
    load_text_queries,
    prepare_eval_set,
    save_eval_csv,
    save_eval_rows,
    save_text_queries,
)
from app.services.ingestion import is_supported_document, load_document
from app.services.metadata_fields import eval_query_text
from app.services.retrieval_service import retrieve_one_model
from app.services.supabase_store import SupabaseVectorStore, get_supabase_client
from app.api.production.documents import router as production_documents_router
from app.api.production.rag import router as production_rag_router

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

app = FastAPI(
    title="Pawfect RAG Experiment API",
    description="Multi-tenant RAG benchmark: chunk, embed, Supabase index, evaluate P/R/F1.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(production_documents_router, prefix="/api")
app.include_router(production_rag_router, prefix="/api")


def _ensure_eval_ready() -> None:
    if not QUERIES_INPUT.exists():
        raise HTTPException(
            status_code=400,
            detail="Add eval queries first (POST /eval/queries) — one question per line.",
        )
    if not load_chunks():
        raise HTTPException(status_code=400, detail="Upload Word documents before running evaluation.")
    # Always rebuild ground-truth mapping from current chunks (section IDs change after re-ingest).
    prepare_eval_set()


def _require_eval_for_job() -> None:
    if not QUERIES_INPUT.exists():
        raise ValueError("Add eval queries first (POST /eval/queries).")
    if not load_chunks():
        raise ValueError("Upload Word documents first.")
    prepare_eval_set()


@app.get("/health")
def health() -> dict[str, str]:
    url = settings.supabase_url.strip()
    key = settings.supabase_service_key.strip()
    openai_key = settings.openai_api_key.strip()
    placeholders = ("your-project", "your-service-role-key", "example.com", "sk-your")
    supabase_ok = bool(
        url
        and key
        and not any(p in url for p in placeholders)
        and not any(p in key for p in placeholders)
        and (key.startswith("eyJ") or key.startswith("sb_secret_"))
    )
    openai_ok = bool(
        openai_key
        and not any(p in openai_key for p in placeholders)
        and openai_key.startswith("sk-")
    )
    return {
        "status": "ok",
        "supabase_configured": str(supabase_ok).lower(),
        "openai_api_configured": str(openai_ok).lower(),
    }


@app.get("/models", response_model=ModelsResponse)
def list_models() -> ModelsResponse:
    return ModelsResponse(
        models=[
            ModelInfo(
                key=key,
                display_name=cfg["display_name"],
                dimensions=cfg["dimensions"],
                type=cfg["type"],
            )
            for key, cfg in MODELS.items()
            if key in BENCHMARK_MODEL_KEYS
        ]
    )


@app.get("/pipeline/status", response_model=PipelineStatusResponse)
def get_pipeline_status() -> PipelineStatusResponse:
    snap = pipeline_state.snapshot()
    return PipelineStatusResponse(**snap)


@app.get("/chunks/preview", response_model=ChunksPreviewResponse)
def preview_chunks() -> ChunksPreviewResponse:
    chunks = load_chunks()
    rule_chunks = [c for c in chunks if c.get("is_rule")]
    return ChunksPreviewResponse(
        total=len(chunks),
        rule_count=len(rule_chunks),
        samples=chunks[:5],
    )


@app.get("/chunks/breakdown")
def chunk_breakdown() -> list[dict[str, Any]]:
    return get_chunking_breakdown()


@app.get("/chunks/all")
def get_all_chunks() -> list[dict[str, Any]]:
    return load_chunks()


@app.post("/chunks/relabel")
def relabel_existing_chunks() -> dict[str, Any]:
    chunks = relabel_chunks()
    rule_count = sum(1 for c in chunks if c.get("is_rule"))
    pipeline_state.add_log(f"Relabeled {len(chunks)} chunks ({rule_count} content chunks)")
    return {"total": len(chunks), "rule_count": rule_count, "chunks": chunks}


@app.post("/chunks/auto-tag")
def auto_tag_existing_chunks(overwrite: bool = False) -> dict[str, Any]:
    chunks, stats = auto_tag_chunks(overwrite=overwrite)
    pipeline_state.add_log(
        f"Auto-tagged chunks: updated {stats['updated']}/{stats['total']} (overwrite={overwrite})"
    )
    return {"overwrite": overwrite, **stats, "chunks": chunks}


@app.post("/ingest", response_model=IngestResponse)
async def ingest_documents(
    tenant_id: str = Form(default=""),
    files: list[UploadFile] = File(...),
) -> IngestResponse:
    tenant = tenant_id or settings.default_tenant_id
    pipeline_state.current_job = "ingest"
    pipeline_state.tenant_id = tenant
    pipeline_state.set_step("upload", StepStatus.RUNNING, "Saving uploaded files")

    DATA_RAW.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    all_pages = []

    try:
        for upload in files:
            if not upload.filename or not is_supported_document(upload.filename):
                raise HTTPException(
                    status_code=400,
                    detail=f"Only Word (.docx) files supported: {upload.filename}",
                )

            dest = DATA_RAW / Path(upload.filename).name
            content = await upload.read()
            dest.write_bytes(content)
            saved.append(dest.name)
            all_pages.extend(load_document(dest))

        if not all_pages:
            raise HTTPException(status_code=400, detail="No text extracted from uploaded Word documents")

        pipeline_state.set_step("chunk", StepStatus.RUNNING, "Structure-aware chunking")
        # Merge into existing corpus so multiple documents accumulate.
        chunks = chunk_documents(all_pages, tenant, merge=True)
        rule_chunks = [c for c in chunks if c.get("is_rule")]
        corpus_files = sorted({c.get("source_file", "") for c in chunks if c.get("source_file")})

        if QUERIES_INPUT.exists():
            prepare_eval_set()

        pipeline_state.chunk_count = len(chunks)
        pipeline_state.set_step("upload", StepStatus.DONE, f"{len(saved)} file(s) saved")
        pipeline_state.set_step(
            "chunk",
            StepStatus.DONE,
            f"{len(chunks)} chunks across {len(corpus_files)} document(s)",
        )
        message = (
            f"Added/updated {len(saved)} file(s). "
            f"Corpus now has {len(corpus_files)} document(s), {len(chunks)} chunks."
        )
        pipeline_state.add_log(message)

        return IngestResponse(
            tenant_id=tenant,
            files_saved=saved,
            files_in_corpus=corpus_files,
            chunk_count=len(chunks),
            rule_chunk_count=len(rule_chunks),
            sample_chunks=chunks[:5],
            message=message,
        )
    except HTTPException:
        pipeline_state.set_step("upload", StepStatus.ERROR, "Upload failed")
        raise
    except Exception as exc:
        pipeline_state.set_step("chunk", StepStatus.ERROR, str(exc))
        pipeline_state.add_log(str(exc), level="error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        pipeline_state.current_job = None


@app.get("/chunks/sources")
def get_chunk_sources() -> list[str]:
    return list_source_files()


@app.get("/chunks/documents")
def get_chunk_documents() -> list[dict[str, Any]]:
    """List all ingested documents with per-file chunk counts."""
    return list_documents()


@app.delete("/chunks/documents/{source_file:path}")
def delete_chunk_document(source_file: str) -> dict[str, Any]:
    """Remove one document from the local corpus (re-index after this)."""
    name = Path(source_file).name
    chunks = remove_document(name)
    raw_path = DATA_RAW / name
    if raw_path.exists():
        raw_path.unlink()

    if QUERIES_INPUT.exists() and chunks:
        prepare_eval_set()

    pipeline_state.chunk_count = len(chunks)
    pipeline_state.add_log(f"Removed document {name}; {len(chunks)} chunks remain")
    return {
        "removed": name,
        "chunk_count": len(chunks),
        "documents": list_documents(),
    }


@app.get("/eval/queries")
def get_saved_queries() -> dict[str, Any]:
    if not QUERIES_INPUT.exists():
        return {"queries": [], "rows": [], "source_pdf": ""}
    rows, source_pdf = load_eval_rows()
    return {
        "queries": [r.get("query", "") for r in rows if str(r.get("query", "")).strip()],
        "rows": rows,
        "source_pdf": source_pdf,
    }


@app.delete("/eval/queries")
def clear_saved_queries() -> dict[str, Any]:
    deleted: list[str] = []
    for p in (QUERIES_INPUT, QUERIES_JSONL, QUERIES_CSV):
        try:
            if p.exists():
                p.unlink()
                deleted.append(p.name)
        except OSError:
            # best-effort cleanup; keep going
            pass
    return {"status": "ok", "deleted": deleted}


@app.get("/eval/prepared")
def get_prepared_eval() -> list[dict[str, Any]]:
    return load_prepared_eval()


@app.get("/eval/retrieval/{query_id}")
def get_eval_retrieval(
    query_id: str,
    model_key: str = "",
    tenant_id: str = "",
    mode: str = "threshold",
    k: int = MAX_RETRIEVE_K,
) -> dict[str, Any]:
    """Lazy-load retrieval: tenant → embed → metadata fallback post-filter → Top-K / Part 4.

    Vector search uses tenant_id only (no metadata in RPC). Metadata fallback is applied
    after embedding on the ranked candidate pool.
    """
    row = load_eval_query_by_id(query_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Query not found: {query_id}")

    query_text = eval_query_text(row)
    if not query_text:
        raise HTTPException(status_code=400, detail="Query has empty text.")

    key = model_key.strip() or BENCHMARK_MODEL_KEYS[0]
    if key not in MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {key}")

    retrieval_mode = "topk" if mode.strip().lower() == "topk" else "threshold"
    top_k = max(1, min(int(k), 20))
    tenant = tenant_id or pipeline_state.tenant_id or settings.default_tenant_id

    try:
        get_supabase_client()
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    result = retrieve_one_model(
        key, query_text, tenant, k=top_k, mode=retrieval_mode, eval_row=row
    )
    return {
        "query_id": str(row.get("query_id") or row.get("eval_id") or query_id),
        "query": query_text,
        "tenant_id": tenant,
        "mode": retrieval_mode,
        "k": top_k,
        "similarity_threshold": SIMILARITY_THRESHOLD,
        "max_threshold_results": MAX_THRESHOLD_RESULTS,
        "model_key": key,
        "result": result,
    }


@app.post("/eval/retrieval/run-model")
def run_model_retrieval(
    model_key: str = "",
    tenant_id: str = "",
    offset: int = 0,
    limit: int = 0,
    mode: str = "threshold",
    k: int = MAX_RETRIEVE_K,
) -> dict[str, Any]:
    """Run retrieval for eval queries (sequential).

    Flow: tenant_id → embed → metadata post-filter fallback → Top-K / Part 4 pool.
    """
    key = model_key.strip() or BENCHMARK_MODEL_KEYS[0]
    if key not in MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {key}")

    if not QUERIES_JSONL.exists():
        raise HTTPException(status_code=400, detail="No eval queries. Upload an eval CSV first.")

    retrieval_mode = "topk" if mode.strip().lower() == "topk" else "threshold"
    top_k = max(1, min(int(k), 20))
    tenant = tenant_id or pipeline_state.tenant_id or settings.default_tenant_id
    start = max(0, int(offset))
    page_size = max(0, int(limit))

    try:
        get_supabase_client()
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    all_rows: list[dict[str, Any]] = []
    for line in QUERIES_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        all_rows.append(json.loads(line))

    if not all_rows:
        raise HTTPException(status_code=400, detail="Eval query file is empty.")

    rows = all_rows[start:] if page_size <= 0 else all_rows[start : start + page_size]
    if not rows:
        return {
            "model_key": key,
            "display_name": MODELS[key]["display_name"],
            "tenant_id": tenant,
            "mode": retrieval_mode,
            "k": top_k,
            "similarity_threshold": SIMILARITY_THRESHOLD,
            "max_threshold_results": MAX_THRESHOLD_RESULTS,
            "offset": start,
            "total": len(all_rows),
            "query_count": 0,
            "results": [],
        }

    display = MODELS[key]["display_name"]
    if start == 0:
        if retrieval_mode == "topk":
            pipeline_state.add_log(
                f"Running tenant-scoped Top-{top_k} for {display} on {len(all_rows)} queries…"
            )
        else:
            pipeline_state.add_log(
                f"Running tenant-scoped retrieval (full ranked preview) for {display} "
                f"on {len(all_rows)} queries…"
            )

    results: list[dict[str, Any]] = []
    for i, row in enumerate(rows, start=start + 1):
        query_text = eval_query_text(row)
        qid = str(row.get("query_id") or row.get("eval_id") or f"Q{i:03d}")
        if not query_text:
            result = {
                "model_key": key,
                "display_name": display,
                "skipped": True,
                "reason": "empty query text",
                "low_confidence": False,
                "follow_up": "",
                "filter_mode": "",
                "hits": [],
            }
        else:
            raw = retrieve_one_model(
                key,
                query_text,
                tenant,
                k=top_k,
                mode=retrieval_mode,
                eval_row=row,
            )
            # Keep full chunk text so Part 4/5 UI can show complete hits.
            result = {
                **raw,
                "hits": raw.get("hits", []),
            }
        results.append({"query_id": qid, "query": query_text, "result": result})
        if i == 1 or i % 25 == 0 or i == len(all_rows):
            pipeline_state.add_log(f"{display}: {i}/{len(all_rows)} queries done")

    if start + len(rows) >= len(all_rows):
        pipeline_state.add_log(f"{display}: finished {len(all_rows)} queries")

    return {
        "model_key": key,
        "display_name": display,
        "tenant_id": tenant,
        "mode": retrieval_mode,
        "k": top_k,
        "similarity_threshold": SIMILARITY_THRESHOLD,
        "max_threshold_results": MAX_THRESHOLD_RESULTS,
        "offset": start,
        "total": len(all_rows),
        "query_count": len(results),
        "results": results,
    }


@app.post("/eval/queries", response_model=EvalQueriesResponse)
def save_eval_queries(body: EvalQueriesRequest) -> EvalQueriesResponse:
    try:
        if body.rows:
            saved_rows = save_eval_rows([r.model_dump() for r in body.rows], source_pdf=body.source_pdf or "")
            saved = [row["query"] for row in saved_rows]
        else:
            saved = save_text_queries(body.queries, body.source_pdf or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    prepared = False
    prepared_rows: list[dict[str, Any]] = []
    query_count = len(saved)
    message = f"Saved {query_count} query(s). Upload Word documents first if not done yet."

    try:
        if load_chunks():
            prepare_eval_set()
            prepared = True
            prepared_rows = load_prepared_eval()
            message = f"Saved {query_count} query(s) and mapped them to document chunks."
            pipeline_state.add_log(message)
    except FileNotFoundError:
        message = f"Saved {query_count} query(s). Upload Word documents next to map queries to chunks."

    return EvalQueriesResponse(
        query_count=query_count,
        prepared=prepared,
        message=message,
        queries=saved,
        rows=[],
        prepared_rows=prepared_rows,
    )


@app.post("/eval/upload", response_model=EvalQueriesResponse)
async def upload_eval_csv(
    file: UploadFile = File(...),
    source_pdf: str = Form(default=""),
) -> EvalQueriesResponse:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files supported.")

    try:
        raw = await file.read()
        csv_text = raw.decode("utf-8-sig", errors="replace")
        saved_rows = save_eval_csv(csv_text, source_pdf=source_pdf or "")
        saved_queries = [row["query"] for row in saved_rows]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    prepared = False
    prepared_rows: list[dict[str, Any]] = []
    message = f"Uploaded {len(saved_queries)} eval row(s). Upload Word documents first if not done yet."

    try:
        if load_chunks():
            prepare_eval_set()
            prepared = True
            prepared_rows = load_prepared_eval()
            message = f"Uploaded {len(saved_queries)} eval row(s) and mapped them to document chunks."
            pipeline_state.add_log(message)
    except FileNotFoundError:
        message = f"Uploaded {len(saved_queries)} eval row(s). Upload Word documents next to map queries to chunks."

    return EvalQueriesResponse(
        query_count=len(saved_queries),
        prepared=prepared,
        message=message,
        queries=saved_queries,
        rows=saved_rows,
        prepared_rows=prepared_rows,
    )


@app.post("/index/{model_key}", response_model=IndexResponse)
def index_model(model_key: str, tenant_id: str = "") -> IndexResponse:
    if model_key not in MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_key}")

    tenant = tenant_id or pipeline_state.tenant_id or settings.default_tenant_id
    chunks = load_chunks()
    if not chunks:
        raise HTTPException(status_code=400, detail="No chunks found. Upload documents first.")

    for chunk in chunks:
        chunk["tenant_id"] = tenant

    pipeline_state.current_job = f"index:{model_key}"
    pipeline_state.set_step("index", StepStatus.RUNNING, f"Indexing {model_key}")

    try:
        store = SupabaseVectorStore.from_model(model_key, tenant)
        count = store.upsert_chunks(chunks)
        if count == 0:
            raise HTTPException(
                status_code=400,
                detail="0 chunks indexed. Try POST /chunks/relabel then re-index, or re-upload your Word document.",
            )
        if model_key not in pipeline_state.indexed_models:
            pipeline_state.indexed_models.append(model_key)
        pipeline_state.set_step("index", StepStatus.DONE, f"{count} chunks indexed ({model_key})")
        pipeline_state.add_log(f"Indexed {count} chunks with {MODELS[model_key]['display_name']}")

        return IndexResponse(
            model_key=model_key,
            display_name=MODELS[model_key]["display_name"],
            tenant_id=tenant,
            chunks_indexed=count,
        )
    except HTTPException as exc:
        pipeline_state.set_step("index", StepStatus.ERROR, str(exc.detail))
        raise
    except ValueError as exc:
        pipeline_state.set_step("index", StepStatus.ERROR, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EnvironmentError as exc:
        pipeline_state.set_step("index", StepStatus.ERROR, str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        pipeline_state.set_step("index", StepStatus.ERROR, str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        pipeline_state.current_job = None


@app.post("/evaluate/{model_key}", response_model=EvaluateResponse)
def evaluate_model(model_key: str, tenant_id: str = "") -> EvaluateResponse:
    if model_key not in MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_key}")

    tenant = tenant_id or pipeline_state.tenant_id or settings.default_tenant_id
    pipeline_state.current_job = f"evaluate:{model_key}"
    pipeline_state.set_step("evaluate", StepStatus.RUNNING, f"Evaluating {model_key}")

    try:
        _ensure_eval_ready()
        store = SupabaseVectorStore.from_model(model_key, tenant)
        if store.count() == 0:
            raise HTTPException(
                status_code=400,
                detail=f"No indexed chunks for {model_key}. Run index first.",
            )

        summary = evaluate_retrieval(store, model_key, tenant)
        if model_key not in pipeline_state.evaluated_models:
            pipeline_state.evaluated_models.append(model_key)
        pipeline_state.set_step("evaluate", StepStatus.DONE, f"F1@3={summary['f1@3']:.4f}")
        pipeline_state.set_step("compare", StepStatus.DONE, "Results updated")
        pipeline_state.add_log(f"Evaluated {MODELS[model_key]['display_name']}: F1@3={summary['f1@3']:.4f}")

        return EvaluateResponse(
            model_key=model_key,
            display_name=MODELS[model_key]["display_name"],
            metrics=summary,
        )
    except HTTPException as exc:
        pipeline_state.set_step("evaluate", StepStatus.ERROR, str(exc.detail))
        raise
    except EnvironmentError as exc:
        pipeline_state.set_step("evaluate", StepStatus.ERROR, str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        pipeline_state.set_step("evaluate", StepStatus.ERROR, str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        pipeline_state.current_job = None


async def _run_benchmark_job(tenant_id: str) -> None:
    chunks = load_chunks()
    if not chunks:
        pipeline_state.add_log("No chunks to benchmark", level="error")
        return

    for chunk in chunks:
        chunk["tenant_id"] = tenant_id

    try:
        _require_eval_for_job()
    except ValueError as exc:
        pipeline_state.add_log(str(exc), level="error")
        pipeline_state.current_job = None
        return

    reset_comparison()
    run_models: list[str] = []

    for model_key in BENCHMARK_MODEL_KEYS:
        display = MODELS[model_key]["display_name"]
        try:
            pipeline_state.set_step("index", StepStatus.RUNNING, f"Indexing {display}")
            store = SupabaseVectorStore.from_model(model_key, tenant_id)
            count = store.upsert_chunks(chunks)
            pipeline_state.add_log(f"Indexed {count} chunks with {display}")

            pipeline_state.set_step("evaluate", StepStatus.RUNNING, f"Evaluating {display}")
            summary = evaluate_retrieval(store, model_key, tenant_id)
            pipeline_state.add_log(f"{display}: F1@3={summary['f1@3']:.4f}")
            run_models.append(model_key)
        except EnvironmentError as exc:
            pipeline_state.add_log(f"Skipped {display}: {exc}", level="warning")

    pipeline_state.indexed_models = run_models
    pipeline_state.evaluated_models = run_models
    pipeline_state.set_step("index", StepStatus.DONE, f"{len(run_models)} models indexed")
    pipeline_state.set_step("evaluate", StepStatus.DONE, f"{len(run_models)} models evaluated")
    pipeline_state.set_step("compare", StepStatus.DONE, "Benchmark complete")
    pipeline_state.current_job = None


@app.post("/benchmark/all", response_model=BenchmarkResponse)
async def benchmark_all(
    background_tasks: BackgroundTasks,
    tenant_id: str = "",
    run_async: bool = False,
) -> BenchmarkResponse | dict[str, str]:
    tenant = tenant_id or pipeline_state.tenant_id or settings.default_tenant_id
    pipeline_state.tenant_id = tenant

    chunks = load_chunks()
    if not chunks:
        raise HTTPException(status_code=400, detail="No chunks found. Upload documents first.")

    try:
        get_supabase_client()
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if run_async:
        pipeline_state.current_job = "benchmark:all"
        background_tasks.add_task(_run_benchmark_job, tenant)
        return {"status": "started", "tenant_id": tenant}

    run_models: list[str] = []
    skipped: list[dict[str, str]] = []

    for chunk in chunks:
        chunk["tenant_id"] = tenant

    _ensure_eval_ready()
    reset_comparison()

    for model_key in BENCHMARK_MODEL_KEYS:
        display = MODELS[model_key]["display_name"]
        try:
            pipeline_state.set_step("index", StepStatus.RUNNING, f"Indexing {display}")
            store = SupabaseVectorStore.from_model(model_key, tenant)
            store.upsert_chunks(chunks)
            evaluate_retrieval(store, model_key, tenant)
            run_models.append(model_key)
        except EnvironmentError as exc:
            skipped.append({"model_key": model_key, "reason": str(exc)})

    pipeline_state.indexed_models = run_models
    pipeline_state.evaluated_models = run_models
    pipeline_state.set_step("index", StepStatus.DONE, f"{len(run_models)} models")
    pipeline_state.set_step("evaluate", StepStatus.DONE, f"{len(run_models)} models")
    pipeline_state.set_step("compare", StepStatus.DONE, "Done")

    return BenchmarkResponse(
        tenant_id=tenant,
        models_run=run_models,
        models_skipped=skipped,
        comparison=load_comparison(),
    )


@app.get("/results/{model_key}/per-query-metrics")
def get_per_query_metrics(model_key: str) -> list[dict[str, Any]]:
    if model_key not in MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_key}")
    return load_per_query_metrics(model_key)


@app.get("/results/comparison")
def get_comparison() -> list[dict[str, Any]]:
    return load_comparison()


@app.get("/results/dm-test")
def get_dm_test() -> dict[str, Any]:
    """Diebold–Mariano pairwise comparison of embedding models (post-eval)."""
    return load_dm_test_payload()


@app.get("/results/confusion-matrices")
def get_confusion_matrices(k: int = 3) -> list[dict[str, Any]]:
    """Binary confusion matrices derived from retrieval evaluation logs."""
    if k < 1:
        raise HTTPException(status_code=400, detail="k must be >= 1")
    return load_all_confusion_matrices(k=k)


@app.get("/results/{model_key}/confusion-matrix")
def get_confusion_matrix(model_key: str, k: int = 3) -> dict[str, Any]:
    if model_key not in MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_key}")
    if k < 1:
        raise HTTPException(status_code=400, detail="k must be >= 1")
    return compute_confusion_matrix(model_key, k=k)


@app.get("/results/{model_key}/retrieval-log")
def get_retrieval_log(model_key: str) -> list[dict[str, Any]]:
    if model_key not in MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_key}")
    return load_retrieval_log(model_key)


@app.get("/results/{model_key}/metrics")
def get_metrics(model_key: str) -> dict[str, Any]:
    if model_key not in MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_key}")
    from app.config import RESULTS_DIR

    path = RESULTS_DIR / MODELS[model_key]["display_name"] / "metrics.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Metrics not found for this model")
    import json

    return json.loads(path.read_text(encoding="utf-8"))
