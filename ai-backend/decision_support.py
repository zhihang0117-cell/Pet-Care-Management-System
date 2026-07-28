"""Evidence-grounded decision support for the Pawfect chat runtime.

This module deliberately separates the pre-tool plan from the post-tool
decision.  It does not execute tools or customer writes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class NextAction(str, Enum):
    ANSWER_DIRECTLY = "ANSWER_DIRECTLY"
    RETRIEVE_POLICY = "RETRIEVE_POLICY"
    QUERY_RELATIONAL = "QUERY_RELATIONAL"
    RETRIEVE_AND_QUERY = "RETRIEVE_AND_QUERY"
    ASK_CRITICAL_CLARIFICATION = "ASK_CRITICAL_CLARIFICATION"
    COMPARE_CANDIDATES = "COMPARE_CANDIDATES"
    REQUEST_CONFIRMATION = "REQUEST_CONFIRMATION"
    EXECUTE_ACTION = "EXECUTE_ACTION"
    RECOVER_WITH_ALTERNATIVE = "RECOVER_WITH_ALTERNATIVE"
    ESCALATE_TO_STAFF = "ESCALATE_TO_STAFF"


class GoalStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    AWAITING_EVIDENCE = "awaiting_evidence"
    AWAITING_USER = "awaiting_user"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RESOLVED = "resolved"
    BLOCKED = "blocked"


class EvidenceNeed(BaseModel):
    rag: bool = False
    relational: bool = False
    reason: str = ""


class CandidateRequirements(BaseModel):
    hard_constraints: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)


class PlannedToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class MemoryPatch(BaseModel):
    active_goal: str = ""
    confirmed_facts: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)
    rejected_options: list[str] = Field(default_factory=list)
    pending_question: str = ""


class DecisionPlan(BaseModel):
    user_goal: str
    current_decision: str
    response_mode: str = "GENERAL"
    main_intent: str = "UNKNOWN"
    scenario_intent: str = "UNKNOWN"
    service_type: str = "UNKNOWN"
    extracted_entities: dict[str, Any] = Field(default_factory=dict)
    goal_status: GoalStatus
    known_facts: dict[str, Any] = Field(default_factory=dict)
    critical_unknowns: list[str] = Field(default_factory=list)
    evidence_need: EvidenceNeed = Field(default_factory=EvidenceNeed)
    candidate_requirements: CandidateRequirements = Field(default_factory=CandidateRequirements)
    tool_calls: list[PlannedToolCall] = Field(default_factory=list)
    memory_patch: MemoryPatch = Field(default_factory=MemoryPatch)
    next_action: NextAction


class EvidenceValidation(BaseModel):
    valid: bool
    evidence_ids: list[str] = Field(default_factory=list)
    rejected_evidence_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class GroundedRecommendation(BaseModel):
    trigger: bool = False
    selected_candidate_id: str | None = None
    decisive_factors: list[str] = Field(default_factory=list)
    trade_offs: list[str] = Field(default_factory=list)


class GroundedDecision(BaseModel):
    goal_status: GoalStatus
    valid_candidate_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    recommendation: GroundedRecommendation = Field(default_factory=GroundedRecommendation)
    next_action: NextAction


def model_to_dict(model: BaseModel) -> dict:
    """Support both Pydantic v1 and v2."""
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()


def _facts_from_session(session) -> dict[str, Any]:
    if session is None:
        return {}
    values = {
        "customer_id": getattr(session, "customer_id", None),
        "pet_id": getattr(session, "pet_id", None),
        "pet_name": getattr(session, "pet_name", ""),
        "service_type": getattr(session, "last_service_type", ""),
        "preferred_date": getattr(session, "preferred_date", ""),
        "preferred_time": getattr(session, "preferred_time", ""),
        "selected_package": getattr(session, "selected_package", ""),
    }
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


def build_decision_plan(
    *,
    user_message: str,
    intent_json: dict,
    route_result: dict,
    session=None,
) -> DecisionPlan:
    """Build an auditable pre-tool plan from normalized runtime state."""
    scenario = str(intent_json.get("scenario_intent") or "UNKNOWN").strip()
    route = str(route_result.get("route") or "HUMAN_HANDOFF").strip()
    missing = [str(item) for item in (intent_json.get("missing_information") or []) if str(item)]
    facts = _facts_from_session(session)
    facts.update(
        {
            key: value
            for key, value in dict(intent_json.get("entities") or {}).items()
            if value not in (None, "", [], {})
        }
    )

    goal = str(intent_json.get("user_goal") or "").strip()
    if not goal:
        goal = scenario.lower().replace("_", " ")

    action_by_route = {
        "CALL_KNOWLEDGE_RAG": NextAction.RETRIEVE_POLICY,
        "CALL_DATABASE": NextAction.QUERY_RELATIONAL,
        "CALL_RAG_AND_DATABASE": NextAction.RETRIEVE_AND_QUERY,
        "CALL_RAG_THEN_ASK_MISSING_INFO": NextAction.RETRIEVE_POLICY,
        "ASK_MISSING_INFO": NextAction.ASK_CRITICAL_CLARIFICATION,
        "HUMAN_HANDOFF": NextAction.ESCALATE_TO_STAFF,
    }
    next_action = action_by_route.get(route, NextAction.ANSWER_DIRECTLY)
    if missing:
        status = GoalStatus.AWAITING_USER
    elif next_action in {
        NextAction.RETRIEVE_POLICY,
        NextAction.QUERY_RELATIONAL,
        NextAction.RETRIEVE_AND_QUERY,
    }:
        status = GoalStatus.AWAITING_EVIDENCE
    elif next_action == NextAction.ESCALATE_TO_STAFF:
        status = GoalStatus.BLOCKED
    else:
        status = GoalStatus.IN_PROGRESS

    recommendation_scenarios = {
        "MAKE_BOOKING",
        "CHECK_AVAILABILITY",
        "GET_BOOKING_SERVICE_OPTIONS",
        "REPEAT_LAST_BOOKING",
        "CHECK_COUPON_ELIGIBILITY",
        "CUSTOMER_GREETING",
    }
    constraints: list[str] = []
    if scenario in recommendation_scenarios:
        constraints = ["candidate_exists", "candidate_is_available", "candidate_is_policy_compliant"]

    return DecisionPlan(
        user_goal=goal,
        current_decision=f"next best action for {goal}",
        goal_status=status,
        known_facts=facts,
        critical_unknowns=missing,
        evidence_need=EvidenceNeed(
            rag=route in {"CALL_KNOWLEDGE_RAG", "CALL_RAG_AND_DATABASE", "CALL_RAG_THEN_ASK_MISSING_INFO"},
            relational=route in {"CALL_DATABASE", "CALL_RAG_AND_DATABASE"},
            reason=str(route_result.get("reason") or ""),
        ),
        candidate_requirements=CandidateRequirements(
            hard_constraints=constraints,
            soft_preferences=["user-stated preference", "conversation continuity"],
        ),
        next_action=next_action,
    )


def validate_evidence(
    *,
    plan: DecisionPlan,
    rag_context: list[dict],
    database_result: dict,
    active_tenant_id: str = "",
) -> EvidenceValidation:
    """Validate provenance and reject cross-tenant RAG evidence."""
    evidence_ids: list[str] = []
    rejected: list[str] = []
    issues: list[str] = []

    for index, chunk in enumerate(rag_context or []):
        metadata = dict(chunk.get("metadata") or {})
        evidence_id = str(
            chunk.get("chunk_id")
            or metadata.get("chunk_id")
            or metadata.get("document_id")
            or f"rag:{index}"
        )
        tenant_id = str(
            chunk.get("company_id")
            or chunk.get("tenant_id")
            or metadata.get("company_id")
            or metadata.get("tenant_id")
            or ""
        )
        if active_tenant_id and tenant_id and tenant_id != active_tenant_id:
            rejected.append(evidence_id)
            issues.append("cross_tenant_evidence_rejected")
            continue
        if str(chunk.get("text") or "").strip():
            evidence_ids.append(evidence_id)

    status = str((database_result or {}).get("status") or "not_applicable")
    action = str((database_result or {}).get("action") or "unknown")
    if status not in {"not_applicable", ""}:
        evidence_ids.append(f"db:{action}:{status}")
    if status == "error":
        issues.append("database_error")

    if plan.evidence_need.rag and not any(not item.startswith("db:") for item in evidence_ids):
        issues.append("required_rag_evidence_missing")
    if plan.evidence_need.relational and status in {"not_applicable", "", "error"}:
        issues.append("required_relational_evidence_missing")

    return EvidenceValidation(
        valid=not issues,
        evidence_ids=evidence_ids,
        rejected_evidence_ids=rejected,
        issues=sorted(set(issues)),
    )


def _availability_candidates(database_result: dict) -> tuple[list[str], str | None, list[str]]:
    data = dict((database_result or {}).get("data") or {})
    availability = dict(data.get("availability_result") or {})
    slots = [
        str(item)
        for item in (availability.get("all_available_slots") or data.get("available_slots") or [])
        if str(item)
    ]
    requested = str(availability.get("requested_time") or data.get("requested_time") or "")
    matched = str((availability.get("matched_slot") or {}).get("start_time") or "")
    alternatives = [str(item) for item in (availability.get("alternative_slots") or []) if str(item)]
    selected = matched or (alternatives[0] if alternatives else (slots[0] if slots else None))
    reasons = ["verified available slot"]
    if selected and requested and selected != requested:
        reasons = ["nearest verified alternative to the requested time"]
    elif selected and not requested:
        reasons = ["earliest verified available slot"]
    return slots, selected, reasons


def _service_candidates(database_result: dict) -> tuple[list[str], str | None, list[str]]:
    options = list(dict((database_result or {}).get("data") or {}).get("service_options") or [])
    candidate_ids = [
        str(item.get("service_id") or item.get("room_type_id") or item.get("service_name") or "")
        for item in options
    ]
    candidate_ids = [item for item in candidate_ids if item]
    return candidate_ids, None, []


def _greeting_candidates(database_result: dict) -> tuple[list[str], str | None, list[str]]:
    latest = dict(dict((database_result or {}).get("data") or {}).get("latest_booking") or {})
    if not latest:
        return [], None, []
    booking_id = str(latest.get("booking_id") or "latest")
    candidate_id = f"repeat_booking:{booking_id}"
    service = str(
        latest.get("service_type")
        or latest.get("last_service_type")
        or latest.get("service_name")
        or ""
    ).strip().lower()
    pet_name = str(latest.get("pet_name") or "").strip()
    factor = "matches the customer's most recent verified booking"
    if service and pet_name:
        factor = f"most recent verified booking was {service} for {pet_name}"
    return [candidate_id], candidate_id, [factor]


def resolve_grounded_decision(
    *,
    plan: DecisionPlan,
    validation: EvidenceValidation,
    database_result: dict,
    handoff_required: bool = False,
) -> GroundedDecision:
    """Resolve the next action from validated post-tool evidence."""
    if handoff_required or not validation.valid:
        return GroundedDecision(
            goal_status=GoalStatus.BLOCKED,
            evidence_ids=validation.evidence_ids,
            next_action=NextAction.ESCALATE_TO_STAFF,
        )

    action = str((database_result or {}).get("action") or "")
    status = str((database_result or {}).get("status") or "not_applicable")
    candidates: list[str] = []
    selected: str | None = None
    factors: list[str] = []

    if action == "check_available_slots" and status == "success":
        candidates, selected, factors = _availability_candidates(database_result)
    elif action == "get_booking_service_options" and status == "success":
        candidates, selected, factors = _service_candidates(database_result)
    elif action == "check_customer_by_phone" and status == "success":
        candidates, selected, factors = _greeting_candidates(database_result)

    if selected:
        return GroundedDecision(
            goal_status=GoalStatus.AWAITING_CONFIRMATION,
            valid_candidate_ids=candidates,
            evidence_ids=validation.evidence_ids,
            recommendation=GroundedRecommendation(
                trigger=True,
                selected_candidate_id=selected,
                decisive_factors=factors,
            ),
            next_action=NextAction.REQUEST_CONFIRMATION,
        )
    if candidates:
        return GroundedDecision(
            goal_status=GoalStatus.AWAITING_USER,
            valid_candidate_ids=candidates,
            evidence_ids=validation.evidence_ids,
            next_action=NextAction.COMPARE_CANDIDATES,
        )
    if plan.critical_unknowns:
        return GroundedDecision(
            goal_status=GoalStatus.AWAITING_USER,
            evidence_ids=validation.evidence_ids,
            next_action=NextAction.ASK_CRITICAL_CLARIFICATION,
        )
    if status == "success" or validation.evidence_ids:
        return GroundedDecision(
            goal_status=GoalStatus.RESOLVED,
            evidence_ids=validation.evidence_ids,
            next_action=NextAction.ANSWER_DIRECTLY,
        )
    return GroundedDecision(
        goal_status=GoalStatus.IN_PROGRESS,
        evidence_ids=validation.evidence_ids,
        next_action=NextAction.ANSWER_DIRECTLY,
    )
