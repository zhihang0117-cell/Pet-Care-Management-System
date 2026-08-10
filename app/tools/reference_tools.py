"""Reference-based tool prototypes — Phase 3 of REFACTOR_PLAN.md.

Today's tools (app/tools/customer_tools.py, app/tools/availability_tools.py)
require the model to pass company_id, customer_id, price, and exact
room/service names back and forth on every call — facts the model should
never have authority to state (or restate wrong) in the first place. These
wrappers reuse the SAME underlying, already-correct business logic (real
catalogue/RAG lookups, real staff/room/pet conflict checks) and change only
the model-facing interface: options and slots come back holding an opaque
`option_ref`/`slot_ref` (minted by app.agent.evidence.EvidenceStore) instead
of the model having to restate their real facts, and company_id/customer_id/
pet_id are parameters this wrapper's CALLER supplies (server-injected from
session state, never something the model fills in) rather than something
threaded through the model-facing tool signature at all.

Not registered as callable LLM tools yet — that wiring is Phase 6's
cutover. This module's job right now is proving the reference shape works
against the real, live business logic underneath (see
tests/test_refactor_phase3_reference_tools.py, which calls these against
real Supabase data).
"""

from __future__ import annotations

from typing import Any

from app.agent.evidence import EvidenceStore


def _is_exact_clock_time(value: str) -> bool:
    """True for a real HH:MM(:SS) clock time — false for empty, or a period
    word like "morning"/"afternoon" (see app.db.time_normalization.
    is_period_token). Used to compute selection_required (below): the
    customer naming a period, or nothing at all, is not the same as
    naming an exact time."""
    from app.db.time_normalization import is_period_token

    return bool(value) and not is_period_token(value)


def _minutes_between(start: str, end: str) -> int | None:
    """Minutes from one HH:MM(:SS) clock time to another on the same day,
    or None if either is missing/unparseable/non-positive — used to give a
    CHECK_OUT-selection slot its own real duration_minutes (each pickup
    choice implies a different one) without trusting a model-supplied
    number for it."""
    from app.db.time_normalization import normalize_time

    start_norm = normalize_time(start)
    end_norm = normalize_time(end)
    if not start_norm or not end_norm:
        return None
    try:
        start_hour, start_minute = (int(part) for part in start_norm.split(":")[:2])
        end_hour, end_minute = (int(part) for part in end_norm.split(":")[:2])
    except (TypeError, ValueError):
        return None
    diff = (end_hour * 60 + end_minute) - (start_hour * 60 + start_minute)
    return diff if diff > 0 else None


def get_service_options(
    evidence: EvidenceStore,
    *,
    company_id: int,
    customer_id: int,
    pet_id: int,
    service_type: str,
) -> dict:
    from app.tools.customer_tools import get_booking_service_options as _existing

    raw = _existing.invoke({
        "company_id": company_id,
        "service_type": service_type,
        "pet_id": pet_id,
        "customer_id": customer_id,
    })
    status = raw.get("status")
    if status != "success":
        return {"ok": False, "code": raw.get("error") or str(status or "ERROR").upper(), "options": [], "add_ons": []}

    data = raw.get("data") or {}
    options = [
        _mint_option(evidence, service_type, option, kind="service")
        for option in (data.get("service_options") or [])
    ]
    add_ons = [
        _mint_option(evidence, service_type, option, kind="add_on")
        for option in (data.get("add_on_options") or [])
    ]
    return {"ok": True, "options": options, "add_ons": add_ons}


def _mint_option(evidence: EvidenceStore, service_type: str, option: dict, *, kind: str) -> dict:
    name = option.get("service_name") or option.get("room_type")
    price = option.get("price")
    ref = evidence.mint(
        "option" if kind == "service" else "addon",
        {
            "service_type": service_type,
            "name": name,
            "price": price,
            "room_type": option.get("room_type"),
            "duration_minutes": option.get("duration_minutes"),
            "kind": kind,
            # Real bug confirmed this session (2026-08-10 review): the
            # earlier version of this function dropped pricing_unit and the
            # duration-bound fields entirely, so preview_booking (below)
            # had no way to know DAYCARE's "Hourly Care" (RM20/hour) is a
            # RATE, not a total — it used option["price"] as the final
            # price regardless of how long the actual verified visit was.
            # Keeping these lets preview_booking compute the real total
            # server-side (never the model) — see its pricing_rule handling.
            "pricing_unit": option.get("pricing_unit"),
            "min_duration_minutes": option.get("min_duration_minutes"),
            "max_duration_minutes": option.get("max_duration_minutes"),
            "min_duration_exclusive": option.get("min_duration_exclusive"),
            "max_duration_exclusive": option.get("max_duration_exclusive"),
        },
    )
    entry = {"option_ref": ref, "name": name, "price": price}
    if option.get("duration_minutes") is not None:
        entry["duration_minutes"] = option["duration_minutes"]
    if option.get("capacity") is not None:
        entry["capacity"] = option["capacity"]
    return entry


