from __future__ import annotations

from unittest.mock import patch

from availability_service import build_availability_reply, build_availability_result
from booking_flow import build_missing_field_reply, compute_booking_missing_fields
from customer_identity import resolve_customer_at_request_start
from session_store import SessionContext


def test_customer_identity_preloads_pets_and_latest_booking_once():
    session = SessionContext(phone_number="+60123456789")
    identity = {
        "action": "check_customer_by_phone",
        "status": "success",
        "data": {"customer_id": 7, "full_name": "Alicia"},
        "error": None,
    }
    latest = {
        "action": "get_latest_booking_by_customer_id",
        "status": "success",
        "data": {"service_type": "GROOMING", "pet_name": "Milo"},
        "error": None,
    }

    with (
        patch("customer_identity.lookup_customer_by_phone", return_value=identity),
        patch(
            "pet_profile.enrich_session_pet_profile",
            return_value={
                "status": "matched",
                "matched_pet": {
                    "pet_id": 3,
                    "pet_name": "Milo",
                    "pet_type": "Cat",
                },
                "pet_names": ["Milo"],
            },
        ) as enrich_pets,
        patch("database_service.fetch_latest_booking_for_entry", return_value=latest) as fetch_latest,
    ):
        first = resolve_customer_at_request_start(session, session.phone_number)
        second = resolve_customer_at_request_start(session, session.phone_number)

    assert enrich_pets.call_count == 2
    fetch_latest.assert_called_once()
    assert session.customer_context_loaded is True
    assert session.last_booking_snapshot["pet_name"] == "Milo"
    assert first["data"]["last_booking"]["service_type"] == "GROOMING"
    assert second["data"]["last_booking"]["pet_name"] == "Milo"
    assert first["data"]["selected_pet_profile"]["pet_name"] == "Milo"
    assert first["data"]["pet_selection_status"] == "matched"
    assert first["data"]["profile_bundle"]["automatic_actions"] == [
        "get_customer_profile",
        "get_pet_profiles",
        "get_latest_booking",
    ]


def test_booking_date_without_time_queries_availability_instead_of_asking_time():
    session = SessionContext(
        phone_number="+60123456789",
        customer_id=7,
        existing_customer=True,
        pet_name="Milo",
        pet_id=3,
        pet_type="CAT",
        pet_size="M",
        customer_pets=[
            {
                "pet_id": 3,
                "pet_name": "Milo",
                "pet_type": "Cat",
                "size": "M",
            }
        ],
    )
    intent = {
        "scenario_intent": "MAKE_BOOKING",
        "service_type": "DAYCARE",
        "entities": {
            "service_type": "DAYCARE",
            "pet_id": "3",
            "pet_name": "Milo",
            "pet_type": "CAT",
            "pet_size": "M",
            "preferred_date": "2026-08-03",
        },
    }

    with patch("booking_flow.resolve_pet_id", return_value=3):
        missing = compute_booking_missing_fields(session, intent, "Book daycare on 3 August")

    assert "preferred_time" not in missing


def test_date_only_availability_reply_offers_real_slots():
    result = build_availability_result(
        requested_date="2026-08-03",
        requested_time="",
        available_slots=["09:00:00", "11:00:00", "14:00:00"],
        service_type="DAYCARE",
    )
    reply = build_availability_reply(
        SessionContext(pet_name="Milo", last_service_type="DAYCARE"),
        result,
        {
            "service_type": "DAYCARE",
            "entities": {"pet_name": "Milo"},
        },
    )

    assert "9:00 AM" in reply
    assert "11:00 AM" in reply
    assert "2:00 PM" in reply
    assert "I recommend 9:00 AM" in reply
    assert "prepare the booking" in reply


def test_multiple_missing_booking_fields_are_asked_together():
    session = SessionContext(existing_customer=True)
    reply = build_missing_field_reply(
        ["pet_name", "preferred_date", "preferred_time"],
        session,
        {"scenario_intent": "MAKE_BOOKING"},
    )

    assert "which pet this is for" in reply
    assert "preferred date" in reply
    assert "one message" in reply
    assert "For example" in reply


