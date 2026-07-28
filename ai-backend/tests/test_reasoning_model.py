from unittest.mock import patch
from datetime import date

import pytest

from decision_support import (
    DecisionPlan,
    EvidenceNeed,
    EvidenceValidation,
    GoalStatus,
    GroundedDecision,
    NextAction,
)
from reasoning_model import (
    planned_read_tool_calls,
    project_plan_to_intent,
    reason_decision_plan,
    reason_grounded_decision,
    route_from_plan,
)


def _fallback_plan() -> DecisionPlan:
    return DecisionPlan(
        user_goal="fallback",
        current_decision="fallback",
        goal_status=GoalStatus.IN_PROGRESS,
        next_action=NextAction.ANSWER_DIRECTLY,
    )


def test_reasoning_plan_uses_model_output_and_controls_evidence_route():
    model_output = {
        "user_goal": "choose a suitable grooming slot",
        "current_decision": "retrieve live slots before comparing",
        "goal_status": "awaiting_evidence",
        "known_facts": {"preferred_date": "2026-07-30"},
        "critical_unknowns": [],
        "evidence_need": {
            "rag": False,
            "relational": True,
            "reason": "availability is live data",
        },
        "candidate_requirements": {
            "hard_constraints": ["slot must be available"],
            "soft_preferences": ["earlier time"],
        },
        "next_action": "QUERY_RELATIONAL",
    }
    with (
        patch("reasoning_model.reasoning_model_enabled", return_value=True),
        patch("reasoning_model._json_completion", return_value=model_output),
    ):
        plan = reason_decision_plan(
            user_message="Anything earlier next Thursday?",
            conversation_state={"preferred_date": "2026-07-30"},
            conversation_history=[{"role": "assistant", "content": "Which day suits you?"}],
            intent_observation={},
            tool_catalog=["availability_db"],
            fallback=_fallback_plan(),
        )

    assert plan.user_goal == "choose a suitable grooming slot"
    assert route_from_plan(plan, {"route": "CALL_KNOWLEDGE_RAG"})["route"] == "CALL_DATABASE"


def test_post_tool_model_cannot_select_unvalidated_candidate():
    plan = _fallback_plan()
    validation = EvidenceValidation(valid=True, evidence_ids=["db:slots:success"])
    deterministic = GroundedDecision(
        goal_status=GoalStatus.AWAITING_USER,
        valid_candidate_ids=["09:00", "10:00"],
        evidence_ids=["db:slots:success"],
        next_action=NextAction.COMPARE_CANDIDATES,
    )
    invalid_output = {
        "goal_status": "awaiting_confirmation",
        "valid_candidate_ids": ["09:00", "10:00"],
        "evidence_ids": ["db:slots:success"],
        "recommendation": {
            "trigger": True,
            "selected_candidate_id": "08:00",
            "decisive_factors": ["earliest"],
            "trade_offs": [],
        },
        "next_action": "REQUEST_CONFIRMATION",
    }
    with (
        patch("reasoning_model.reasoning_model_enabled", return_value=True),
        patch("reasoning_model._json_completion", return_value=invalid_output),
        pytest.raises(ValueError, match="unvalidated candidate"),
    ):
        reason_grounded_decision(
            plan=plan,
            validation=validation,
            deterministic_candidate_decision=deterministic,
            rag_context=[],
            database_result={},
        )


def test_reasoning_plan_projects_read_semantics_but_not_write_semantics():
    read_plan = _fallback_plan().model_copy(
        update={
            "main_intent": "BOOKING_INTENT",
            "scenario_intent": "CHECK_AVAILABILITY",
            "service_type": "GROOMING",
            "extracted_entities": {"preferred_date": "2026-07-31"},
        }
    )
    projected = project_plan_to_intent(
        read_plan,
        {
            "main_intent": "UNKNOWN",
            "scenario_intent": "UNKNOWN",
            "entities": {},
            "confidence": 0,
        },
    )
    assert projected["scenario_intent"] == "CHECK_AVAILABILITY"
    assert projected["entities"]["preferred_date"] == "2026-07-31"

    unsafe_write_plan = _fallback_plan().model_copy(
        update={
            "main_intent": "BOOKING_INTENT",
            "scenario_intent": "CANCEL_BOOKING",
        }
    )
    protected = project_plan_to_intent(
        unsafe_write_plan,
        {
            "main_intent": "UNKNOWN",
            "scenario_intent": "UNKNOWN",
            "entities": {},
            "confidence": 0,
        },
    )
    assert protected["scenario_intent"] == "UNKNOWN"


def test_planned_tool_call_normalizes_date_and_drops_unapproved_arguments():
    payload = _fallback_plan().model_dump()
    payload["tool_calls"] = [
        {
            "name": "check_availability",
            "arguments": {
                "service_type": "GROOMING",
                "preferred_date": "31 July 2026",
                "preferred_time": "morning",
                "pet_id": "internal-id",
            },
        }
    ]
    calls = planned_read_tool_calls(DecisionPlan.model_validate(payload))
    assert calls[0]["arguments"]["preferred_date"] == "2026-07-31"
    assert "pet_id" not in calls[0]["arguments"]


def test_user_relative_date_overrides_model_supplied_past_date():
    payload = _fallback_plan().model_dump()
    payload["tool_calls"] = [
        {
            "name": "check_availability",
            "arguments": {
                "service_type": "GROOMING",
                "preferred_date": "2020-01-01",
                "preferred_time": "morning",
            },
        }
    ]
    calls = planned_read_tool_calls(
        DecisionPlan.model_validate(payload),
        user_message="Can we do next Friday morning?",
    )
    expected_days = (4 - date.today().weekday()) % 7 or 7
    expected = date.fromordinal(date.today().toordinal() + expected_days).isoformat()
    assert calls[0]["arguments"]["preferred_date"] == expected


def test_post_tool_model_action_label_in_trigger_does_not_force_full_fallback():
    plan = _fallback_plan()
    validation = EvidenceValidation(valid=True, evidence_ids=["rag:packages"])
    deterministic = GroundedDecision(
        goal_status=GoalStatus.AWAITING_USER,
        evidence_ids=["rag:packages"],
        next_action=NextAction.ASK_CRITICAL_CLARIFICATION,
    )
    model_output = {
        "goal_status": "awaiting_user",
        "valid_candidate_ids": [],
        "evidence_ids": ["rag:packages"],
        "recommendation": {
            "trigger": "suggest_service_package",
            "selected_candidate_id": None,
            "decisive_factors": [],
            "trade_offs": [],
        },
        "next_action": "ASK_CRITICAL_CLARIFICATION",
    }

    with (
        patch("reasoning_model.reasoning_model_enabled", return_value=True),
        patch("reasoning_model._json_completion", return_value=model_output),
    ):
        decision = reason_grounded_decision(
            plan=plan,
            validation=validation,
            deterministic_candidate_decision=deterministic,
            rag_context=[],
            database_result={},
        )

    assert decision.recommendation.trigger is False
    assert decision.evidence_ids == ["rag:packages"]
