from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch


class _Response:
    def __init__(self, data):
        self.data = data


class _CustomerQuery:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def select(self, columns):
        self.calls.append(("select", columns))
        return self

    def eq(self, column, value):
        self.calls.append(("eq", column, value))
        return self

    def ilike(self, column, value):
        self.calls.append(("ilike", column, value))
        return self

    def limit(self, value):
        self.calls.append(("limit", value))
        return self

    def execute(self):
        return _Response(self.rows)


class _Client:
    def __init__(self, query):
        self.query = query

    def table(self, name):
        assert name == "customer"
        return self.query


def test_customer_lookup_filters_phone_inside_supabase():
    from customer_context import CustomerContext
    from relational_actions import check_customer_by_phone

    query = _CustomerQuery(
        [
            {
                "customer_id": 1,
                "company_id": 1,
                "full_name": "Alicia",
                "phone_number": "+60 12-345 6701",
                "address": "",
            }
        ]
    )
    with patch("relational_actions.get_supabase_client", return_value=_Client(query)):
        result = check_customer_by_phone(
            CustomerContext(phone_number="+60 12-345 6701", company_id=1)
        )

    assert result["status"] == "success"
    assert ("ilike", "phone_number", "%6%0%1%2%3%4%5%6%7%0%1%") in query.calls
    assert ("limit", 10) in query.calls


def test_unregistered_phone_is_normal_new_customer_state():
    from customer_context import CustomerContext
    from relational_actions import check_customer_by_phone

    query = _CustomerQuery([])
    with patch("relational_actions.get_supabase_client", return_value=_Client(query)):
        result = check_customer_by_phone(
            CustomerContext(phone_number="+601999991234", company_id=1)
        )

    assert result["status"] == "not_found"
    assert result["success"] is True
    assert result["handoff_required"] is False
    assert result["handoff_reason"] is None


def test_confirmed_new_customer_booking_provisions_customer_and_pet():
    from customer_context import CustomerContext
    from database_service import _provision_new_booking_customer

    context = CustomerContext(phone_number="+601999991234", company_id=1)
    session = SimpleNamespace(
        customer_name="Jamie Tan",
        customer_id=None,
        existing_customer=False,
        pet_name="Coco",
        pet_type="DOG",
        pet_size="M",
        pet_height="42cm",
        pet_id=None,
        draft_booking_payload={"pet_name": "Coco", "service_type": "GROOMING"},
    )
    payload = {
        "entities": {"pet_name": "Coco", "pet_type": "DOG", "pet_size": "M"},
        "_draft_booking": dict(session.draft_booking_payload),
    }
    with (
        patch(
            "relational_actions.check_customer_by_phone",
            return_value={"status": "not_found", "data": {}},
        ),
        patch(
            "relational_actions.create_customer",
            return_value={"status": "success", "data": {"customer_id": 91}},
        ) as create_customer,
        patch(
            "relational_actions.get_pet_by_customer_and_name",
            return_value={"status": "not_found", "data": {}},
        ),
        patch(
            "relational_actions.create_pet",
            return_value={
                "status": "success",
                "data": {"pet": {"pet_id": 92, "pet_name": "Coco"}},
            },
        ) as create_pet,
    ):
        error = _provision_new_booking_customer(context, payload, session)

    assert error is None
    create_customer.assert_called_once()
    create_pet.assert_called_once()
    assert context.resolved_customer_id == 91
    assert session.customer_id == 91
    assert session.pet_id == 92
    assert payload["entities"]["pet_id"] == "92"
    assert payload["_draft_booking"]["pet_id"] == 92


def test_high_confidence_intent_skips_remote_llm(monkeypatch):
    import llm_service

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("SINGLE_LLM_PER_TURN", "true")
    monkeypatch.setenv("TESTING", "false")
    monkeypatch.delenv("_EVAL_OVERRIDE_ACTIVE", raising=False)

    with patch(
        "real_llm.real_llm_intent_detection",
        side_effect=AssertionError("remote intent LLM must not be called"),
    ):
        result = llm_service.detect_intent("I want to make a grooming booking")

    assert result["provider_used"] == "deterministic"
    assert result["intent_json"]["scenario_intent"] == "MAKE_BOOKING"


def test_ambiguous_intent_still_uses_semantic_llm(monkeypatch):
    import llm_service

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("SINGLE_LLM_PER_TURN", "true")
    monkeypatch.setenv("TESTING", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.delenv("_EVAL_OVERRIDE_ACTIVE", raising=False)

    semantic_result = {
        "intent_json": {
            "main_intent": "UNKNOWN",
            "scenario_intent": "UNKNOWN",
            "confidence": 0.8,
        },
        "model_used": "test-model",
        "provider_used": "openai",
        "base_url_used": "",
    }
    with patch("real_llm.real_llm_intent_detection", return_value=semantic_result):
        result = llm_service.detect_intent("could you sort out that thing from before?")

    assert result["provider_used"] == "openai"
