from __future__ import annotations

import pytest

from intent_schema import normalize_intent_result
from router import route_intent


@pytest.mark.parametrize(
    ("scenario", "expected_route", "expected_action", "expected_source"),
    [
        ("CUSTOMER_GREETING", "CALL_DATABASE", "check_customer_by_phone", None),
        ("COLLECT_CUSTOMER_NAME", "ASK_MISSING_INFO", "", None),
        ("VIEW_BOOKING_STATUS", "CALL_DATABASE", "check_booking_status", None),
        ("CHECK_AVAILABILITY", "CALL_DATABASE", "check_availability", None),
        ("REPEAT_LAST_BOOKING", "CALL_DATABASE", "check_last_booking", None),
        ("CONFIRM_BOOKING", "CALL_DATABASE", "create_booking", None),
        ("CANCEL_BOOKING", "CALL_DATABASE", "cancel_booking", None),
        ("RESCHEDULE_BOOKING", "CALL_DATABASE", "reschedule_booking", None),
        ("CREATE_CUSTOMER", "CALL_DATABASE", "create_customer", None),
        ("CREATE_PET", "CALL_DATABASE", "create_pet", None),
        ("CHECK_LOYALTY_POINTS", "CALL_DATABASE", "check_loyalty_points", None),
        ("CHECK_MEMBERSHIP_STATUS", "CALL_DATABASE", "check_membership_status", None),
        ("LOYALTY_ACCOUNT_INQUIRY", "CALL_DATABASE", "get_loyalty_account", None),
        ("CHECK_COUPON_ELIGIBILITY", "CALL_DATABASE", "check_coupon_eligibility", None),
        ("REDEEM_REWARD", "CALL_DATABASE", "redeem_reward", None),
        ("VIEW_PAYMENT_HISTORY", "CALL_DATABASE", "get_payment_history", None),
        ("VIEW_REDEMPTION_HISTORY", "CALL_DATABASE", "get_redemption_history", None),
        ("VIEW_MESSAGE_HISTORY", "CALL_DATABASE", "get_message_history", None),
        ("VIEW_COMPANY_INFORMATION", "CALL_DATABASE", "get_company_information", None),
        ("VIEW_STAFF_DIRECTORY", "CALL_DATABASE", "get_staff_directory", None),
        ("VIEW_ACCOUNT_STATUS", "CALL_DATABASE", "get_customer_profile", None),
        ("CANCELLATION_POLICY", "CALL_KNOWLEDGE_RAG", "retrieve_policy", "cancellation_policy"),
        ("GROOMING_POLICY", "CALL_KNOWLEDGE_RAG", "retrieve_policy", "grooming_policy"),
        ("BOARDING_POLICY", "CALL_KNOWLEDGE_RAG", "retrieve_policy", "boarding_policy"),
        ("DAYCARE_POLICY", "CALL_KNOWLEDGE_RAG", "retrieve_policy", "daycare_policy"),
        ("VET_REQUIREMENT", "CALL_KNOWLEDGE_RAG", "retrieve_policy", "vet_requirement_policy"),
        ("LOYALTY_POLICY", "CALL_KNOWLEDGE_RAG", "retrieve_policy", "loyalty_policy"),
        ("GENERAL_POLICY", "CALL_KNOWLEDGE_RAG", "retrieve_policy", "general_policy"),
        ("SERVICE_INFORMATION", "CALL_KNOWLEDGE_RAG", "retrieve_service_info", "service_information"),
    ],
)
def test_normalized_intent_contract(
    scenario: str,
    expected_route: str,
    expected_action: str,
    expected_source: str | None,
):
    normalized = normalize_intent_result(
        {
            "main_intent": "BOOKING_INTENT",
            "scenario_intent": scenario,
            "confidence": 0.95,
            "entities": {},
            "missing_information": [],
        }
    )

    assert route_intent(normalized)["route"] == expected_route
    assert normalized["database_action"] == expected_action
    assert normalized["next_action"] == expected_action or scenario == "COLLECT_CUSTOMER_NAME"
    if expected_source:
        assert normalized["retrieval_source"] == [expected_source]
    else:
        assert normalized["retrieval_source"] == []


def test_make_booking_with_missing_fields_collects_before_database():
    normalized = normalize_intent_result(
        {
            "main_intent": "BOOKING_INTENT",
            "scenario_intent": "MAKE_BOOKING",
            "confidence": 0.95,
            "missing_information": ["service_type", "preferred_date"],
            "entities": {},
        }
    )
    assert normalized["scenario_intent"] == "MAKE_BOOKING"
    assert normalized["database_action_needed"] is False
    assert route_intent(normalized)["route"] == "ASK_MISSING_INFO"


def test_make_booking_complete_moves_to_read_only_availability_check():
    normalized = normalize_intent_result(
        {
            "main_intent": "BOOKING_INTENT",
            "scenario_intent": "MAKE_BOOKING",
            "confidence": 0.95,
            "missing_information": [],
            "entities": {},
        }
    )
    assert normalized["scenario_intent"] == "CHECK_AVAILABILITY"
    assert normalized["database_action"] == "check_availability"
    assert route_intent(normalized)["route"] == "CALL_DATABASE"


