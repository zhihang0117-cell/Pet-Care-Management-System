"""
In-memory session store for local testing and development.

Production WhatsApp deployments should replace this with persistent storage
(e.g. Redis, Supabase session table, or webhook provider session cache) so
customer identity, pending actions, and collected entities survive server restarts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from customer_context import normalize_phone_digits
from booking_draft import AWAIT_BOOKING_CONFIRMATION

PROJECTION_ONLY_FIELDS = frozenset(
    {
        "pending_action",
        "sub_flow",
        "booking_creation_flow",
        "booking_missing_snapshot",
        "missing_fields",
        "collected_entities",
        "service_package",
        "last_service_type",
        "last_intent",
        "last_scenario_intent",
    }
)

REPEAT_BOOKING_PENDING_ACTION = "make_booking_pending_info"
BOOKING_CHOOSE_REPEAT_OR_NEW = "booking_choose_repeat_or_new"
COLLECT_CUSTOMER_NAME = "collect_customer_name"
CHECK_AVAILABLE_SLOTS_PENDING = "check_available_slots"
SLOT_AVAILABLE_PENDING = "slot_available_pending"
BOOKING_PRICE_PENDING_SUB_FLOW = "booking_price_pending_info"
BOOKING_SERVICE_INFO_SUB_FLOW = "booking_service_info"
SERVICE_PRICE_PENDING_ACTION = "service_price_pending_info"


@dataclass
class SessionContext:
    """Lightweight per-customer session state keyed by phone number."""

    phone_number: str = ""
    customer_id: int | None = None
    customer_name: str = ""
    existing_customer: bool = False
    last_intent: str = ""
    last_scenario_intent: str = ""
    last_service_type: str = ""
    pet_name: str = ""
    pet_id: int | None = None
    pet_type: str = ""
    pet_size: str = ""
    pet_height: str = ""
    service_package: str = ""
    service_options: list[dict] = field(default_factory=list)
    service_options_for: str = ""
    selected_package: str = ""
    selected_addons: list[str] = field(default_factory=list)
    add_on_service: str = ""
    add_on_type: str = ""
    price_context: str = ""
    requested_package: str = ""
    requested_price_item_type: str = ""
    requested_addon: str = ""
    sub_flow: str = ""
    booking_missing_snapshot: list[str] = field(default_factory=list)
    booking_scenario_snapshot: str = ""
    previous_booking_id: int | None = None
    last_booking_snapshot: dict = field(default_factory=dict)
    preferred_date: str = ""
    preferred_time: str = ""
    selected_slot: str = ""
    selected_staff_id: int | None = None
    price_quote: float | None = None
    draft_booking_payload: dict = field(default_factory=dict)
    availability_result: dict = field(default_factory=dict)
    booking_creation_flow: bool = False
    greeted_this_session: bool = False
    pending_action: str = ""
    missing_fields: list[str] = field(default_factory=list)
    collected_entities: dict[str, str] = field(default_factory=dict)
    customer_pets: list[dict] = field(default_factory=list)
    customer_context_loaded: bool = False
    new_customer_session: bool = False
    current_step: str = ""
    completed_fields: list[str] = field(default_factory=list)
    turn_request_id: str = ""
    turn_response_scenario: str = ""
    turn_response_goal: str = ""
    turn_next_question: str = ""
    turn_generated_reply: str = ""
    turn_extracted_entities: dict[str, str] = field(default_factory=dict)
    suspended_task: dict = field(default_factory=dict)
    resume_after_response: bool = False
    interruption_type: str = ""
    interruption_depth: int = 0
    decision_state: dict = field(default_factory=dict)
    reasoning_memory: dict = field(default_factory=dict)
    recent_messages: list[dict] = field(default_factory=list)
    _runtime_guard: bool = field(default=False, repr=False)
    _allow_projection_write: bool = field(default=False, repr=False)

    def enable_runtime_guard(self) -> None:
        """Enable projection-write diagnostics without changing session semantics.

        The booking flow still contains compatibility writers that assign
        projection fields directly.  The previous guard silently discarded
        those assignments outside tests, leaving the session in a half-updated
        state (for example, a draft existed while ``pending_action`` still
        pointed at the availability step).  A diagnostic guard must never
        change whether conversation memory is persisted, so direct writes are
        allowed until all compatibility writers use one canonical transition
        API.
        """
        object.__setattr__(self, "_runtime_guard", True)

    def write_projection_fields(self, **fields) -> None:
        """Write derived compatibility fields as one explicit state update."""
        object.__setattr__(self, "_allow_projection_write", True)
        try:
            for key, value in fields.items():
                object.__setattr__(self, key, value)
        finally:
            object.__setattr__(self, "_allow_projection_write", False)

    def __setattr__(self, name: str, value) -> None:
        # Do not reject a state transition.  These fields are part of the
        # live conversation memory and dropping one write can make
        # current_step, pending_action and missing_fields disagree, causing
        # the next turn to repeat the previous step indefinitely.
        object.__setattr__(self, name, value)

    def to_dict(self) -> dict:
        return {
            "phone_number": self.phone_number,
            "customer_id": self.customer_id,
            "customer_name": self.customer_name,
            "existing_customer": self.existing_customer,
            "last_intent": self.last_intent,
            "last_scenario_intent": self.last_scenario_intent,
            "last_service_type": self.last_service_type,
            "pet_name": self.pet_name,
            "pet_id": self.pet_id,
            "pet_type": self.pet_type,
            "pet_size": self.pet_size,
            "pet_height": self.pet_height,
            "service_package": self.service_package,
            "service_options": list(self.service_options or []),
            "service_options_for": self.service_options_for,
            "selected_package": self.selected_package,
            "selected_addons": list(self.selected_addons),
            "add_on_service": self.add_on_service,
            "add_on_type": self.add_on_type,
            "price_context": self.price_context,
            "sub_flow": self.sub_flow,
            "booking_missing_snapshot": list(self.booking_missing_snapshot),
            "booking_scenario_snapshot": self.booking_scenario_snapshot,
            "previous_booking_id": self.previous_booking_id,
            "last_booking_snapshot": dict(self.last_booking_snapshot or {}),
            "preferred_date": self.preferred_date,
            "preferred_time": self.preferred_time,
            "selected_slot": self.selected_slot,
            "selected_staff_id": self.selected_staff_id,
            "price_quote": self.price_quote,
            "draft_booking_payload": dict(self.draft_booking_payload),
            "availability_result": dict(self.availability_result or {}),
            "booking_creation_flow": self.booking_creation_flow,
            "greeted_this_session": self.greeted_this_session,
            "pending_action": self.pending_action,
            "missing_fields": list(self.missing_fields),
            "collected_entities": dict(self.collected_entities),
            "customer_pets": list(self.customer_pets),
            "customer_context_loaded": self.customer_context_loaded,
            "new_customer_session": self.new_customer_session,
            "current_step": self.current_step,
            "completed_fields": list(self.completed_fields),
            "turn_request_id": self.turn_request_id,
            "turn_response_scenario": self.turn_response_scenario,
            "turn_response_goal": self.turn_response_goal,
            "turn_next_question": self.turn_next_question,
            "suspended_task": dict(self.suspended_task or {}),
            "resume_after_response": self.resume_after_response,
            "interruption_type": self.interruption_type,
            "interruption_depth": self.interruption_depth,
            "decision_state": dict(self.decision_state or {}),
            "reasoning_memory": dict(self.reasoning_memory or {}),
            "recent_messages": list(self.recent_messages or []),
        }


# phone_digits -> SessionContext (local dev only)
_sessions: dict[str, SessionContext] = {}


def _session_key(phone_number: str) -> str:
    digits = normalize_phone_digits(phone_number)
    return digits or str(phone_number or "").strip()


def get_session_key(phone_number: str) -> str:
    """Public wrapper for session map key (phone digits)."""
    return _session_key(phone_number)


def session_exists(phone_number: str) -> bool:
    """True when an in-memory session already exists for this phone key."""
    key = _session_key(phone_number)
    return bool(key and key in _sessions)


def list_session_keys() -> list[str]:
    """Return all in-memory session keys (phone digits)."""
    return sorted(_sessions.keys())


def get_sanitized_session_for_phone(phone_number: str) -> dict:
    """Return non-secret session snapshot for runtime debugging."""
    key = _session_key(phone_number)
    if not key or key not in _sessions:
        return {"exists": False, "session_key": key or ""}
    session = _sessions[key]
    data = session.to_dict()
    return {
        "exists": True,
        "session_key": key,
        "phone_number": data.get("phone_number"),
        "customer_id": data.get("customer_id"),
        "customer_name": data.get("customer_name"),
        "existing_customer": data.get("existing_customer"),
        "pet_type": data.get("pet_type"),
        "pet_name": data.get("pet_name"),
        "last_service_type": data.get("last_service_type"),
        "pending_action": data.get("pending_action"),
        "current_step": data.get("current_step"),
        "missing_fields": list(data.get("missing_fields") or []),
        "completed_fields": list(data.get("completed_fields") or []),
        "booking_creation_flow": data.get("booking_creation_flow"),
        "collected_entities": dict(data.get("collected_entities") or {}),
        "last_scenario_intent": data.get("last_scenario_intent"),
    }


def get_or_create_session(phone_number: str) -> SessionContext:
    """Return session for phone_number, creating an empty one if needed."""
    key = _session_key(phone_number)
    if not key:
        return SessionContext()
    if key not in _sessions:
        _sessions[key] = SessionContext(phone_number=str(phone_number or "").strip())
    return _sessions[key]


def update_session_from_identity(session: SessionContext, identity_result: dict) -> SessionContext:
    """Merge check_customer_by_phone result into session."""
    status = str(identity_result.get("status") or "").strip()
    data = identity_result.get("data") or {}
    has_pending = bool(str(getattr(session, "pending_action", "") or "").strip())

    if status == "success":
        session.existing_customer = True
        session.customer_id = data.get("customer_id")
        session.customer_name = str(data.get("full_name") or "").strip()
        if not has_pending:
            session.write_projection_fields(missing_fields=[])
        return session

    if status == "not_found":
        session.existing_customer = False
        session.customer_id = None
        collected_name = str(session.customer_name or "").strip()
        if not collected_name:
            session.customer_name = ""
        if not has_pending and not collected_name:
            session.write_projection_fields(missing_fields=["customer_name"])
        elif not has_pending and collected_name:
            session.write_projection_fields(missing_fields=[])
        return session

    return session


def update_session_intent(session: SessionContext, intent_json: dict) -> SessionContext:
    """Record latest intent labels — projection fields derived from canonical sync in Phase 3."""
    return session


def update_session_from_last_booking_inquiry(
    session: SessionContext, database_result: dict
) -> SessionContext:
    """Update session after last-booking inquiry for repeat-or-new choice."""
    status = str(database_result.get("status") or "").strip()
    data = database_result.get("data") or {}

    session.booking_creation_flow = True
    session.pending_action = BOOKING_CHOOSE_REPEAT_OR_NEW
    session.missing_fields = ["repeat_or_new_service_choice"]

    if status == "success":
        service = str(data.get("last_service_type") or "").strip().upper()
        pet = str(data.get("pet_name") or "").strip()
        if service:
            session.last_service_type = service
        if pet:
            session.pet_name = pet
        raw_pet_id = data.get("pet_id")
        session.pet_id = int(raw_pet_id) if raw_pet_id is not None else None
        return session

    if status == "not_found":
        session.pending_action = REPEAT_BOOKING_PENDING_ACTION
        session.missing_fields = ["service_type"]
        session.last_scenario_intent = "MAKE_BOOKING"
        return session

    return session


def update_session_from_last_booking(session: SessionContext, database_result: dict) -> SessionContext:
    """Update session after repeat-last-booking database lookup."""
    status = str(database_result.get("status") or "").strip()
    data = database_result.get("data") or {}

    if status == "success":
        session.last_service_type = str(data.get("last_service_type") or "").strip().upper()
        session.pet_name = str(data.get("pet_name") or "").strip()
        raw_pet_id = data.get("pet_id")
        session.pet_id = int(raw_pet_id) if raw_pet_id is not None else None
        booking_id = data.get("booking_id")
        session.previous_booking_id = booking_id if booking_id is not None else None
        session.pending_action = REPEAT_BOOKING_PENDING_ACTION
        session.missing_fields = ["preferred_date", "preferred_time"]
        session.last_scenario_intent = "REPEAT_LAST_BOOKING"
        session.booking_creation_flow = True
        session.collected_entities = {
            **dict(session.collected_entities or {}),
            "service_type": session.last_service_type,
            "pet_name": session.pet_name,
            **({"pet_id": str(session.pet_id)} if session.pet_id is not None else {}),
        }
        if session.preferred_date:
            session.collected_entities["preferred_date"] = session.preferred_date
        if session.preferred_time:
            session.collected_entities["preferred_time"] = session.preferred_time
        return session

    if status == "not_found":
        session.pending_action = REPEAT_BOOKING_PENDING_ACTION
        session.missing_fields = ["service_type"]
        session.booking_creation_flow = True
        session.last_scenario_intent = "MAKE_BOOKING"
        return session

    if status == "missing_information":
        session.pending_action = ""
        session.missing_fields = ["phone_number"]
        return session

    return session


def clear_availability_and_confirmation_state(session: SessionContext) -> SessionContext:
    """Clear slot selection and confirmation state when date/time changes."""
    session.selected_slot = ""
    session.selected_staff_id = None
    session.price_quote = None
    session.draft_booking_payload = {}
    session.availability_result = {}
    if str(getattr(session, "pending_action", "") or "").strip() in {
        AWAIT_BOOKING_CONFIRMATION,
        SLOT_AVAILABLE_PENDING,
    }:
        session.pending_action = ""
        session.missing_fields = []
        session.current_step = "ASK_TIME"
    return session


def clear_booking_draft(session: SessionContext) -> SessionContext:
    """Clear booking draft fields and confirmation pending state."""
    session.draft_booking_payload = {}
    session.availability_result = {}
    session.selected_slot = ""
    session.selected_staff_id = None
    session.price_quote = None
    session.preferred_date = ""
    session.preferred_time = ""
    session.booking_creation_flow = False
    session.pending_action = ""
    session.missing_fields = []
    session.current_step = ""
    session.completed_fields = []
    return session


def update_session_from_availability_check(
    session: SessionContext,
    intent_json: dict,
    database_result: dict,
) -> SessionContext:
    """Store structured availability result without auto-selecting slot or creating draft."""
    from availability_service import enrich_database_result_with_availability
    from booking_flow import has_complete_booking_fields

    if not has_complete_booking_fields(session, intent_json):
        return session

    data = database_result.get("data") or {}
    entities = dict(intent_json.get("entities") or {})
    service_candidates = (
        intent_json.get("service_type"),
        entities.get("service_type"),
        session.last_service_type,
    )
    service_type = next(
        (
            str(value).strip().upper()
            for value in service_candidates
            if str(value or "").strip().upper() not in {"", "UNKNOWN", "NONE", "NULL"}
        ),
        "",
    )
    preferred_date = str(entities.get("preferred_date") or session.preferred_date or data.get("booking_date") or "").strip()
    from time_normalization import normalize_time

    preferred_time = normalize_time(
        str(entities.get("preferred_time") or session.preferred_time or "")
    ) or str(entities.get("preferred_time") or session.preferred_time or "").strip()

    enriched = enrich_database_result_with_availability(
        database_result,
        requested_date=preferred_date,
        requested_time=preferred_time,
        service_type=service_type,
    )
    availability = dict((enriched.get("data") or {}).get("availability_result") or {})
    session.preferred_date = preferred_date
    session.preferred_time = preferred_time
    session.availability_result = availability
    session.selected_slot = ""
    session.draft_booking_payload = {}
    session.pending_action = SLOT_AVAILABLE_PENDING
    session.missing_fields = ["slot_acceptance"]
    session.current_step = "CHECK_AVAILABILITY"
    session.last_scenario_intent = "CHECK_AVAILABILITY"
    session.booking_creation_flow = True
    session.collected_entities = {
        **dict(getattr(session, "collected_entities", {}) or {}),
        "preferred_time": preferred_time,
        "preferred_date": preferred_date,
    }
    return session


def finalize_session_slot_selection(session: SessionContext, intent_json: dict) -> SessionContext:
    """After customer accepts an available slot, create the booking draft."""
    from booking_draft import (
        build_draft_booking_payload,
        resolve_pet_id,
        resolve_price_quote,
        select_staff_id,
    )
    from time_normalization import normalize_time_to_slot

    availability = dict(getattr(session, "availability_result", {}) or {})
    matched = dict(availability.get("matched_slot") or {})
    if not availability.get("available") or not matched.get("start_time"):
        return session

    entities = dict(intent_json.get("entities") or {})
    service_candidates = (
        intent_json.get("service_type"),
        entities.get("service_type"),
        session.last_service_type,
    )
    service_type = next(
        (
            str(value).strip().upper()
            for value in service_candidates
            if str(value or "").strip().upper() not in {"", "UNKNOWN", "NONE", "NULL"}
        ),
        "",
    )
    pet_name = str(entities.get("pet_name") or session.pet_name or "").strip()
    preferred_date = str(availability.get("requested_date") or session.preferred_date or "").strip()
    preferred_time = str(availability.get("requested_time") or session.preferred_time or "").strip()
    selected_slot = normalize_time_to_slot(matched.get("start_time") or preferred_time)
    booking_date = preferred_date
    pet_id = resolve_pet_id(session.customer_id, pet_name or session.pet_name, session.phone_number)
    selected_staff_id = None
    price_quote = resolve_price_quote(service_type, entities)

    session.pet_name = pet_name or session.pet_name
    session.preferred_date = preferred_date
    session.preferred_time = preferred_time
    session.selected_slot = selected_slot
    session.selected_staff_id = selected_staff_id
    session.price_quote = price_quote
    session.draft_booking_payload = build_draft_booking_payload(
        service_type=service_type,
        pet_id=pet_id,
        pet_name=pet_name or session.pet_name,
        preferred_date=preferred_date,
        preferred_time=preferred_time,
        booking_date=booking_date,
        selected_slot=selected_slot,
        selected_staff_id=selected_staff_id,
        price_quote=price_quote,
        customer_id=session.customer_id,
        customer_name=str(session.customer_name or "").strip(),
        entities=entities,
    )
    session.pending_action = AWAIT_BOOKING_CONFIRMATION
    session.missing_fields = ["confirmation"]
    session.current_step = "WAIT_FOR_CONFIRMATION"
    session.last_scenario_intent = "CHECK_AVAILABILITY"
    return session


def clear_sessions() -> None:
    """Clear in-memory sessions (useful for tests)."""
    _sessions.clear()


def clear_session_for_phone(phone_number: str) -> bool:
    """
    Remove the in-memory session for a phone number (local testing only).

    Does not affect database rows (customer, pet, or booking records).
    """
    key = _session_key(phone_number)
    if not key:
        return False
    if key in _sessions:
        del _sessions[key]
    return True