def test_repeat_booking_recommends_fast_path_to_booking():
    from booking_flow import build_repeat_or_new_reply

    session = SessionContext(existing_customer=True, customer_name="Alicia")
    session.last_booking_snapshot = {
        "service_type": "GROOMING",
        "service_name": "Full Grooming",
        "pet_name": "Milo",
    }

    reply = build_repeat_or_new_reply(session, {})

    assert "recommend repeating" in reply
    assert "preferred date" in reply
    assert "available times" in reply


def test_exact_available_slot_guides_customer_to_confirmation():
    result = build_availability_result(
        requested_date="2026-08-03",
        requested_time="11:00:00",
        available_slots=["11:00:00"],
        service_type="DAYCARE",
    )

    reply = build_availability_reply(SessionContext(), result, {"service_type": "DAYCARE"})

    assert "recommend securing" in reply
    assert "Reply yes" in reply
    assert "booking confirmation" in reply


def test_new_customer_confirmed_booking_ends_with_membership_invitation():
    from booking_draft import build_booking_confirmed_reply

    session = SessionContext(new_customer_session=True)
    session.draft_booking_payload = {
        "pet_name": "Milo",
        "service_type": "GROOMING",
    }
    result = {
        "status": "success",
        "data_found": True,
        "data": {
            "verified": True,
            "booking_id": 88,
            "booking_status": "confirmed",
            "booking_date": "2026-08-03",
            "booking_time": "11:00:00",
        },
    }

    reply = build_booking_confirmed_reply(result, session)

    assert "booking is confirmed" in reply
    assert "join Pawfect Membership" in reply
    assert "loyalty points" in reply


def test_existing_customer_confirmed_booking_does_not_repeat_membership_invitation():
    from booking_draft import build_booking_confirmed_reply

    session = SessionContext(existing_customer=True, new_customer_session=False)
    result = {
        "status": "success",
        "data_found": True,
        "data": {
            "verified": True,
            "booking_id": 89,
            "booking_status": "confirmed",
        },
    }

    reply = build_booking_confirmed_reply(result, session)

    assert "booking is confirmed" in reply
    assert "join Pawfect Membership" not in reply


def test_greeting_template_marks_session_and_does_not_greet_again():
    from response_generator import _build_greeting_reply

    session = SessionContext(existing_customer=True, customer_name="Alicia")
    result = {
        "action": "check_customer_by_phone",
        "status": "success",
        "data": {"full_name": "Alicia"},
    }

    first = _build_greeting_reply(result, session)
    second = _build_greeting_reply(result, session)

    assert "Hi Alicia" in first
    assert "welcome back" in first
    assert session.greeted_this_session is True
    assert "Hi" not in second
    assert "welcome" not in second.lower()


def test_new_customer_name_follow_up_does_not_repeat_welcome():
    from response_generator import _build_greeting_reply

    session = SessionContext()
    result = {"action": "check_customer_by_phone", "status": "not_found", "data": {}}

    first = _build_greeting_reply(result, session)
    second = _build_greeting_reply(result, session)

    assert "welcome to Pawfect" in first
    assert "May I have your name" in second
    assert "welcome" not in second.lower()


def test_pending_customer_name_overrides_unknown_llm_intent():
    from booking_flow import handle_collect_customer_name
    from session_store import COLLECT_CUSTOMER_NAME

    session = SessionContext(
        phone_number="01262736272",
        existing_customer=False,
        new_customer_session=True,
        greeted_this_session=True,
        pending_action=COLLECT_CUSTOMER_NAME,
        missing_fields=["full_name"],
    )
    result = handle_collect_customer_name(
        session,
        "hung wei",
        {
            "main_intent": "UNKNOWN",
            "scenario_intent": "UNKNOWN",
            "confidence": 0.0,
            "entities": {},
        },
    )

    assert session.customer_name == "hung wei"
    assert result["main_intent"] == "GREETING_INTENT"
    assert result["scenario_intent"] == "COLLECT_CUSTOMER_NAME"
    assert result["database_action_needed"] is False
    assert result["retrieval_needed"] is False
    assert result["confidence"] == 0.99


