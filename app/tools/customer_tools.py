from __future__ import annotations

from langchain_core.tools import tool
from typing import Literal

ServiceType = Literal["GROOMING", "DAYCARE", "BOARDING"]

from app.db.customer_context import CustomerContext
from app.db.relational_provider import get_relational_repository
from app.tools.booking_window import today_business


def _repo():
    return get_relational_repository()


# Real height breakpoints from the company's own grooming price tables
# (service_information.docx, via retrieve_policy) — cat and dog are sized
# on different scales. Kept here so a new pet's size is COMPUTED from an
# objective measurement instead of trusted from whatever the model or
# customer says a size "is"; grooming pricing is size-tiered, so a wrong
# size directly means a wrong quoted price later.
_CAT_SIZE_BREAKPOINTS = [(20, "S"), (40, "M"), (60, "L")]  # cm, exclusive upper bound; above last -> XL
_CAT_MAX_SIZE = "XL"
_DOG_SIZE_BREAKPOINTS = [(25, "XS"), (40, "S"), (55, "M"), (70, "L"), (85, "XL")]
_DOG_MAX_SIZE = "XXL"


def compute_verified_pet_size(pet_type: str, height_cm: int | float | None) -> str | None:
    """
    Derive the real size tier (S/M/L/... ) from a pet's species + height_cm,
    per the company's own grooming price breakpoints. Returns None if
    height_cm is missing or pet_type isn't cat/dog (no verified breakpoints
    exist for other species) — callers must not invent a size in that case.
    """
    if height_cm is None:
        return None
    try:
        height = float(height_cm)
    except (TypeError, ValueError):
        return None

    species = str(pet_type or "").strip().lower()
    if species == "cat":
        breakpoints, max_size = _CAT_SIZE_BREAKPOINTS, _CAT_MAX_SIZE
    elif species == "dog":
        breakpoints, max_size = _DOG_SIZE_BREAKPOINTS, _DOG_MAX_SIZE
    else:
        return None

    for upper_bound, size in breakpoints:
        if height < upper_bound:
            return size
    return max_size


@tool
def create_customer(
    company_id: str | int, full_name: str, phone_number: str, address: str = ""
) -> dict:
    """
    Register a brand-new customer (RUNTIME_CONTEXT.customer.found is false).
    Call this once you have their name — phone_number is already known from
    RUNTIME_CONTEXT, never ask the customer for it. address is optional.

    Returns {"status": "error", ...} if a customer already exists for this
    phone number — do not retry with a different name, that means identity
    resolution already found them (should not normally happen since
    RUNTIME_CONTEXT.customer.found would have been true).
    """
    return _repo().create_customer(int(company_id), full_name, str(phone_number), address)


@tool
def create_pet(
    company_id: str | int,
    customer_id: str | int,
    pet_name: str,
    pet_type: str,
    height_cm: int | float,
    breed: str,
) -> dict:
    """
    Register a new pet for an existing customer. breed and height_cm are
    REQUIRED, but breed is not restricted to a fixed list. Preserve the
    customer's wording; pass "mixed" for a mixed breed or "unknown" when the
    customer explicitly says they do not know. Never infer a breed from species,
    size, name, or appearance.

    The pet's size (S/M/L/XL/...) is computed from height_cm against the company's real
    grooming price breakpoints, never guessed or asked as a separate free-text
    field. If you don't know the pet's exact height, ask the customer for it
    (e.g. "about how tall is Milo, roughly, in cm?") rather than assuming a
    size — an unverified size risks quoting the wrong grooming price later.

    pet_type must be "cat" or "dog" for a verified size to be computed; other
    species are still recorded but size stays unset (no grooming price
    breakpoints exist for them in this company's documents).
    """
    from app.db.relational_repository import PetData

    verified_size = compute_verified_pet_size(pet_type, height_cm)
    pet_data = PetData(
        pet_name=pet_name,
        pet_type=pet_type,
        pet_size=verified_size or "",
        height_cm=int(height_cm) if height_cm is not None else None,
        breed=breed,
    )
    result = _repo().create_pet(int(company_id), int(customer_id), pet_data)
    if result.get("status") == "success":
        result = {**result, "verified_size": verified_size}
    return result