def preview_booking(
    evidence: EvidenceStore,
    *,
    company_id: int,
    customer_id: int,
    pet_id: int,
    pet_name: str,
    option_ref: str,
    slot_ref: str,
    add_on_ref: str | None = None,
) -> dict:
    """Phase 4 of REFACTOR_PLAN.md — replaces create_booking's overloaded
    preview+write with two explicit steps. Computes the exact booking
    payload from VERIFIED evidence only (never from anything the model
    restates directly) and mints a preview_ref carrying it. Never calls the
    real write — confirm_booking() below is the only function that does.

    This removes the "verified_turn == current_turn" bug class at the
    root: the exact bug fixed today in app/agent/guardrails.py's
    reject_unverified_booking_payload (a BOARDING check_out_time the
    customer legitimately stated could never match the cached slot's
    always-empty check_out_time field) can't happen here — there is no
    separate cache to fall out of sync with; the preview_ref itself carries
    the one and only copy of what was actually verified.

    preferred_staff is no longer a separate parameter here (item #4 of the
    2026-08-10 review): it comes from the slot itself — whatever
    check_availability actually verified staffing against — never a value
    restated on this later call, which could silently name a DIFFERENT
    staff member than the one the slot's availability was actually checked
    for (confirmed as a real gap, not yet exploitable into a wrong booking
    only because create_booking's own write-time recheck would catch a
    mismatch — but recheck-and-reject is not the same guarantee as never
    letting the mismatch arise in the first place).
    """
    option = evidence.resolve(option_ref)
    if option is None:
        return {"ok": False, "code": "UNKNOWN_OR_EXPIRED_OPTION_REF"}
    slot = evidence.resolve(slot_ref)
    if slot is None:
        return {"ok": False, "code": "UNKNOWN_OR_EXPIRED_SLOT_REF"}
    if slot.get("option_ref") != option_ref:
        return {"ok": False, "code": "SLOT_DOES_NOT_MATCH_OPTION"}
    if slot.get("status") == "candidate":
        # This slot only passed a partial (handoff-only) check — e.g. a
        # DAYCARE check-in shown before duration/checkout was known. Never
        # build a booking payload from it; the caller must call
        # check_availability again with the missing piece to get a
        # "verified" slot_ref before this can proceed.
        return {
            "ok": False,
            "code": "SLOT_NOT_YET_VERIFIED",
            "still_needs": slot.get("still_needs") or [],
        }
    if slot.get("selection_required"):
        # Real Agent-control gap confirmed 2026-08-10, reproduced live:
        # "book grooming this Friday" (a pet and a date, nothing else)
        # reached preview_booking with the model having silently picked a
        # time slot the customer never chose or delegated ("verified" is
        # not the same as "selected"). A slot only stops being
        # selection_required when it matches an exact clock time the
        # customer actually specified to check_availability — never
        # reachable by just being the one real time that happened to come
        # back. The caller must present the candidates (or an explicit
        # delegated preference like "the earliest one") and get the
        # customer's actual pick, then call check_availability again with
        # that exact time to get a non-selection_required slot_ref.
        return {"ok": False, "code": "CUSTOMER_SLOT_SELECTION_REQUIRED"}

    add_on = None
    if add_on_ref:
        add_on = evidence.resolve(add_on_ref)
        if add_on is None:
            return {"ok": False, "code": "UNKNOWN_OR_EXPIRED_ADD_ON_REF"}

    # Real bug confirmed this session (2026-08-10 review): option["price"]
    # is a per-hour RATE for pricing_unit=="hour" options (currently only
    # DAYCARE's "Hourly Care", RM20/hour) — never a total. It used to be
    # passed straight through as the final price regardless of the actual
    # verified visit length. The server computes the real total here, from
    # the slot's own verified duration_minutes — never the model, and never
    # a value the model could get wrong the way "12pm to 5pm" -> 180
    # minutes was confirmed live to happen. Every other pricing_unit
    # (flat/day, and BOARDING's per-room-per-night rate, which the
    # underlying create_booking already multiplies by nights itself) keeps
    # using option["price"] unchanged — this only branches for "hour".
    price = option["price"]
    if str(option.get("pricing_unit") or "").strip().lower() == "hour":
        duration_minutes = slot.get("duration_minutes")
        if not duration_minutes:
            return {
                "ok": False,
                "code": "SLOT_MISSING_VERIFIED_DURATION_FOR_HOURLY_PRICING",
                "message": "This slot has no verified duration to price against. Call check_availability again with a duration/checkout to get a fully verified slot_ref.",
            }
        price = round(float(option["price"]) * (duration_minutes / 60), 2)

    booking_args = {
        "company_id": company_id,
        "customer_id": customer_id,
        "pet_id": pet_id,
        "pet_name": pet_name,
        "service_type": option["service_type"],
        "package_name": option["name"],
        "date": slot["date"],
        "time": slot["time"],
        "price": price,
        "check_out_date": slot.get("check_out_date") or "",
        "check_out_time": slot.get("check_out_time") or "",
        "preferred_staff": slot.get("preferred_staff") or "",
        "add_on": add_on["name"] if add_on else "",
        "add_on_price": add_on["price"] if add_on else None,
        # The slot's own verified duration (the actual checked visit
        # length) is the authoritative one when present — option's is only
        # a structured fixed-duration package's length (rare; None for
        # rate-based options like Hourly Care, which is exactly why pricing
        # above uses the slot's, never the option's).
        "duration_minutes": slot.get("duration_minutes") or option.get("duration_minutes"),
    }
    # preview_ref is used directly as the write's idempotency key —
    # EvidenceStore.mint() always returns a fresh, unique ref (see its own
    # docstring), so this can't collide the way an earlier, content-
    # addressed version of mint() did (confirmed live: two unrelated
    # preview_booking calls describing identical booking facts minted the
    # SAME ref back then, so the second confirm_booking silently replayed
    # the first's cached result instead of writing).
    preview_ref = evidence.mint("preview", {"booking_args": booking_args})

    return {
        "ok": True,
        "preview_ref": preview_ref,
        "summary": {
            "service_type": booking_args["service_type"],
            "option_name": booking_args["package_name"],
            "date": booking_args["date"],
            "time": booking_args["time"],
            "check_out_date": booking_args["check_out_date"] or None,
            "check_out_time": booking_args["check_out_time"] or None,
            "price": booking_args["price"],
            "add_on": booking_args["add_on"] or None,
            "add_on_price": booking_args["add_on_price"],
        },
    }


