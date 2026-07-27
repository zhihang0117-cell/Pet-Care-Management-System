"""
Structured Hugging Face / OpenAI-compatible API error capture for evaluation.
"""

from __future__ import annotations

import contextvars
import json
from typing import Any

CURRENT_EVAL_ID = contextvars.ContextVar("current_eval_id", default="")
CURRENT_EVAL_ATTEMPT = contextvars.ContextVar("current_eval_attempt", default=1)

EMPTY_API_ERROR_FIELDS = {
    "query_json_http_status": "",
    "final_response_http_status": "",
    "query_json_api_error_type": "",
    "final_response_api_error_type": "",
    "query_json_api_error_message": "",
    "final_response_api_error_message": "",
    "retry_after_seconds": "",
    "hf_rate_limit_info": "",
    "query_json_fallback_used": "false",
    "final_response_fallback_used": "false",
    "query_json_error": "",
    "final_response_error": "",
    "json_parse_error": "",
}


def is_eval_strict_mode() -> bool:
    import os

    return bool(os.getenv("_EVAL_OVERRIDE_ACTIVE"))


def classify_error_type(http_status: int | str | None, exc: Exception) -> str:
    try:
        status = int(http_status) if http_status not in (None, "") else None
    except (TypeError, ValueError):
        status = None

    exc_name = exc.__class__.__name__.lower()
    if status == 429:
        return "rate_limit_error"
    if status == 503:
        return "provider_unavailable"
    if status == 504:
        return "timeout"
    if status in (401, 403):
        return "auth_or_permission_error"
    if "timeout" in exc_name:
        return "timeout"
    return "hf_api_error"


def _safe_response_text(response: Any) -> str:
    if response is None:
        return ""
    for attr in ("text", "body"):
        value = getattr(response, attr, None)
        if value is None:
            continue
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
    return ""


def _extract_headers(response: Any) -> dict[str, str]:
    if response is None:
        return {}
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    try:
        return {str(key): str(value) for key, value in headers.items()}
    except Exception:
        return {}


def _extract_rate_limit_info(headers: dict[str, str]) -> str:
    rate_headers = {
        key: value
        for key, value in headers.items()
        if "ratelimit" in key.lower() or "rate-limit" in key.lower() or key.lower() == "x-rate-limit"
    }
    return json.dumps(rate_headers, ensure_ascii=False) if rate_headers else ""


def _parse_retry_after(headers: dict[str, str]) -> str:
    for key in ("retry-after", "Retry-After"):
        if key in headers and headers[key]:
            return str(headers[key]).strip()
    return ""


def build_llm_api_error(
    stage: str,
    exc: Exception,
    eval_id: str = "",
    attempt: int = 1,
) -> "LlmApiError":
    http_status = getattr(exc, "status_code", "") or ""
    response = getattr(exc, "response", None)
    headers = _extract_headers(response)
    response_body = _safe_response_text(response)
    retry_after = _parse_retry_after(headers)
    rate_limit_info = _extract_rate_limit_info(headers)
    error_type = classify_error_type(http_status, exc)
    error_message = str(exc).strip() or exc.__class__.__name__

    return LlmApiError(
        stage=stage,
        eval_id=eval_id or CURRENT_EVAL_ID.get(),
        attempt=attempt,
        http_status=str(http_status) if http_status != "" else "",
        error_type=error_type,
        error_message=error_message,
        response_body=response_body,
        response_headers=json.dumps(headers, ensure_ascii=False),
        retry_after_seconds=retry_after,
        hf_rate_limit_info=rate_limit_info,
    )


def log_llm_api_error(error: "LlmApiError") -> None:
    print(
        "[LLM API ERROR] "
        f"stage={error.stage} "
        f"eval_id={error.eval_id} "
        f"attempt={error.attempt} "
        f"http_status={error.http_status or 'n/a'} "
        f"error_type={error.error_type} "
        f"message={error.error_message} "
        f"response_body={error.response_body[:500]} "
        f"retry_after={error.retry_after_seconds or 'n/a'} "
        f"rate_limit={error.hf_rate_limit_info or 'n/a'} "
        f"headers={error.response_headers[:500]}"
    )


def build_json_parse_error(
    stage: str,
    exc: Exception,
    content: str = "",
    eval_id: str = "",
    attempt: int = 1,
) -> "LlmJsonParseError":
    return LlmJsonParseError(
        stage=stage,
        eval_id=eval_id or CURRENT_EVAL_ID.get(),
        attempt=attempt,
        error_message=str(exc).strip() or exc.__class__.__name__,
        raw_content=content,
    )