def test_llm_reply_cannot_add_greeting_after_session_was_already_greeted():
    from response_generator import finalize_customer_reply

    reply = finalize_customer_reply(
        "Hi Jason! 😊\n\nYou have 544 loyalty points.\n\nLet's continue your booking.",
        {
            "scenario_intent": "MAKE_BOOKING",
            "_session_greeted_before_turn": True,
        },
    )

    assert reply.startswith("You have 544 loyalty points.")
    assert "Hi Jason" not in reply


def test_first_non_greeting_message_gets_one_conversation_greeting():
    from response_generator import finalize_customer_reply

    session = SessionContext(existing_customer=True, customer_name="Alicia Lee")
    first = finalize_customer_reply(
        "You currently have 407 loyalty points 😊",
        {
            "scenario_intent": "CHECK_LOYALTY_POINTS",
            "_session_greeted_before_turn": False,
        },
        session=session,
    )
    second = finalize_customer_reply(
        "Your next booking is tomorrow at 2:00 PM.",
        {
            "scenario_intent": "VIEW_BOOKING_STATUS",
            "_session_greeted_before_turn": True,
        },
        session=session,
    )

    assert first.startswith("Hi Alicia Lee, welcome back to Pawfect! 😊")
    assert "407 loyalty points" in first
    assert session.greeted_this_session is True
    assert not second.startswith(("Hi", "Hello", "Hey"))


def test_first_turn_does_not_duplicate_a_flow_specific_greeting():
    from response_generator import finalize_customer_reply

    session = SessionContext(existing_customer=True, customer_name="Alicia Lee")
    reply = finalize_customer_reply(
        "Hi Alicia Lee, welcome back to Pawfect! 😊\n\nWhich service would you like to book?",
        {
            "scenario_intent": "MAKE_BOOKING",
            "_session_greeted_before_turn": False,
        },
        session=session,
    )

    assert reply.count("Hi Alicia Lee") == 1
    assert session.greeted_this_session is True


def test_coupon_question_with_booking_routes_to_coupon_database_read_first():
    from intent_schema import apply_message_pattern_overrides
    from router import route_intent

    message = "I want to make booking for my pet, but I am not sure my points can redeem any coupon?"
    intent = apply_message_pattern_overrides(
        message,
        {
            "main_intent": "BOOKING_INTENT",
            "scenario_intent": "MAKE_BOOKING",
            "confidence": 0.9,
            "entities": {},
        },
    )

    assert intent["scenario_intent"] == "CHECK_COUPON_ELIGIBILITY"
    assert intent["database_action"] == "check_coupon_eligibility"
    assert intent["coupon_eligibility_with_booking"] is True
    assert route_intent(intent)["route"] == "CALL_DATABASE"


def test_availability_never_invents_date_when_customer_did_not_supply_one():
    from customer_context import CustomerContext
    from relational_actions import check_available_slots

    result = check_available_slots(
        CustomerContext(company_id=1),
        {"service_type": "GROOMING", "entities": {}},
    )

    assert result["status"] == "missing_information"
    assert result["data"]["missing_fields"] == ["preferred_date"]


