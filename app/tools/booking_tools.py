import re
from datetime import date as date_cls
from typing import Literal

from langchain_core.tools import tool

from app.db.relational_provider import get_relational_repository
from app.db.relational_repository import BookingCommand
from app.tools.booking_window import booking_window_error, resolve_date_string


def _repo():
    return get_relational_repository()


_GENERIC_PACKAGE_NAMES = {"", "grooming", "daycare", "boarding", "service", "general"}
ServiceType = Literal["GROOMING", "DAYCARE", "BOARDING"]

# A catalogue line naming a per-unit RATE ("RM15/hour") rather than a flat
# total must never be trusted as ground truth for an hourly booking's actual
# charged total (rate x hours) — only used to disqualify a matched
# catalogue line in _verify_flat_price_against_catalogue below, never to
# reject on its own.
_RATE_MARKER_RE = re.compile(
    r"/\s*hour\b|per\s+hour\b|/\s*hr\b|each\s+hour\b", re.IGNORECASE
)


def _find_catalogue_match(catalogue: list[dict], name: str) -> dict | None:
    normalized_name = re.sub(r"\s+", " ", str(name or "")).strip().casefold()
    return next(
        (
            opt for opt in catalogue
            if re.sub(r"\s+", " ", str(opt.get("service_name") or "")).strip().casefold() == normalized_name
        ),
        None,
    )


def _price_mismatch_error(
    field_label: str, submitted_price, match: dict,
    *, duration_minutes: int | None = None,
) -> dict | None:
    if match is None:
        return {
            "error": "UNVERIFIED_SERVICE_OPTION",
            "message": f"{field_label} could not be matched to an exact catalogue option.",
        }
    try:
        catalogue_price = float(match["price"])
        submitted = float(submitted_price)
    except (TypeError, ValueError):
        return {"error": "INVALID_PRICE", "message": f"{field_label} must be a valid amount."}
    pricing_unit = str(match.get("pricing_unit") or "").casefold()
    looks_hourly = pricing_unit == "hour" or bool(
        _RATE_MARKER_RE.search(str(match.get("service_name") or ""))
    )
    expected_price = catalogue_price
    if looks_hourly:
        if duration_minutes is None or duration_minutes <= 0:
            return {
                "error": "MISSING_DURATION_FOR_PRICE",
                "message": "An hourly package requires a verified visit duration before its total can be checked.",
            }
        expected_price = catalogue_price * duration_minutes / 60
    if abs(submitted - expected_price) <= 0.01:
        return None
    return {
        "error": "INVALID_PRICE",
        "message": (
            f"{field_label} ({submitted_price}) does not match {match['service_name']!r}'s verified "
            f"total (RM{expected_price:g}) from get_booking_service_options. "
            "Use its exact price, not a recalled, rounded, or guessed number."
        ),
    }