@tool
def update_pet_vaccination(
    company_id: str | int, customer_id: str | int, pet_id: str | int, vaccination_expiry_text: str
) -> dict:
    """
    Record a customer's self-reported vaccination update for their pet
    (e.g. "I just got him revaccinated, expires next year 30 August").
    Call this to actually persist the new expiry date and mark the pet
    vaccinated — do NOT just tell the customer their pet is now eligible
    without calling this first; that fact isn't real until this succeeds.

    Pass the customer's own date wording as vaccination_expiry_text (e.g.
    "next year 30 august", "30/08/2027") — this resolves it internally the
    same way resolve_datetime does. Do not pre-resolve or compute it
    yourself first; pass their words through as-is (the model has been
    observed computing its own, wrong date here instead of letting the
    resolver handle it — same risk resolve_datetime's own rule warns about).

    This is accepted on the customer's word alone over chat — company
    policy still requires a physical vaccination certificate at check-in,
    so tell the customer that too; this update just lets the booking
    proceed now and leaves a note on the pet's record for staff to verify
    the certificate against at check-in, it does not skip that check.
    """
    from app.db.date_normalization import extract_customer_date

    parsed = extract_customer_date(vaccination_expiry_text, today=today_business())
    if parsed is None:
        return {
            "error": "INVALID_DATE",
            "message": (
                f"Could not resolve a date from {vaccination_expiry_text!r}. Ask the "
                "customer for a clearer date (a specific day and month, e.g. "
                "'30 August' or '30/08/2027') and retry."
            ),
        }

    pet_lookup = _repo().list_customer_pets(int(company_id), int(customer_id))
    current_notes = ""
    if pet_lookup.get("status") == "success":
        for pet in (pet_lookup.get("data") or {}).get("pets", []):
            if int(pet.get("pet_id") or -1) == int(pet_id):
                current_notes = str(pet.get("service_notes") or "").strip()
                break

    flag_note = (
        f"Vaccination updated via customer self-report on {today_business().isoformat()} "
        f"(new expiry {parsed.isoformat()}) — pending physical certificate "
        "verification at check-in."
    )
    combined_notes = f"{current_notes} {flag_note}".strip() if current_notes and current_notes != "-" else flag_note

    updates = {
        "vaccination_status": "Vaccinated",
        "vaccination_expired_date": parsed.strftime("%d/%m/%Y"),
        "service_notes": combined_notes,
    }
    return _repo().update_pet(int(company_id), int(customer_id), int(pet_id), updates)


@tool
def get_customer_by_phone(company_id: str | int, phone_number: str) -> dict:
    """
    Look up a customer by their WhatsApp phone number for the current company.

    NOT normally needed as an LLM tool call: identity is already resolved
    before your turn starts and provided at RUNTIME_CONTEXT.customer. Only
    call this yourself if you have a specific reason to re-verify identity
    mid-conversation.
    """
    return _repo().get_customer_by_phone(int(company_id), phone_number)


@tool
def get_pets(company_id: str | int, customer_id: str | int) -> dict:
    """
    List all pets belonging to a customer.

    Call this on greetings (to personalize) and before any booking/pricing
    action, so you know which pets exist before asking the customer to name
    one (MULTI-PET DISAMBIGUATION rule).
    """
    return _repo().list_customer_pets(int(company_id), int(customer_id))


@tool
def find_pet_by_name(company_id: str | int, customer_id: str | int, pet_name: str) -> dict:
    """Find one of the customer's pets by name — use once you know which pet the customer means."""
    return _repo().find_pet_by_name(int(company_id), int(customer_id), pet_name)


@tool
def get_latest_booking(company_id: str | int, customer_id: str | int) -> dict:
    """
    Get the customer's most recent booking across all services — this can be
    an UPCOMING booking (Scheduled/Pending, not yet happened) or a past one,
    whichever has the latest booking_date. Call this on greetings (to
    personalize, e.g. "welcome back") and whenever the customer asks about
    their booking status or wants to act on "my booking" without naming a
    specific one.

    Do NOT use this for "same as last time"/"repeat my previous booking" —
    those phrases mean a booking that already happened; this tool can return
    a future one instead. Use get_last_completed_booking for that case.
    """
    return _repo().get_latest_booking(int(company_id), int(customer_id))


