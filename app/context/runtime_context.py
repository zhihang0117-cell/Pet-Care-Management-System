"""Runtime context assembly for the model-facing turn.

This is the "context" layer of the constrained agent: deterministic identity
resolution (never left to the model to decide — see resolve_identity's own
docstring) plus the RUNTIME_CONTEXT payload handed to the model alongside the
static system prompt (app/prompts/system_prompt.py). Kept separate from the
tool-calling loop itself (app/orchestrator.py) and from tool implementations
(app/tools/).
"""

from __future__ import annotations

import json
import logging
import time as time_module
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.db.relational_provider import get_relational_repository
from app.scenarios.loader import load_scenario
from app.tools.text_formatting import first_name, format_pet_names

logger = logging.getLogger(__name__)


def resolve_identity(
    company_id: str,
    state: Any,
    *,
    tool_executor: ThreadPoolExecutor,
    timeout_seconds: float,
    cache_single_pet: Callable[[Any, list[dict]], None],
) -> dict:
    """
    Deterministic identity resolution — never left to the model to decide.

    Customer/pet/latest-booking hydration is independent of greeting and
    conversation flow. Any turn whose session lacks a successful profile
    read performs the missing reads before the model runs; failed reads
    remain retryable on the next turn, while a verified empty result is
    retained as genuinely empty rather than confused with "not loaded".
    """
    if state.customer_id is not None and state.customer_name:
        # Reuse the cached name/address instead of re-querying Supabase
        # every turn — but never drop them the way a bare {"customer_id"}
        # shortcut previously did (the model had no name from turn 2 on).
        customer = {
            "found": True,
            "customer_id": state.customer_id,
            "full_name": state.customer_name,
            "address": state.customer_address,
            "phone_number": state.phone_number,
        }
    else:
        result = get_relational_repository().get_customer_by_phone(int(company_id), state.phone_number)
        if result.get("status") == "success" and result.get("data_found"):
            data = result.get("data") or {}
            state.customer_id = data.get("customer_id")
            state.customer_name = data.get("full_name")
            state.customer_address = data.get("address")
            customer = {"found": True, **data}
        elif result.get("status") in ("not_found", "success"):
            customer = {"found": False}
        else:
            # A repository/network failure is not evidence that this is a
            # brand-new customer. Keep the uncertainty explicit so the
            # agent does not create a duplicate profile or claim no record
            # exists merely because the lookup failed.
            customer = {
                "found": None,
                "identity_context_status": "unavailable",
            }

    if customer.get("found"):
        repo = get_relational_repository()
        hydrate_pets = state.pets_context_status != "available"
        hydrate_booking = state.booking_context_status not in {"available", "not_found"}
        pets_future = (
            tool_executor.submit(
                repo.list_customer_pets, int(company_id), int(state.customer_id)
            )
            if hydrate_pets else None
        )
        booking_future = (
            tool_executor.submit(
                repo.get_latest_booking, int(company_id), int(state.customer_id)
            )
            if hydrate_booking else None
        )
        # Fetched alongside latest_booking, not instead of it — see
        # ConversationState.recent_booking/upcoming_booking for why a single
        # "latest" blend can't represent both concepts at once. Guarded with
        # getattr (not a hard repo.get_recent_completed_booking call) so a
        # test double/fake repository implementing only the older
        # get_latest_booking surface keeps working unchanged instead of
        # hard-failing on a missing attribute.
        recent_fn = getattr(repo, "get_recent_completed_booking", None)
        recent_future = (
            tool_executor.submit(recent_fn, int(company_id), int(state.customer_id))
            if hydrate_booking and recent_fn is not None else None
        )
        upcoming_fn = getattr(repo, "get_upcoming_booking", None)
        upcoming_future = (
            tool_executor.submit(upcoming_fn, int(company_id), int(state.customer_id))
            if hydrate_booking and upcoming_fn is not None else None
        )
        # Real gap confirmed 2026-08-10: hydrated once per session here
        # (not per turn — loyalty_context_status stays "available" once
        # set), same reasoning and same getattr-guarded pattern as
        # recent/upcoming above. See ConversationState.loyalty_account.
        hydrate_loyalty = state.loyalty_context_status != "available"
        loyalty_fn = getattr(repo, "get_loyalty_account", None)
        loyalty_future = (
            tool_executor.submit(loyalty_fn, int(company_id), int(state.customer_id))
            if hydrate_loyalty and loyalty_fn is not None else None
        )
        prefetch_deadline = time_module.monotonic() + timeout_seconds

        def remaining_prefetch_time() -> float:
            return max(0.0, prefetch_deadline - time_module.monotonic())

        if pets_future is not None:
            try:
                pets_result = pets_future.result(timeout=remaining_prefetch_time())
            except FutureTimeoutError:
                pets_future.cancel()
                pets_result = {"status": "error", "error": "PREFETCH_TIMEOUT"}
            except Exception as exc:
                pets_result = {"status": "error", "error": f"PREFETCH_FAILED:{exc}"}
                logger.exception("Pet prefetch failed: %s", exc)
        else:
            pets_result = None
        if booking_future is not None:
            try:
                booking_result = booking_future.result(timeout=remaining_prefetch_time())
            except FutureTimeoutError:
                booking_future.cancel()
                booking_result = {"status": "error", "error": "PREFETCH_TIMEOUT"}
            except Exception as exc:
                booking_result = {"status": "error", "error": f"PREFETCH_FAILED:{exc}"}
                logger.exception("Booking prefetch failed: %s", exc)
        else:
            booking_result = None
        if recent_future is not None:
            try:
                recent_result = recent_future.result(timeout=remaining_prefetch_time())
            except FutureTimeoutError:
                recent_future.cancel()
                recent_result = {"status": "error", "error": "PREFETCH_TIMEOUT"}
            except Exception as exc:
                recent_result = {"status": "error", "error": f"PREFETCH_FAILED:{exc}"}
                logger.exception("Recent-booking prefetch failed: %s", exc)
        else:
            recent_result = None
        if upcoming_future is not None:
            try:
                upcoming_result = upcoming_future.result(timeout=remaining_prefetch_time())
            except FutureTimeoutError:
                upcoming_future.cancel()
                upcoming_result = {"status": "error", "error": "PREFETCH_TIMEOUT"}
            except Exception as exc:
                upcoming_result = {"status": "error", "error": f"PREFETCH_FAILED:{exc}"}
                logger.exception("Upcoming-booking prefetch failed: %s", exc)
        else:
            upcoming_result = None
        if loyalty_future is not None:
            try:
                loyalty_result = loyalty_future.result(timeout=remaining_prefetch_time())
            except FutureTimeoutError:
                loyalty_future.cancel()
                loyalty_result = {"status": "error", "error": "PREFETCH_TIMEOUT"}
            except Exception as exc:
                loyalty_result = {"status": "error", "error": f"PREFETCH_FAILED:{exc}"}
                logger.exception("Loyalty prefetch failed: %s", exc)
        else:
            loyalty_result = None

        if pets_result is not None:
            if pets_result.get("status") == "success":
                pets = (pets_result.get("data") or {}).get("pets", [])
                state.pets_context_status = "available"
                cache_single_pet(state, pets)
                state.known_pets = [
                    {
                        "pet_id": p.get("pet_id"),
                        "pet_type": p.get("pet_type"),
                        "pet_name": p.get("pet_name"),
                        "pet_size": p.get("size"),
                        "pet_breed": p.get("breed"),
                    }
                    for p in pets
                ]
            else:
                state.pets_context_status = "unavailable"

        if booking_result is not None:
            if booking_result.get("status") == "success":
                state.latest_booking = booking_result.get("data")
                state.booking_context_status = (
                    "available" if state.latest_booking else "not_found"
                )
            elif booking_result.get("status") == "not_found":
                state.latest_booking = None
                state.booking_context_status = "not_found"
            else:
                # Do not silently turn a database/network failure into
                # "this customer has no booking history". The unavailable
                # status remains retryable on the next customer turn.
                state.booking_context_status = "unavailable"
        if recent_result is not None and recent_result.get("status") in ("success", "not_found"):
            state.recent_booking = recent_result.get("data") if recent_result.get("status") == "success" else None
        if upcoming_result is not None and upcoming_result.get("status") in ("success", "not_found"):
            state.upcoming_booking = upcoming_result.get("data") if upcoming_result.get("status") == "success" else None
        if loyalty_result is not None:
            # "not_found" (a real, verified non-member) is just as
            # complete an answer as "success" — both mark hydration done
            # so this isn't re-queried every turn. A real read failure
            # leaves status unset (None), retryable on a later turn,
            # exactly like pets/booking_context_status above.
            if loyalty_result.get("status") == "success":
                state.loyalty_account = loyalty_result.get("data")
                state.loyalty_context_status = "available"
            elif loyalty_result.get("status") == "not_found":
                state.loyalty_account = None
                state.loyalty_context_status = "available"

        customer["pets"] = (
            state.known_pets if state.pets_context_status == "available" else None
        )
        customer["pets_context_status"] = state.pets_context_status or "unavailable"
        customer["latest_booking"] = (
            state.latest_booking if state.booking_context_status == "available" else None
        )
        customer["booking_context_status"] = (
            state.booking_context_status or "unavailable"
        )
        customer["recent_booking"] = state.recent_booking
        customer["upcoming_booking"] = state.upcoming_booking
        customer["loyalty_account"] = (
            state.loyalty_account if state.loyalty_context_status == "available" else None
        )
        customer["loyalty_context_status"] = state.loyalty_context_status or "unavailable"
        # Deterministic formatting only (see text_formatting.py) — the
        # model still decides what to say; this just removes
        # "Milo, Luna and Coco" vs "Milo, Luna,
        # or Coco" style drift when a message needs to list pet names.
        customer["first_name"] = first_name(customer.get("full_name"))
        customer["pets_formatted"] = format_pet_names(customer["pets"] or [])

    if customer.get("found"):
        customer["first_name"] = first_name(customer.get("full_name"))
        if "pets_formatted" not in customer:
            customer["pets_formatted"] = format_pet_names(customer.get("pets") or state.known_pets)

    return customer


