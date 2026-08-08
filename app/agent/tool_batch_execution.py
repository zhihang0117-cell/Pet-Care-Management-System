"""Small, behavior-preserving helpers for one batch of tool calls."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any


def plan_tool_batch(
    tool_calls: list[dict],
    state: Any,
    *,
    ordered_tool_calls: Callable[[list[dict]], tuple[list[dict], bool]],
    mutation_signature: Callable[[str, dict], str],
    cacheable_read_tools: set[str],
    company_scoped_tools: set[str],
    customer_scoped_tools: set[str],
) -> tuple[list[dict], bool, str, str | None]:
    """Choose parallel or sequential execution without running any tool."""
    ordered_calls, has_dependency = ordered_tool_calls(tool_calls)
    read_signatures = [
        mutation_signature(
            tool_call["name"],
            {
                **dict(tool_call.get("args") or {}),
                **(
                    {"company_id": state.company_id}
                    if tool_call["name"] in company_scoped_tools
                    else {}
                ),
                **(
                    {"customer_id": state.customer_id}
                    if (
                        tool_call["name"] in customer_scoped_tools
                        and state.customer_id is not None
                    )
                    else {}
                ),
            },
        )
        for tool_call in tool_calls
        if tool_call["name"] in cacheable_read_tools
    ]
    has_duplicate_read_call = len(read_signatures) != len(set(read_signatures))
    unsafe_parallel_tools = sorted({
        call["name"]
        for call in tool_calls
        if call["name"] not in cacheable_read_tools
    })
    run_in_parallel = bool(
        len(tool_calls) > 1
        and not unsafe_parallel_tools
        and not has_dependency
        and not has_duplicate_read_call
    )
    if run_in_parallel:
        return ordered_calls, True, "parallel", None
    reason = (
        "single_call_in_iteration"
        if len(tool_calls) == 1
        else (
            "contains_dependency_chain"
            if has_dependency
            else (
                "contains_duplicate_read_calls"
                if has_duplicate_read_call
                else f"contains_non_parallel_safe_tools:{','.join(unsafe_parallel_tools)}"
            )
        )
    )
    return ordered_calls, False, "sequential", reason


def await_tool_future_result(
    future: Any,
    tool_call: dict,
    *,
    execution_mode: str,
    batch_started_at: float,
    submitted_at: dict[str, float],
    timeout_seconds: float,
    mutating_tool_names: set[str],
):
    """Bound one worker result and report unknown write outcomes safely."""
    timeout_origin = (
        batch_started_at
        if execution_mode == "parallel"
        else submitted_at.get(tool_call["id"], batch_started_at)
    )
    remaining_seconds = max(
        0.0,
        timeout_seconds - (time.perf_counter() - timeout_origin),
    )
    try:
        return future.result(timeout=remaining_seconds)
    except FutureTimeoutError:
        future.cancel()
        outcome_unknown = tool_call["name"] in mutating_tool_names
        return (
            {
                "status": "error",
                "error_code": (
                    "TOOL_TIMEOUT_OUTCOME_UNKNOWN"
                    if outcome_unknown
                    else "TOOL_TIMEOUT"
                ),
                "recoverable": not outcome_unknown,
                "message": (
                    f"{tool_call['name']} did not return within "
                    f"{timeout_seconds:g} seconds. "
                    + (
                        "Its write outcome is unknown: do not repeat the action or claim "
                        "success; staff must verify the record first."
                        if outcome_unknown
                        else "Do not claim success; retry once if useful or ask the customer "
                        "to try again."
                    )
                ),
                "handoff_required": outcome_unknown,
                "handoff_reason": (
                    "TOOL_TIMEOUT_OUTCOME_UNKNOWN" if outcome_unknown else None
                ),
            },
            round((time.perf_counter() - batch_started_at) * 1000, 1),
            0.0,
        )
    except Exception as exc:
        logging.getLogger(__name__).exception(
            "Tool worker failed for %s: %s", tool_call["name"], exc
        )
        return (
            {
                "status": "error",
                "error_code": "TOOL_WORKER_ERROR",
                "recoverable": False,
                "message": "The tool worker failed before returning a usable result.",
                "handoff_required": True,
                "handoff_reason": "TOOL_WORKER_ERROR",
            },
            round((time.perf_counter() - batch_started_at) * 1000, 1),
            0.0,
        )
