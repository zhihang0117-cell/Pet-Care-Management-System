"""Conversation-aware LLM planning and post-tool decision resolution."""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Type

from decision_support import (
    DecisionPlan,
    EvidenceValidation,
    GoalStatus,
    GroundedDecision,
    NextAction,
    model_to_dict,
)
from intent_schema import (
    ALLOWED_MAIN_INTENTS,
    ALLOWED_SCENARIO_INTENTS,
    ALLOWED_SERVICE_TYPES,
    normalize_intent_result,
)
from llm_call_logging import log_llm_call
from pydantic import BaseModel, ValidationError


logger = logging.getLogger(__name__)


def _as_string_list(value) -> list[str]:
    """Tolerate common empty-container mistakes at the LLM schema boundary."""
    if value in (None, "", False, {}):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _normalize_plan_output(raw: dict) -> dict:
    normalized = dict(raw or {})
    normalized["critical_unknowns"] = _as_string_list(
        normalized.get("critical_unknowns")
    )
    evidence_need = dict(normalized.get("evidence_need") or {})
    reason = evidence_need.get("reason")
    evidence_need["reason"] = "" if reason in (None, False, {}) else str(reason)
    normalized["evidence_need"] = evidence_need
    requirements = dict(normalized.get("candidate_requirements") or {})
    requirements["hard_constraints"] = _as_string_list(
        requirements.get("hard_constraints")
    )
    requirements["soft_preferences"] = _as_string_list(
        requirements.get("soft_preferences")
    )
    normalized["candidate_requirements"] = requirements
    tool_calls = normalized.get("tool_calls")
    normalized["tool_calls"] = [] if tool_calls in (None, "", False, {}) else tool_calls
    memory_patch = dict(normalized.get("memory_patch") or {})
    memory_patch["active_goal"] = (
        ""
        if memory_patch.get("active_goal") in (None, False, {})
        else str(memory_patch.get("active_goal") or "")
    )
    memory_patch["confirmed_facts"] = (
        dict(memory_patch.get("confirmed_facts") or {})
        if isinstance(memory_patch.get("confirmed_facts"), dict)
        else {}
    )
    memory_patch["preferences"] = (
        dict(memory_patch.get("preferences") or {})
        if isinstance(memory_patch.get("preferences"), dict)
        else {}
    )
    memory_patch["rejected_options"] = _as_string_list(
        memory_patch.get("rejected_options")
    )
    memory_patch["pending_question"] = (
        ""
        if memory_patch.get("pending_question") in (None, False, {})
        else str(memory_patch.get("pending_question") or "")
    )
    normalized["memory_patch"] = memory_patch
    return normalized


def reasoning_model_enabled() -> bool:
    from testing_mode import is_testing_mode

    return (
        not is_testing_mode()
        and os.getenv("LLM_PROVIDER", "mock").strip().lower() == "openai"
        and os.getenv("REASONING_MODEL_ENABLED", "true").strip().lower()
        in {"1", "true", "yes", "on"}
    )


