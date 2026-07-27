"""Guard the behavior rules that must reach the live OpenAI calls."""

from prompts import FINAL_RESPONSE_PROMPT
from query_json_prompt import QUERY_JSON_PROMPT


def test_final_prompt_contains_natural_booking_state_rules():
    required = (
        "Never ask for them again",
        "partial option name",
        "Do not greet again",
        "Prefer a recommendation",
        "Never ask \"What time do you prefer?\"",
        "Never show dog trimming packages",
        "Membership after booking",
    )

    for phrase in required:
        assert phrase in FINAL_RESPONSE_PROMPT


def test_intent_prompt_forbids_invented_date_and_maps_relational_reads():
    required = (
        "entities.preferred_date must be empty",
        "entities.preferred_time must be empty",
        "VIEW_PAYMENT_HISTORY / get_payment_history",
        "VIEW_REDEMPTION_HISTORY / get_redemption_history",
        "VIEW_MESSAGE_HISTORY / get_message_history",
        "VIEW_COMPANY_INFORMATION / get_company_information",
    )

    for phrase in required:
        assert phrase in QUERY_JSON_PROMPT
