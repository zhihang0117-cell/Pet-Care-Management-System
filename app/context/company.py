from __future__ import annotations

from app.db.customer_context import CustomerContext
from app.db.relational_actions import SLOT_MINUTES

TIMEZONE = "Asia/Kuala_Lumpur"  # matches time_normalization.BUSINESS_TIMEZONE

_DAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def _real_business_hours(company_id: int) -> list[dict]:
    """
    Real per-day hours from company_business_hours — hours genuinely vary by
    day for this company (e.g. shorter Saturday, closed Sunday), so a single
    flat open/close window here would be wrong on most days. Missing or
    incomplete rows stay explicitly unconfigured; runtime context must not
    teach the model invented fallback hours.
    """
    from app.db.supabase_client import get_supabase_client

    rows = (
        get_supabase_client()
        .table("company_business_hours")
        .select("day_of_week, open_time, close_time, is_closed")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    by_day = {row["day_of_week"]: row for row in rows}
    schedule = []
    for day_of_week, day_name in enumerate(_DAY_NAMES):
        row = by_day.get(day_of_week)
        if row is None:
            schedule.append({"day": day_name, "configured": False})
        elif row.get("is_closed"):
            schedule.append({"day": day_name, "closed": True})
        else:
            open_time = str(row.get("open_time") or "").strip()[:5]
            close_time = str(row.get("close_time") or "").strip()[:5]
            schedule.append(
                ({
                    "day": day_name,
                    "open": open_time,
                    "close": close_time,
                } if open_time and close_time else {"day": day_name, "configured": False})
            )
    return schedule


def get_company_config(company_id: str) -> dict:
    """Load the current company's safe public profile and operating hours.

    This runs for every chat request, so company profile answers reflect the
    current Supabase row instead of session memory or RAG guesses.
    """
    from app.db.relational_actions import get_company_information

    context = CustomerContext(company_id=int(company_id))
    result = get_company_information(context)
    company = (result.get("data") or {}).get("company") or {}
    address_parts = [
        company.get("street_address"),
        company.get("city"),
        company.get("state"),
        company.get("postcode"),
        company.get("country"),
    ]
    formatted_address = ", ".join(
        str(part).strip() for part in address_parts if str(part or "").strip()
    )

    return {
        "company_id": str(company_id),
        # Keep the read outcome explicit: an unavailable DB read must not look
        # like a company intentionally left every profile field blank.
        "company_profile_status": result.get("status") or "error",
        "company_name": company.get("company_name"),
        "business_description": company.get("business_description"),
        "street_address": company.get("street_address"),
        "city": company.get("city"),
        "state": company.get("state"),
        "postcode": company.get("postcode"),
        "country": company.get("country"),
        "address": formatted_address or None,
        "business_hours": _real_business_hours(int(company_id)),
        "slot_minutes": SLOT_MINUTES,
        "timezone": TIMEZONE,
    }
