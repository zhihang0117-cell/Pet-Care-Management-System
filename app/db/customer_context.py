"""
Customer identity context for relational database actions.

In production WhatsApp flow, phone_number comes from webhook sender metadata —
not from the customer message text.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from dotenv import load_dotenv


def normalize_phone_digits(phone: str) -> str:
    """Keep digits only for storage-format-independent comparison."""
    return re.sub(r"\D", "", phone or "")


def validate_phone_number(phone: str) -> str:
    """Validate a customer/WhatsApp number without forcing one display format.

    E.164 allows at most 15 digits. Eight digits is the minimum accepted here
    so a short suffix can never become an identity credential. Formatting
    characters are accepted, but alphabetic/extension text is not.
    """
    raw = str(phone or "").strip()
    if not raw or not re.fullmatch(r"\+?[0-9][0-9\s().-]*", raw):
        raise ValueError("phone_number must contain a valid international or local phone number")
    digits = normalize_phone_digits(raw)
    if not 8 <= len(digits) <= 15:
        raise ValueError("phone_number must contain between 8 and 15 digits")
    return raw


def _canonical_phone_digits(phone: str) -> str:
    digits = normalize_phone_digits(phone)
    if digits.startswith("00"):
        digits = digits[2:]
    # This deployment is Malaysian. Treat 01x... and +601x... as the same
    # number, but never use generic suffix matching ("6705" must not match a
    # stranger's +60123456705).
    country_code = re.sub(r"\D", "", os.getenv("DEFAULT_PHONE_COUNTRY_CODE", "60"))
    if country_code and digits.startswith("0"):
        digits = country_code + digits[1:]
    return digits


def canonical_phone_number(phone: str) -> str:
    """Return one stable E.164-like key after validating the number."""
    validate_phone_number(phone)
    return f"+{_canonical_phone_digits(phone)}"


def phones_match(left: str, right: str) -> bool:
    """Exact normalized match, allowing only local/international prefix equivalence."""
    try:
        validate_phone_number(left)
        validate_phone_number(right)
    except ValueError:
        return False
    return _canonical_phone_digits(left) == _canonical_phone_digits(right)


def get_relational_company_id() -> int:
    load_dotenv(override=not os.getenv("_EVAL_OVERRIDE_ACTIVE"))
    raw = os.getenv("RELATIONAL_COMPANY_ID") or os.getenv("RAG_COMPANY_ID") or "1"
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return 1


def resolve_company_id_from_chat_key(chat_api_key: str) -> int | None:
    """
    Look up which company a presented X-Chat-Key belongs to, via the
    company_chat_key table (see backend/sql/chat_api_key_migration.sql).
    Returns None if the key is blank, the table doesn't exist yet (fresh
    deployment that hasn't run the migration), or no row matches — callers
    must fall back to the legacy single-company CHAT_API_KEY env var in that
    case, never invent/guess a company_id from an unmatched key.
    """
    key = str(chat_api_key or "").strip()
    if not key:
        return None
    try:
        from app.db.supabase_client import get_supabase_client

        rows = (
            get_supabase_client()
            .table("company_chat_key")
            .select("company_id")
            .eq("chat_api_key", key)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        # Table not migrated yet, or a transient DB error — treat exactly
        # like "no match" so the legacy env-var path still works instead of
        # 500ing every /chat request on a deployment that hasn't run the
        # migration.
        return None
    if not rows:
        return None
    try:
        return int(rows[0]["company_id"])
    except (KeyError, TypeError, ValueError):
        return None


@dataclass
class CustomerContext:
    """
    Identity context threaded through every relational_actions call.

    phone_number: WhatsApp sender ID.
    resolved_customer_id: numeric customer_id after lookup.
    """

    phone_number: str = ""
    request_customer_id: str = ""
    company_id: int = field(default_factory=get_relational_company_id)
    resolved_customer_id: int | None = None
    customer_record: dict | None = None

    @property
    def has_phone(self) -> bool:
        return bool(str(self.phone_number or "").strip())

    @property
    def has_identity(self) -> bool:
        return self.resolved_customer_id is not None


def missing_identity_result(action: str) -> dict:
    return {
        "action": action,
        "status": "missing_information",
        "success": True,
        "data_found": False,
        "data": {"required_fields": ["phone_number"]},
        "error": None,
        "handoff_required": False,
        "handoff_reason": None,
    }
