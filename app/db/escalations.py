"""Persist customer enquiries that require a real staff follow-up."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.supabase_client import get_supabase_client
from app.db.time_normalization import BUSINESS_TIMEZONE


def save_staff_enquiry(
    company_id: int | str,
    customer_id: int | None,
    user_message: str,
    handoff_reason: str | None,
) -> dict:
    """Insert one pending row into ``messages`` and verify it was returned.

    This deliberately raises on failure. Callers may keep the customer chat
    alive, but they must not silently claim that staff were notified when the
    dashboard row was never created.
    """
    if customer_id is None:
        raise ValueError("Cannot create a staff enquiry before customer identity is resolved")
    message = str(user_message or "").strip()
    if not message:
        raise ValueError("Cannot create a staff enquiry from an empty message")

    # Business-local (Asia/Kuala_Lumpur), not naive server time — the
    # container runs in UTC, so a bare datetime.now() recorded a
    # receive_date/receive_time up to 8 hours off (and, near midnight
    # either side, the wrong calendar date) on the staff dashboard.
    now = datetime.now(ZoneInfo(BUSINESS_TIMEZONE))
    payload = {
        "company_id": int(company_id),
        "sender_type": "customer",
        "sender_id": int(customer_id),
        "message_text": f"[Needs staff follow-up — {handoff_reason or 'handoff'}] {message}",
        "intent_label": "handoff",
        "receive_date": now.date().isoformat(),
        "receive_time": now.strftime("%H:%M:%S"),
        "reply_date": None,
        "reply_time": None,
        "reply_text": None,
        "replied_by_staff_id": None,
    }
    rows = get_supabase_client().table("messages").insert(payload).execute().data or []
    if not rows:
        raise RuntimeError("Staff enquiry insert returned no row")
    saved = dict(rows[0])
    if int(saved.get("sender_id") or 0) != int(customer_id):
        raise RuntimeError("Staff enquiry read-back did not match the customer")
    return saved
