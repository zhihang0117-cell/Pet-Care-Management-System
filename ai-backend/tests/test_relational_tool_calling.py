from __future__ import annotations

from unittest.mock import patch

from relational_tool_calling import execute_relational_tool_calls


def test_tool_executor_uses_scoped_database_dispatcher_and_keeps_arguments():
    calls = [
        {
            "id": "call_123",
            "name": "get_booking_status",
            "arguments": {
                "booking_id": 44,
                "service_type": "GROOMING",
                "pet_name": "Luna",
            },
        }
    ]
    db_result = {
        "action": "check_booking_status",
        "status": "success",
        "data_found": True,
        "data": {"booking_status": "confirmed"},
        "error": None,
    }

    with patch("database_service.execute_database_action", return_value=db_result) as execute:
        result = execute_relational_tool_calls(
            calls,
            intent_json={"scenario_intent": "VIEW_BOOKING_STATUS", "entities": {}},
            customer_id="7",
            phone_number="+60123456789",
            session=None,
            user_message="Check Luna's booking",
        )

    payload = execute.call_args.args[0]
    assert payload["scenario_intent"] == "VIEW_BOOKING_STATUS"
    assert payload["entities"]["booking_id"] == 44
    assert payload["entities"]["pet_name"] == "Luna"
    assert execute.call_args.kwargs["customer_id"] == "7"
    assert execute.call_args.kwargs["phone_number"] == "+60123456789"
    assert result["tool_calling"]["used"] is True
    assert result["tool_calling"]["tool_call_id"] == "call_123"


def test_unknown_or_write_tool_is_never_executed():
    with patch("database_service.execute_database_action") as execute:
        result = execute_relational_tool_calls(
            [{"id": "bad", "name": "execute_sql", "arguments": {"sql": "DELETE"}}],
            intent_json={},
            customer_id="7",
            phone_number="+60123456789",
            session=None,
            user_message="delete everything",
        )

    assert result == {}
    execute.assert_not_called()