def test_coupon_reply_lists_verified_choices_then_collects_booking_date():
    from response_generator import _build_coupon_eligibility_reply

    session = SessionContext(
        existing_customer=True,
        pet_name="Coco",
        pet_type="DOG",
        pet_size="M",
    )
    reply = _build_coupon_eligibility_reply(
        {
            "status": "success",
            "data": {
                "points_balance": 544,
                "eligible_coupons": [
                    {
                        "reward_name": "RM10 Grooming Voucher",
                        "points_required": 500,
                        "discount_value": 10,
                    }
                ],
            },
        },
        {
            "coupon_eligibility_with_booking": True,
            "deferred_booking_missing": ["preferred_date"],
        },
        session,
    )

    assert "544 loyalty points" in reply
    assert "RM10 Grooming Voucher: 500 points" in reply
    assert "preferred date" in reply
    assert "09:00" not in reply
    assert "10th October" not in reply


def test_coupon_booking_question_drops_stale_schedule_and_never_routes_to_availability():
    from booking_flow import apply_booking_collection_rules, apply_booking_entry_rules

    message = "I want to make booking for my pet, but I am not sure my points can redeem any coupon?"
    session = SessionContext(
        existing_customer=True,
        booking_creation_flow=True,
        preferred_date="2026-10-10",
        preferred_time="09:00:00",
        selected_slot="09:00:00",
        last_service_type="GROOMING",
        collected_entities={
            "preferred_date": "2026-10-10",
            "preferred_time": "09:00:00",
            "service_type": "GROOMING",
        },
    )
    intent = {
        "main_intent": "LOYALTY_INTENT",
        "scenario_intent": "CHECK_COUPON_ELIGIBILITY",
        "coupon_eligibility_with_booking": True,
        "database_action_needed": True,
        "database_action": "check_coupon_eligibility",
        "next_action": "check_coupon_eligibility",
        "entities": {
            "preferred_date": "2026-10-10",
            "preferred_time": "09:00:00",
        },
    }

    after_entry = apply_booking_entry_rules(session, intent, message)
    final = apply_booking_collection_rules(session, after_entry, message)

    assert session.preferred_date == ""
    assert session.preferred_time == ""
    assert "preferred_date" in final["deferred_booking_missing"]
    assert "service_type" in final["deferred_booking_missing"]
    assert final["scenario_intent"] == "CHECK_COUPON_ELIGIBILITY"
    assert final["database_action"] == "check_coupon_eligibility"


def test_generic_my_pet_phrase_never_overwrites_loaded_single_pet_profile():
    from booking_flow import compute_booking_missing_fields

    session = SessionContext(
        phone_number="+60123456702",
        customer_id=2,
        existing_customer=True,
        booking_creation_flow=True,
        customer_pets=[
            {
                "pet_id": 2,
                "pet_name": "Coco",
                "pet_type": "Dog",
                "size": "M",
                "height_cm": 41,
            }
        ],
    )
    intent = {
        "scenario_intent": "MAKE_BOOKING",
        "service_type": "GROOMING",
        "entities": {"service_type": "GROOMING"},
    }

    missing = compute_booking_missing_fields(
        session,
        intent,
        "I want to book grooming for my pet",
    )

    assert session.pet_name == "Coco"
    assert session.pet_id == 2
    assert session.pet_type == "DOG"
    assert session.pet_size == "medium"
    assert session.pet_height == "41cm"
    assert "pet_name" not in missing
    assert "pet_type" not in missing
    assert "pet_size_or_height" not in missing


def test_service_choice_reply_uses_loaded_pet_without_asking_pet_name_again():
    session = SessionContext(
        existing_customer=True,
        pet_name="Milo",
        pet_id=1,
        pet_type="CAT",
        pet_size="medium",
    )

    reply = build_missing_field_reply(
        ["service_type", "preferred_date", "preferred_time"],
        session,
        {"scenario_intent": "MAKE_BOOKING"},
    )

    assert "for Milo" in reply
    assert "service you need and your preferred date" in reply
    assert "pet's name" not in reply


