"""
Global customer identity resolution for every /chat request.

Phone number from WhatsApp metadata resolves customer_id and customer_name
before intent detection and routing. customer_id is internal only.
"""

from __future__ import annotations

from database_service import lookup_customer_by_phone
from session_store import update_session_from_identity


def resolve_customer_at_request_start(
    session,
    phone_number: str,
    customer_id: str = "",
    user_message: str = "",
) -> dict:
    """Look up customer by phone, load pets, and merge into session before intent processing."""
    phone = str(phone_number or "").strip()
    if not phone:
        return {
            "action": "check_customer_by_phone",
            "status": "missing_information",
            "data": {},
            "error": None,
        }

    result = lookup_customer_by_phone(phone, customer_id=str(customer_id or "").strip())
    update_session_from_identity(session, result)

    if str(result.get("status") or "").strip() == "success":
        from pet_profile import enrich_session_pet_profile

        enrich_session_pet_profile(session, user_message)

    return result
