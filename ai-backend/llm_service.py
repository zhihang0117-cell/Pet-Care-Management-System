"""
LLM service layer for Pawfect intent detection.

Uses the Query JSON Prompt (real_llm.py) for structured intent understanding.
Mock LLM remains available for local testing and safe fallback.
The router does not care whether intent came from mock or real LLM.
"""

import os

from dotenv import load_dotenv

from llm_api_error import (
    LlmApiError,
    LlmJsonParseError,
    LlmStageError,
    build_llm_api_error,
    is_eval_strict_mode,
    log_llm_api_error,
    log_llm_stage_error,
)
from mock_llm import mock_llm_intent_detection
from testing_mode import should_reraise_on_error

load_dotenv(override=True)


def detect_intent(user_message: str) -> dict:
    """
    Detect customer intent using configured LLM provider with safe mock fallback.

    Args:
        user_message: The customer's WhatsApp message text.

    Returns:
        dict with:
            - intent_json: standardized intent detection result
            - provider_used: "mock" or "openai"
    """
    # Reload .env on each request so local .env changes apply without full server restart.
    # During evaluation runs, keep eval_model_context overrides intact.
    load_dotenv(override=not os.getenv("_EVAL_OVERRIDE_ACTIVE"))
    provider = os.getenv("LLM_PROVIDER", "mock").strip().lower()

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            if should_reraise_on_error():
                raise ValueError("OPENAI_API_KEY is not set for evaluation run")
            return {
                "intent_json": mock_llm_intent_detection(user_message),
                "provider_used": "mock",
                "query_json_model_used": "mock",
                "query_json_provider_used": "mock",
                "query_json_base_url_used": "",
            }

        try:
            from real_llm import real_llm_intent_detection

            result = real_llm_intent_detection(user_message)
            return {
                "intent_json": result["intent_json"],
                "provider_used": "openai",
                "query_json_model_used": result["model_used"],
                "query_json_provider_used": result["provider_used"],
                "query_json_base_url_used": result["base_url_used"],
            }
        except LlmStageError:
            raise
        except Exception as exc:
            if should_reraise_on_error():
                if is_eval_strict_mode():
                    api_error = build_llm_api_error(stage="query_json", exc=exc, attempt=1)
                    log_llm_api_error(api_error)
                    raise api_error from exc
                raise
            return {
                "intent_json": mock_llm_intent_detection(user_message),
                "provider_used": "mock",
                "query_json_model_used": "mock",
                "query_json_provider_used": "mock",
                "query_json_base_url_used": "",
            }

    if should_reraise_on_error() and is_eval_strict_mode():
        raise ValueError(f"LLM_PROVIDER must be openai during evaluation, got '{provider}'")

    return {
        "intent_json": mock_llm_intent_detection(user_message),
        "provider_used": "mock",
        "query_json_model_used": "mock",
        "query_json_provider_used": "mock",
        "query_json_base_url_used": "",
    }