def test_daycare_requires_specific_service_before_availability():
    from booking_flow import apply_booking_collection_rules

    session = SessionContext(
        existing_customer=True,
        customer_id=7,
        pet_name="Milo",
        pet_id=3,
        pet_type="CAT",
        pet_size="medium",
        booking_creation_flow=True,
    )
    intent = {
        "main_intent": "BOOKING_INTENT",
        "scenario_intent": "MAKE_BOOKING",
        "service_type": "DAYCARE",
        "entities": {
            "service_type": "DAYCARE",
            "pet_name": "Milo",
            "pet_id": "3",
            "pet_type": "CAT",
            "pet_size": "medium",
            "preferred_date": "2026-08-03",
        },
    }

    with patch("booking_flow.resolve_pet_id", return_value=3):
        updated = apply_booking_collection_rules(session, intent, "Book daycare on 3 August")

    assert updated["scenario_intent"] == "GET_BOOKING_SERVICE_OPTIONS"
    assert updated["database_action"] == "get_booking_service_options"
    assert "service_package" in updated["deferred_booking_missing"]
    from router import route_intent

    assert route_intent(updated)["route"] == "CALL_RAG_AND_DATABASE"


def test_booking_service_options_reply_uses_verified_catalogue():
    from response_generator import _build_booking_service_options_reply

    reply = _build_booking_service_options_reply(
        {
            "status": "success",
            "data": {
                "service_type": "DAYCARE",
                "service_options": [
                    {
                        "service_id": 41,
                        "service_name": "Daycare Hourly Care",
                        "price_display": "RM20/hour",
                    },
                    {
                        "service_id": 42,
                        "service_name": "Daycare Above 3 Hours",
                        "price_display": "RM55/day",
                    },
                ],
            },
        },
        SessionContext(pet_name="Milo"),
    )

    assert "for Milo" in reply
    assert "Daycare Hourly Care — RM20/hour" in reply
    assert "Daycare Above 3 Hours — RM55/day" in reply
    assert "preferred date in the same message" in reply


def test_numbered_service_option_selection_is_saved():
    from session_continuation import extract_fields_from_message

    session = SessionContext(
        service_options=[
            {"service_id": 41, "service_name": "Daycare Hourly Care"},
            {"service_id": 42, "service_name": "Daycare Above 3 Hours"},
        ]
    )

    extracted = extract_fields_from_message("Option 2", ["service_package"], session)

    assert extracted["service_package"] == "Daycare Above 3 Hours"
    assert session.service_package == "Daycare Above 3 Hours"


def test_boarding_requires_room_type_before_availability():
    from booking_flow import apply_booking_collection_rules

    session = SessionContext(
        existing_customer=True,
        customer_id=7,
        pet_name="Milo",
        pet_id=3,
        pet_type="CAT",
        pet_size="medium",
        booking_creation_flow=True,
    )
    intent = {
        "main_intent": "BOOKING_INTENT",
        "scenario_intent": "MAKE_BOOKING",
        "service_type": "BOARDING",
        "entities": {
            "service_type": "BOARDING",
            "pet_name": "Milo",
            "pet_id": "3",
            "preferred_date": "2026-08-03",
        },
    }

    with patch("booking_flow.resolve_pet_id", return_value=3):
        updated = apply_booking_collection_rules(session, intent, "Book boarding on 3 August")

    assert updated["scenario_intent"] == "GET_BOOKING_SERVICE_OPTIONS"
    assert "service_package" in updated["deferred_booking_missing"]


def test_boarding_room_options_show_price_and_capacity():
    from response_generator import _build_booking_service_options_reply

    reply = _build_booking_service_options_reply(
        {
            "status": "success",
            "data": {
                "service_type": "BOARDING",
                "service_options": [
                    {
                        "service_id": "ROOM007",
                        "service_name": "Jupiter Suite",
                        "price_display": "RM158/night",
                        "capacity": 2,
                    }
                ],
            },
        },
        SessionContext(pet_name="Milo"),
    )

    assert "room types" in reply
    assert "Jupiter Suite — RM158/night · up to 2 pets" in reply