def confirm_booking(evidence: EvidenceStore, *, preview_ref: str) -> dict:
    """The only function that actually writes. Re-derives its exact args
    from the preview_ref's stored evidence — never from anything restated
    on this later turn — then calls the real, unmodified
    app.tools.booking_tools.create_booking, which performs its own fresh
    real-time staff/room/pet conflict recheck at write time regardless of
    how old the preview is (see the "recheck availability" step in the
    proposed architecture)."""
    preview = evidence.resolve(preview_ref)
    if preview is None:
        return {
            "ok": False,
            "code": "UNKNOWN_OR_EXPIRED_PREVIEW_REF",
            "message": "This preview has expired or was never created. Call preview_booking again.",
        }

    from app.agent.envelope import normalize_tool_result
    from app.tools.booking_tools import create_booking as _existing

    raw = _existing.invoke({
        **preview["booking_args"],
        "idempotency_key": preview_ref,
    })
    result = normalize_tool_result(raw)
    payment_id = result.get("data", {}).get("payment_id")
    if result.get("ok") and payment_id is not None:
        # redeem_reward needs a payment_ref, never a raw payment_id the
        # model states — this is the one place a real payment_id is first
        # known, so it's the one place a ref for it can be minted.
        result["payment_ref"] = evidence.mint("payment", {"payment_id": payment_id})
    return result


