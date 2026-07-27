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
    if str(result.get("status") or "").strip() == "not_found":
        # Preserve how this conversation started even if a customer record is
        # created later during the booking flow.
        session.new_customer_session = True

    if str(result.get("status") or "").strip() == "success":
        from pet_profile import enrich_session_pet_profile

        # Re-match the current message against the cached pet collection on
        # every turn. This is cheap when cached and is necessary when a
        # multi-pet customer names a different pet later in the conversation.
        enrich_session_pet_profile(session, user_message)
        if not bool(getattr(session, "customer_context_loaded", False)):
            from database_service import fetch_latest_booking_for_entry

            latest_booking = fetch_latest_booking_for_entry(session)
            if str(latest_booking.get("status") or "").strip() == "success":
                session.last_booking_snapshot = dict(latest_booking.get("data") or {})
            else:
                session.last_booking_snapshot = {}
            session.customer_context_loaded = True

    if str(result.get("status") or "").strip() == "success":
        enriched = dict(result)
        data = dict(result.get("data") or {})
        data["pet_profiles"] = list(getattr(session, "customer_pets", []) or [])
        data["last_booking"] = dict(getattr(session, "last_booking_snapshot", {}) or {})
        enriched["data"] = data
        return enriched

    return result
