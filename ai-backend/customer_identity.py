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
    context_was_loaded = bool(getattr(session, "customer_context_loaded", False))
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

    pet_resolution = {
        "status": "not_loaded",
        "matched_pet": None,
        "pet_names": [],
    }
    latest_booking_status = "not_loaded"
    if str(result.get("status") or "").strip() == "success":
        from pet_profile import enrich_session_pet_profile

        # Re-match the current message against the cached pet collection on
        # every turn. This is cheap when cached and is necessary when a
        # multi-pet customer names a different pet later in the conversation.
        pet_resolution = enrich_session_pet_profile(session, user_message)
        if not bool(getattr(session, "customer_context_loaded", False)):
            from database_service import fetch_latest_booking_for_entry

            latest_booking = fetch_latest_booking_for_entry(session)
            latest_booking_status = str(latest_booking.get("status") or "not_found")
            if str(latest_booking.get("status") or "").strip() == "success":
                session.last_booking_snapshot = dict(latest_booking.get("data") or {})
            else:
                session.last_booking_snapshot = {}
            session.customer_context_loaded = True
        else:
            latest_booking_status = (
                "success"
                if dict(getattr(session, "last_booking_snapshot", {}) or {})
                else "not_found"
            )

    if str(result.get("status") or "").strip() == "success":
        enriched = dict(result)
        data = dict(result.get("data") or {})
        data["pet_profiles"] = list(getattr(session, "customer_pets", []) or [])
        data["selected_pet_profile"] = dict(
            pet_resolution.get("matched_pet") or {}
        )
        data["pet_selection_status"] = str(
            pet_resolution.get("status") or "none"
        )
        data["last_booking"] = dict(getattr(session, "last_booking_snapshot", {}) or {})
        data["profile_bundle"] = {
            "customer_profile_loaded": True,
            "pet_profiles_loaded": True,
            "last_booking_loaded": latest_booking_status == "success",
            "automatic_actions": [
                "get_customer_profile",
                "get_pet_profiles",
                "get_latest_booking",
            ],
        }
        enriched["data"] = data
        tool_names = ["get_customer_profile", "get_pet_profiles"]
        if not context_was_loaded:
            tool_names.append("get_latest_booking")
        enriched["tool_calling"] = {
            "used": True,
            "count": len(tool_names),
            "source": "identity_context",
            "tool_names": tool_names,
            "calls": [
                {
                    "tool_name": tool_name,
                    "status": (
                        latest_booking_status
                        if tool_name == "get_latest_booking"
                        else "success"
                    ),
                }
                for tool_name in tool_names
            ],
        }
        return enriched

    return result