def _json_completion(
    system_prompt: str,
    payload: dict,
    response_model: Type[BaseModel],
) -> dict:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required for reasoning-model mode")
    runtime = log_llm_call("query_json")
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL", "").strip() or None,
    )
    response = client.chat.completions.create(
        model=runtime["model"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("Reasoning model returned an empty response")
    return json.loads(content)


_PLAN_PROMPT = """
You are Pawfect's pre-tool reasoning model. Reason from the complete conversation
state and the current user goal; do not classify by keywords or fixed phrases.

Decide what must happen next and return one JSON object matching DecisionPlan:
- user_goal: concise current goal
- current_decision: the decision that moves the goal forward now
- response_mode: GREETING for a conversational opening, otherwise GENERAL
- main_intent and scenario_intent: the operational meaning of this turn
- service_type and extracted_entities: only values supported by the conversation
- goal_status: in_progress, awaiting_evidence, awaiting_user, resolved, or blocked
- known_facts: facts explicitly present in the message or verified state
- critical_unknowns: only missing facts that can change the decision
- evidence_need: {rag, relational, reason}
- candidate_requirements: {hard_constraints, soft_preferences}
- tool_calls: [{name, arguments, reason}] using only available_tools; use read
  tools only and return [] when verified state already contains the needed data
- memory_patch: {active_goal, confirmed_facts, preferences, rejected_options,
  pending_question}; persist only facts supported by the user or verified tools
- next_action: ANSWER_DIRECTLY, RETRIEVE_POLICY, QUERY_RELATIONAL,
  RETRIEVE_AND_QUERY, ASK_CRITICAL_CLARIFICATION, COMPARE_CANDIDATES,
  REQUEST_CONFIRMATION, EXECUTE_ACTION, RECOVER_WITH_ALTERNATIVE, or
  ESCALATE_TO_STAFF

Use RAG for tenant-specific policy/service knowledge. Use relational evidence
for live customer, pet, booking, payment, loyalty, catalogue, capacity, or
availability facts. Use both only when both are decision-critical.

Do not select a candidate before tools return. Do not invent facts, IDs,
policies, prices, availability, preferences, or completed actions.
Return JSON only.
"""


_RESOLVE_PROMPT = """
You are Pawfect's post-tool grounded decision model. Use only validated evidence
and valid candidate IDs supplied by the backend.

Return one JSON object matching GroundedDecision:
- goal_status
- valid_candidate_ids: copy only IDs supplied by the backend
- evidence_ids: copy only validated IDs supplied by the backend
- recommendation: {trigger, selected_candidate_id, decisive_factors, trade_offs}
- next_action

A selected_candidate_id must be in valid_candidate_ids. Trigger a suggestion
only when it reduces decision effort and no critical unknown can reverse it.
If evidence is invalid, select ESCALATE_TO_STAFF. Never report an action as
completed unless a verified backend action result says it succeeded.
Return JSON only.
"""


def reason_decision_plan(
    *,
    user_message: str,
    conversation_state: dict,
    conversation_history: list[dict] | None,
    intent_observation: dict,
    tool_catalog: list[str],
    fallback: DecisionPlan,
) -> DecisionPlan:
    if not reasoning_model_enabled():
        return fallback
    try:
        raw = _json_completion(
            _PLAN_PROMPT,
            {
                "user_message": user_message,
                "current_date": date.today().isoformat(),
                "conversation_history": conversation_history or [],
                "conversation_state": conversation_state,
                "intent_observation": intent_observation,
                "available_tools": tool_catalog,
            },
            DecisionPlan,
        )
        plan = DecisionPlan.model_validate(_normalize_plan_output(raw))
        allowed_tools = set(tool_catalog)
        plan.tool_calls = [
            call for call in plan.tool_calls if call.name in allowed_tools
        ]
        from date_normalization import extract_customer_date

        message_date = extract_customer_date(user_message)
        if message_date is not None:
            normalized_date = message_date.isoformat()
            if "preferred_date" in plan.extracted_entities:
                plan.extracted_entities["preferred_date"] = normalized_date
            for call in plan.tool_calls:
                if call.name == "check_availability":
                    call.arguments["preferred_date"] = normalized_date
        return plan
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning(
            "reasoning_plan_fallback type=%s error=%s",
            type(exc).__name__,
            exc,
        )
        return fallback


_WRITE_SCENARIOS = {
    "CONFIRM_BOOKING",
    "CANCEL_BOOKING",
    "RESCHEDULE_BOOKING",
    "REDEEM_REWARD",
    "CREATE_CUSTOMER",
    "CREATE_PET",
}


def project_plan_to_intent(plan: DecisionPlan, observed_intent: dict) -> dict:
    """Project model semantics into the legacy execution contract.

    Read/greeting/RAG semantics may be corrected by the reasoning model. Write
    semantics remain owned by the validated process-flow guardrails.
    """
    projected = dict(observed_intent or {})
    observed_scenario = str(projected.get("scenario_intent") or "UNKNOWN")
    planned_scenario = str(plan.scenario_intent or "UNKNOWN").strip().upper()
    if (
        planned_scenario in ALLOWED_SCENARIO_INTENTS
        and planned_scenario not in _WRITE_SCENARIOS
        and observed_scenario not in _WRITE_SCENARIOS
    ):
        projected["scenario_intent"] = planned_scenario
        planned_main = str(plan.main_intent or "UNKNOWN").strip().upper()
        if planned_main in ALLOWED_MAIN_INTENTS:
            projected["main_intent"] = planned_main
        planned_service = str(plan.service_type or "UNKNOWN").strip().upper()
        if planned_service in ALLOWED_SERVICE_TYPES:
            projected["service_type"] = planned_service
        entities = dict(projected.get("entities") or {})
        entities.update(
            {
                key: value
                for key, value in dict(plan.extracted_entities or {}).items()
                if value not in (None, "", [], {})
            }
        )
        projected["entities"] = entities
        projected["missing_information"] = list(plan.critical_unknowns or [])
        projected["confidence"] = max(float(projected.get("confidence") or 0), 0.99)
    return normalize_intent_result(projected)


def planned_read_tool_calls(
    plan: DecisionPlan,
    *,
    user_message: str = "",
) -> list[dict]:
    """Convert an allow-listed reasoning tool plan to the existing executor."""
    allowed_arguments = {
        "get_customer_profile": set(),
        "get_pet_profiles": {"pet_name"},
        "get_booking_status": {"booking_id", "service_type", "pet_name"},
        "get_latest_booking": set(),
        "check_availability": {"service_type", "preferred_date", "preferred_time"},
        "get_loyalty_points": set(),
        "get_membership_status": set(),
        "get_loyalty_account": set(),
        "get_payment_history": set(),
        "get_redemption_history": set(),
        "get_message_history": set(),
        "get_company_information": set(),
        "get_staff_directory": set(),
    }
    calls: list[dict] = []
    for index, call in enumerate(plan.tool_calls, start=1):
        arguments = {
            key: value
            for key, value in dict(call.arguments or {}).items()
            if key in allowed_arguments.get(call.name, set())
        }
        if call.name == "check_availability" and arguments.get("preferred_date"):
            from date_normalization import extract_customer_date, parse_customer_date

            parsed_date = parse_customer_date(str(arguments["preferred_date"]))
            message_date = extract_customer_date(user_message)
            if message_date is not None:
                parsed_date = message_date
            if parsed_date is not None and parsed_date >= date.today():
                arguments["preferred_date"] = parsed_date.isoformat()
            else:
                arguments.pop("preferred_date", None)
        calls.append(
            {
            "id": f"reasoning_{index}",
            "name": call.name,
            "arguments": arguments,
            }
        )
    return calls


def route_from_plan(
    plan: DecisionPlan,
    safe_route: dict,
    *,
    protected_action: bool = False,
) -> dict:
    """Translate model evidence needs into an allow-listed backend route."""
    existing = str(safe_route.get("route") or "")
    if protected_action:
        return safe_route
    if existing == "ASK_MISSING_INFO" and plan.evidence_need.rag:
        return {
            "route": "CALL_RAG_THEN_ASK_MISSING_INFO",
            "reason": plan.evidence_need.reason,
        }
    if existing == "ASK_MISSING_INFO":
        return safe_route
    if plan.next_action == NextAction.ASK_CRITICAL_CLARIFICATION:
        return {"route": "ASK_MISSING_INFO", "reason": plan.current_decision}
    if plan.next_action == NextAction.ESCALATE_TO_STAFF:
        return {"route": "HUMAN_HANDOFF", "reason": plan.current_decision}
    if plan.evidence_need.rag and plan.evidence_need.relational:
        return {"route": "CALL_RAG_AND_DATABASE", "reason": plan.evidence_need.reason}
    if plan.evidence_need.rag:
        return {"route": "CALL_KNOWLEDGE_RAG", "reason": plan.evidence_need.reason}
    if plan.evidence_need.relational:
        return {"route": "CALL_DATABASE", "reason": plan.evidence_need.reason}
    return {"route": "ANSWER_DIRECTLY", "reason": plan.current_decision}


def reason_grounded_decision(
    *,
    plan: DecisionPlan,
    validation: EvidenceValidation,
    deterministic_candidate_decision: GroundedDecision,
    rag_context: list[dict],
    database_result: dict,
) -> GroundedDecision:
    if not reasoning_model_enabled():
        return deterministic_candidate_decision
    if not validation.valid:
        return GroundedDecision(
            goal_status=GoalStatus.BLOCKED,
            evidence_ids=validation.evidence_ids,
            next_action=NextAction.ESCALATE_TO_STAFF,
        )
    try:
        raw = _json_completion(
            _RESOLVE_PROMPT,
            {
                "decision_plan": model_to_dict(plan),
                "evidence_validation": model_to_dict(validation),
                "valid_candidate_ids": deterministic_candidate_decision.valid_candidate_ids,
                "validated_evidence_ids": validation.evidence_ids,
                "rag_evidence": rag_context,
                "database_result": database_result,
            },
            GroundedDecision,
        )
        decision = GroundedDecision.model_validate(raw)
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning(
            "grounded_decision_fallback type=%s error=%s",
            type(exc).__name__,
            exc,
        )
        return deterministic_candidate_decision
    allowed_candidates = set(deterministic_candidate_decision.valid_candidate_ids)
    if set(decision.valid_candidate_ids) - allowed_candidates:
        raise ValueError("Reasoning model returned an unvalidated candidate")
    selected = decision.recommendation.selected_candidate_id
    if selected and selected not in allowed_candidates:
        raise ValueError("Reasoning model selected an unvalidated candidate")
    if set(decision.evidence_ids) - set(validation.evidence_ids):
        raise ValueError("Reasoning model referenced unvalidated evidence")
    return decision
