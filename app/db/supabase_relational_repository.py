"""Supabase relational repository — real CRUD via relational_actions."""

from __future__ import annotations

from .customer_context import CustomerContext, missing_identity_result
from .relational_repository import BookingCommand, PetData


class SupabaseRelationalRepository:
    """Wraps relational_actions.py against live Supabase tables."""

    def find_customer_by_phone(self, company_id: int, phone_number: str) -> dict:
        return self.get_customer_by_phone(company_id, phone_number)

    def get_customer_by_phone(self, company_id: int, phone_number: str) -> dict:
        from .relational_actions import check_customer_by_phone

        context = CustomerContext(phone_number=str(phone_number or "").strip(), company_id=company_id)
        return check_customer_by_phone(context)

    def create_customer(self, company_id: int, customer_name: str, phone_number: str, address: str = "") -> dict:
        from .relational_actions import create_customer

        context = CustomerContext(phone_number=str(phone_number or "").strip(), company_id=company_id)
        return create_customer(context, customer_name, phone_number, address)

    def update_customer(self, company_id: int, customer_id: int, updates: dict) -> dict:
        from .relational_actions import update_customer

        context = CustomerContext(company_id=company_id)
        context.resolved_customer_id = int(customer_id)
        return update_customer(context, updates)

    def list_customer_pets(self, company_id: int, customer_id: int) -> dict:
        return self.get_pets_by_customer_id(company_id, customer_id)

    def get_pets_by_customer_id(self, company_id: int, customer_id: int) -> dict:
        from .relational_actions import get_pets_by_customer_id

        context = CustomerContext(company_id=company_id)
        context.resolved_customer_id = int(customer_id)
        return get_pets_by_customer_id(context)

    def find_pet_by_name(self, company_id: int, customer_id: int, pet_name: str) -> dict:
        from .relational_actions import get_pet_by_customer_and_name

        context = CustomerContext(company_id=company_id)
        context.resolved_customer_id = int(customer_id)
        result = get_pet_by_customer_and_name(context, pet_name)
        if result.get("status") == "success":
            pet = (result.get("data") or {}).get("pet") or {}
            return {"status": "success", "data": dict(pet), "error": None}
        return {"status": result.get("status", "not_found"), "data": {}, "error": result.get("error")}

    def create_pet(self, company_id: int, customer_id: int, pet_data: PetData) -> dict:
        from .relational_actions import create_pet

        context = CustomerContext(company_id=company_id)
        context.resolved_customer_id = int(customer_id)
        return create_pet(
            context,
            pet_name=pet_data.pet_name,
            pet_type=pet_data.pet_type,
            size=pet_data.pet_size,
            height_cm=pet_data.height_cm,
            breed=pet_data.breed,
        )

    def update_pet(self, company_id: int, customer_id: int, pet_id: int, updates: dict) -> dict:
        from .relational_actions import update_pet

        context = CustomerContext(company_id=company_id)
        context.resolved_customer_id = int(customer_id)
        return update_pet(context, int(pet_id), updates)

    def get_latest_booking(self, company_id: int, customer_id: int, *, phone_number: str = "") -> dict:
        from .relational_actions import get_latest_booking_by_customer_id

        context = CustomerContext(phone_number=str(phone_number or "").strip(), company_id=company_id)
        context.resolved_customer_id = int(customer_id)
        return get_latest_booking_by_customer_id(context)

    def get_booking_by_id(
        self, company_id: int, customer_id: int, booking_id: int, service_type: str = "GROOMING"
    ) -> dict:
        from .relational_actions import get_booking_by_id

        context = CustomerContext(company_id=company_id)
        context.resolved_customer_id = int(customer_id)
        return get_booking_by_id(context, int(booking_id), service_type)

    def get_booking_status(
        self,
        company_id: int,
        customer_id: int,
        *,
        booking_id: int | None = None,
        intent_json: dict | None = None,
    ) -> dict:
        from .relational_actions import check_booking_status

        context = CustomerContext(company_id=company_id)
        context.resolved_customer_id = int(customer_id)
        return check_booking_status(context, intent_json or {})

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
        from .relational_actions import check_available_slots

        context = CustomerContext(company_id=company_id)
        payload = dict(intent_json or {})
        entities = dict(payload.get("entities") or {})
        if date:
            entities["preferred_date"] = date
        if time:
            entities["preferred_time"] = time
        if duration_minutes is not None:
            entities["duration_minutes"] = duration_minutes
        if service:
            payload["service_type"] = service
            entities["service_type"] = service
        payload["entities"] = entities
        return check_available_slots(context, payload)

    def create_booking(self, company_id: int, booking_command: BookingCommand, intent_json: dict | None = None) -> dict:
        from .relational_actions import create_booking

        context = CustomerContext(company_id=company_id, phone_number=str(booking_command.phone_number or ""))
        context.resolved_customer_id = int(booking_command.customer_id)
        payload = dict(intent_json or {})
        payload["service_type"] = booking_command.service_type
        payload["entities"] = {
            **dict(payload.get("entities") or {}),
            "pet_id": booking_command.pet_id,
            "pet_name": booking_command.pet_name,
            "preferred_date": booking_command.preferred_date,
            "preferred_time": booking_command.selected_slot or booking_command.preferred_time,
            "service_type": booking_command.service_type,
            "package_name": booking_command.package_name,
            "check_out_date": booking_command.check_out_date,
            "check_out_time": booking_command.check_out_time,
            "preferred_staff": booking_command.preferred_staff,
            "add_on": booking_command.add_on,
            "add_on_price": booking_command.add_on_price,
            "duration_minutes": booking_command.duration_minutes,
        }
        payload["_draft_booking"] = {
            "pet_id": booking_command.pet_id,
            "pet_name": booking_command.pet_name,
            "service_type": booking_command.service_type,
            "booking_date": booking_command.preferred_date,
            "selected_slot": booking_command.selected_slot,
            "preferred_time": booking_command.preferred_time,
            "price_quote": booking_command.price_quote,
            "duration_minutes": booking_command.duration_minutes,
        }
        return create_booking(context, payload)

    def update_booking(self, company_id: int, booking_id: int, service_type: str, updates: dict) -> dict:
        from .relational_actions import update_booking

        context = CustomerContext(company_id=company_id)
        return update_booking(context, int(booking_id), service_type, updates)

    def cancel_booking(self, company_id: int, customer_id: int, intent_json: dict) -> dict:
        from .relational_actions import cancel_booking

        context = CustomerContext(company_id=company_id)
        context.resolved_customer_id = int(customer_id)
        return cancel_booking(context, intent_json)

    def reschedule_booking(self, company_id: int, customer_id: int, intent_json: dict) -> dict:
        from .relational_actions import reschedule_booking

        context = CustomerContext(company_id=company_id)
        context.resolved_customer_id = int(customer_id)
        return reschedule_booking(context, intent_json)

    def get_loyalty_account(self, company_id: int, customer_id: int) -> dict:
        from .relational_actions import get_loyalty_account

        context = CustomerContext(company_id=company_id)
        context.resolved_customer_id = int(customer_id)
        return get_loyalty_account(context)


    def redeem_reward(self, company_id: int, customer_id: int, intent_json: dict) -> dict:
        from .relational_actions import redeem_reward

        context = CustomerContext(company_id=company_id)
        context.resolved_customer_id = int(customer_id)
        return redeem_reward(context, intent_json)

    def create_pending_booking(self, company_id: int, booking_command: BookingCommand) -> dict:
        return self.create_booking(company_id, booking_command)

    def clear_test_data_for_session(self, phone_number: str) -> dict:
        return {"action": "clear_test_data_for_session", "status": "not_applicable", "data": {}, "error": None}

    def dispatch_database_action(self, intent_json: dict, context: CustomerContext) -> dict:
        from .relational_actions import relational_database_action

        return relational_database_action(intent_json, context)