def _verify_flat_price_against_catalogue(
    company_id, service_type: str, package_name: str, price,
    pet_type: str = "", pet_size: str = "",
    add_on: str = "", add_on_price=None,
    duration_minutes: int | None = None,
) -> dict | None:
    """
    Fail-closed cross-check of a GROOMING/DAYCARE package (and, if
    given, add-on) price against the same RAG catalogue
    get_booking_service_options already parses for the model
    (app.tools.customer_tools._extract_daycare_catalogue_options — genuinely
    service-agnostic despite its name: it structures any RAG segment
    containing exactly one RM amount, which is exactly what a
    pet-size-narrowed grooming chunk, a daycare package line, or a grooming
    add-on line (nail trim, ear cleaning, teeth brushing, ...) looks like).

    Unlike BOARDING (a real per-night rate in the structured `room` table),
    GROOMING/DAYCARE prices only ever exist as unstructured document text,
    this path blocks the write when the catalogue cannot be retrieved or an
    exact label cannot be matched. Hourly options are verified as rate times
    the customer-approved duration; flat options and add-ons must match their
    exact catalogue amount.
    """
    normalized_service = str(service_type or "").strip().upper()
    if normalized_service not in {"GROOMING", "DAYCARE"}:
        return None
    try:
        from app.rag.retriever import CompanyRAGRetriever
        from app.tools.customer_tools import _extract_daycare_catalogue_options

        if normalized_service == "GROOMING":
            query = f"{pet_type or ''} grooming packages price by size".strip()
            rag_rows = CompanyRAGRetriever().search(
                company_id, query, service_type="grooming",
                pet_type=(pet_type or None), pet_size=(pet_size or None),
            )
        else:
            rag_rows = CompanyRAGRetriever().search(
                company_id, "daycare packages and prices", service_type="daycare",
            )
        services, add_ons = _extract_daycare_catalogue_options(
            rag_rows, pet_size=pet_size if normalized_service == "GROOMING" else ""
        )
    except Exception as exc:
        return {
            "error": "PRICE_VERIFICATION_UNAVAILABLE",
            "message": (
                "The catalogue price could not be verified, so no booking was written. "
                f"Retry get_booking_service_options before creating the booking ({exc})."
            ),
        }

    catalogue = services + add_ons
    package_mismatch = _price_mismatch_error(
        "price", price, _find_catalogue_match(catalogue, package_name),
        duration_minutes=duration_minutes,
    )
    if package_mismatch:
        return package_mismatch

    if str(add_on or "").strip() and add_on_price is not None:
        return _price_mismatch_error(
            "add_on_price", add_on_price, _find_catalogue_match(catalogue, add_on)
        )
    return None