def check_availability(
    evidence: EvidenceStore,
    *,
    company_id: int,
    customer_id: int,
    pet_id: int,
    option_ref: str,
    date: str,
    check_out_date: str = "",
    time_preference: str = "",
    duration_minutes: int | None = None,
    check_out_time: str = "",
    preferred_staff: str = "",
    selection_target: str = "CHECK_IN",
    check_in_time: str = "",
    exclude_booking_id: str | int = "",
) -> dict:
    """duration_minutes/check_out_time/preferred_staff/selection_target/
    check_in_time/exclude_booking_id restore parity with the underlying
    app.tools.availability_tools.check_availability — the first draft of
    this wrapper dropped all six, which meant DAYCARE (needs duration or a
    checkout time), BOARDING pickup-time selection, a requested staff
    member, and reschedule (exclude_booking_id, or a room/staff always
    conflicts with its own current booking) were all unreachable through
    the reference-based path."""
    option = evidence.resolve(option_ref)
    if option is None:
        return {"ok": False, "code": "UNKNOWN_OR_EXPIRED_OPTION_REF", "slots": []}

    from app.tools.availability_tools import check_availability as _existing

    raw = _existing.invoke({
        "company_id": company_id,
        "service_type": option["service_type"],
        "date": date,
        "time": time_preference,
        "room_type": option.get("room_type") or "",
        "check_out_date": check_out_date,
        "customer_id": customer_id,
        "pet_id": pet_id,
        "duration_minutes": duration_minutes,
        "check_out_time": check_out_time,
        "preferred_staff": preferred_staff,
        "selection_target": selection_target,
        "check_in_time": check_in_time,
        "exclude_booking_id": exclude_booking_id,
    })
    status = raw.get("status")
    if status != "success":
        return {"ok": False, "code": raw.get("error") or str(status or "ERROR").upper(), "slots": []}

    data = raw.get("data") or {}
    resolved_check_out_date = data.get("check_out_date") or check_out_date or ""
    is_check_out_selection = str(selection_target or "").upper() == "CHECK_OUT"

    # candidate vs verified: a "preliminary" result (see
    # relational_actions.check_available_slots's preliminary_checkin_only
    # branch) has already run a real staff/pet handoff check for the times
    # it returns, but not the full-visit validation still_needs names —
    # e.g. a DAYCARE check-in shown before a duration/checkout is known.
    # Each minted slot carries this status so preview_booking (below) can
    # refuse to build a booking payload from one until it's re-verified —
    # the one thing this reference-based path adds beyond the raw "preliminary"
    # flag the live orchestrator already surfaces to the model directly.
    is_candidate = bool(data.get("preliminary"))
    slot_status = "candidate" if is_candidate else "verified"
    still_needs = data.get("still_needs") or []

    # Real Agent-control gap confirmed 2026-08-10, reproduced live: "book
    # grooming this Friday" (a pet and a date, nothing else) reached
    # preview_booking with the model having silently picked BOTH a
    # package and a time slot the customer never chose or delegated.
    # Verified availability is not the same as a customer selection —
    # a slot is only NOT selection_required when it matches an exact
    # clock time the customer actually specified this call (never a
    # period like "afternoon", never "nothing at all"). preview_booking
    # (below) refuses a selection_required slot_ref; the model must
    # present candidates and get the customer's pick (or an explicit
    # delegated preference like "the earliest one") first.
    exact_check_in_requested = _is_exact_clock_time(time_preference) or _is_exact_clock_time(check_in_time)
    exact_check_out_requested = _is_exact_clock_time(check_out_time)

    if is_check_out_selection:
        # Each candidate here IS a distinct pickup time, with check-in
        # already fixed (check_in_time) — mint one slot_ref per pickup
        # choice instead of per drop-off choice. Each pickup choice implies
        # its own real duration (pickup - check-in), computed here rather
        # than left for preview_booking to guess — see check_availability's
        # module-level note on why the slot must carry its own verified
        # duration_minutes for hourly-rate pricing to be correct.
        fixed_check_in = data.get("check_in_time") or check_in_time
        slots = [
            {
                "slot_ref": evidence.mint(
                    "slot",
                    {
                        "service_type": option["service_type"],
                        "date": date,
                        "time": fixed_check_in,
                        "room_type": option.get("room_type"),
                        "check_out_date": resolved_check_out_date,
                        "check_out_time": pickup,
                        "option_ref": option_ref,
                        "status": slot_status,
                        "still_needs": still_needs,
                        "duration_minutes": (
                            _minutes_between(fixed_check_in, pickup) if not is_candidate else None
                        ),
                        # Item #4 of the 2026-08-10 review: whatever staff
                        # constraint this slot was actually checked against
                        # travels WITH it — preview_booking reads it from
                        # here, never as a separately-restated parameter
                        # that could silently name someone else.
                        "preferred_staff": preferred_staff or None,
                        "selection_required": not (
                            exact_check_out_requested and pickup.startswith(check_out_time[:5])
                        ),
                    },
                ),
                "time": pickup,
                "status": slot_status,
                "selection_required": not (
                    exact_check_out_requested and pickup.startswith(check_out_time[:5])
                ),
            }
            for pickup in (data.get("available_check_out_times") or [])
        ]
    else:
        resolved_check_out_time = data.get("check_out_time") or ""
        # service_duration_minutes is the SAME real duration the
        # availability engine actually used to compute these slots (either
        # the customer's explicit duration/checkout, or None for a
        # candidate result that hasn't seen either yet) — reuse it rather
        # than recomputing, so the slot's stored duration can never drift
        # from what was actually checked.
        verified_duration = data.get("service_duration_minutes") if not is_candidate else None
        requested_check_in_value = time_preference if _is_exact_clock_time(time_preference) else check_in_time
        slots = [
            {
                "slot_ref": evidence.mint(
                    "slot",
                    {
                        "service_type": option["service_type"],
                        "date": date,
                        "time": slot,
                        "room_type": option.get("room_type"),
                        "check_out_date": resolved_check_out_date,
                        "check_out_time": resolved_check_out_time,
                        "option_ref": option_ref,
                        "status": slot_status,
                        "still_needs": still_needs,
                        "duration_minutes": verified_duration,
                        "preferred_staff": preferred_staff or None,
                        "selection_required": not (
                            exact_check_in_requested and slot.startswith(requested_check_in_value[:5])
                        ),
                    },
                ),
                "time": slot,
                "selection_required": not (
                    exact_check_in_requested and slot.startswith(requested_check_in_value[:5])
                ),
                "status": slot_status,
            }
            for slot in (data.get("available_slots") or [])
        ]
    result: dict[str, Any] = {"ok": True, "slots": slots}
    if is_candidate:
        result["status"] = "candidate"
        result["still_needs"] = still_needs
    if data.get("pet_already_booked"):
        # The exact fix from earlier today — carried through unchanged so
        # the reference-based caller can still explain the REAL reason
        # instead of a generic "nothing available".
        result["pet_already_booked"] = data["pet_already_booked"]
    if data.get("room_capacity"):
        result["room_capacity"] = data["room_capacity"]
    return result


