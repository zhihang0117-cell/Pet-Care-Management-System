"""Offline end-to-end demo for Pawfect decision-support output."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.update(
    {
        "TESTING": "true",
        "DATABASE_PROVIDER": "mock",
        "RELATIONAL_PROVIDER": "mock",
        "FINAL_RESPONSE_PROVIDER": "mock",
        "LLM_PROVIDER": "mock",
        "RAG_PROVIDER": "mock",
    }
)

from fastapi.testclient import TestClient

import main
from main import app
from mock_llm import mock_llm_intent_detection
from mock_database import mock_database_action
from mock_rag import mock_rag_retrieve


def _mock_detect_intent(message: str) -> dict:
    return {
        "intent_json": mock_llm_intent_detection(message),
        "provider_used": "mock",
        "query_json_model_used": "mock",
        "query_json_provider_used": "mock",
        "query_json_base_url_used": "",
    }


def _compact_response(case: str, message: str, response: dict) -> dict:
    decision = dict(response.get("decision_support") or {})
    return {
        "case": case,
        "input": message,
        "reply": response.get("reply"),
        "route": (response.get("route_result") or {}).get("route"),
        "rag_used": response.get("rag_used"),
        "database_used": response.get("database_used"),
        "plan": decision.get("plan"),
        "evidence_validation": decision.get("evidence_validation"),
        "grounded_decision": decision.get("grounded_decision"),
    }


def main_demo() -> None:
    os.environ.update(
        {
            "TESTING": "true",
            "DATABASE_PROVIDER": "mock",
            "RELATIONAL_PROVIDER": "mock",
            "FINAL_RESPONSE_PROVIDER": "mock",
            "LLM_PROVIDER": "mock",
            "RAG_PROVIDER": "mock",
        }
    )
    os.environ.pop("_EVAL_OVERRIDE_ACTIVE", None)
    cases = [
        (
            "existing_customer_greeting_recommendation",
            {"message": "Hi", "phone_number": "+60 12-345 6701"},
        ),
        (
            "policy_rag_answer",
            {"message": "What is your boarding policy?", "phone_number": "+601100000101"},
        ),
        (
            "verified_availability_recommendation",
            {
                "message": "Is grooming available on 30 July 2026?",
                "phone_number": "+601100000102",
                "customer_id": "1",
            },
        ),
        (
            "critical_clarification",
            {
                "message": "How much is grooming?",
                "phone_number": "+601100000103",
                "customer_id": "1",
            },
        ),
        (
            "unsupported_request_handoff",
            {"message": "Can you diagnose why my dog is sick?", "phone_number": "+601100000104"},
        ),
    ]
    outputs = []
    def _mock_real_rag(message: str, intent_json: dict, **_kwargs) -> dict:
        route = "CALL_KNOWLEDGE_RAG"
        scenario = str(intent_json.get("scenario_intent") or "")
        if scenario in {"CANCEL_BOOKING", "RESCHEDULE_BOOKING"}:
            route = "CALL_RAG_AND_DATABASE"
        chunks = mock_rag_retrieve(message, intent_json, route)
        return {"chunks": chunks, "rag_debug": {"raw_result_count": len(chunks)}}

    def _mock_database(intent_json: dict, customer_id: str = "", phone_number: str = "", **_kwargs) -> dict:
        return mock_database_action(intent_json, customer_id, phone_number)

    def _mock_latest_booking(session) -> dict:
        from mock_database import mock_get_latest_booking_by_customer_id

        return mock_get_latest_booking_by_customer_id(
            getattr(session, "customer_id", None),
            str(getattr(session, "phone_number", "") or ""),
        )

    with (
        patch.object(main, "detect_intent", side_effect=_mock_detect_intent),
        patch("database_service.load_dotenv", return_value=False),
        patch("rag_service.load_dotenv", return_value=False),
        patch("response_generator.load_dotenv", return_value=False),
        patch("llm_service.load_dotenv", return_value=False),
        patch.object(main, "execute_relational_tool_calls", return_value={}),
        patch.object(main, "execute_database_action", side_effect=_mock_database),
        patch("database_service.fetch_latest_booking_for_entry", side_effect=_mock_latest_booking),
        patch("rag_service.real_rag_retrieve", side_effect=_mock_real_rag),
        patch("rag_service.embed_query", return_value=[0.0] * 8),
    ):
        with TestClient(app) as client:
            for name, payload in cases:
                response = client.post("/chat", json=payload)
                response.raise_for_status()
                outputs.append(_compact_response(name, payload["message"], response.json()))
    print(json.dumps(outputs, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main_demo()