def test_booking_option_rag_queries_target_service_information_chunks():
    from booking_service_info import build_service_info_retrieval_query

    daycare = build_service_info_retrieval_query(
        "I want daycare",
        {
            "scenario_intent": "GET_BOOKING_SERVICE_OPTIONS",
            "service_type": "DAYCARE",
            "entities": {"service_type": "DAYCARE"},
        },
    )
    boarding = build_service_info_retrieval_query(
        "I want boarding",
        {
            "scenario_intent": "GET_BOOKING_SERVICE_OPTIONS",
            "service_type": "BOARDING",
            "entities": {"service_type": "BOARDING", "pet_type": "CAT"},
        },
    )

    assert "Hourly Care" in daycare
    assert "Splash Pool Session" in daycare
    assert "Cat Hotel Price" in boarding
    assert "room types capacity price" in boarding


def test_grooming_policy_price_rows_use_document_height_bands():
    from booking_service_info import extract_matching_grooming_price_rows

    chunks = [
        {
            "text": (
                "Dog Bathing Packages:\n\n"
                "For XS size dogs (Below 25cm) - Standard Short Fur is RM35.\n\n"
                "For S size dogs (25cm - 40cm) - Standard Short Fur is RM43.\n\n"
                "For M size dogs (40cm - 55cm) - Standard Short Fur is RM62."
            ),
            "metadata": {"section_title": "Dog Bathing Packages"},
        }
    ]

    rows = extract_matching_grooming_price_rows(chunks, pet_height="37cm")

    assert len(rows) == 1
    assert "For S size dogs" in rows[0][1]
    assert "RM43" in rows[0][1]


def test_grooming_policy_price_rows_translate_normalized_session_size():
    from booking_service_info import extract_matching_grooming_price_rows

    chunks = [
        {
            "text": (
                "Cat Trimming Packages:\n\n"
                "For S size cats (Below 20cm) - Half Shave Short Fur is RM125.\n\n"
                "For M size cats (20cm - 40cm) - Half Shave Short Fur is RM151."
            ),
            "metadata": {"section_title": "Cat Trimming Packages"},
        }
    ]

    rows = extract_matching_grooming_price_rows(chunks, pet_size="medium")

    assert len(rows) == 1
    assert "For M size cats" in rows[0][1]
    assert "RM151" in rows[0][1]


def test_service_option_follow_up_accepts_natural_partial_name():
    from session_continuation import extract_fields_from_message

    session = SessionContext(
        pet_type="CAT",
        service_options=[
            {"service_name": "Premium Bath (HYPONIC)"},
            {"service_name": "Dog Trimming Packages"},
        ],
    )

    extracted = extract_fields_from_message(
        "Premium Bath sounds good.",
        ["service_package"],
        session,
    )

    assert extracted["service_package"] == "Premium Bath (HYPONIC)"


def test_cached_grooming_options_are_filtered_after_pet_species_is_known():
    session = SessionContext(
        pet_type="CAT",
        last_service_type="GROOMING",
        service_options_for="GROOMING",
        service_options=[
            {"service_name": "Premium Bath (HYPONIC)", "price_display": "RM120"},
            {"service_name": "Cat Trimming Packages", "price_display": "RM125 - RM363"},
            {"service_name": "Dog Trimming Packages", "price_display": "RM88 - RM352"},
        ],
    )

    reply = build_missing_field_reply(
        ["service_package"],
        session,
        {"service_type": "GROOMING"},
    )

    assert "Cat Trimming Packages" in reply
    assert "Dog Trimming Packages" not in reply


def test_customer_friendly_date_normalizes_for_availability():
    from datetime import date

    from date_normalization import parse_customer_date

    assert parse_customer_date("6 August", today=date(2026, 7, 27)) == date(2026, 8, 6)
    assert parse_customer_date("6th August 2026", today=date(2026, 7, 27)) == date(
        2026, 8, 6
    )


