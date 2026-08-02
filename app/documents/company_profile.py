"""
Real company fields used to "skin" the shared PDF layouts (see
pdf_builder.py) — one fixed layout, parameterized per company from real
columns: companies.logo_path (Supabase Storage public URL) and
companies.settings_json (invoice_prefix, tax_name, currency — see
backend/sql/company_settings_migration.sql for the full key list this
column was designed to hold).
"""

from __future__ import annotations

import re

from app.db.supabase_client import get_supabase_client

# setting.html's currency <select> stores the whole display option, e.g.
# "MYR (RM)"/"SGD (S$)"/"THB (฿)" (see web/setting.html cfg_currency) —
# not just a plain code. Used as-is, PDF table headers rendered as
# "Price · MYR (RM)" (confirmed on the real Happy Paws Center company row),
# which duplicates the code and reads oddly. Pull out just the parenthesized
# symbol for display; fall back to the raw value if it isn't in that shape
# (e.g. the "MYR" default below, or a value set some other way).
_CURRENCY_SYMBOL_RE = re.compile(r"\(([^)]+)\)\s*$")


def _currency_symbol(raw: str) -> str:
    match = _CURRENCY_SYMBOL_RE.search(raw)
    return match.group(1) if match else raw


def get_billing_profile(company_id: int) -> dict:
    rows = (
        get_supabase_client()
        .table("companies")
        .select(
            "company_name, country, street_address, city, state, postcode, "
            "logo_path, settings_json"
        )
        .eq("company_id", company_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    row = rows[0] if rows else {}
    settings = row.get("settings_json") or {}
    address_parts = [
        row.get("street_address"),
        row.get("city"),
        row.get("state"),
        row.get("postcode"),
        row.get("country"),
    ]
    return {
        "company_name": row.get("company_name") or "Our Business",
        "address": ", ".join(part for part in address_parts if part),
        "logo_path": row.get("logo_path") or None,
        # Falls back to a plain "INV"/"BC" prefix if the company hasn't set
        # one in setting.html yet — never blocks document generation.
        "invoice_prefix": (settings.get("invoice_prefix") or "").strip() or "INV",
        "tax_name": (settings.get("tax_name") or "").strip(),
        "currency": _currency_symbol((settings.get("currency") or "MYR").strip()),
    }