# ---------------------------------------------------------------------------
# Phase 7 of REFACTOR_PLAN.md — policy, booking history, cancel/reschedule,
# loyalty, documents, staff handoff. Same reuse principle as Phases 3/4:
# wrap the existing, unmodified business logic; change only the model-facing
# interface (refs instead of raw booking_id/coupon_id/payment_id, no
# company_id/customer_id in what the model has to restate).
# ---------------------------------------------------------------------------


def retrieve_policy(
    *,
    company_id: int,
    query: str,
    service_type: str | None = None,
    pet_type: str | None = None,
    pet_size: str | None = None,
) -> dict:
    """Company policy/SOP retrieval — read-only, no identifiers involved, so
    no reference indirection is needed here; just server-injects
    company_id."""
    from app.tools.policy_tools import retrieve_policy as _existing

    chunks = _existing.invoke({
        "company_id": company_id, "query": query, "service_type": service_type,
        "pet_type": pet_type, "pet_size": pet_size,
    })
    return {"ok": True, "chunks": chunks if isinstance(chunks, list) else []}


def get_active_bookings(evidence: EvidenceStore, *, company_id: int, customer_id: int) -> dict:
    """List the customer's real active (Scheduled/Pending) bookings, each
    with a booking_ref — the reference cancel_booking/reschedule_booking
    below target instead of a raw booking_id the model would otherwise have
    to state (and could state wrong)."""
    from app.db.customer_context import CustomerContext
    from app.db.relational_actions import list_active_bookings as _existing

    context = CustomerContext(company_id=company_id)
    context.resolved_customer_id = customer_id
    raw = _existing(context)
    if raw.get("status") != "success":
        return {"ok": False, "code": raw.get("error") or "ERROR", "bookings": []}

    bookings = [
        {
            "booking_ref": evidence.mint("booking", {
                "booking_id": b.get("booking_id"), "service_type": b.get("service_type"),
            }),
            "service_type": b.get("service_type"),
            "pet_name": b.get("pet_name"),
            "package_name": b.get("package_name") or b.get("service_name") or b.get("room_type"),
            "date": b.get("booking_date") or b.get("check_in_date"),
        }
        for b in (raw.get("data") or {}).get("bookings", [])
    ]
    return {"ok": True, "bookings": bookings}


