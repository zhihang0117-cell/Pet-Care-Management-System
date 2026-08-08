"""
Orchestrates: build the right PDF -> store it -> (attempt to) send it over
WhatsApp. Called deterministically right after a real DB write succeeds
(create_booking/reschedule_booking, or the invoice endpoint triggered by the
Node dashboard's mark-paid action) — never left to the LLM to remember to
"also send a confirmation", same reasoning as every other server-side
backstop in this codebase.

Every function here is best-effort: if PDF generation/storage/sending fails,
it returns a result dict describing that instead of raising — a document
delivery hiccup must never fail the underlying booking/payment write, which
already succeeded for real by the time this runs.
"""

from __future__ import annotations

from app.documents.company_profile import get_billing_profile
from app.documents.dispatch import send_whatsapp_document
from app.documents.pdf_builder import build_booking_confirmation_pdf, build_invoice_pdf
from app.documents.storage import upload_customer_document
from app.db.supabase_client import get_supabase_client


def _delivery_succeeded(send_result: dict) -> bool:
    """True only for send_whatsapp_document's real success statuses ("sent"
    from the live provider, "sent_console" from the local test outbox) —
    "error" and "not_configured" are failures. Both functions below used to
    return a hard-coded {"status": "success"} regardless of this value, so a
    failed/unconfigured WhatsApp send was reported as a delivered document.
    Mirrors the same guardrail applied to the notice endpoints in main.py."""
    return str((send_result or {}).get("status") or "") in {"sent", "sent_console"}


