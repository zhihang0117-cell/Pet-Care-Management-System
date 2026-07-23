"""Provider-neutral relational repository interface for conversation runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from customer_context import CustomerContext


@dataclass
class PetData:
    pet_name: str
    pet_type: str = ""
    pet_size: str = ""
    height_cm: int | None = None
    breed: str = ""


@dataclass
class BookingCommand:
    company_id: int
    customer_id: int
    pet_id: int | None
    pet_name: str
    service_type: str
    package_name: str = ""
    variant: str = ""
    addons: list[str] = field(default_factory=list)
    preferred_date: str = ""
    preferred_time: str = ""
    selected_slot: str = ""
    phone_number: str = ""
    price_quote: float | None = None
    idempotency_key: str = ""


class RelationalRepository(Protocol):
    """Single abstraction for relational reads and mock writes."""

    def find_customer_by_phone(self, company_id: int, phone_number: str) -> dict:
        ...

    def create_customer(self, company_id: int, customer_name: str, phone_number: str) -> dict:
        ...

    def list_customer_pets(self, company_id: int, customer_id: int) -> dict:
        ...

    def find_pet_by_name(self, company_id: int, customer_id: int, pet_name: str) -> dict:
        ...

    def create_pet(self, company_id: int, customer_id: int, pet_data: PetData) -> dict:
        ...

    def get_latest_booking(self, company_id: int, customer_id: int, *, phone_number: str = "") -> dict:
        ...

    def get_booking_status(
        self,
        company_id: int,
        customer_id: int,
        *,
        booking_id: int | None = None,
        intent_json: dict | None = None,
    ) -> dict:
        ...

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
        ...

    def create_pending_booking(self, company_id: int, booking_command: BookingCommand) -> dict:
        ...

    def clear_test_data_for_session(self, phone_number: str) -> dict:
        ...

    def dispatch_database_action(
        self,
        intent_json: dict,
        context: CustomerContext,
    ) -> dict:
        """Scenario-based dispatcher used by execute_database_action."""
