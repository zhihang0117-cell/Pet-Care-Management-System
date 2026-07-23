"""In-memory relational repository for DATABASE_PROVIDER=mock tests."""

from __future__ import annotations

from customer_context import CustomerContext, get_relational_company_id
from mock_database import (
    mock_check_customer_by_phone,
    mock_database_action,
    mock_get_customer_pets,
    mock_get_latest_booking_by_customer_id,
)
from relational_repository import BookingCommand, PetData


class MockRelationalRepository:
    def find_customer_by_phone(self, company_id: int, phone_number: str) -> dict:
        del company_id
        return mock_check_customer_by_phone(phone_number)

    def create_customer(self, company_id: int, customer_name: str, phone_number: str) -> dict:
        del company_id, customer_name, phone_number
        return {"status": "not_supported", "data": {}, "error": "mock_create_customer_disabled"}

    def list_customer_pets(self, company_id: int, customer_id: int) -> dict:
        del company_id
        return mock_get_customer_pets(customer_id=customer_id)

    def find_pet_by_name(self, company_id: int, customer_id: int, pet_name: str) -> dict:
        del company_id
        pets_result = mock_get_customer_pets(customer_id=customer_id)
        if pets_result.get("status") != "success":
            return {"status": "not_found", "data": {}, "error": None}
        name = str(pet_name or "").strip().lower()
        for pet in pets_result.get("data", {}).get("pets", []):
            if str(pet.get("pet_name") or "").strip().lower() == name:
                return {"status": "success", "data": dict(pet), "error": None}
        return {"status": "not_found", "data": {}, "error": None}

    def create_pet(self, company_id: int, customer_id: int, pet_data: PetData) -> dict:
        del company_id, customer_id, pet_data
        return {"status": "not_supported", "data": {}, "error": "mock_create_pet_disabled"}

    def get_latest_booking(self, company_id: int, customer_id: int, *, phone_number: str = "") -> dict:
        del company_id
        return mock_get_latest_booking_by_customer_id(customer_id=customer_id, phone_number=phone_number)

    def get_booking_status(
        self,
        company_id: int,
        customer_id: int,
        *,
        booking_id: int | None = None,
        intent_json: dict | None = None,
    ) -> dict:
        del company_id, customer_id, booking_id, intent_json
        return {"status": "success", "data": {"booking_status": "confirmed"}, "error": None}

    def check_availability(
        self,
        company_id: int,
        *,
        service: str,
        date: str,
        time: str = "",
        duration_minutes: int | None = None,
        intent_json: dict | None = None,
    ) -> dict:
        del company_id, duration_minutes
        probe = dict(intent_json or {})
        probe.setdefault("scenario_intent", "CHECK_AVAILABILITY")
        probe["service_type"] = service
        probe.setdefault("entities", {})
        probe["entities"]["preferred_date"] = date
        if time:
            probe["entities"]["preferred_time"] = time
        return mock_database_action(probe, "", "")

    def create_pending_booking(self, company_id: int, booking_command: BookingCommand) -> dict:
        del company_id, booking_command
        return {"status": "not_supported", "data": {}, "error": "mock_pending_booking_disabled"}

    def clear_test_data_for_session(self, phone_number: str) -> dict:
        del phone_number
        return {"status": "success", "data": {}, "error": None}

    def dispatch_database_action(self, intent_json: dict, context: CustomerContext) -> dict:
        del get_relational_company_id
        return mock_database_action(
            intent_json,
            str(context.request_customer_id or ""),
            str(context.phone_number or ""),
        )
