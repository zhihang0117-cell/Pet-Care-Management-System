"""
Small booking-support helpers used by relational_actions.py.

Trimmed from the original ai-backend booking_draft.py: this keeps only the
pure data-layer helpers (staff auto-selection, room price lookup, date
display) and drops the old intent-schema/session-shaped reply-building
functions that belonged to the retired intent/router pipeline.
"""

from __future__ import annotations

from datetime import date, timedelta

from .customer_context import get_relational_company_id


def _parse_iso_date(value: str | None) -> date | None:
    from .date_normalization import parse_customer_date

    return parse_customer_date(value)


def format_date_for_display(value: str | None) -> str:
    parsed = _parse_iso_date(value)
    if parsed:
        return f"{parsed.day} {parsed.strftime('%b %Y')}"
    text = str(value or "").strip()
    if text.lower() == "tomorrow":
        d = date.today() + timedelta(days=1)
        return f"{d.day} {d.strftime('%b %Y')}"
    return text or "your preferred date"


def select_staff_id(available_staff: list[dict]) -> int | None:
    staff_ids: list[int] = []
    for row in available_staff or []:
        raw = row.get("staff_id")
        if raw is None:
            continue
        try:
            staff_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return min(staff_ids) if staff_ids else None


def resolve_price_quote(service_type: str, entities: dict, company_id: int | None = None) -> float | None:
    """
    Return a numeric price only from approved relational sources (no LLM/RAG).
    Currently: boarding room nightly rate from the room table when room_type is known.
    """
    svc = str(service_type or "").strip().upper()
    if svc != "BOARDING":
        return None

    room_type = str(entities.get("room_type") or "").strip()
    if not room_type:
        return None

    try:
        from .supabase_client import get_supabase_client

        cid = company_id if company_id is not None else get_relational_company_id()
        client = get_supabase_client()
        response = (
            client.table("room")
            .select("price, room_type")
            .eq("company_id", cid)
            .eq("room_type", room_type)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        price = rows[0].get("price")
        if price is None:
            return None
        return float(price)
    except (TypeError, ValueError, ImportError):
        return None