@tool
def create_booking(
    company_id: str | int,
    customer_id: str | int,
    pet_id: str | int | None,
    pet_name: str,
    service_type: ServiceType,
    package_name: str,
    date: str,
    time: str,
    price: float | None = None,
    check_out_date: str = "",
    check_out_time: str = "",
    preferred_staff: str = "",
    add_on: str = "",
    add_on_price: float | None = None,
    duration_minutes: int | None = None,
) -> dict:
    """
    Create a confirmed booking. Call this ONLY after: (1) get_booking_service_options
    has been used to show the customer real package
    options with prices and the customer picked one — package_name must be
    the specific package they chose (e.g. "Standard Bath - Groomers
    Choice", or the specific room_type for BOARDING), never the bare
    service category, and price must be that package's real price, (2)
    check_availability confirmed the slot is free, and (3) you have
    restated the booking details (service, pet, date, time, price) and the
    customer has explicitly confirmed in this conversation — never on a
    guess or before an explicit "yes"/"confirm". Staff assignment is
    automatic unless the customer names a preferred staff member — pass
    their name or staff_id as preferred_staff, honored only if that person
    is actually free at the requested date/time; otherwise this returns a
    clear error instead of silently assigning someone else, and you should
    tell the customer that staff member isn't available then and ask if
    they'd like a different time or any available staff instead. The normal
    suggestion window is 14 days, but a customer-selected future date can be
    booked up to the server's sanity limit.

    GROOMING or DAYCARE add-ons: pass
    the add-on's name as `add_on` and its own price as `add_on_price` —
    NEVER fold the add-on's cost into `price`. `price` is always the base
    package price alone; `add_on_price` is the add-on alone. The booking
    record and confirmation PDF show these as two separate line items, so a
    summed `price` with `add_on`/`add_on_price` left blank shows the
    customer paying for an add-on with no record of what it was. Only
    GROOMING and DAYCARE bookings support add-ons; leave both blank for
    BOARDING.

    For BOARDING: `date`/`time` are the check-in date/time, `price` is the
    PRICE PER NIGHT (from the room's price in get_booking_service_options),
    and check_out_date is REQUIRED — resolve it with resolve_datetime same
    as any other date the customer states (e.g. "checking out next
    Friday"). Do not omit it or default to a 1-night stay; a missing
    check_out_date silently loses however many nights the customer actually
    asked for.

    For DAYCARE with an hourly-rate package (e.g. "Hourly Care" — check the
    package name/RAG content for "per hour" pricing, as opposed to a flat
    per-day package): pass check_out_time reflecting the actual pickup time
    the customer stated, and set price to (hourly rate x number of hours),
    not the bare hourly rate — state that computed total to the customer
    before confirming. If the customer states a duration rather than a
    pickup clock time, pass duration_minutes from resolve_datetime and the
    server derives/validates check_out_time.
    """
    resolved_date = resolve_date_string(date)
    if resolved_date is None:
        return {"error": "INVALID_DATE", "message": f"Could not resolve a date from {date!r}."}
    date = resolved_date

    window_error = booking_window_error(date)
    if window_error:
        return window_error
    if str(package_name).strip().lower() in _GENERIC_PACKAGE_NAMES:
        return {
            "error": "MISSING_PACKAGE_SELECTION",
            "message": (
                "package_name is missing or just the generic service category "
                f"({package_name!r}), not a real package. Call "
                "get_booking_service_options for this "
                "pet's species/size, present the specific package options and "
                "prices to the customer, get their choice, then retry "
                "create_booking with the real package_name and price."
            ),
        }
    if not price:
        return {
            "error": "MISSING_PRICE",
            "message": (
                "price is missing or zero. Use the real price for the "
                "selected package from get_booking_service_options, not null or 0."
            ),
        }
    if not str(time or "").strip():
        return {
            "error": "MISSING_TIME",
            "message": (
                "time is missing — the customer's actual preferred "
                "drop-off/check-in time, not a guessed or placeholder value. "
                "Ask the customer for it (resolve with resolve_datetime if "
                "they gave a relative expression), confirm it's within "
                "check_availability's returned available_slots, then retry "
                "create_booking with it set."
            ),
        }
    normalized_service = str(service_type).strip().upper()
    has_add_on_name = str(add_on or "").strip() not in {"", "-"}
    has_add_on_price = add_on_price is not None
    if normalized_service == "BOARDING" and (has_add_on_name or has_add_on_price):
        return {
            "error": "ADD_ON_NOT_SUPPORTED",
            "message": "BOARDING does not support add_on/add_on_price; use the selected room price only.",
        }
    if normalized_service in {"GROOMING", "DAYCARE"} and has_add_on_name != has_add_on_price:
        return {
            "error": "INCOMPLETE_ADD_ON",
            "message": "add_on and add_on_price must be supplied together as separate fields.",
        }
    if normalized_service == "BOARDING":
        if not check_out_date:
            return {
                "error": "MISSING_CHECK_OUT_DATE",
                "message": (
                    "check_out_date is required for BOARDING. Resolve it with "
                    "resolve_datetime (do not guess or default to 1 night) and "
                    "retry create_booking with it set."
                ),
            }
        resolved_check_out = resolve_date_string(check_out_date)
        if resolved_check_out is None:
            return {
                "error": "INVALID_CHECK_OUT_DATE",
                "message": f"Could not resolve a date from check_out_date {check_out_date!r}.",
            }
        check_out_date = resolved_check_out
        if not str(check_out_time or "").strip():
            return {
                "error": "MISSING_CHECK_OUT_TIME",
                "message": (
                    "check_out_time is required for BOARDING. Offer only pickup/check-out "
                    "times returned by check_availability, then use the customer's exact choice."
                ),
            }
        if date_cls.fromisoformat(check_out_date) <= date_cls.fromisoformat(date):
            return {
                "error": "INVALID_CHECK_OUT_DATE",
                "message": (
                    f"check_out_date ({check_out_date}) is not strictly after the "
                    f"check-in date ({date}). This almost always means check_out_date "
                    "was computed/guessed instead of resolved — re-run resolve_datetime "
                    "on the customer's ORIGINAL check-out date expression (not the "
                    "check-in one) and retry create_booking with its exact date output, "
                    "not a value you adjusted yourself."
                ),
            }

        # BOARDING pricing is fully structured (the real `room` table), so it
        # can be verified server-side — unlike GROOMING/DAYCARE, where prices
        # only exist as unstructured RAG text. package_name/price have been
        # observed being invented/misremembered rather than copied from the
        # tool's own real output; catch that here instead of trusting it.
        from app.db.supabase_client import get_supabase_client

        room_rows = (
            get_supabase_client()
            .table("room")
            .select("room_type, price")
            .eq("company_id", int(company_id))
            .ilike("room_type", str(package_name).strip())
            .execute()
            .data
            or []
        )
        if not room_rows:
            return {
                "error": "INVALID_PACKAGE_NAME",
                "message": (
                    f"package_name {package_name!r} does not match any real room "
                    "for this company. Call get_booking_service_options again and "
                    "use its exact room_type, not an invented or misremembered name."
                ),
            }
        real_price = room_rows[0].get("price")
        try:
            price_matches = real_price is None or round(float(price), 2) == round(float(real_price), 2)
        except (TypeError, ValueError):
            price_matches = False
        if not price_matches:
            return {
                "error": "INVALID_PRICE",
                "message": (
                    f"price ({price}) does not match {room_rows[0].get('room_type')}'s "
                    f"real per-night price (RM{real_price}) from "
                    "get_booking_service_options. Use its exact price, not a "
                    "recalled, rounded, or guessed number."
                ),
            }
    elif normalized_service == "DAYCARE" and not str(check_out_time or "").strip():
        if duration_minutes:
            from app.db.time_normalization import normalize_time

            start = normalize_time(time)
            try:
                start_hour, start_minute = (int(part) for part in start.split(":"))
                duration = int(duration_minutes)
                end = start_hour * 60 + start_minute + duration
                if not 0 < duration <= 24 * 60 or end >= 24 * 60:
                    raise ValueError
                check_out_time = f"{end // 60:02d}:{end % 60:02d}"
            except (TypeError, ValueError):
                return {
                    "error": "INVALID_DURATION",
                    "message": "duration_minutes must be a positive duration that ends on the same day.",
                }
        else:
            return {
                "error": "MISSING_CHECK_OUT_TIME",
                "message": (
                    "check_out_time is required for DAYCARE — pass the customer's actual "
                    "pickup time, or pass duration_minutes from resolve_datetime so the "
                    "server can derive it without relying on LLM time arithmetic."
                ),
            }
    if normalized_service in {"GROOMING", "DAYCARE"}:
        verified_duration = duration_minutes
        if normalized_service == "DAYCARE" and verified_duration in (None, ""):
            from app.db.time_normalization import normalize_time

            try:
                start_hour, start_minute = (int(part) for part in normalize_time(time).split(":"))
                end_hour, end_minute = (int(part) for part in normalize_time(check_out_time).split(":"))
                verified_duration = (end_hour * 60 + end_minute) - (start_hour * 60 + start_minute)
            except (TypeError, ValueError):
                verified_duration = None
        catalogue_pet_type = ""
        catalogue_pet_size = ""
        if normalized_service == "GROOMING" and pet_id:
            try:
                from app.tools.customer_tools import _pet_details_for

                catalogue_pet_type, catalogue_pet_size = _pet_details_for(int(company_id), int(pet_id))
            except Exception:
                pass  # A lookup hiccup here must never block the write — falls through to no verification.
        price_mismatch = _verify_flat_price_against_catalogue(
            company_id, normalized_service, package_name, price,
            pet_type=catalogue_pet_type, pet_size=catalogue_pet_size,
            add_on=add_on, add_on_price=add_on_price,
            duration_minutes=int(verified_duration) if verified_duration else None,
        )
        if price_mismatch:
            return price_mismatch
    command = BookingCommand(
        company_id=int(company_id),
        customer_id=int(customer_id),
        pet_id=int(pet_id) if pet_id else None,
        pet_name=pet_name,
        service_type=service_type,
        package_name=package_name,
        preferred_date=date,
        preferred_time=time,
        price_quote=price,
        check_out_date=check_out_date,
        check_out_time=check_out_time,
        preferred_staff=preferred_staff,
        add_on=add_on,
        add_on_price=add_on_price,
    )
    return _repo().create_booking(int(company_id), command)


