"""
Text-only WhatsApp notices for staff-side status changes made from the
dashboard (Node backend) — bookings.js PATCH /:type/:id and redemptions.js
POST /:id/decision. Neither of those previously told the customer (or this
Python system) anything happened; these builders turn a bare status
transition into the right customer-facing message, deterministically (not
via an LLM call) — same "don't leave a must-happen customer message to
chance" reasoning as every other server-side backstop in this codebase.

Every build_* function returns None when the transition isn't one that
needs a notice (e.g. Scheduled -> Done, which never actually happens), so
the caller can just skip sending anything rather than special-casing "no
message" itself.
"""

from __future__ import annotations

from app.db.supabase_client import get_supabase_client
from app.documents.company_profile import get_billing_profile
from app.documents.service import _customer_for_pet, _staff_name  # noqa: F401 (staff_name unused today, kept for parity)

_SERVICE_TABLES = {
    "GROOMING": ("grooming_booking", "grooming_booking_id", "booking_date", "booking_time", "service_name"),
    "DAYCARE": ("daycare_booking", "daycare_booking_id", "booking_date", "check_in_time", "package_type"),
    "BOARDING": ("boarding_booking", "boarding_booking_id", "check_in_date", "check_in_time", "room_type"),
}


def _load_booking(company_id: int, service_type: str, booking_id: int) -> dict | None:
    spec = _SERVICE_TABLES.get(str(service_type or "").strip().upper())
    if spec is None:
        return None
    table, id_col, date_col, time_col, package_col = spec
    rows = (
        get_supabase_client()
        .table(table)
        .select("*")
        .eq("company_id", company_id)
        .eq(id_col, int(booking_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "booking_id": row.get(id_col),
        "pet_id": row.get("pet_id"),
        "package_name": row.get(package_col),
        "date": row.get(date_col),
        "time": row.get(time_col),
    }


def _norm(status: str) -> str:
    return str(status or "").strip().lower().replace("_", " ")


def build_booking_status_notice(
    company_id: int, service_type: str, booking_id: int, old_status: str, new_status: str
) -> dict | None:
    """
    {"phone_number", "message", "pet_name"} for the transitions that need
    one, else None:
    - Scheduled -> Pending: the appointment day has arrived — remind the customer.
    - Pending -> No Show: they didn't turn up — ask what happened / rebook.
    - Pending -> Done: service is finished — invite them to pick up their pet.
    - (Scheduled|Pending) -> Cancelled: staff cancelled it from the dashboard
      — the customer would otherwise never find out at all (a customer-
      initiated cancellation via chat already gets told directly in that
      same conversation, so this only needs to cover the staff-side path).
    """
    old_s, new_s = _norm(old_status), _norm(new_status)
    tracked_transitions = {
        ("scheduled", "pending"),
        ("pending", "no show"),
        ("pending", "done"),
        ("scheduled", "cancelled"),
        ("pending", "cancelled"),
    }
    if (old_s, new_s) not in tracked_transitions:
        return None

    booking = _load_booking(company_id, service_type, booking_id)
    if booking is None:
        return None
    customer = _customer_for_pet(company_id, booking.get("pet_id"))
    if not customer.get("phone_number"):
        return None

    pet_rows = (
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
    pet_name = pet_rows[0].get("pet_name") if pet_rows else ""
    service_label = str(service_type or "").strip().title()
    package = booking.get("package_name") or service_label
    name = customer.get("customer_name") or "there"

    if (old_s, new_s) == ("scheduled", "pending"):
        message = (
            f"Hi {name}! Just a reminder — {pet_name}'s {package} is today at "
            f"{booking.get('time') or 'the scheduled time'}. See you soon! 🐾"
        )
    elif (old_s, new_s) == ("pending", "no show"):
        message = (
            f"Hi {name}, we noticed {pet_name} didn't make it in for today's {package} "
            "appointment. Is everything okay? Let us know if you'd like to reschedule, "
            "or if there's anything else we can help with."
        )
    elif new_s == "cancelled":
        message = (
            f"Hi {name}, we're sorry to let you know that {pet_name}'s {package} booking "
            f"on {booking.get('date') or 'the scheduled date'} has been cancelled. Please "
            "reach out if you'd like to rebook or have any questions."
        )
    else:  # pending -> done
        message = (
            f"Hi {name}! {pet_name}'s {package} is all done ✅ — feel free to come by "
            "anytime to pick them up. Thank you for choosing us!"
        )

    return {"phone_number": customer["phone_number"], "message": message, "pet_name": pet_name}


def build_redemption_decision_notice(company_id: int, redemption_id: int, new_status: str) -> dict | None:
    """
    {"phone_number", "message"} for a redemption's Approved/Rejected
    decision, else None. Reflects the REAL decide_redemption() SQL
    function's actual behaviour (backend/sql/verify_payment_function.sql):
    points are only deducted at Approval time, not when the redemption was
    first requested — a Rejected redemption never touched the customer's
    balance in the first place, so there is nothing to "refund".
    """
    status = _norm(new_status)
    if status not in ("approved", "rejected"):
        return None

    client = get_supabase_client()
    redemption_rows = (
        client.table("redemption")
        .select("loyalty_id, coupon_id, loyalty_spend")
        .eq("company_id", company_id)
        .eq("redemption_id", int(redemption_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not redemption_rows:
        return None
    redemption = redemption_rows[0]

    loyalty_rows = (
        client.table("loyaltymember")
        .select("customer_id, points_balance")
        .eq("company_id", company_id)
        .eq("loyalty_id", redemption.get("loyalty_id"))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not loyalty_rows:
        return None
    customer_id = loyalty_rows[0].get("customer_id")
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
    if not customer_rows or not customer_rows[0].get("phone_number"):
        return None
    name = customer_rows[0].get("full_name") or "there"
    phone_number = customer_rows[0]["phone_number"]

    reward_name = ""
    coupon_id = redemption.get("coupon_id")
    if coupon_id is not None:
        coupon_rows = (
            client.table("coupon")
            .select("reward_name")
            .eq("company_id", company_id)
            .eq("coupon_id", int(coupon_id))
            .limit(1)
            .execute()
            .data
            or []
        )
        if coupon_rows:
            reward_name = coupon_rows[0].get("reward_name") or ""
    reward_label = reward_name or "loyalty redemption"

    if status == "approved":
        balance = loyalty_rows[0].get("points_balance")
        message = (
            f"Good news, {name}! Your {reward_label} redemption has been approved. "
            f"Your updated points balance is {balance}."
        )
    else:
        message = (
            f"Hi {name}, your {reward_label} redemption request couldn't be approved this time. "
            "No points were deducted for this request. Please reach out if you have any questions!"
        )

    return {"phone_number": phone_number, "message": message}


def build_payment_refund_notice(company_id: int, payment_id: int) -> dict | None:
    """
    {"phone_number", "message"} once a payment is refunded (see
    backend/sql/enquiry_refund_logo_migration.sql refund_payment()) —
    another staff-side action with previously zero customer-facing
    follow-up. Returns None if the payment/its owning customer can't be
    resolved (never raises — a notice is best-effort, the refund itself
    already happened for real by the time this runs).
    """
    client = get_supabase_client()
    payment_rows = (
        client.table("payment")
        .select("payment_id, service, final_amount, status, refund_reason")
        .eq("company_id", company_id)
        .eq("payment_id", int(payment_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not payment_rows or _norm(payment_rows[0].get("status")) != "refunded":
        return None
    payment = payment_rows[0]

    pet_id = None
    for table, id_col, _date_col, _time_col, _package_col in _SERVICE_TABLES.values():
        rows = (
            client.table(table)
            .select("pet_id")
            .eq("company_id", company_id)
            .eq("payment_id", int(payment_id))
            .limit(1)
            .execute()
            .data
            or []
        )
        if rows:
            pet_id = rows[0].get("pet_id")
            break
    if pet_id is None:
        return None

    customer = _customer_for_pet(company_id, pet_id)
    if not customer.get("phone_number"):
        return None

    name = customer.get("customer_name") or "there"
    amount = payment.get("final_amount") or 0
    # Was hardcoded "RM" regardless of the company's actual configured
    # currency (see company_profile.get_billing_profile) — wrong for any
    # SGD/THB company.
    currency = get_billing_profile(company_id)["currency"]
    reason = str(payment.get("refund_reason") or "").strip()
    reason_clause = f" ({reason})" if reason else ""
    message = (
        f"Hi {name}, your payment for {payment.get('service') or 'your booking'} "
        f"({currency}{amount}) has been refunded{reason_clause}. Please allow a few "
        "business days for it to reflect, depending on your payment method."
    )
    return {"phone_number": customer["phone_number"], "message": message}
