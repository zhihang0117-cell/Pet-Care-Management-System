"""Truthful deterministic rendering for consequential tool outcomes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any


def _latest_result(trace: list[dict], tool_names: set[str]) -> tuple[str, dict] | None:
    attempt = next(
        (item for item in reversed(trace) if item.get("tool") in tool_names),
        None,
    )
    if attempt is None:
        return None
    try:
        result = json.loads(attempt.get("result") or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict):
        return None
    return str(attempt.get("tool") or ""), result


def ground_document_delivery_response(
    response: Any,
    user_message: str,
    trace: list[dict],
    *,
    trace_has_successful_delivery: Callable[[list[dict]], bool],
):
    """Never let prose turn a failed document dispatch into fake success."""
    document_intent = bool(re.search(
        r"\b(?:confirmation|document|slip|pdf)\b|确认单|确认文件|文件",
        user_message or "",
        re.IGNORECASE,
    ))
    if not document_intent or trace_has_successful_delivery(trace):
        return response
    observed = _latest_result(
        trace,
        {"create_booking", "reschedule_booking", "send_booking_confirmation"},
    )
    if observed is None:
        return response
    _, result = observed
    chinese = bool(re.search(r"[\u3400-\u9fff]", user_message or ""))
    if result.get("handoff_required"):
        truthful = (
            "这次确认单没有成功发送。我已经把文件发送问题记录给员工跟进。"
            if chinese else
            "The confirmation document was not delivered successfully. I've logged the "
            "delivery problem for staff follow-up."
        )
    else:
        truthful = (
            "这次确认单没有发送：系统未能确认可发送的目标预约。请确认预约后再试。"
            if chinese else
            "The confirmation document was not sent because the target booking could not "
            "be verified. Please clarify the booking and try again."
        )
    return response.model_copy(update={"content": truthful})


def ground_membership_response(
    response: Any, user_message: str, trace: list[dict], state: Any = None
):
    """Render membership completion only from an observed successful write."""
    observed = _latest_result(trace, {"register_loyalty_member"})
    if observed is None:
        return response
    _, result = observed
    if result.get("status") != "success":
        return response

    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    tier = data.get("tier") or data.get("membership_status") or "Bronze"
    points = data.get("points_balance")
    points = 0 if points is None else points
    chinese = bool(re.search(r"[\u3400-\u9fff]", user_message or ""))
    if data.get("already_member"):
        truthful = (
            f"您已经是会员。目前等级为 {tier}，积分余额为 {points}。"
            if chinese else
            f"You're already a loyalty member. Your current tier is {tier}, with {points} points."
        )
    else:
        truthful = (
            f"会员注册已完成。您的等级为 {tier}，初始积分为 {points}。"
            if chinese else
            f"Your loyalty membership is now active. Your tier is {tier}, with {points} points."
        )
    if state is not None and state.active_scenario == "MAKE_BOOKING":
        existing = str(response.content or "").strip()
        combined = "\n\n".join(part for part in (truthful, existing) if part)
        return response.model_copy(update={"content": combined})
    return response.model_copy(update={"content": truthful})


def ground_booking_preview_response(response: Any, user_message: str, trace: list[dict]):
    """Render the exact observed booking preview instead of model prose."""
    observed = _latest_result(trace, {"create_booking"})
    if observed is None:
        return response
    _, result = observed
    if result.get("status") != "confirmation_required":
        return response
    preview = (result.get("data") or {}).get("preview")
    if not isinstance(preview, dict):
        return response

    pet = preview.get("pet_name") or "your pet"
    package = preview.get("package_name") or preview.get("service_type") or "service"
    date_value = preview.get("date") or ""
    start = str(preview.get("time") or "")[:5]
    checkout = str(preview.get("check_out_time") or "")[:5]
    duration = preview.get("duration_minutes")
    price = preview.get("price")
    price_text = f"RM{float(price):g}" if price not in (None, "") else ""
    chinese = bool(re.search(r"[\u3400-\u9fff]", user_message or ""))
    if chinese:
        timing = f"{start} 至 {checkout}" if checkout else (
            f"{start}，时长 {duration} 分钟" if duration else start
        )
        content = (
            f"请确认预约：{pet}，{package}，{date_value} {timing}"
            f"{f'，{price_text}' if price_text else ''}。回复“确认”后才会正式建立预约。"
        )
    else:
        timing = f"{start} to {checkout}" if checkout else (
            f"{start} for {duration} minutes" if duration else start
        )
        content = (
            f"Please confirm this booking for {pet}: {package} on {date_value}, "
            f"{timing}{f', {price_text}' if price_text else ''}. "
            "Reply yes to create the booking."
        )
    return response.model_copy(update={"content": content})
