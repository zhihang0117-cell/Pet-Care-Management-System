from decision_support import (
    GoalStatus,
    NextAction,
    build_decision_plan,
    resolve_grounded_decision,
    validate_evidence,
)
from session_store import SessionContext


def test_plan_selects_mixed_evidence_without_premature_recommendation():
    session = SessionContext(
        customer_id=42,
        pet_id=114,
        pet_name="Milo",
        last_service_type="GROOMING",
        preferred_date="2026-08-07",
    )
    plan = build_decision_plan(
        user_message="Can Milo do grooming next Friday?",
        intent_json={
            "scenario_intent": "MAKE_BOOKING",
            "entities": {"preferred_date": "2026-08-07"},
            "missing_information": [],
        },
        route_result={"route": "CALL_RAG_AND_DATABASE", "reason": "policy and availability"},
        session=session,
    )

    assert plan.next_action == NextAction.RETRIEVE_AND_QUERY
    assert plan.goal_status == GoalStatus.AWAITING_EVIDENCE
    assert plan.evidence_need.rag is True
    assert plan.evidence_need.relational is True
    assert "selected_candidate_id" not in plan.model_dump()


def test_cross_tenant_evidence_is_rejected():
    plan = build_decision_plan(
        user_message="What is the boarding policy?",
        intent_json={"scenario_intent": "POLICY_INFORMATION", "missing_information": []},
        route_result={"route": "CALL_KNOWLEDGE_RAG", "reason": "tenant policy"},
    )
    validation = validate_evidence(
        plan=plan,
        rag_context=[
            {
                "chunk_id": "wrong-tenant",
                "company_id": 2,
                "text": "External food is forbidden.",
            }
        ],
        database_result={"action": "unknown", "status": "not_applicable", "data": {}},
        active_tenant_id="1",
    )

    assert validation.valid is False
    assert validation.rejected_evidence_ids == ["wrong-tenant"]
    assert "cross_tenant_evidence_rejected" in validation.issues


def test_availability_recommendation_uses_verified_candidate_only():
    plan = build_decision_plan(
        user_message="Any grooming slots tomorrow?",
        intent_json={"scenario_intent": "CHECK_AVAILABILITY", "missing_information": []},
        route_result={"route": "CALL_DATABASE", "reason": "live availability"},
    )
    database_result = {
        "action": "check_available_slots",
        "status": "success",
        "data": {
            "available_slots": ["10:00", "16:00"],
            "availability_result": {
                "requested_time": "",
                "all_available_slots": ["10:00", "16:00"],
            },
        },
    }
    validation = validate_evidence(
        plan=plan,
        rag_context=[],
        database_result=database_result,
        active_tenant_id="1",
    )
    decision = resolve_grounded_decision(
        plan=plan,
        validation=validation,
        database_result=database_result,
    )

    assert decision.recommendation.trigger is True
    assert decision.recommendation.selected_candidate_id == "10:00"
    assert decision.recommendation.selected_candidate_id in decision.valid_candidate_ids
    assert decision.next_action == NextAction.REQUEST_CONFIRMATION


def test_failed_required_database_evidence_escalates():
    plan = build_decision_plan(
        user_message="What is my booking status?",
        intent_json={"scenario_intent": "VIEW_BOOKING_STATUS", "missing_information": []},
        route_result={"route": "CALL_DATABASE", "reason": "live booking data"},
    )
    database_result = {"action": "check_booking_status", "status": "error", "data": {}}
    validation = validate_evidence(
        plan=plan,
        rag_context=[],
        database_result=database_result,
        active_tenant_id="1",
    )
    decision = resolve_grounded_decision(
        plan=plan,
        validation=validation,
        database_result=database_result,
    )

    assert validation.valid is False
    assert decision.next_action == NextAction.ESCALATE_TO_STAFF
    assert decision.recommendation.trigger is False


def test_greeting_recommends_verified_latest_booking():
    plan = build_decision_plan(
        user_message="Hi",
        intent_json={"scenario_intent": "CUSTOMER_GREETING", "missing_information": []},
        route_result={"route": "CALL_DATABASE", "reason": "load customer context"},
    )
    database_result = {
        "action": "check_customer_by_phone",
        "status": "success",
        "data": {
            "full_name": "Alicia",
            "latest_booking": {
                "booking_id": "B101",
                "service_type": "GROOMING",
                "pet_name": "Milo",
            },
        },
    }
    validation = validate_evidence(
        plan=plan,
        rag_context=[],
        database_result=database_result,
        active_tenant_id="1",
    )
    decision = resolve_grounded_decision(
        plan=plan,
        validation=validation,
        database_result=database_result,
    )

    assert decision.recommendation.trigger is True
    assert decision.recommendation.selected_candidate_id == "repeat_booking:B101"
    assert decision.recommendation.selected_candidate_id in decision.valid_candidate_ids