def _mint_candidates(evidence: EvidenceStore, candidates: list[dict]) -> list[dict]:
    return [
        {
            "booking_ref": evidence.mint("booking", {
                "booking_id": c.get("booking_id"), "service_type": c.get("service_type"),
            }),
            "service_type": c.get("service_type"),
            "pet_name": c.get("pet_name"),
            "package_name": c.get("package_name"),
            "date": c.get("date"),
        }
        for c in candidates
    ]


def cancel_booking(
    evidence: EvidenceStore, *, company_id: int, customer_id: int,
    booking_ref: str = "", confirm_pet_name: str = "",
) -> dict:
    """Cancel a booking. Same two-step protection as the underlying tool
    (confirm_pet_name empty previews; the customer must then type the pet's
    name — a plain "yes" is deliberately not enough for something this hard
    to undo) — this wrapper only changes booking_id into a validated
    booking_ref so the model can't name an ID that isn't genuinely this
    customer's own."""
    from app.tools.booking_tools import cancel_booking as _existing

    booking_id = ""
    service_type = "GROOMING"
    if booking_ref:
        booking = evidence.resolve(booking_ref)
        if booking is None:
            return {"ok": False, "code": "UNKNOWN_OR_EXPIRED_BOOKING_REF"}
        booking_id = booking.get("booking_id") or ""
        service_type = booking.get("service_type") or "GROOMING"

    raw = _existing.invoke({
        "company_id": company_id, "customer_id": customer_id,
        "booking_id": booking_id, "service_type": service_type,
        "confirm_pet_name": confirm_pet_name,
    })
    if raw.get("status") == "ambiguous":
        candidates = (raw.get("data") or {}).get("candidates") or []
        return {"ok": False, "code": "AMBIGUOUS_BOOKING", "candidates": _mint_candidates(evidence, candidates)}
    from app.agent.envelope import normalize_tool_result
    return normalize_tool_result(raw)


def reschedule_booking(
    evidence: EvidenceStore, *, company_id: int, customer_id: int,
    booking_ref: str = "", datetime_ref: str = "", check_out_datetime_ref: str = "",
    duration_minutes: int | None = None, confirm_pet_name: str = "",
) -> dict:
    """Reschedule a booking to a new datetime_ref (never a raw date/time the
    model states). Same two-step confirm_pet_name protection as the
    underlying tool."""
    from app.tools.booking_tools import reschedule_booking as _existing

    booking_id = ""
    service_type = "GROOMING"
    if booking_ref:
        booking = evidence.resolve(booking_ref)
        if booking is None:
            return {"ok": False, "code": "UNKNOWN_OR_EXPIRED_BOOKING_REF"}
        booking_id = booking.get("booking_id") or ""
        service_type = booking.get("service_type") or "GROOMING"

    new_date = new_time = new_check_out_date = new_check_out_time = ""
    if datetime_ref:
        dt = evidence.resolve(datetime_ref)
        if dt is None:
            return {"ok": False, "code": "UNKNOWN_OR_EXPIRED_DATETIME_REF"}
        new_date = dt.get("date") or ""
        new_time = dt.get("time") or ""
    if check_out_datetime_ref:
        checkout_dt = evidence.resolve(check_out_datetime_ref)
        if checkout_dt is None:
            return {"ok": False, "code": "UNKNOWN_OR_EXPIRED_CHECKOUT_DATETIME_REF"}
        new_check_out_date = checkout_dt.get("date") or ""
        new_check_out_time = checkout_dt.get("time") or ""

    raw = _existing.invoke({
        "company_id": company_id, "customer_id": customer_id,
        "booking_id": booking_id, "service_type": service_type,
        "new_date": new_date, "new_time": new_time,
        "new_check_out_date": new_check_out_date, "new_check_out_time": new_check_out_time,
        "duration_minutes": duration_minutes, "confirm_pet_name": confirm_pet_name,
    })
    if raw.get("status") == "ambiguous":
        candidates = (raw.get("data") or {}).get("candidates") or []
        return {"ok": False, "code": "AMBIGUOUS_BOOKING", "candidates": _mint_candidates(evidence, candidates)}
    from app.agent.envelope import normalize_tool_result
    return normalize_tool_result(raw)


