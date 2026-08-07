"""Provider-neutral relational repository interface for the LangChain harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


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
    # BOARDING only — check-in is preferred_date; without this the write path
    # (relational_actions.create_booking) silently defaults every stay to a
    # single night regardless of what the customer asked for.
    check_out_date: str = ""
    # DAYCARE only — without this the write path silently defaults every
    # stay to a flat 3 hours regardless of what the customer asked for
    # (relevant for hourly-rate packages, where duration changes the price).
    check_out_time: str = ""
    # Optional — customer's requested staff (name or numeric staff_id).
    # Honored only if that staff member is actually free at the chosen
    # date/time; otherwise create_booking rejects with a clear reason
    # instead of silently assigning someone else.
    preferred_staff: str = ""
    # GROOMING/DAYCARE — the add-on's own name/price, kept separate from
    # price_quote so each service table stores the item and its amount
    # instead of silently folding it into the base package price.
    add_on: str = ""
    add_on_price: float | None = None
    # Exact package/visit duration when the catalogue or customer provides
    # one. Grooming falls back to 90 minutes only when no verified duration
    # exists; daycare normally derives this from its pickup endpoint.
    duration_minutes: int | None = None


class RelationalRepository(Protocol):
    """Single abstraction for relational reads/writes, backed by relational_actions.py."""

    def get_customer_by_phone(self, company_id: int, phone_number: str) -> dict: ...
    def create_customer(self, company_id: int, customer_name: str, phone_number: str, address: str = "") -> dict: ...
    def list_customer_pets(self, company_id: int, customer_id: int) -> dict: ...
    def find_pet_by_name(self, company_id: int, customer_id: int, pet_name: str) -> dict: ...
    def create_pet(self, company_id: int, customer_id: int, pet_data: PetData) -> dict: ...
    def update_pet(self, company_id: int, customer_id: int, pet_id: int, updates: dict) -> dict: ...
    def get_latest_booking(self, company_id: int, customer_id: int, *, phone_number: str = "") -> dict: ...
    def get_booking_by_id(
        self, company_id: int, customer_id: int, booking_id: int, service_type: str = "GROOMING"
    ) -> dict: ...

    def check_availability(
        self,
        company_id: int,
        *,
        service: str,
        date: str,
        time: str = "",
        duration_minutes: int | None = None,
        intent_json: dict | None = None,
    ) -> dict: ...

    def create_booking(self, company_id: int, booking_command: BookingCommand, intent_json: dict | None = None) -> dict: ...
    def cancel_booking(self, company_id: int, customer_id: int, intent_json: dict) -> dict: ...
    def reschedule_booking(self, company_id: int, customer_id: int, intent_json: dict) -> dict: ...
    def get_loyalty_account(self, company_id: int, customer_id: int) -> dict: ...
    def redeem_reward(self, company_id: int, customer_id: int, intent_json: dict) -> dict: ...