def customer_context_for_state(customer: dict, state: Any) -> dict:
    """Merge facts learned mid-turn into the model-facing customer view."""
    merged = dict(customer or {})
    if state.customer_id is not None:
        merged.update({
            "found": True,
            "customer_id": state.customer_id,
            "full_name": state.customer_name,
            "address": state.customer_address,
            "phone_number": state.phone_number,
        })
    if state.pets_context_status:
        merged["pets"] = (
            state.known_pets if state.pets_context_status == "available" else None
        )
        merged["pets_context_status"] = state.pets_context_status
    if state.booking_context_status:
        merged["latest_booking"] = (
            state.latest_booking if state.booking_context_status == "available" else None
        )
        merged["booking_context_status"] = state.booking_context_status
        merged["recent_booking"] = state.recent_booking
        merged["upcoming_booking"] = state.upcoming_booking
    if state.loyalty_context_status:
        merged["loyalty_account"] = (
            state.loyalty_account if state.loyalty_context_status == "available" else None
        )
        merged["loyalty_context_status"] = state.loyalty_context_status
    if merged.get("found"):
        merged["first_name"] = first_name(merged.get("full_name"))
        merged["pets_formatted"] = format_pet_names(merged.get("pets") or [])
    return merged


def build_runtime_context(
    company_context: dict,
    customer: dict,
    state: Any,
    resolved_selection: dict | None = None,
    available_tools: list | None = None,
    *,
    sanitize_model_value: Callable[[Any], Any],
) -> dict:
    try:
        scenario = load_scenario(state.active_scenario) if state.active_scenario else None
    except ValueError:
        scenario = None

    timezone_name = str((company_context or {}).get("timezone") or "Asia/Kuala_Lumpur")
    try:
        business_now = datetime.now(ZoneInfo(timezone_name))
    except Exception:
        business_now = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))

    # The clock configuration is an internal implementation detail. Give
    # the model the resolved business date/time it needs, without inviting
    # it to repeat configuration details in customer-facing answers.
    runtime_company = {
        **{
            key: value
            for key, value in (company_context or {}).items()
            if key != "timezone"
        },
        "business_date": business_now.date().isoformat(),
        "business_datetime": business_now.isoformat(timespec="seconds"),
    }
    # Actual dialogue history remains real messages rather than duplicated
    # JSON. Everything else is refreshed after each tool result, so the
    # next model decision observes newly-created customers/pets, resolved
    # dates, evidence, scenario changes, and completion state immediately.
    state_dict = sanitize_model_value(
        {k: v for k, v in state.__dict__.items() if k != "history"}
    )
    runtime_context = {
        "company": runtime_company,
        "customer": customer_context_for_state(customer, state),
        "conversation_state": state_dict,
        "scenario_definition": scenario,
        "available_tools": [tool.name for tool in (available_tools or [])],
    }
    if resolved_selection is not None:
        runtime_context["resolved_ordinal_selection"] = resolved_selection
    return runtime_context


def runtime_message(
    company_context: dict,
    customer: dict,
    state: Any,
    resolved_selection: dict | None = None,
    available_tools: list | None = None,
    *,
    sanitize_model_value: Callable[[Any], Any],
) -> tuple[str, str]:
    context = build_runtime_context(
        company_context,
        customer,
        state,
        resolved_selection,
        available_tools,
        sanitize_model_value=sanitize_model_value,
    )
    return ("system", "RUNTIME_CONTEXT:\n" + json.dumps(context, ensure_ascii=False, default=str))
