"""Tests for TESTING mode and production error handling."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_chat_reraises_in_testing_mode(client):
    with patch("main.detect_intent", side_effect=RuntimeError("boom")):
        try:
            client.post("/chat", json={"message": "hi", "phone_number": "+60 99-000 0001"})
            raised = False
        except RuntimeError as exc:
            raised = True
            assert str(exc) == "boom"
    assert raised, "TESTING mode must re-raise unexpected /chat errors"


def test_chat_returns_safe_fallback_in_production(client, production_error_handling):
    with patch("main.detect_intent", side_effect=RuntimeError("boom")):
        response = client.post(
            "/chat",
            json={"message": "hi", "phone_number": "+60 99-000 0002"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["handoff_required"] is True
    assert "trouble" in body["reply"].lower()
    assert body["llm_provider_used"] == "error_fallback"
    assert body["identity_result"].get("error_id")


def test_llm_service_reraises_in_testing_mode(monkeypatch):
    import llm_service

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.delenv("_EVAL_OVERRIDE_ACTIVE", raising=False)
    monkeypatch.setattr(
        "real_llm.real_llm_intent_detection",
        lambda _msg: (_ for _ in ()).throw(RuntimeError("llm down")),
    )
    with pytest.raises(RuntimeError, match="llm down"):
        llm_service.detect_intent("hello")