def _customer_for_pet(company_id: int, pet_id: int | None) -> dict:
    """{customer_id, customer_name, phone_number} for the customer who owns pet_id."""
    empty = {"customer_id": None, "customer_name": "", "phone_number": ""}
    if pet_id is None:
        return empty
    client = get_supabase_client()
    pet_rows = (
        client.table("pet")
        .select("customer_id")
        .eq("company_id", company_id)
        .eq("pet_id", int(pet_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not pet_rows:
        return empty
    customer_id = pet_rows[0].get("customer_id")
    customer_rows = (
        client.table("customer")
        .select("full_name, phone_number")
        .eq("company_id", company_id)
        .eq("customer_id", customer_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not customer_rows:
        return {**empty, "customer_id": customer_id}
    return {
        "customer_id": customer_id,
        "customer_name": customer_rows[0].get("full_name") or "",
        "phone_number": customer_rows[0].get("phone_number") or "",
    }


def _staff_name(company_id: int, staff_id: int | None) -> str:
    if staff_id is None:
        return ""
    rows = (
        get_supabase_client()
        .table("staff")
        .select("staff_name")
        .eq("company_id", company_id)
        .eq("staff_id", int(staff_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0].get("staff_name") or "" if rows else ""


def _loyalty_snapshot(company_id: int, customer_id: int | None) -> dict | None:
    """{points_balance, tier} for the customer, or None if not a loyalty member."""
    if customer_id is None:
        return None
    rows = (
        get_supabase_client()
        .table("loyaltymember")
        .select("points_balance, tier")
        .eq("company_id", company_id)
        .eq("customer_id", int(customer_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return None
    return {"points_balance": rows[0].get("points_balance"), "tier": rows[0].get("tier")}


def _redemption_for_payment(company_id: int, payment: dict) -> dict | None:
    """
    Voucher details for this SPECIFIC linked payment. request_redemption()
    creates the ledger row and payment.redemption_id in one transaction, so
    both pending requests and approved redemptions are visible here.
    """
    redemption_id = payment.get("redemption_id")
    if redemption_id is None:
        return None
    client = get_supabase_client()
    rows = (
        client.table("redemption")
        .select("loyalty_spend, coupon_id, status")
        .eq("company_id", company_id)
        .eq("redemption_id", int(redemption_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return None
    row = rows[0]
    reward_name = ""
    coupon_rows: list[dict] = []
    coupon_id = row.get("coupon_id")
    if coupon_id is not None:
        coupon_rows = (
            client.table("coupon")
            .select('reward_name, reward_type, "discount_value (RM)", points_required')
            .eq("company_id", company_id)
            .eq("coupon_id", int(coupon_id))
            .limit(1)
            .execute()
            .data
            or []
        )
        if coupon_rows:
            reward_name = coupon_rows[0].get("reward_name") or ""
    coupon = coupon_rows[0] if coupon_rows else {}
    return {
        "points_spent": row.get("loyalty_spend") or coupon.get("points_required") or 0,
        "reward_name": reward_name,
        "reward_type": coupon.get("reward_type"),
        "discount_value": coupon.get("discount_value (RM)"),
        "status": row.get("status"),
    }


def _fill_pet_name(company_id: int, booking: dict) -> dict:
    if booking.get("pet_name") or booking.get("pet_id") is None:
        return booking
    rows = (
        get_supabase_client()
        .table("pet")
        .select("pet_name")
        .eq("company_id", company_id)
        .eq("pet_id", int(booking["pet_id"]))
        .limit(1)
        .execute()
        .data
        or []
    )
    if rows:
        return {**booking, "pet_name": rows[0].get("pet_name")}
    return booking


def _payment_status_for(company_id: int, booking: dict) -> str:
    """The linked payment's real status ("Pending" until staff verify it,
    "Paid", "Refunded", etc).

    booking_status is "Pending"/"Scheduled" at this exact point regardless
    of path — every booking starts there — so it tells the customer nothing
    they don't already know from having just booked. payment_status is what
    they actually still need to act on. create_booking's own result already
    carries payment_status (fetched at write time); this only re-fetches it
    when a caller's booking dict didn't already have it (reschedule_booking,
    and document_tools.py's on-demand "resend my confirmation" path)."""
    if booking.get("payment_status"):
        return str(booking["payment_status"])
    payment_id = booking.get("payment_id")
    if payment_id is None:
        return "Pending"
    rows = (
        get_supabase_client()
        .table("payment")
        .select("status")
        .eq("company_id", company_id)
        .eq("payment_id", int(payment_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    return str(rows[0].get("status")) if rows and rows[0].get("status") else "Pending"


def generate_and_send_booking_confirmation(company_id: int, booking: dict, redemption: dict | None = None) -> dict:
    """booking: the persisted record returned by create_booking/
    reschedule_booking (has booking_id, service_type, pet_id, pet_name,
    staff_id, package_name, dates/times, price, booking_status)."""
    try:
        booking = _fill_pet_name(company_id, booking)
        profile = get_billing_profile(company_id)
        customer = _customer_for_pet(company_id, booking.get("pet_id"))
        booking = {
            **booking,
            "staff_name": _staff_name(company_id, booking.get("staff_id")),
            "payment_status": _payment_status_for(company_id, booking),
        }
        loyalty = _loyalty_snapshot(company_id, customer["customer_id"])
        pdf_bytes = build_booking_confirmation_pdf(
            profile, booking, customer["customer_name"], loyalty, redemption
        )
        # booking_id is only unique WITHIN one service's table
        # (grooming_booking_id/daycare_booking_id/boarding_booking_id are
        # three independent auto-increment sequences) — using it bare here
        # let a GROOMING #7 and a DAYCARE #7 (different customers) collide
        # on the exact same storage path. Since upload_customer_document
        # upserts, the second upload silently overwrote the first, so an
        # already-sent confirmation link could later resolve to a totally
        # different customer's booking. service_type makes the path unique
        # per booking again.
        service_slug = str(booking.get("service_type") or "booking").strip().lower()
        filename = f"{service_slug}-{booking.get('booking_id')}.pdf"
        document_url = upload_customer_document(company_id, "booking-confirmations", filename, pdf_bytes)
        send_result = send_whatsapp_document(
            customer["phone_number"],
            document_url,
            f"Your booking confirmation — BC-{str(booking.get('service_type') or 'BOOKING').upper()}-{booking.get('booking_id')}",
        )
        return {
            "status": "success" if _delivery_succeeded(send_result) else "delivery_failed",
            "document_url": document_url,
            "send_result": send_result,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def regenerate_booking_confirmation_for_payment(company_id: int, payment_id: int) -> dict:
    """Regenerate the same confirmation after a voucher request is linked.

    The first PDF is sent immediately after booking creation, before the
    loyalty choice. This second deterministic pass updates that document to
    show the requested points/discount and its Pending/Approved state.
    """
    client = get_supabase_client()
    payment_rows = (
        client.table("payment").select("*")
        .eq("company_id", company_id).eq("payment_id", payment_id).limit(1).execute().data or []
    )
    if not payment_rows:
        return {"status": "error", "error": f"payment_id {payment_id} not found"}
    payment = dict(payment_rows[0])
    redemption = _redemption_for_payment(company_id, payment)
    if redemption is None:
        return {"status": "error", "error": "payment has no linked redemption"}

    for service_type, table in (
        ("GROOMING", "grooming_booking"),
        ("DAYCARE", "daycare_booking"),
        ("BOARDING", "boarding_booking"),
    ):
        rows = (
            client.table(table).select("*").eq("company_id", company_id)
            .eq("payment_id", payment_id).limit(1).execute().data or []
        )
        if rows:
            from app.db.relational_actions import _serialize_booking_row

            booking = _serialize_booking_row(table, rows[0])
            booking["service_type"] = service_type
            return generate_and_send_booking_confirmation(company_id, booking, redemption)
    return {"status": "error", "error": "No booking is linked to this payment"}


def generate_and_send_invoice(company_id: int, payment: dict, booking: dict, *, send: bool = True) -> dict:
    """payment: a real row from the `payment` table (payment_id, service,
    base_price, add_ons, final_amount, payment_method, date, status,
    redemption_id). booking: the linked booking row (for pet_name/
    booking_id/staff_id context) — may be {} if it couldn't be resolved;
    the invoice still generates without it.

    send=False regenerates/re-uploads the PDF and returns its signed URL
    without dispatching a WhatsApp message — used by the dashboard's own
    View/Print Invoice buttons, which must not re-notify the customer every
    time staff looks at their own copy of a document already sent once."""
    try:
        booking = _fill_pet_name(company_id, booking)
        profile = get_billing_profile(company_id)
        customer = _customer_for_pet(company_id, booking.get("pet_id"))
        booking = {**booking, "staff_name": _staff_name(company_id, booking.get("staff_id"))}
        loyalty = _loyalty_snapshot(company_id, customer["customer_id"])
        redemption = _redemption_for_payment(company_id, payment)
        pdf_bytes = build_invoice_pdf(profile, payment, booking, customer["customer_name"], loyalty, redemption)
        filename = f"invoice-{payment.get('payment_id')}.pdf"
        document_url = upload_customer_document(company_id, "invoices", filename, pdf_bytes)
        if not send:
            return {"status": "success", "document_url": document_url}
        send_result = send_whatsapp_document(
            customer["phone_number"],
            document_url,
            f"Your invoice — {profile['invoice_prefix']}-{payment.get('payment_id')}",
        )
        return {
            "status": "success" if _delivery_succeeded(send_result) else "delivery_failed",
            "document_url": document_url,
            "send_result": send_result,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
