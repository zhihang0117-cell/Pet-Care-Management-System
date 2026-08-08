"""Pure ordering, normalization, and trace policies for the agent tool loop."""

from __future__ import annotations

import json
import re


BATCH_DEPENDENCIES = {
    "get_last_completed_booking": {"find_pet_by_name", "get_pets"},
    "check_availability": {
        "resolve_datetime", "get_last_completed_booking", "get_booking_service_options"
    },
    "check_availability_range": {
        "resolve_datetime", "get_last_completed_booking", "get_booking_service_options"
    },
    "create_pet": {"create_customer"},
    "get_booking_service_options": {
        "create_pet", "find_pet_by_name", "get_pets", "get_last_completed_booking"
    },
    "create_booking": {
        "create_customer", "create_pet", "find_pet_by_name",
        "get_booking_service_options", "resolve_datetime",
        "check_availability", "check_availability_range",
        "update_pet_vaccination", "get_loyalty_balance",
        "check_coupon_eligibility",
    },
    "cancel_booking": {"get_latest_booking", "get_booking_by_id"},
    "reschedule_booking": {
        "get_latest_booking", "get_booking_by_id", "resolve_datetime",
        "check_availability", "check_availability_range",
    },
    "redeem_reward": {"create_booking", "check_coupon_eligibility"},
}


def mutation_signature(tool_name: str, args: dict) -> str:
    """Build the stable identity used to deduplicate one concrete action."""
    args = {
        key: value
        for key, value in args.items()
        if key != "idempotency_key" and not str(key).startswith("_internal_")
    }
    if tool_name == "register_loyalty_member":
        args = {key: value for key, value in args.items() if key != "confirmed"}
    if tool_name in {"cancel_booking", "reschedule_booking"}:
        args = {key: value for key, value in args.items() if key != "confirm_pet_name"}
    normalized = {
        key: (round(value, 2) if isinstance(value, float) else value)
        for key, value in sorted(args.items())
    }
    return f"{tool_name}:{json.dumps(normalized, sort_keys=True, default=str)}"


def tool_result_status(result) -> str:
    if isinstance(result, list):
        return "success" if result else "not_found"
    if not isinstance(result, dict):
        return "error"
    if result.get("status"):
        return str(result["status"])
    if result.get("error"):
        return "error"
    if result.get("found") is False:
        return "not_found"
    return "success"


def compact_evidence_result(result, max_chars: int = 1800):
    """Bound cross-turn evidence size and remove internal delivery fields."""

    def sanitize(value, depth: int = 0):
        if depth > 3:
            return "[nested data omitted]"
        if isinstance(value, dict):
            return {
                key: sanitize(item, depth + 1)
                for key, item in value.items()
                if not str(key).startswith("_internal_")
            }
        if isinstance(value, list):
            return [sanitize(item, depth + 1) for item in value[:8]]
        if isinstance(value, str) and len(value) > 500:
            return value[:500] + "…"
        return value

    cleaned = sanitize(result)
    encoded = json.dumps(cleaned, ensure_ascii=False, default=str)
    if len(encoded) <= max_chars:
        return cleaned
    return {"preview": encoded[:max_chars] + "…", "truncated": True}


def ordered_tool_calls(tool_calls: list[dict]) -> tuple[list[dict], bool]:
    """Return a stable topological order for dependencies in one response."""
    names = {call["name"] for call in tool_calls}
    remaining = list(enumerate(tool_calls))
    completed_names: set[str] = set()
    ordered: list[dict] = []
    has_dependency = False

    while remaining:
        progressed = False
        for position, (_, call) in enumerate(remaining):
            required = BATCH_DEPENDENCIES.get(call["name"], set()) & names
            if call["name"] != "update_conversation_state" and "update_conversation_state" in names:
                required = required | {"update_conversation_state"}
            if required:
                has_dependency = True
            if required <= completed_names:
                ordered.append(call)
                completed_names.add(call["name"])
                remaining.pop(position)
                progressed = True
                break
        if not progressed:
            ordered.extend(call for _, call in remaining)
            break
    return ordered, has_dependency


def response_requests_customer_input(content: str) -> bool:
    text = (content or "").strip().lower()
    return bool(re.search(
        r"[?？]|\b(?:what|which|when|where|who|could you|can you|please provide|please confirm)\b"
        r"|请问|哪一|什么时候|几点|可以告诉|请提供|请确认|请回复"
        r"|\b(?:apa|bila|di\s*mana|siapa|bolehkah|sila\s+(?:beritahu|sahkan|berikan))\b",
        text,
    ))


def _parsed_trace_result(item: dict):
    raw_result = item.get("result")
    try:
        return json.loads(raw_result) if isinstance(raw_result, str) else raw_result
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def trace_has_successful_mutation(trace: list[dict]) -> bool:
    mutation_tools = {"create_booking", "cancel_booking", "reschedule_booking", "redeem_reward"}
    return any(
        item.get("tool") in mutation_tools
        and isinstance(result := _parsed_trace_result(item), dict)
        and result.get("status") == "success"
        for item in trace
    )


def trace_has_successful_document_delivery(trace: list[dict]) -> bool:
    delivery_tools = {"create_booking", "reschedule_booking", "send_booking_confirmation"}
    for item in trace:
        if item.get("tool") not in delivery_tools:
            continue
        result = _parsed_trace_result(item)
        if not isinstance(result, dict) or result.get("status") != "success":
            continue
        data = result.get("data") or {}
        delivery_status = str(
            data.get("delivery_status")
            or data.get("confirmation_delivery_status")
            or ""
        )
        if delivery_status in {"sent", "sent_console"}:
            return True
    return False


def trace_has_document_delivery_attempt(trace: list[dict]) -> bool:
    delivery_tools = {"create_booking", "reschedule_booking", "send_booking_confirmation"}
    return any(item.get("tool") in delivery_tools for item in trace)


def trace_has_successful_availability(trace: list[dict]) -> bool:
    """Only a successful current-turn read can support offered times."""
    for item in trace:
        if item.get("tool") not in {"check_availability", "check_availability_range"}:
            continue
        result = _parsed_trace_result(item)
        if not isinstance(result, dict):
            continue
        if item.get("tool") == "check_availability_range" and isinstance(
            result.get("days"), list
        ):
            return True
        if tool_result_status(result) == "success":
            return True
    return False


def successful_trace_tools(trace: list[dict]) -> set[str]:
    """Tool names with an actually successful result, not merely a call."""
    successful: set[str] = set()
    for item in trace:
        tool_name = item.get("tool")
        if not tool_name:
            continue
        if tool_result_status(_parsed_trace_result(item)) == "success":
            successful.add(str(tool_name))
    return successful