def test_service_option_reply_never_asks_for_time_before_availability():
    from response_generator import _enforce_service_option_conversation_contract

    reply = _enforce_service_option_conversation_contract(
        "Hi, welcome to Pawfect! 😊\n\n"
        "I can help with that! 😊\n\n"
        "Please send your pet name and your preferred date and time."
    )

    assert reply.count("I can help with that") == 0
    assert "date and time" not in reply.lower()
    assert "preferred date" in reply.lower()


def test_new_customer_service_options_collect_name_without_duplicate_intro():
    from response_generator import _enforce_service_option_conversation_contract

    session = SessionContext(existing_customer=False, customer_name="")
    reply = _enforce_service_option_conversation_contract(
        "Hi, welcome to Pawfect! 😊\n\n"
        "I can help with that! 😊\n\n"
        "Please provide these details:\n\n"
        "• Pet name\n"
        "• Pet type\n"
        "• your preferred date",
        session,
    )

    assert "I can help with that" not in reply
    assert "• Your name" in reply
    assert "• Preferred date" in reply


def test_new_customer_natural_question_still_collects_customer_name():
    from response_generator import _enforce_service_option_conversation_contract

    reply = _enforce_service_option_conversation_contract(
        "May I have your pet's name, type, and preferred date?",
        SessionContext(existing_customer=False, customer_name=""),
    )

    assert "your name, your pet's name" in reply

    second_wording = _enforce_service_option_conversation_contract(
        "Could you please provide your pet's name, type, and preferred date?",
        SessionContext(existing_customer=False, customer_name=""),
    )
    assert "provide your name, your pet's name" in second_wording


def test_existing_customer_with_multiple_pets_gets_natural_named_choice():
    from response_generator import _enforce_service_option_conversation_contract

    session = SessionContext(existing_customer=True, customer_name="Jamie")
    session.customer_pets = [
        {"pet_name": "Milo", "pet_type": "Cat"},
        {"pet_name": "Coco", "pet_type": "Dog"},
    ]
    session.last_booking_snapshot = {"pet_name": "Milo"}

    reply = _enforce_service_option_conversation_contract(
        "Could you please provide your pet's name, type, and preferred date?",
        session,
    )

    assert "Would you like to make the booking for Milo or Coco?" in reply
    assert "I'd recommend Milo" in reply
    assert "provide your pet's name" not in reply
    assert "• Milo" not in reply


def test_unknown_pet_type_grooming_query_targets_both_species():
    from booking_service_info import build_service_info_retrieval_query

    query = build_service_info_retrieval_query(
        "I want to book grooming",
        {
            "scenario_intent": "GET_BOOKING_SERVICE_OPTIONS",
            "service_type": "GROOMING",
            "entities": {"service_type": "GROOMING"},
        },
    )

    assert "cat and dog grooming" in query


def test_booking_asks_pet_type_before_retrieving_species_specific_packages():
    from booking_flow import apply_booking_collection_rules

    session = SessionContext(
        customer_name="Hung Wei",
        existing_customer=False,
        new_customer_session=True,
        booking_creation_flow=True,
        pending_action="make_booking_pending_info",
        last_scenario_intent="MAKE_BOOKING",
        last_service_type="GROOMING",
        pet_name="Coco",
        preferred_date="3 August",
        collected_entities={
            "customer_name": "Hung Wei",
            "service_type": "GROOMING",
            "pet_name": "Coco",
            "preferred_date": "3 August",
        },
    )
    intent = apply_booking_collection_rules(
        session,
        {
            "main_intent": "BOOKING_INTENT",
            "scenario_intent": "MAKE_BOOKING",
            "service_type": "GROOMING",
            "entities": dict(session.collected_entities),
            "confidence": 0.99,
        },
        "grooming for Coco on 3 August",
    )

    assert intent["scenario_intent"] == "MAKE_BOOKING"
    assert "pet_type" in intent["missing_information"]
    assert intent.get("database_action") != "get_booking_service_options"