@tool
def cancel_booking(
    company_id: str | int,
    customer_id: str | int,
    booking_id: str | int = "",
    service_type: ServiceType = "GROOMING",
    confirm_pet_name: str = "",
) -> dict:
    """
    Cancel a booking (updates booking_status to Cancelled; never deletes the
    row). Only ever targets a currently active (Scheduled/Pending) booking —
    never a completed/historical one, even if it's the customer's "latest".

    Two-step confirmation, required: call this FIRST with confirm_pet_name
    empty to identify/preview the target booking. It will return
    status="confirmation_required" with the real booking's pet_name/service/
    date — show these details to the customer and ask them to type the
    pet's name to confirm (a plain "yes" is not enough for something this
    hard to undo). Only call this AGAIN, with confirm_pet_name set to
    exactly what they typed, once they reply. Leave booking_id empty to
    target the customer's one currently-active booking (or get an
    "ambiguous" result listing candidates if they have more than one).
    """
    intent_json = {
        "entities": {"booking_id": str(booking_id), "confirm_pet_name": confirm_pet_name},
        "service_type": service_type,
    }
    return _repo().cancel_booking(int(company_id), int(customer_id), intent_json)


@tool
def reschedule_booking(
    company_id: str | int,
    customer_id: str | int,
    new_date: str,
    new_time: str,
    booking_id: str | int = "",
    service_type: ServiceType = "GROOMING",
    new_check_out_date: str = "",
    new_check_out_time: str = "",
    duration_minutes: int | None = None,
    confirm_pet_name: str = "",
) -> dict:
    """
    Reschedule a booking to a new date/time after the customer has explicitly
    confirmed the new slot. Use check_availability first when you need to find
    or present candidate slots. This tool also revalidates the final interval
    internally before writing, so a separate pre-check is not a hard
    prerequisite when the customer already chose a specific date/time. Only
    ever targets a currently active
    (Scheduled/Pending) booking — never a completed/historical one.

    Two-step confirmation, required: call this FIRST with confirm_pet_name
    empty (new_date/new_time can be your best-known values) to
    identify/preview the target booking. It will return
    status="confirmation_required" with the real booking's pet_name/service/
    date — show these details and ask the customer to type the pet's name
    to confirm THIS is the booking to reschedule. Only call again, with
    confirm_pet_name set to exactly what they typed (plus the real new
    date/time), once they reply.

    For BOARDING, new_check_out_date is REQUIRED — a boarding stay always
    has a check-in AND a check-out date (BOARDING — TWO DATES rule), so
    rescheduling one without the other would silently keep the old
    check-out date and change how many nights the stay actually covers.
    Resolve it with resolve_datetime same as the check-in date; do not
    default to the original stay length. Changing the number of nights
    automatically recalculates total_price from the room's real
    price-per-night — you do not need to (and should not) pass a new price.

    For DAYCARE, pass the customer's new pickup time as
    new_check_out_time, or duration_minutes from resolve_datetime. If both
    are omitted, the backend preserves the original visit duration instead
    of keeping the old pickup clock time. The full visit is revalidated
    against operating hours.
    """
    resolved_new_date = resolve_date_string(new_date)
    if resolved_new_date is None:
        return {"error": "INVALID_DATE", "message": f"Could not resolve a date from {new_date!r}."}
    new_date = resolved_new_date

    window_error = booking_window_error(new_date)
    if window_error:
        return window_error
    if str(service_type).strip().upper() == "BOARDING":
        if not new_check_out_date:
            return {
                "error": "MISSING_CHECK_OUT_DATE",
                "message": (
                    "new_check_out_date is required when rescheduling a BOARDING "
                    "booking. Resolve it with resolve_datetime and retry "
                    "reschedule_booking with it set."
                ),
            }
        resolved_check_out = resolve_date_string(new_check_out_date)
        if resolved_check_out is None:
            return {
                "error": "INVALID_DATE",
                "message": f"Could not resolve a date from new_check_out_date {new_check_out_date!r}.",
            }
        new_check_out_date = resolved_check_out
    intent_json = {
        "entities": {
            "booking_id": str(booking_id),
            "new_preferred_date": new_date,
            "new_preferred_time": new_time,
            "new_check_out_date": new_check_out_date,
            "new_check_out_time": new_check_out_time,
            "duration_minutes": duration_minutes,
            "confirm_pet_name": confirm_pet_name,
        },
        "service_type": service_type,
    }
    return _repo().reschedule_booking(int(company_id), int(customer_id), intent_json)
