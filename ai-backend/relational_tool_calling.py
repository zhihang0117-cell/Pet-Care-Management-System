"""OpenAI function-tool planning and safe execution for relational reads."""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv


READ_ONLY_TOOL_SCENARIOS = {
    "get_customer_profile": "CUSTOMER_GREETING",
    "get_pet_profiles": "GET_PET_PROFILES",
    "get_booking_status": "VIEW_BOOKING_STATUS",
    "get_latest_booking": "REPEAT_LAST_BOOKING",
    "check_availability": "CHECK_AVAILABILITY",
    "get_loyalty_points": "CHECK_LOYALTY_POINTS",
    "get_membership_status": "CHECK_MEMBERSHIP_STATUS",
    "get_loyalty_account": "LOYALTY_ACCOUNT_INQUIRY",
}

RELATIONAL_READ_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_customer_profile",
            "description": "Retrieve the authenticated customer's profile.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pet_profiles",
            "description": "Retrieve pets belonging to the authenticated customer.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "pet_name": {
                        "type": ["string", "null"],
                        "description": "Optional pet name to narrow the result.",
                    }
                },
                "required": ["pet_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_booking_status",
            "description": "Retrieve a booking and its current status.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {"type": ["integer", "null"]},
                    "service_type": {
                        "type": ["string", "null"],
                        "enum": ["GROOMING", "DAYCARE", "BOARDING", None],
                    },
                    "pet_name": {"type": ["string", "null"]},
                },
                "required": ["booking_id", "service_type", "pet_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_latest_booking",
            "description": "Retrieve the authenticated customer's most recent booking.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Check live availability for a pet-care service.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "service_type": {
                        "type": "string",
                        "enum": ["GROOMING", "DAYCARE", "BOARDING"],
                    },
                    "preferred_date": {"type": "string"},
                    "preferred_time": {"type": ["string", "null"]},
                },
                "required": ["service_type", "preferred_date", "preferred_time"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_loyalty_points",
            "description": "Retrieve the authenticated customer's loyalty points.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_membership_status",
            "description": "Retrieve membership tier and status.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_loyalty_account",
            "description": "Retrieve loyalty account details.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]


def relational_tool_calling_enabled() -> bool:
    # Explicit process/test environment must win over .env. Reloading with
    # override=True here can silently turn mock tests into live OpenAI calls.
    load_dotenv(override=False)
    from testing_mode import is_testing_mode

    if is_testing_mode():
        return False
    enabled = os.getenv("RELATIONAL_TOOL_CALLING_ENABLED", "true").strip().lower()
    return enabled in {"1", "true", "yes", "on"} and os.getenv("LLM_PROVIDER", "mock").strip().lower() == "openai"


def plan_relational_tool_calls(user_message: str, intent_json: dict) -> list[dict[str, Any]]:
    """Ask the model to select zero or more allowlisted read tools."""
    if not relational_tool_calling_enabled():
        return []

    from openai import OpenAI
    from llm_call_logging import log_llm_call

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return []
    runtime = log_llm_call("query_json")
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL", "").strip() or None,
    )
    response = client.chat.completions.create(
        model=runtime["model"],
        messages=[
            {
                "role": "system",
                "content": (
                    "Select only the relational read tools needed to answer the user. "
                    "Never request data unrelated to the question. Customer and company "
                    "identity are enforced by the server; do not ask for their IDs."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"message": user_message, "intent": intent_json},
                    ensure_ascii=False,
                ),
            },
        ],
        tools=RELATIONAL_READ_TOOLS,
        tool_choice="auto",
        temperature=0,
    )
    calls = response.choices[0].message.tool_calls or []
    planned: list[dict[str, Any]] = []
    for call in calls:
        name = str(call.function.name or "")
        if name not in READ_ONLY_TOOL_SCENARIOS:
            continue
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
        planned.append({"id": call.id, "name": name, "arguments": arguments})
    return planned


def execute_relational_tool_calls(
    tool_calls: list[dict[str, Any]],
    *,
    intent_json: dict,
    customer_id: str,
    phone_number: str,
    session,
    user_message: str,
) -> dict:
    """Execute allowlisted tool calls through the existing scoped dispatcher."""
    from database_service import execute_database_action

    results: list[dict[str, Any]] = []
    for call in tool_calls:
        name = str(call.get("name") or "")
        scenario = READ_ONLY_TOOL_SCENARIOS.get(name)
        if not scenario:
            continue
        payload = dict(intent_json)
        payload["scenario_intent"] = scenario
        entities = dict(payload.get("entities") or {})
        for key, value in dict(call.get("arguments") or {}).items():
            if value is not None:
                entities[key] = value
        payload["entities"] = entities
        result = execute_database_action(
            payload,
            customer_id=customer_id,
            phone_number=phone_number,
            session=session,
            user_message=user_message,
        )
        results.append(
            {
                "tool_call_id": call.get("id"),
                "tool_name": name,
                "result": result,
            }
        )

    if not results:
        return {}
    if len(results) == 1:
        single = dict(results[0]["result"])
        single["tool_calling"] = {
            "used": True,
            "tool_name": results[0]["tool_name"],
            "tool_call_id": results[0]["tool_call_id"],
        }
        return single
    return {
        "action": "relational_tool_calls",
        "status": "success",
        "success": True,
        "data_found": any(
            item["result"].get("data_found") is not False
            and item["result"].get("status") == "success"
            for item in results
        ),
        "data": {"tool_results": results},
        "error": None,
        "tool_calling": {"used": True, "count": len(results)},
    }
