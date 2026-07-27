"""
Customer identity context for relational database actions.

In production WhatsApp flow, phone_number comes from webhook sender metadata —
not from the customer message text. Local /chat testing may pass phone_number
optionally in the request body.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field

from dotenv import load_dotenv


def normalize_phone_digits(phone: str) -> str:
    """Keep digits only for fuzzy phone comparison."""
    return re.sub(r"\D", "", phone or "")


def phones_match(left: str, right: str) -> bool:
    """Match phone numbers with or without country/spacing formatting."""
    left_digits = normalize_phone_digits(left)
    right_digits = normalize_phone_digits(right)
    if not left_digits or not right_digits:
        return False
    if left_digits == right_digits:
        return True
    return left_digits.endswith(right_digits) or right_digits.endswith(left_digits)


def get_relational_company_id() -> int:
    load_dotenv(override=not os.getenv("_EVAL_OVERRIDE_ACTIVE"))
    raw = os.getenv("RELATIONAL_COMPANY_ID") or os.getenv("RAG_COMPANY_ID") or "1"
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return 1


@dataclass
class CustomerContext:
    """
    Request/session identity context for database actions.

    phone_number: WhatsApp sender ID or optional local test field.
    request_customer_id: legacy/demo request field (string).
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

    def to_session_dict(self) -> dict:
        return {
            "phone_number": self.phone_number,
            "request_customer_id": self.request_customer_id,
            "resolved_customer_id": self.resolved_customer_id,
            "company_id": self.company_id,
            "customer_found": self.customer_record is not None,
        }


def apply_request_context_to_intent(intent_json: dict, phone_number: str, customer_id: str = "") -> dict:
    """
    Merge webhook/request identity into intent_json without changing the prompt.

    If phone_number is already available in request context, do not treat it as
    missing_information for routing/database actions.
    """
    updated = copy.deepcopy(intent_json)
    phone = str(phone_number or "").strip()
    request_customer_id = str(customer_id or "").strip()

    missing = list(updated.get("missing_information") or [])
    if phone and "phone_number" in missing:
        missing = [field_name for field_name in missing if field_name != "phone_number"]
    updated["missing_information"] = missing

    entities = dict(updated.get("entities") or {})
    if phone and not str(entities.get("phone_number", "")).strip():
        entities["phone_number"] = phone
    if request_customer_id and not str(entities.get("customer_identifier", "")).strip():
        entities["customer_identifier"] = request_customer_id
    updated["entities"] = entities
    return updated


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