def build_final_response_error(
    exc: Exception,
    eval_id: str = "",
    attempt: int = 1,
) -> "LlmFinalResponseError":
    return LlmFinalResponseError(
        eval_id=eval_id or CURRENT_EVAL_ID.get(),
        attempt=attempt,
        error_message=str(exc).strip() or exc.__class__.__name__,
    )


def log_llm_stage_error(error: "LlmStageError") -> None:
    if isinstance(error, LlmApiError):
        log_llm_api_error(error)
        return
    print(
        "[LLM STAGE ERROR] "
        f"stage={error.stage} "
        f"eval_id={error.eval_id} "
        f"attempt={error.attempt} "
        f"error_type={error.error_type} "
        f"message={error.error_message}"
    )


class LlmStageError(Exception):
    def __init__(
        self,
        *,
        stage: str,
        eval_id: str = "",
        attempt: int = 1,
        error_type: str = "stage_error",
        error_message: str = "",
        partial_pipeline: dict | None = None,
    ) -> None:
        self.stage = stage
        self.eval_id = eval_id
        self.attempt = attempt
        self.error_type = error_type
        self.error_message = error_message
        self.partial_pipeline = partial_pipeline or {}
        super().__init__(self.to_error_text())

    def to_error_text(self) -> str:
        return (
            f"{self.error_type} stage={self.stage} eval_id={self.eval_id} "
            f"attempt={self.attempt} message={self.error_message}"
        )

    def to_row_fields(self) -> dict[str, str]:
        fields = dict(EMPTY_API_ERROR_FIELDS)
        if self.stage == "query_json":
            fields["query_json_api_error_type"] = self.error_type
            fields["query_json_api_error_message"] = self.error_message
            fields["query_json_error"] = self.to_error_text()
            if self.error_type == "json_parse_error":
                fields["json_parse_error"] = self.error_message
        elif self.stage == "final_response":
            fields["final_response_api_error_type"] = self.error_type
            fields["final_response_api_error_message"] = self.error_message
            fields["final_response_error"] = self.to_error_text()
        return fields


class LlmJsonParseError(LlmStageError):
    def __init__(
        self,
        *,
        stage: str = "query_json",
        eval_id: str = "",
        attempt: int = 1,
        error_message: str = "",
        raw_content: str = "",
        partial_pipeline: dict | None = None,
    ) -> None:
        self.raw_content = raw_content
        super().__init__(
            stage=stage,
            eval_id=eval_id,
            attempt=attempt,
            error_type="json_parse_error",
            error_message=error_message,
            partial_pipeline=partial_pipeline,
        )


class LlmFinalResponseError(LlmStageError):
    def __init__(
        self,
        *,
        eval_id: str = "",
        attempt: int = 1,
        error_message: str = "",
        partial_pipeline: dict | None = None,
    ) -> None:
        super().__init__(
            stage="final_response",
            eval_id=eval_id,
            attempt=attempt,
            error_type="final_response_error",
            error_message=error_message,
            partial_pipeline=partial_pipeline,
        )


class LlmApiError(LlmStageError):
    def __init__(
        self,
        *,
        stage: str,
        eval_id: str = "",
        attempt: int = 1,
        http_status: str = "",
        error_type: str = "hf_api_error",
        error_message: str = "",
        response_body: str = "",
        response_headers: str = "",
        retry_after_seconds: str = "",
        hf_rate_limit_info: str = "",
        partial_pipeline: dict | None = None,
    ) -> None:
        self.http_status = http_status
        self.response_body = response_body
        self.response_headers = response_headers
        self.retry_after_seconds = retry_after_seconds
        self.hf_rate_limit_info = hf_rate_limit_info
        super().__init__(
            stage=stage,
            eval_id=eval_id,
            attempt=attempt,
            error_type=error_type,
            error_message=error_message,
            partial_pipeline=partial_pipeline,
        )

    def to_error_text(self) -> str:
        return (
            f"{self.error_type} stage={self.stage} eval_id={self.eval_id} "
            f"attempt={self.attempt} http_status={self.http_status or 'n/a'} "
            f"message={self.error_message}"
        )

    def to_row_fields(self) -> dict[str, str]:
        fields = super().to_row_fields()
        fields["retry_after_seconds"] = self.retry_after_seconds
        fields["hf_rate_limit_info"] = self.hf_rate_limit_info
        if self.stage == "query_json":
            fields["query_json_http_status"] = self.http_status
        elif self.stage == "final_response":
            fields["final_response_http_status"] = self.http_status
        return fields
