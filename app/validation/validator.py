"""
Vaccination-policy validation gate.

The actual pre-write checks (required fields, ownership, availability
re-check, atomic insert + persisted-row verification) happen for real inside
app/db/relational_actions.py (create_booking/cancel_booking/reschedule_booking
/redeem_reward) — this module does not duplicate that logic. ValidationResult
is used directly by check_vaccination_eligibility below.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    handoff_required: bool = False
    handoff_reason: str | None = None


# Company vaccination policy (service_information/policies documents, via
# RAG) is NOT uniform across services:
# - GROOMING: "Pets without full vaccination may still be groomed, but the
#   owner assumes related risks" — explicitly not a hard requirement.
# - DAYCARE: "Dogs should be up to date on their vaccinations... required to
#   protect the safety of all dogs" — a real requirement.
# - BOARDING: "Pets must complete required vaccinations before check-in...
#   may not be allowed to stay" — the strictest, explicit hard requirement.
# Only DAYCARE/BOARDING are gated here for that reason.
_VACCINATION_REQUIRED_SERVICE_TYPES = {"DAYCARE", "BOARDING"}


def check_vaccination_eligibility(
    company_id: int, pet_id: int, service_type: str, service_date: str
) -> ValidationResult:
    """
    Verify a pet meets company vaccination policy for a DAYCARE/BOARDING
    booking on service_date (the check-in date). Applies equally to a
    pet just registered this session and one on record for years — a
    vaccination can lapse at any time, so this is not a one-time
    new-customer-only check (see create_booking/reschedule_booking, both of
    which call this before writing).
    """
    category = str(service_type or "").strip().upper()
    if category not in _VACCINATION_REQUIRED_SERVICE_TYPES:
        return ValidationResult(ok=True)

    from app.db.supabase_client import get_supabase_client
    from app.db.date_normalization import parse_customer_date

    rows = (
        get_supabase_client()
        .table("pet")
        .select("pet_name, vaccination_status, vaccination_expired_date")
        .eq("company_id", company_id)
        .eq("pet_id", pet_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    pet = rows[0] if rows else {}
    pet_name = pet.get("pet_name") or "This pet"

    status = str(pet.get("vaccination_status") or "").strip().lower()
    if status != "vaccinated":
        return ValidationResult(
            ok=False,
            errors=[
                f"{pet_name} is not marked as vaccinated "
                f"({pet.get('vaccination_status') or 'Unknown'}) — company policy requires "
                f"an up-to-date vaccination before {category.title()} check-in."
            ],
        )

    expiry = parse_customer_date(pet.get("vaccination_expired_date"))
    check_date = parse_customer_date(service_date)
    if expiry and check_date and expiry <= check_date:
        return ValidationResult(
            ok=False,
            errors=[
                f"{pet_name}'s vaccination expires on {pet.get('vaccination_expired_date')}, "
                f"on or before the {category.title()} date ({service_date}) — company policy "
                "requires it to still be valid at check-in."
            ],
        )
    return ValidationResult(ok=True)