def get_loyalty(evidence: EvidenceStore, *, company_id: int, customer_id: int) -> dict:
    """Loyalty balance plus eligible coupons in one call, each coupon
    holding a coupon_ref — the actual fix for a real observed bug: the
    model stating a raw coupon_id has been seen picking the WRONG voucher
    (customer asked for "the RM20 voucher", model submitted the RM10 one's
    ID instead). A coupon_ref can only resolve to the exact coupon it was
    minted for."""
    from app.db.customer_context import CustomerContext
    from app.db.relational_actions import check_coupon_eligibility as _check_coupons
    from app.db.relational_provider import get_relational_repository

    account = get_relational_repository().get_loyalty_account(company_id, customer_id)
    context = CustomerContext(company_id=company_id)
    context.resolved_customer_id = customer_id
    coupons_raw = _check_coupons(context)
    coupons = [
        {
            "coupon_ref": evidence.mint("coupon", {
                "coupon_id": c.get("coupon_id"), "reward_name": c.get("reward_name"),
            }),
            "reward_name": c.get("reward_name"),
            "discount_value": c.get("discount_value"),
            "eligible": c.get("eligible"),
        }
        for c in (coupons_raw.get("data") or {}).get("eligible_coupons", [])
    ] if coupons_raw.get("status") == "success" else []
    return {
        "ok": account.get("status") == "success",
        "balance": (account.get("data") or {}).get("points_balance"),
        "tier": (account.get("data") or {}).get("tier"),
        "coupons": coupons,
    }


def redeem_reward(evidence: EvidenceStore, *, company_id: int, customer_id: int, coupon_ref: str, payment_ref: str) -> dict:
    """Submit a coupon redemption request against a specific payment.
    payment_ref comes from confirm_booking's own result (see
    ConversationState.last_created_payment_ref) — never a value the model
    computes."""
    coupon = evidence.resolve(coupon_ref)
    if coupon is None:
        return {"ok": False, "code": "UNKNOWN_OR_EXPIRED_COUPON_REF"}
    payment = evidence.resolve(payment_ref)
    if payment is None:
        return {"ok": False, "code": "UNKNOWN_OR_EXPIRED_PAYMENT_REF"}

    from app.db.relational_provider import get_relational_repository

    intent_json = {"entities": {
        "payment_id": str(payment.get("payment_id")),
        "coupon_id": str(coupon.get("coupon_id")),
    }}
    raw = get_relational_repository().redeem_reward(company_id, customer_id, intent_json)
    from app.agent.envelope import normalize_tool_result
    return normalize_tool_result(raw)


def preview_membership(evidence: EvidenceStore, *, company_id: int, customer_id: int) -> dict:
    """Item #8 of the 2026-08-10 architecture review: membership
    registration follows the same preview_ref->confirm_ref pattern as
    preview_booking/confirm_booking, instead of a bare model-supplied
    confirmed=True/False flag (the same "restate a fact the model should
    never have authority over" problem Phase 4 fixed for bookings). An
    already-enrolled customer's real account comes back immediately —
    no preview/confirm needed for that case."""
    from app.tools.loyalty_tools import register_loyalty_member as _existing

    raw = _existing.invoke({"company_id": company_id, "customer_id": customer_id, "confirmed": False})
    status = raw.get("status")
    if status == "success":
        data = raw.get("data") or {}
        return {"ok": True, "already_member": True, "account": data}
    if status != "confirmation_required":
        return {"ok": False, "code": raw.get("error") or str(status or "ERROR").upper()}

    member_ref = evidence.mint("member", {"company_id": company_id, "customer_id": customer_id})
    return {"ok": True, "member_ref": member_ref}


def confirm_membership(evidence: EvidenceStore, *, member_ref: str) -> dict:
    """The only function that actually writes the enrolment. Re-derives
    company_id/customer_id from the member_ref's stored evidence — never
    from anything restated on this later turn."""
    member = evidence.resolve(member_ref)
    if member is None:
        return {
            "ok": False,
            "code": "UNKNOWN_OR_EXPIRED_MEMBER_REF",
            "message": "This membership preview has expired or was never created. Call preview_membership again.",
        }

    from app.agent.envelope import normalize_tool_result
    from app.tools.loyalty_tools import register_loyalty_member as _existing

    raw = _existing.invoke({
        "company_id": member["company_id"],
        "customer_id": member["customer_id"],
        "confirmed": True,
    })
    return normalize_tool_result(raw)


