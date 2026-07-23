"""Shared pytest fixtures for Pawfect backend conversation-flow tests."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from dotenv import load_dotenv

load_dotenv()


@pytest.fixture(scope="session")
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture(autouse=True)
def testing_env(monkeypatch):
    """Enable TESTING mode and mock-friendly provider defaults for every test."""
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setenv("DATABASE_PROVIDER", "mock")
    monkeypatch.setenv("RELATIONAL_PROVIDER", "mock")
    monkeypatch.setenv("FINAL_RESPONSE_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("RAG_PROVIDER", "mock")
    monkeypatch.delenv("_EVAL_OVERRIDE_ACTIVE", raising=False)


@pytest.fixture(autouse=True)
def reset_relational_provider():
    """Reset cached relational repository between tests."""
    from relational_provider import reset_relational_repository

    reset_relational_repository()
    yield
    reset_relational_repository()


@pytest.fixture(autouse=True)
def mock_conversation_dependencies(monkeypatch):
    """
    Keep conversation-flow tests offline: no OpenAI, Supabase, embeddings, or RAG network calls.
    """
    from mock_llm import mock_llm_intent_detection
    from mock_rag import mock_rag_retrieve

    def _mock_detect_intent(user_message: str) -> dict:
        return {
            "intent_json": mock_llm_intent_detection(user_message),
            "provider_used": "mock",
            "query_json_model_used": "mock",
            "query_json_provider_used": "mock",
            "query_json_base_url_used": "",
        }

    def _mock_real_rag(user_message: str, intent_json: dict, **kwargs) -> dict:
        from mock_rag import mock_rag_retrieve

        route = str(kwargs.get("route") or intent_json.get("route") or "CALL_KNOWLEDGE_RAG")
        chunks = mock_rag_retrieve(user_message, intent_json, route)
        return {
            "chunks": chunks,
            "rag_debug": {"raw_result_count": len(chunks)},
        }

    monkeypatch.setattr("main.detect_intent", _mock_detect_intent)
    monkeypatch.setattr("rag_service.real_rag_retrieve", _mock_real_rag)
    monkeypatch.setattr("rag_service.embed_query", lambda *_args, **_kwargs: [0.0] * 8)
    monkeypatch.setattr(
        "database_service.get_database_provider",
        lambda: "mock",
    )


@pytest.fixture
def production_error_handling(monkeypatch):
    """Simulate production mode where /chat returns a safe fallback instead of raising."""
    monkeypatch.delenv("TESTING", raising=False)
    with patch("testing_mode.is_testing_mode", return_value=False):
        yield
