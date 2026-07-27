"""Regression checks for guarded multi-turn booking session memory."""

from __future__ import annotations

import unittest
import os

from session_store import (
    AWAIT_BOOKING_CONFIRMATION,
    SLOT_AVAILABLE_PENDING,
    SessionContext,
    clear_sessions,
    finalize_session_slot_selection,
    get_or_create_session,
    update_session_from_availability_check,
)
from booking_flow import apply_booking_confirmation_safety


class BookingSessionMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_sessions()

    def test_same_phone_reuses_session_memory(self) -> None:
        first = get_or_create_session("+60 12-345 6789")
        first.enable_runtime_guard()
        first.pending_action = "make_booking_pending_info"
        first.missing_fields = ["preferred_date"]
        first.collected_entities = {"service_type": "GROOMING"}

        second = get_or_create_session("60123456789")

        self.assertIs(second, first)
        self.assertEqual(second.pending_action, "make_booking_pending_info")
        self.assertEqual(second.missing_fields, ["preferred_date"])
        self.assertEqual(second.collected_entities["service_type"], "GROOMING")

    def test_guard_does_not_discard_booking_progress(self) -> None:
        session = SessionContext(phone_number="+60123456789")
        session.enable_runtime_guard()

        session.write_projection_fields(
            pending_action="make_booking_pending_info",
            missing_fields=["preferred_date"],
            booking_creation_flow=True,
        )
        session.pending_action = SLOT_AVAILABLE_PENDING
        session.missing_fields = ["slot_acceptance"]
        session.collected_entities = {
            "service_type": "GROOMING",
            "preferred_date": "2026-08-01",
            "preferred_time": "10:00",
        }

        self.assertEqual(session.pending_action, SLOT_AVAILABLE_PENDING)
        self.assertEqual(session.missing_fields, ["slot_acceptance"])
        self.assertEqual(session.collected_entities["preferred_time"], "10:00")

    def test_availability_advances_to_confirmation_without_looping(self) -> None:
        session = SessionContext(
            phone_number="+60123456789",
            customer_id=10,
            customer_name="Alicia",
            existing_customer=True,
            pet_name="Milo",
            pet_id=20,
            last_service_type="GROOMING",
            preferred_date="2026-08-01",
            preferred_time="10:00",
            booking_creation_flow=True,
            collected_entities={
                "service_type": "GROOMING",
                "pet_name": "Milo",
                "pet_id": "20",
                "preferred_date": "2026-08-01",
                "preferred_time": "10:00",
            },
        )
        session.enable_runtime_guard()
        intent = {
            "service_type": "GROOMING",
            "entities": dict(session.collected_entities),
        }
        database_result = {
            "action": "check_available_slots",
            "status": "success",
            "data": {
                "available_slots": ["10:00"],
            },
        }

        update_session_from_availability_check(session, intent, database_result)

        self.assertEqual(session.pending_action, SLOT_AVAILABLE_PENDING)
        self.assertEqual(session.missing_fields, ["slot_acceptance"])
        self.assertEqual(session.current_step, "CHECK_AVAILABILITY")
        self.assertTrue(session.availability_result.get("available"))

        finalize_session_slot_selection(session, intent)

        self.assertEqual(session.pending_action, AWAIT_BOOKING_CONFIRMATION)
        self.assertEqual(session.missing_fields, ["confirmation"])
        self.assertEqual(session.current_step, "WAIT_FOR_CONFIRMATION")
        self.assertTrue(session.draft_booking_payload)

        slot_acceptance_intent = {
            "scenario_intent": "MAKE_BOOKING",
            "slot_just_accepted": True,
            "missing_information": ["confirmation"],
        }
        guarded = apply_booking_confirmation_safety(
            session,
            slot_acceptance_intent,
            "yes",
        )
        self.assertEqual(guarded["scenario_intent"], "MAKE_BOOKING")
        self.assertNotEqual(guarded.get("database_action"), "create_booking")

    def test_complete_chat_booking_process_uses_memory_and_exits(self) -> None:
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
        from main import app

        client = TestClient(app)
        phone = "+60 12-345 6701"
        messages = [
            "Hi",
            "I want to make a grooming booking",
            "Milo",
            "Full grooming",
            "tomorrow",
            "10 am",
            "yes",
            "yes",
        ]
        turns = [
            client.post(
                "/chat",
                json={"message": message, "phone_number": phone},
            ).json()
            for message in messages
        ]

        slot_offer = turns[5]
        slot_accepted = turns[6]
        confirmed = turns[7]

        self.assertEqual(
            slot_offer["session_context"]["pending_action"],
            SLOT_AVAILABLE_PENDING,
        )
        self.assertEqual(
            slot_accepted["session_context"]["pending_action"],
            AWAIT_BOOKING_CONFIRMATION,
        )
        self.assertEqual(
            slot_accepted["session_context"]["current_step"],
            "WAIT_FOR_CONFIRMATION",
        )
        self.assertTrue(slot_accepted["session_context"]["draft_booking_payload"])
        self.assertIn("confirm", slot_accepted["reply"].lower())

        self.assertEqual(confirmed["database_result"]["action"], "create_booking")
        self.assertEqual(confirmed["database_result"]["status"], "success")
        self.assertEqual(confirmed["session_context"]["previous_booking_id"], 9001)
        self.assertEqual(confirmed["session_context"]["pending_action"], "")
        self.assertEqual(confirmed["session_context"]["current_step"], "")
        self.assertFalse(confirmed["session_context"]["draft_booking_payload"])


if __name__ == "__main__":
    unittest.main()