def send_booking_confirmation(evidence: EvidenceStore, *, company_id: int, customer_id: int, booking_ref: str = "") -> dict:
    """Resend a booking confirmation document. booking_ref optional — the
    underlying tool defaults to the customer's latest booking when omitted."""
    from app.tools.document_tools import send_booking_confirmation as _existing

    booking_id = ""
    service_type = None
    if booking_ref:
        booking = evidence.resolve(booking_ref)
        if booking is None:
            return {"ok": False, "code": "UNKNOWN_OR_EXPIRED_BOOKING_REF"}
        booking_id = booking.get("booking_id") or ""
        service_type = booking.get("service_type")

    raw = _existing.invoke({
        "company_id": company_id, "customer_id": customer_id,
        "booking_id": booking_id, "service_type": service_type,
    })
    from app.agent.envelope import normalize_tool_result
    return normalize_tool_result(raw)


def create_customer(*, company_id: int, full_name: str, phone_number: str) -> dict:
    """Item #7 of the 2026-08-10 architecture review: a brand-new customer
    (no real customer_id yet) couldn't complete a V2 booking at all —
    get_service_options requires a real pet_ref, and there was no way to
    get one. phone_number is the CALLER's own session phone number
    (server-injected — see app.agent.runtime._bind_tools — never something
    the model states or could get wrong); full_name is the one genuinely
    customer-provided fact this needs. No ref minted for the result — every
    other tool already takes company_id/customer_id as caller-injected
    parameters, never model-facing, so there's nothing for the model to
    reference here at all; the customer becomes real and resolvable via
    the normal identity flow on the NEXT turn."""
    from app.tools.customer_tools import create_customer as _existing

    raw = _existing.invoke({"company_id": company_id, "full_name": full_name, "phone_number": phone_number})
    from app.agent.envelope import normalize_tool_result
    return normalize_tool_result(raw)


def create_pet(
    *, company_id: int, customer_id: int | None, pet_name: str, pet_type: str, height_text: str, breed: str,
) -> dict:
    """Register a new pet for an already-registered customer (see
    create_customer above — customer_id must be real, never None). Reuses
    the existing height_text -> verified size computation unchanged (see
    app.tools.customer_tools.create_pet's own docstring); no ref minted for
    the same reason as create_customer — the new pet becomes resolvable as
    a real pet_ref via the normal identity flow on the NEXT turn."""
    if customer_id is None:
        return {"ok": False, "code": "CUSTOMER_NOT_YET_REGISTERED"}
    from app.tools.customer_tools import create_pet as _existing

    raw = _existing.invoke({
        "company_id": company_id, "customer_id": customer_id, "pet_name": pet_name,
        "pet_type": pet_type, "height_text": height_text, "breed": breed,
    })
    if isinstance(raw, dict) and raw.get("error"):
        return {"ok": False, "code": raw["error"], "message": raw.get("message")}
    if isinstance(raw, dict) and "verified_size" in raw and isinstance(raw.get("data"), dict):
        # normalize_tool_result only carries through raw["data"] — a
        # sibling top-level key like verified_size (the whole reason to
        # call this: telling the model the computed size) would otherwise
        # be silently dropped.
        raw = {**raw, "data": {**raw["data"], "verified_size": raw["verified_size"]}}
    from app.agent.envelope import normalize_tool_result
    return normalize_tool_result(raw)


def handoff_to_staff(*, company_id: int, customer_id: int | None, user_message: str, reason: str) -> dict:
    """Write a real row to the staff Enquiries dashboard — the only thing
    that makes "I've escalated this to our team" true. No reference
    indirection needed (nothing here is a business fact the model could
    misstate)."""
    from app.db.escalations import save_staff_enquiry

    try:
        save_staff_enquiry(company_id, customer_id, user_message, reason)
    except Exception as exc:
        # save_staff_enquiry deliberately raises on failure rather than
        # silently reporting success — this is the boundary that turns
        # that into an envelope the tool-calling loop can actually handle,
        # instead of an uncaught exception ending the turn.
        return {"ok": False, "code": "ESCALATION_SAVE_FAILED", "message": str(exc)}
    return {"ok": True}
