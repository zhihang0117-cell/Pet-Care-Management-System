from __future__ import annotations

from session_store import COLLECT_CUSTOMER_NAME, REPEAT_BOOKING_PENDING_ACTION, SessionContext


def test_name_answer_is_resolved_from_memory_before_llm():
    from session_continuation import resolve_pending_intent_before_llm

    session = SessionContext(
        pending_action=COLLECT_CUSTOMER_NAME,
        missing_fields=["full_name"],
        last_scenario_intent="CUSTOMER_GREETING",
    )
    result = resolve_pending_intent_before_llm(session, "hung wei")
    assert result is not None
    assert result["scenario_intent"] == "COLLECT_CUSTOMER_NAME"
    assert result["confidence"] == 0.99


def test_booking_date_answer_is_resolved_from_memory_before_llm():
    from session_continuation import resolve_pending_intent_before_llm

    session = SessionContext(
        pending_action=REPEAT_BOOKING_PENDING_ACTION,
        missing_fields=["preferred_date"],
        last_scenario_intent="MAKE_BOOKING",
        booking_creation_flow=True,
    )
    result = resolve_pending_intent_before_llm(session, "3 August")
    assert result is not None
    assert result["main_intent"] == "BOOKING_INTENT"


def test_unrelated_question_is_not_forced_into_pending_field():
    from session_continuation import resolve_pending_intent_before_llm

    session = SessionContext(
        pending_action=REPEAT_BOOKING_PENDING_ACTION,
        missing_fields=["preferred_date"],
        last_scenario_intent="MAKE_BOOKING",
        booking_creation_flow=True,
    )
    assert resolve_pending_intent_before_llm(
        session, "How many loyalty points do I have?"
    ) is None


def test_read_only_interruption_restores_booking_and_appends_resume_question():
    from session_continuation import (
        append_pending_resume_prompt,
        capture_pending_flow,
        restore_pending_flow,
        should_resume_pending_flow,
    )

    session = SessionContext(
        pending_action=REPEAT_BOOKING_PENDING_ACTION,
        missing_fields=["preferred_date"],
        last_scenario_intent="MAKE_BOOKING",
        last_service_type="GROOMING",
        pet_name="Milo",
        booking_creation_flow=True,
    )
    snapshot = capture_pending_flow(session)
    session.pending_action = ""
    session.missing_fields = []
    intent = {
        "scenario_intent": "CHECK_LOYALTY_POINTS",
        "entities": {},
        "missing_information": [],
    }
    assert should_resume_pending_flow(snapshot, intent) is True
    restore_pending_flow(session, snapshot)
    reply = append_pending_resume_prompt(
        "You currently have 544 loyalty points.",
        session,
        intent,
    )
    assert session.pending_action == REPEAT_BOOKING_PENDING_ACTION
    assert session.missing_fields == ["preferred_date"]
    assert "continue where we left off" in reply
    assert "date" in reply.lower()