@tool
def get_last_completed_booking(company_id: str | int, customer_id: str | int) -> dict:
    """
    Get the customer's most recent booking that has ALREADY happened
    (booking_date strictly before today) — use this for "same as last time",
    "repeat my previous booking", "like last time", "same one as before".
    get_latest_booking can return a future Scheduled/Pending booking instead,
    which is NOT what "previous"/"last time" means.

    Returns {"found": false} if the customer has no booking with a past date
    (only upcoming ones, or none at all) — in that case say so and ask the
    customer to describe what they'd like to book, do not invent one.
    """
    from app.db.relational_actions import get_last_completed_booking_by_customer_id

    context = CustomerContext(company_id=int(company_id))
    context.resolved_customer_id = int(customer_id)
    result = get_last_completed_booking_by_customer_id(context)
    if result.get("status") != "success" or not result.get("data_found"):
        return {"found": False}

    data = result.get("data") or {}
    return {"found": True, **data}


@tool
def get_booking_by_id(
    company_id: str | int, customer_id: str | int, booking_id: str | int, service_type: ServiceType
) -> dict:
    """Get one specific booking by its ID and service type (GROOMING/DAYCARE/BOARDING)."""
    return _repo().get_booking_by_id(int(company_id), int(customer_id), int(booking_id), service_type)


def _pet_details_for(company_id: int, pet_id: int) -> tuple[str, str]:
    """Look up a pet's species and size directly — never trust the model to pass the right values."""
    from app.db.supabase_client import get_supabase_client

    rows = (
        get_supabase_client()
        .table("pet")
        .select("pet_type, size")
        .eq("company_id", company_id)
        .eq("pet_id", pet_id)
        .limit(1)
        .execute()
        .data
    )
    row = rows[0] if rows else {}
    return str(row.get("pet_type") or "").strip(), str(row.get("size") or "").strip()


@tool
def get_booking_service_options(
    company_id: str | int, service_type: ServiceType, pet_id: str | int | None = ""
) -> dict:
    """
    List bookable services/rooms and prices for a service category
    (GROOMING/DAYCARE/BOARDING). When pet_id is available it is used to
    automatically exclude packages that don't apply to that pet's species
    (e.g. dog packages for a cat), and pricing without it risks blending
    wrong-species options together.

    For a personalized booking quote, resolve pet_id first via
    find_pet_by_name/get_pets (or RUNTIME_CONTEXT.customer.pets). If this is
    a pet NOT yet on record (a new customer, or an existing customer's new
    pet), call create_pet first — collecting name/species/height_cm so its
    size is genuinely verified — and use the pet_id it returns. Do not
    compute a size or quote any price yourself before that pet is actually
    registered; nothing is real until create_pet/find_pet_by_name has run.

    This is the single catalogue/pricing tool. For GROOMING and DAYCARE it
    automatically fetches the detailed
    RAG pricing table (by size for grooming, by package for daycare) and
    returns it as detailed_pricing_by_size — you do not need to separately
    call retrieve_policy for these; it is bundled in here so pricing is
    never missing just because a separate tool call was skipped. BOARDING
    prices come from the real room catalogue in service_options directly
    (room_type/capacity/price), no RAG needed. Call this proactively when
    discussing a service so you can state real prices (PROACTIVE
    RECOMMENDATIONS rule) instead of waiting to be asked.
    """
    from app.db.relational_actions import get_booking_service_options as _get_options

    pet_type, pet_size = _pet_details_for(int(company_id), int(pet_id)) if pet_id else ("", "")
    context = CustomerContext(company_id=int(company_id))
    result = _get_options(context, service_type, pet_type)

    category = str(service_type).strip().upper()
    if category in ("GROOMING", "DAYCARE"):
        from app.rag.retriever import CompanyRAGRetriever

        species = pet_type.strip().lower() or None
        size = pet_size.strip().upper() or None
        if category == "GROOMING":
            query = f"{species or ''} grooming packages price by size".strip()
            rag_service_type = "grooming"
        else:
            query = "daycare packages and prices"
            rag_service_type = "daycare"
            species = None  # daycare packages aren't species-split like grooming
            size = None  # nor size-split
        try:
            rag_rows = CompanyRAGRetriever().search(
                company_id, query, service_type=rag_service_type, pet_type=species, pet_size=size
            )
        except Exception as exc:
            rag_rows = [{"error": str(exc)}]
        result = {**result, "detailed_pricing_by_size": rag_rows}

    return result