@pytest.mark.parametrize(
    "scenario",
    ["CANCEL_BOOKING", "RESCHEDULE_BOOKING", "REDEEM_REWARD", "CREATE_CUSTOMER", "CREATE_PET"],
)
def test_write_intents_never_execute_with_missing_fields(scenario: str):
    normalized = normalize_intent_result(
        {
            "main_intent": "BOOKING_INTENT",
            "scenario_intent": scenario,
            "confidence": 0.95,
            "missing_information": ["required_field"],
            "entities": {},
        }
    )
    assert route_intent(normalized)["route"] == "ASK_MISSING_INFO"


def test_unknown_is_the_only_generic_handoff_contract():
    normalized = normalize_intent_result(
        {
            "main_intent": "UNKNOWN",
            "scenario_intent": "UNKNOWN",
            "confidence": 0.4,
            "entities": {},
        }
    )
    assert route_intent(normalized)["route"] == "HUMAN_HANDOFF"


def test_new_customer_phone_not_found_is_not_handoff():
    from response_generator import resolve_turn_handoff

    required, reason = resolve_turn_handoff(
        user_message="hi",
        intent_json=normalize_intent_result(
            {
                "main_intent": "GREETING_INTENT",
                "scenario_intent": "CUSTOMER_GREETING",
                "confidence": 0.95,
            }
        ),
        route_result={"route": "CALL_DATABASE"},
        database_result={
            "action": "check_customer_by_phone",
            "status": "not_found",
            "success": True,
            "data_found": False,
            "data": {},
            "handoff_required": False,
            "handoff_reason": None,
        },
        rag_result={},
    )
    assert required is False
    assert reason == ""


def test_missing_service_options_is_not_mislabeled_as_loyalty_failure():
    from response_generator import database_handoff_reason

    reason = database_handoff_reason(
        {
            "action": "get_booking_service_options",
            "status": "not_found",
            "data_found": False,
            "data": {},
        },
        "GET_BOOKING_SERVICE_OPTIONS",
    )
    assert reason == ""


@pytest.mark.parametrize(
    ("scenario", "function_name"),
    [
        ("CONFIRM_BOOKING", "create_booking"),
        ("CANCEL_BOOKING", "cancel_booking"),
        ("RESCHEDULE_BOOKING", "reschedule_booking"),
        ("REDEEM_REWARD", "redeem_reward"),
        ("MAKE_BOOKING", "check_available_slots"),
        ("VIEW_BOOKING_STATUS", "check_booking_status"),
        ("CHECK_AVAILABILITY", "check_available_slots"),
        ("CHECK_MEMBERSHIP_STATUS", "check_membership_status"),
        ("CHECK_LOYALTY_POINTS", "check_loyalty_points"),
        ("LOYALTY_ACCOUNT_INQUIRY", "get_loyalty_account"),
        ("CHECK_COUPON_ELIGIBILITY", "check_coupon_eligibility"),
        ("VIEW_PAYMENT_HISTORY", "get_payment_history"),
        ("VIEW_REDEMPTION_HISTORY", "get_redemption_history"),
        ("VIEW_MESSAGE_HISTORY", "get_message_history"),
        ("VIEW_COMPANY_INFORMATION", "get_company_information"),
        ("VIEW_STAFF_DIRECTORY", "get_staff_directory"),
        ("VIEW_ACCOUNT_STATUS", "check_customer_by_phone"),
        ("CUSTOMER_GREETING", "check_customer_by_phone"),
        ("REPEAT_LAST_BOOKING", "check_last_booking"),
    ],
)
def test_relational_dispatch_contract(monkeypatch, scenario: str, function_name: str):
    import relational_actions
    from customer_context import CustomerContext

    monkeypatch.setattr(
        relational_actions,
        function_name,
        lambda *_args, **_kwargs: {
            "action": function_name,
            "status": "success",
            "data": {},
        },
    )
    result = relational_actions.relational_database_action(
        {"scenario_intent": scenario, "entities": {}},
        CustomerContext(phone_number="+60111111111", company_id=1),
    )
    assert result["action"] == function_name


def test_internal_service_option_dispatch_contract(monkeypatch):
    import relational_actions
    from customer_context import CustomerContext

    monkeypatch.setattr(
        relational_actions,
        "get_booking_service_options",
        lambda *_args, **_kwargs: {
            "action": "get_booking_service_options",
            "status": "success",
            "data": {},
        },
    )
    result = relational_actions.relational_database_action(
        {
            "scenario_intent": "GET_BOOKING_SERVICE_OPTIONS",
            "service_type": "GROOMING",
            "entities": {"pet_type": "DOG"},
        },
        CustomerContext(phone_number="+60111111111", company_id=1),
    )
    assert result["action"] == "get_booking_service_options"
