"""Data-driven regressions derived from real multi-turn failure transcripts."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage

from app.context.state import ConversationState
from app.orchestrator import PawfectOrchestrator


CASES = json.loads(
    (Path(__file__).parent / "evals" / "conversation_regressions.json").read_text(
        encoding="utf-8"
    )
)


def test_confirmation_transcripts():
    for case in CASES["confirmation"]:
        actual = PawfectOrchestrator._confirmation_intent(case["user"])
        assert actual == case["expected"], case["id"]


def test_pet_resolution_transcripts():
    for index, case in enumerate(CASES["pet_resolution"], start=1):
        state = ConversationState(phone_number="+60123456705", company_id="1")
        state.turn_counter = index
        state.known_pets = case["pets"]
        state.pet_id = case["initial_pet_id"]

        PawfectOrchestrator._match_named_pet(state, case["user"])

        assert state.pet_id == case["expected_pet_id"], case["id"]


def test_evidence_scope_transcripts():
    for case in CASES["evidence"]:
        state = ConversationState(phone_number="+60123456705", company_id="1")
        state.turn_counter = 4
        state.verified_facts[case["fact_key"]] = case["evidence"]

        actual = PawfectOrchestrator._needs_tool_repair(
            state,
            case["user"],
            case["response"],
            [],
        )

        assert actual is case["expected_repair"], case["id"]


def test_optional_booking_transcripts():
    for case in CASES["optional_booking"]:
        state = ConversationState(phone_number="+60123456705", company_id="1")
        state.turn_counter = 6
        state.booking_flow_started_turn = case["flow_started_turn"]
        state.history = case["history"]
        state.preferred_staff = case.get("preferred_staff")

        result = PawfectOrchestrator._reject_unconfirmed_optional_booking_fields(
            state,
            case["args"],
            case["user"],
        )
        actual_error = result.get("error") if isinstance(result, dict) else None

        assert actual_error == case["expected_error"], case["id"]


def test_response_grounding_transcripts():
    tool_by_kind = {
        "booking_preview": "create_booking",
        "membership": "register_loyalty_member",
        "document": "send_booking_confirmation",
    }
    grounder_by_kind = {
        "booking_preview": PawfectOrchestrator._ground_booking_preview_response,
        "membership": PawfectOrchestrator._ground_membership_response,
        "document": PawfectOrchestrator._ground_document_delivery_response,
    }

    for case in CASES["response_grounding"]:
        response = AIMessage(content=case["model_response"])
        trace = [{
            "tool": tool_by_kind[case["kind"]],
            "result": json.dumps(case["result"]),
        }]

        grounded = grounder_by_kind[case["kind"]](response, case["user"], trace)

        for expected in case["expected_contains"]:
            assert expected in grounded.content, case["id"]
        for rejected in case["expected_absent"]:
            assert rejected not in grounded.content, case["id"]
