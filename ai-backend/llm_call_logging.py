"""
Shared runtime helpers for logging which LLM endpoint is called.
"""

from __future__ import annotations

import os

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def _infer_provider(model: str, base_url: str) -> str:
    if "openrouter.ai" in base_url.lower():
        return "openrouter"
    if "router.huggingface.co" in base_url.lower():
        return "huggingface"
    if model.lower().startswith("gpt-"):
        return "openai"
    return "openai_compatible"


def resolve_query_json_runtime() -> dict[str, str]:
    model = os.getenv("LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or DEFAULT_OPENAI_BASE_URL
    return {
        "stage": "query_json",
        "model": model,
        "provider": _infer_provider(model, base_url),
        "base_url": base_url,
    }


def resolve_final_response_runtime() -> dict[str, str]:
    model = (
        os.getenv("FINAL_RESPONSE_MODEL", "").strip()
        or os.getenv("LLM_MODEL", "gpt-4o-mini").strip()
        or "gpt-4o-mini"
    )
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or DEFAULT_OPENAI_BASE_URL
    return {
        "stage": "final_response",
        "model": model,
        "provider": _infer_provider(model, base_url),
        "base_url": base_url,
    }


def log_llm_call(stage: str) -> dict[str, str]:
    if stage == "query_json":
        runtime = resolve_query_json_runtime()
    else:
        runtime = resolve_final_response_runtime()
    if os.getenv("_EVAL_OVERRIDE_ACTIVE"):
        print(
            f"[LLM CALL] stage={runtime['stage']} model={runtime['model']} "
            f"provider={runtime['provider']} base_url={runtime['base_url']}"
        )
    return runtime
