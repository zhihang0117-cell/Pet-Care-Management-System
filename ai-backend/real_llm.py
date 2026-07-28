"""
Real LLM intent detection for Pawfect backend.

Uses the Query JSON Prompt for structured intent understanding only.
Final customer replies are generated separately in the response step.
"""

import json
import os

from openai import OpenAI

from intent_schema import normalize_intent_result
from llm_api_error import (
    CURRENT_EVAL_ATTEMPT,
    build_json_parse_error,
    build_llm_api_error,
    is_eval_strict_mode,
    log_llm_api_error,
    log_llm_stage_error,
)
from llm_call_logging import log_llm_call
from query_json_prompt import QUERY_JSON_PROMPT


def _raise_or_return_api_error(stage: str, exc: Exception, attempt: int) -> None:
    api_error = build_llm_api_error(stage=stage, exc=exc, attempt=attempt)
    log_llm_api_error(api_error)
    raise api_error from exc


def _create_chat_completion(client: OpenAI, request_kwargs: dict):
    attempt_token = CURRENT_EVAL_ATTEMPT.set(1)
    try:
        try:
            return client.chat.completions.create(
                **request_kwargs,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            if is_eval_strict_mode():
                _raise_or_return_api_error("query_json", exc, attempt=1)
            CURRENT_EVAL_ATTEMPT.set(2)
            return client.chat.completions.create(**request_kwargs)
    finally:
        CURRENT_EVAL_ATTEMPT.reset(attempt_token)


def real_llm_intent_detection(
    user_message: str,
    conversation_state: dict | None = None,
    conversation_history: list[dict] | None = None,
) -> dict:
    """
    Call OpenAI with the Query JSON Prompt to classify customer intent.

    Args:
        user_message: The customer's WhatsApp message text.

    Returns:
        Structured intent JSON for routing and backend actions.

    Raises:
        Exception: If API key is missing or the OpenAI call fails.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")

    runtime = log_llm_call("query_json")
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
    client = OpenAI(api_key=api_key, base_url=base_url)

    system_prompt = QUERY_JSON_PROMPT.format(customer_message=user_message)

    request_kwargs = {
        "model": runtime["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_message": user_message,
                        "conversation_history": conversation_history or [],
                        "conversation_state": conversation_state or {},
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0,
    }

    try:
        response = _create_chat_completion(client, request_kwargs)
    except Exception as exc:
        if is_eval_strict_mode():
            if hasattr(exc, "stage"):
                raise
            _raise_or_return_api_error("query_json", exc, attempt=CURRENT_EVAL_ATTEMPT.get())
        raise

    content = response.choices[0].message.content
    if not content:
        if is_eval_strict_mode():
            parse_error = build_json_parse_error(
                stage="query_json",
                exc=ValueError("OpenAI returned an empty response"),
                content="",
                attempt=CURRENT_EVAL_ATTEMPT.get(),
            )
            log_llm_stage_error(parse_error)
            raise parse_error
        raise ValueError("OpenAI returned an empty response")

    try:
        raw_result = json.loads(content)
    except json.JSONDecodeError as exc:
        if is_eval_strict_mode():
            parse_error = build_json_parse_error(
                stage="query_json",
                exc=exc,
                content=content,
                attempt=CURRENT_EVAL_ATTEMPT.get(),
            )
            log_llm_stage_error(parse_error)
            raise parse_error from exc
        raise
    return {
        "intent_json": normalize_intent_result(raw_result),
        "model_used": runtime["model"],
        "provider_used": runtime["provider"],
        "base_url_used": runtime["base_url"],
    }
