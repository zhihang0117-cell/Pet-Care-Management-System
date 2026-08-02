from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool

from app.db.relational_provider import get_relational_repository


ServiceType = Literal["GROOMING", "DAYCARE", "BOARDING"]
_DELIVERED_STATUSES = {"sent", "sent_console"}


@tool
def send_booking_confirmation(
    company_id: str | int,
    customer_id: str | int,
    booking_id: str | int = "",
    service_type: ServiceType | None = None,
) -> dict:
    """
    Regenerate and send a real booking-confirmation PDF for a booking owned by
    this customer. Use this when the customer asks where their confirmation
    slip/document is, says it was not received, or explicitly asks for it to
    be sent again. This is the only tool that makes a resend claim true.

    If booking_id and service_type are known, pass both. If neither is given,
    the tool resolves the customer's latest booking from the database. Do not
    invent a booking ID or claim the document was sent from an old chat message
    or stored URL: only status=success with delivery_status=sent/sent_console
    means this attempt was actually dispatched.
    """
    company = int(company_id)
    customer = int(customer_id)
    repo = get_relational_repository()

    normalized_service = str(service_type or "").strip().upper()
    resolved_booking_id: int | None = None
    if str(booking_id or "").strip():
        try:
            resolved_booking_id = int(booking_id)
        except (TypeError, ValueError):
            return {
                "status": "error",
                "error": "INVALID_BOOKING_ID",
                "message": "booking_id must be a real numeric booking ID.",
                "handoff_required": False,
            }
        if normalized_service not in {"GROOMING", "DAYCARE", "BOARDING"}:
            return {
                "status": "missing_information",
                "data": {"missing_fields": ["service_type"]},
                "message": "service_type is needed with an explicit booking_id.",
                "handoff_required": False,
            }
    else:
        latest = repo.get_latest_booking(company, customer)
        if latest.get("status") != "success":
            return {
                **latest,
                "message": (
                    latest.get("message")
                    or "No booking could be resolved for this customer."
                ),
            }
        latest_data = latest.get("data") or {}
        resolved_booking_id = latest_data.get("booking_id")
        normalized_service = str(
            latest_data.get("service_type")
            or latest_data.get("last_service_type")
            or ""
        ).strip().upper()

    if resolved_booking_id is None or normalized_service not in {"GROOMING", "DAYCARE", "BOARDING"}:
        return {
            "status": "error",
            "error": "BOOKING_REFERENCE_INCOMPLETE",
            "message": "The booking was found but its ID/service type could not be resolved.",
            "handoff_required": True,
            "handoff_reason": "DOCUMENT_DELIVERY_ERROR",
        }

    # get_booking_by_id performs the customer-ownership check; a model-supplied
    # booking ID can never be used to send another customer's document.
    booking_result = repo.get_booking_by_id(
        company, customer, int(resolved_booking_id), normalized_service
    )
    if booking_result.get("status") != "success":
        return {
            **booking_result,
            "message": (
                booking_result.get("message")
                or "That booking could not be found for this customer."
            ),
        }

    from app.documents.service import generate_and_send_booking_confirmation

    booking = dict(booking_result.get("data") or {})
    booking["service_type"] = normalized_service
    document_result = generate_and_send_booking_confirmation(company, booking)
    send_result = document_result.get("send_result") or {}
    delivery_status = str(send_result.get("status") or "")
    data = {
        "document_type": "booking_confirmation",
        "booking_id": int(resolved_booking_id),
        "service_type": normalized_service,
        "pet_name": booking.get("pet_name"),
        "delivery_status": delivery_status or document_result.get("status"),
        "provider": send_result.get("provider") or (
            "console" if delivery_status == "sent_console" else None
        ),
        # Kept out of model-facing evidence and customer prose. main.py reads
        # it from the authenticated trace and exposes a typed documents[] item
        # for the eval console; real WhatsApp delivery uses it server-side.
        "_internal_document_url": document_result.get("document_url"),
    }
    delivered = (
        document_result.get("status") == "success"
        and delivery_status in _DELIVERED_STATUSES
    )
    if delivered:
        return {
            "status": "success",
            "data": data,
            "message": "The booking confirmation document was dispatched successfully.",
            "handoff_required": False,
        }
    return {
        "status": "error",
        "data": data,
        "error": document_result.get("error") or send_result.get("error") or "DOCUMENT_NOT_DELIVERED",
        "message": "The confirmation PDF could not be delivered. Do not claim that it was sent.",
        "handoff_required": True,
        "handoff_reason": "DOCUMENT_DELIVERY_ERROR",
    }
