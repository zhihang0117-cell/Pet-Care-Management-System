from __future__ import annotations
import json
import logging
import os
import re
import time as time_module
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import ToolMessage
from langchain_openai import ChatOpenAI

from app.context.company import get_company_config
from app.db.relational_provider import get_relational_repository
from app.prompts.system_prompt import SYSTEM_PROMPT
from app.scenarios.loader import load_scenario
from app.tools.customer_tools import (
    create_customer,
    create_pet,
    find_pet_by_name,
    get_booking_by_id,
    get_booking_service_options,
    get_last_completed_booking,
    get_latest_booking,
    get_pets,
    update_pet_vaccination,
)
from app.tools.calendar_tools import resolve_datetime
from app.tools.availability_tools import check_availability, check_availability_range
from app.tools.booking_tools import create_booking, cancel_booking, reschedule_booking
from app.tools.policy_tools import retrieve_policy
from app.tools.loyalty_tools import (
    check_coupon_eligibility,
    get_loyalty_balance,
    redeem_reward,
    register_loyalty_member,
)
from app.tools.history_tools import get_payment_history, get_redemption_history
from app.tools.document_tools import send_booking_confirmation
from app.tools.state_tools import update_conversation_state
from app.tools.text_formatting import first_name, format_pet_names
from app.db.time_normalization import extract_duration_minutes

# get_customer_by_phone is intentionally NOT in the bindable tool list: identity
# resolution happens deterministically in PawfectOrchestrator.invoke_with_trace
# before the model is ever called (see _resolve_identity below).
ALL_TOOLS = [
    create_customer,
    create_pet,
    update_pet_vaccination,
    get_pets,
    find_pet_by_name,
    get_latest_booking,
    get_last_completed_booking,
    get_booking_by_id,
    get_booking_service_options,
    resolve_datetime,
    check_availability,
    check_availability_range,
    create_booking,
    cancel_booking,
    reschedule_booking,
    get_loyalty_balance,
    check_coupon_eligibility,
    redeem_reward,
    register_loyalty_member,
    get_payment_history,
    get_redemption_history,
    send_booking_confirmation,
    retrieve_policy,
    update_conversation_state,
]
TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}

# Authority is determined by the request/session, not by whether the model
# happened to include an ID argument. Inject these fields even when omitted;
# wrong supplied values are overwritten the same way.
COMPANY_SCOPED_TOOL_NAMES = {
    tool.name for tool in ALL_TOOLS if tool.name not in {"resolve_datetime", "update_conversation_state"}
}
CUSTOMER_SCOPED_TOOL_NAMES = {
    "create_pet",
    "update_pet_vaccination",
    "get_pets",
    "find_pet_by_name",
    "get_latest_booking",
    "get_last_completed_booking",
    "get_booking_by_id",
    "check_availability",
    "create_booking",
    "cancel_booking",
    "reschedule_booking",
    "get_loyalty_balance",
    "check_coupon_eligibility",
    "redeem_reward",
    "register_loyalty_member",
    "get_payment_history",
    "get_redemption_history",
    "send_booking_confirmation",
}
MUTATING_TOOL_NAMES = {
    "create_customer",
    "create_pet",
    "update_pet_vaccination",
    "create_booking",
    "cancel_booking",
    "reschedule_booking",
    "redeem_reward",
    "register_loyalty_member",
    "send_booking_confirmation",
}

# Tools available in every scenario (and when no scenario is active yet).
CORE_TOOL_NAMES = {
    "resolve_datetime",
    "retrieve_policy",
    "get_booking_service_options",
    "get_pets",
    "get_latest_booking",
    "get_last_completed_booking",
    "get_payment_history",
    "get_redemption_history",
    "send_booking_confirmation",
    "update_conversation_state",
    # Identity/pet registration can be needed at any point in a conversation
    # (a brand-new customer might be greeting, booking, or asking a loyalty
    # question) — never scenario-gated, same reasoning as get_pets above.
    "create_customer",
    "create_pet",
    "update_pet_vaccination",
    # A customer can ask to cancel/reschedule the thing they JUST booked in
    # the same conversation. Keep these reachable during every scenario so a
    # side request never has to wait for bookkeeping to catch up.
    "cancel_booking",
    "reschedule_booking",
    "get_booking_by_id",
    # Loyalty can be a side question or a useful pre-booking recommendation,
    # so these remain reachable regardless of the active scenario.
    "get_loyalty_balance",
    "check_coupon_eligibility",
    "redeem_reward",
    "register_loyalty_member",
}

# Tools scoped to each scenario, on top of a broad cross-scenario core. The
# scenario narrows only the few high-consequence actions; reads and common side
# requests stay available. With no active scenario, every tool is available.
SCENARIO_TOOL_NAMES = {
    "MAKE_BOOKING": {"find_pet_by_name", "check_availability", "check_availability_range", "create_booking"},
    "CANCEL_BOOKING": {"get_booking_by_id", "cancel_booking"},
    "RESCHEDULE_BOOKING": {"get_booking_by_id", "check_availability", "check_availability_range", "reschedule_booking"},
    "LOYALTY_QUERY": {"get_loyalty_balance", "check_coupon_eligibility", "redeem_reward"},
    "POLICY_QUERY": set(),
}

# Was 6 — the LOYALTY_OFFER_PENDING gate (create_booking rejected once,
# then get_loyalty_balance/check_coupon_eligibility, then a real retry) adds
# a mandatory extra round trip on top of package-selection/price-grounding
# retries that were already close to this ceiling; confirmed live hitting
# MAX_TOOL_ITERATIONS on an otherwise-normal "yes confirm" once the gate
# was added.
MAX_TOOL_ITERATIONS = 9
MAX_HISTORY_TURNS = 6  # keep the last N human+ai turn pairs
MAX_RECENT_TOOL_EVIDENCE = 20


class ToolLoopError(RuntimeError):
    """An agent loop failure that preserves calls already completed."""

    def __init__(self, message: str, trace: list[dict]):
        super().__init__(message)
        self.trace = list(trace)


def _tool_executor_workers() -> int:
    """A small, bounded pool shared by every chat handled in this process."""
    try:
        configured = int(os.getenv("TOOL_EXECUTOR_MAX_WORKERS", "4"))
    except ValueError:
        configured = 4
    # At least two workers preserves real parallelism; the upper bound prevents
    # one model response (or several simultaneous customers) from exhausting
    # Supabase/http socket resources with an unbounded number of threads.
    return min(max(configured, 2), 16)


def _tool_timeout_seconds() -> float:
    try:
        configured = float(os.getenv("TOOL_CALL_TIMEOUT_SECONDS", "20"))
    except ValueError:
        configured = 20.0
    return min(max(configured, 2.0), 60.0)


TOOL_EXECUTOR_MAX_WORKERS = _tool_executor_workers()
TOOL_CALL_TIMEOUT_SECONDS = _tool_timeout_seconds()
# Module/process lifetime pool: ThreadPoolExecutor creates workers lazily on
# first submit and keeps them available until interpreter shutdown. Uvicorn is
# currently deployed with one worker, so this is one shared pool per service
# instance rather than a fresh pool for every LLM iteration.
_TOOL_EXECUTOR = ThreadPoolExecutor(
    max_workers=TOOL_EXECUTOR_MAX_WORKERS,
    thread_name_prefix="pawfect-tool",
)


def _tools_for_scenario(active_scenario: str | None) -> list:
    if not active_scenario or active_scenario not in SCENARIO_TOOL_NAMES:
        return ALL_TOOLS
    allowed = CORE_TOOL_NAMES | SCENARIO_TOOL_NAMES[active_scenario]
    return [t for t in ALL_TOOLS if t.name in allowed]


class PawfectOrchestrator:
    """
    Simple LangChain harness:
    Prompt + Context + GPT-4o mini + approved tools, looped until the model
    stops requesting tool calls and produces the customer-facing response.

    This is intentionally NOT a multi-agent graph. Validation lives inside
    the real data layer (app/db/relational_actions.py) — app/validation/
    validator.py just reads back the outcome uniformly.

    Two things are handled deterministically in code rather than left to
    model judgment (see the "teach the LLM when to fetch what" methods this
    implements):
    - Customer identity resolution (get_customer_by_phone) happens before the
      model is invoked at all; the result is injected as RUNTIME_CONTEXT.customer.
    - The tool list bound to the model is scoped to state.active_scenario
      (set by the model itself via update_conversation_state), reducing the
      chance of the wrong tool being picked when many are available.
    """

    def __init__(self, model_name: str = "gpt-4o-mini"):
        # Explicit timeout/retries — the default client has no request
        # timeout at all, so a slow/hung OpenAI response would otherwise
        # block a customer's WhatsApp turn indefinitely instead of failing
        # fast into main.py's fallback message.
        self._base_model = ChatOpenAI(model=model_name, temperature=0, timeout=30, max_retries=2)

    def _resolve_identity(self, company_id: str, state) -> dict:
        """
        Deterministic identity resolution — never left to the model to decide.

        On the very first message of a session (state.history still empty),
        also deterministically prefetches pets + latest booking (SMART
        GREETING needs these) so the model doesn't have to spend a whole
        extra LLM round trip deciding to call get_pets/get_latest_booking
        itself — a session's first turn is exactly when that rule applies.
        """
        is_first_message = not state.history

        if state.customer_id is not None:
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

        if is_first_message and customer.get("found"):
            repo = get_relational_repository()
            pets_future = _TOOL_EXECUTOR.submit(
                repo.list_customer_pets, int(company_id), int(state.customer_id)
            )
            booking_future = _TOOL_EXECUTOR.submit(
                repo.get_latest_booking, int(company_id), int(state.customer_id)
            )
            prefetch_deadline = time_module.monotonic() + TOOL_CALL_TIMEOUT_SECONDS

            def remaining_prefetch_time() -> float:
                return max(0.0, prefetch_deadline - time_module.monotonic())

            try:
                pets_result = pets_future.result(timeout=remaining_prefetch_time())
            except FutureTimeoutError:
                pets_future.cancel()
                pets_result = {"status": "error", "error": "PREFETCH_TIMEOUT"}
            except Exception as exc:
                pets_result = {"status": "error", "error": f"PREFETCH_FAILED:{exc}"}
                logging.getLogger(__name__).exception("Pet prefetch failed: %s", exc)
            try:
                booking_result = booking_future.result(timeout=remaining_prefetch_time())
            except FutureTimeoutError:
                booking_future.cancel()
                booking_result = {"status": "error", "error": "PREFETCH_TIMEOUT"}
            except Exception as exc:
                booking_result = {"status": "error", "error": f"PREFETCH_FAILED:{exc}"}
                logging.getLogger(__name__).exception("Booking prefetch failed: %s", exc)

            if pets_result.get("status") == "success":
                customer["pets"] = (pets_result.get("data") or {}).get("pets", [])
                customer["pets_context_status"] = "available"
            else:
                customer["pets"] = None
                customer["pets_context_status"] = "unavailable"
            if booking_result.get("status") == "success":
                customer["latest_booking"] = booking_result.get("data")
                state.latest_booking = booking_result.get("data")
                customer["booking_context_status"] = "available"
            elif booking_result.get("status") == "not_found":
                customer["latest_booking"] = None
                state.latest_booking = None
                customer["booking_context_status"] = "not_found"
            else:
                # Do not silently turn a database/network failure into "this
                # customer has no booking history". The model must avoid making
                # that false claim, and diagnostics can now distinguish failure
                # from a genuinely empty history.
                customer["latest_booking"] = None
                customer["booking_context_status"] = "unavailable"
            # Deterministic formatting only (see text_formatting.py) — the
            # model still decides what to say; this just removes
            # "Milo, Luna and Coco" vs "Milo, Luna,
            # or Coco" style drift when a message needs to list pet names.
            customer["first_name"] = first_name(customer.get("full_name"))
            customer["pets_formatted"] = format_pet_names(customer["pets"])
            # Same tool never runs as a traced tool_call on the first message
            # (this prefetch bypasses _run_tool), so caching has to happen
            # here too — otherwise a single-pet customer's pet_id/pet_type
            # stays unset all session and every downstream tool call risks
            # getting pet_id=None (breaking species filtering) until the
            # model happens to call find_pet_by_name/get_pets itself.
            self._cache_single_pet(state, customer["pets"] or [])
            state.known_pets = [
                {
                    "pet_id": p.get("pet_id"),
                    "pet_type": p.get("pet_type"),
                    "pet_name": p.get("pet_name"),
                    "pet_size": p.get("size"),
                    "pet_breed": p.get("breed"),
                }
                for p in (customer["pets"] or [])
            ]

        if customer.get("found") and "latest_booking" not in customer and state.latest_booking:
            customer["latest_booking"] = state.latest_booking
            customer["booking_context_status"] = "available"

        if customer.get("found"):
            customer["first_name"] = first_name(customer.get("full_name"))
            if "pets_formatted" not in customer:
                customer["pets_formatted"] = format_pet_names(customer.get("pets") or state.known_pets)

        return customer

    @staticmethod
    def _match_named_pet(state, user_message: str) -> None:
        """
        Multi-pet customers can't rely on the single-pet auto-cache, and the
        model has shown it will guess pet_id/pet_type (including outright
        wrong species) rather than reliably calling find_pet_by_name first
        when the customer just names a pet ("book grooming for Milo"). Do
        the same lookup deterministically: if exactly one known pet's name
        appears in this message, resolve it in code before the model runs.
        """
        if not state.known_pets or not user_message:
            return
        text = user_message.lower()
        matches = [
            p
            for p in state.known_pets
            if p.get("pet_name") and re.search(rf"\b{re.escape(str(p['pet_name']).lower())}\b", text)
        ]
        if len(matches) == 1:
            state.pet_id = matches[0].get("pet_id")
            state.pet_type = matches[0].get("pet_type")
            state.pet_name = matches[0].get("pet_name")
            state.pet_size = matches[0].get("pet_size")
            state.pet_breed = matches[0].get("pet_breed")

    @staticmethod
    def _customer_context_for_state(customer: dict, state) -> dict:
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
        if state.known_pets:
            merged["pets"] = state.known_pets
            merged["pets_context_status"] = "available"
        if state.latest_booking:
            merged["latest_booking"] = state.latest_booking
            merged["booking_context_status"] = "available"
        if merged.get("found"):
            merged["first_name"] = first_name(merged.get("full_name"))
            merged["pets_formatted"] = format_pet_names(merged.get("pets") or [])
        return merged

    def _runtime_context(
        self,
        company_context: dict,
        customer: dict,
        state,
        resolved_selection: dict | None = None,
        available_tools: list | None = None,
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

        runtime_company = {
            **(company_context or {}),
            "business_date": business_now.date().isoformat(),
            "business_datetime": business_now.isoformat(timespec="seconds"),
        }
        # Actual dialogue history remains real messages rather than duplicated
        # JSON. Everything else is refreshed after each tool result, so the
        # next model decision observes newly-created customers/pets, resolved
        # dates, evidence, scenario changes, and completion state immediately.
        state_dict = {k: v for k, v in state.__dict__.items() if k != "history"}
        runtime_context = {
            "company": runtime_company,
            "customer": self._customer_context_for_state(customer, state),
            "conversation_state": state_dict,
            "scenario_definition": scenario,
            "available_tools": [tool.name for tool in (available_tools or [])],
        }
        if resolved_selection is not None:
            runtime_context["resolved_ordinal_selection"] = resolved_selection
        return runtime_context

    def _runtime_message(
        self,
        company_context: dict,
        customer: dict,
        state,
        resolved_selection: dict | None = None,
        available_tools: list | None = None,
    ) -> tuple[str, str]:
        context = self._runtime_context(
            company_context, customer, state, resolved_selection, available_tools
        )
        return ("system", "RUNTIME_CONTEXT:\n" + json.dumps(context, ensure_ascii=False, default=str))

    def build_messages(
        self,
        company_context: dict,
        customer: dict,
        state,
        user_message: str,
        resolved_selection: dict | None = None,
        available_tools: list | None = None,
    ):
        messages = [
            ("system", SYSTEM_PROMPT),
            self._runtime_message(
                company_context, customer, state, resolved_selection, available_tools
            ),
        ]
        for turn in state.history:
            messages.append((turn["role"], turn["content"]))
        messages.append(("human", user_message))
        return messages

    def _refresh_runtime_message(
        self,
        messages: list,
        company_context: dict,
        customer: dict,
        state,
        resolved_selection: dict | None,
        available_tools: list,
    ) -> None:
        messages[1] = self._runtime_message(
            company_context, customer, state, resolved_selection, available_tools
        )

    # Every date argument each date-sensitive tool takes — used to enforce
    # that it was actually produced by resolve_datetime (see
    # _reject_unverified_date) rather than computed/guessed by the model,
    # which has been observed live, repeatedly, even after resolve_datetime
    # existed and was available to call.
    _DATE_ARG_NAMES = {
        "check_availability": ("date", "check_out_date"),
        "check_availability_range": ("start_date", "end_date"),
        "create_booking": ("date", "check_out_date"),
        "reschedule_booking": ("new_date", "new_check_out_date"),
    }

    @staticmethod
    def _reject_unverified_date(state, tool_name: str, args: dict) -> dict | None:
        """None if every date argument present has actually come out of a
        resolve_datetime call this session; otherwise a rejection dict."""
        arg_names = PawfectOrchestrator._DATE_ARG_NAMES.get(tool_name)
        if not arg_names:
            return None
        for arg_name in arg_names:
            value = args.get(arg_name)
            if value and value not in state.resolved_dates:
                return {
                    "error": "UNVERIFIED_DATE",
                    "message": (
                        f"{arg_name} ({value!r}) has not come out of a resolve_datetime "
                        "call this conversation. Call resolve_datetime on the customer's "
                        "ORIGINAL wording for this date, then retry with its exact date "
                        "output — never compute, guess, or reuse a self-adjusted date value."
                    ),
                }
        return None

    @staticmethod
    def _reject_unconfirmed_height(state, args: dict, user_message: str) -> dict | None:
        """
        None if height_cm actually appears (as a real number, not just any
        digit) somewhere in what the customer has actually said this
        session; otherwise a rejection dict. create_pet requiring height_cm
        doesn't stop the model from simply inventing a plausible number
        instead of really asking — confirmed live (height_cm=30 sent with
        no customer message ever containing that number). This can't verify
        the number is TRUE, only that it was actually said by the customer
        rather than fabricated wholesale.
        """
        height_cm = args.get("height_cm")
        if height_cm is None:
            return None
        try:
            height_val = float(height_cm)
        except (TypeError, ValueError):
            return None
        recent_human_text = " ".join(
            [user_message or ""]
            + [t["content"] for t in state.history[-8:] if t.get("role") == "human"]
        )
        mentioned_numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", recent_human_text)]
        if any(abs(n - height_val) <= 2 for n in mentioned_numbers):
            return None
        return {
            "error": "UNCONFIRMED_HEIGHT",
            "message": (
                f"height_cm ({height_cm!r}) does not match any number the customer has "
                "actually said this conversation. Do not invent/estimate a height — ask "
                "the customer directly for their pet's height in cm (a rough estimate is "
                "fine if they don't know exactly), then retry create_pet with the number "
                "they actually give you."
            ),
        }

    @staticmethod
    def _reject_unconfirmed_breed(state, args: dict, user_message: str) -> dict | None:
        """Require breed to come from the customer without enforcing a breed list.

        Exact wording, a meaningful word overlap (for example, "golden" ->
        "Golden Retriever"), and common multilingual mixed/unknown answers are
        accepted. This prevents the model from filling a required schema field
        with an invented breed while leaving customers free to describe it in
        their own words.
        """
        breed = str(args.get("breed") or "").strip()
        if not breed or breed == "-":
            return {
                "error": "MISSING_BREED",
                "message": (
                    "Breed has not been provided. Ask the customer for it without using a "
                    "fixed script; if they do not know or the pet is mixed, preserve that answer."
                ),
            }

        generic_species = {"dog", "cat", "canine", "feline", "犬", "狗", "猫", "貓"}
        if breed.casefold() in generic_species:
            return {
                "error": "SPECIES_IS_NOT_BREED",
                "message": (
                    f"{breed!r} is a species, not a breed. Ask for the breed, or use "
                    "'unknown' only after the customer says they do not know."
                ),
            }

        recent_human_text = " ".join(
            [user_message or ""]
            + [turn["content"] for turn in state.history[-8:] if turn.get("role") == "human"]
        )

        def compact(value: str) -> str:
            return re.sub(r"[^\w\u3400-\u9fff]+", "", value.casefold())

        compact_breed = compact(breed)
        compact_human = compact(recent_human_text)
        if compact_breed and compact_breed in compact_human:
            return None

        # Permit useful natural shorthand without requiring the tool argument
        # to be a byte-for-byte copy of the customer's sentence.
        meaningful_parts = [
            compact(part)
            for part in re.split(r"[^\w\u3400-\u9fff]+", breed)
            if len(compact(part)) >= 3
        ]
        if any(part in compact_human for part in meaningful_parts):
            return None

        unknown_markers = {
            "unknown", "unsure", "notsure", "dontknow", "donotknow", "noidea",
            "taktahu", "tidaktahu", "kurangpasti",
            "不知道", "不清楚", "不确定", "不確定", "不晓得", "不曉得",
        }
        mixed_markers = {
            "mixed", "mixedbreed", "crossbreed", "mongrel", "kacukan",
            "混种", "混種", "米克斯", "串串",
        }
        normalized_answer = compact_breed
        if normalized_answer in {"unknown", "unsure"} and any(
            compact(marker) in compact_human for marker in unknown_markers
        ):
            return None
        if normalized_answer in {"mixed", "mixedbreed"} and any(
            compact(marker) in compact_human for marker in mixed_markers
        ):
            return None

        return {
            "error": "UNCONFIRMED_BREED",
            "message": (
                f"breed ({breed!r}) is not supported by anything the customer has said in "
                "this conversation. Do not infer it; ask naturally, and accept mixed or "
                "unknown when that is the customer's answer."
            ),
        }

    @staticmethod
    def _reject_mismatched_coupon(state, args: dict, user_message: str) -> dict | None:
        """
        None if redeem_reward's coupon_id matches what the customer actually
        asked for by RM amount in recent conversation, or if there's nothing
        to cross-check against; otherwise a rejection dict.

        Confirmed live: customer asked "can I use the RM20 voucher?", model
        replied "I've submitted the request to redeem your RM20 voucher",
        but the actual redeem_reward call used the RM10 Voucher's coupon_id
        (200 points requested, not RM20's 400) — a real, silent swap that
        would have shown staff the wrong voucher to approve. Only rejects
        when a DIFFERENT cached coupon's amount was recently mentioned and
        the chosen one's wasn't — never blocks a redemption made without
        ever stating an RM amount at all (e.g. "redeem points for a free
        grooming session" has no number to check).
        """
        if not state.known_coupons:
            return None
        try:
            coupon_id = int(args.get("coupon_id"))
        except (TypeError, ValueError):
            return None
        chosen = next((c for c in state.known_coupons if c.get("coupon_id") == coupon_id), None)
        if chosen is None:
            return None
        recent_human_text = " ".join(
            [user_message or ""]
            + [t["content"] for t in state.history[-8:] if t.get("role") == "human"]
        ).lower()
        mentioned_amounts = set(re.findall(r"rm\s*(\d+)", recent_human_text))
        if not mentioned_amounts:
            return None
        chosen_amount = str(chosen.get("discount_value") or "").strip()
        if chosen_amount in mentioned_amounts:
            return None
        better = next(
            (
                c for c in state.known_coupons
                if str(c.get("discount_value") or "").strip() in mentioned_amounts
                and c.get("coupon_id") != coupon_id
            ),
            None,
        )
        if better is None:
            return None
        return {
            "error": "COUPON_MISMATCH",
            "message": (
                f"coupon_id {coupon_id} is {chosen.get('reward_name')!r}, but the customer's "
                f"recent messages mention RM{'/RM'.join(sorted(mentioned_amounts))} — that "
                f"matches {better.get('reward_name')!r} (coupon_id {better.get('coupon_id')}) "
                "instead. Retry redeem_reward with the coupon_id that actually matches what "
                "the customer asked for."
            ),
        }

    @staticmethod
    def _reject_unverified_payment_id(state, args: dict) -> dict | None:
        """
        None if redeem_reward's payment_id matches the real payment_id from
        this session's most recent successful create_booking, or if there's
        no such cached value to check against (e.g. redeeming against a
        booking from an earlier session); otherwise a rejection dict.

        Confirmed live: in the very same turn create_booking returned a real
        payment_id (636) in its own result, the model's redeem_reward call
        used payment_id=1 instead — a fabricated placeholder, not anything
        create_booking actually returned. redeem_reward's own docstring
        already says never to guess this; this is the deterministic backstop
        for when that gets ignored anyway.
        """
        if state.last_created_payment_id is None:
            return None
        try:
            given = int(args.get("payment_id"))
        except (TypeError, ValueError):
            given = None
        if given == state.last_created_payment_id:
            return None
        return {
            "error": "UNVERIFIED_PAYMENT_ID",
            "message": (
                f"payment_id {args.get('payment_id')!r} does not match {state.last_created_payment_id}, "
                "the real payment_id this session's own create_booking result actually returned. "
                "Never guess or invent a payment_id — retry redeem_reward with the exact "
                "payment_id (or _internal_payment_id) value from that result."
            ),
        }

    @staticmethod
    def _strip_unconfirmed_pet_name(args: dict, tool_name: str, user_message: str) -> dict:
        """
        cancel_booking/reschedule_booking require confirm_pet_name to match
        the target booking's real pet — but the model already knows that
        name from context and can just fill it in itself instead of
        genuinely waiting for the customer to type it (confirmed live: a
        plain "yes please", not the pet's name, still produced
        confirm_pet_name="Rex" and the cancellation went through). If the
        name given doesn't actually appear in what the customer just typed
        THIS turn, drop it so the tool's own confirmation_required gate
        fires for real instead of trusting the model's say-so.
        """
        if tool_name not in ("cancel_booking", "reschedule_booking"):
            return args
        name = str(args.get("confirm_pet_name") or "").strip()
        if not name:
            return args
        # Word-boundary match, not bare substring — a short/common pet name
        # (e.g. "Bo") would otherwise be satisfied by an unrelated word like
        # "about" or "book" appearing anywhere in the customer's message.
        if re.search(rf"\b{re.escape(name.lower())}\b", (user_message or "").lower()):
            return args
        return {**args, "confirm_pet_name": ""}

    @staticmethod
    def _capture_explicit_loyalty_decision(state, user_message: str) -> None:
        """Remember only loyalty choices the customer actually expressed."""
        text = (user_message or "").strip().lower()
        if not text:
            return
        loyalty_topic = bool(re.search(
            r"\b(?:loyalty|voucher|coupon|points?)\b|积分|点数|优惠券|礼券|"
            r"\b(?:baucar|kupon|ganjaran|keahlian)\b",
            text,
        ))
        declined = bool(
            re.search(
                r"(?:\b(?:no|skip|without|don't|do not|not now)\b.*\b(?:loyalty|voucher|coupon|points?)\b)"
                r"|(?:(?:不用|不要|不需要|跳过).*(?:积分|点数|优惠券|礼券))"
                r"|(?:(?:积分|点数|优惠券|礼券).*(?:不用|不要|不需要|跳过))"
                r"|(?:\b(?:tak|tidak)\b.*\b(?:baucar|kupon|ganjaran|points?)\b)"
                r"|(?:\b(?:baucar|kupon|ganjaran|points?)\b.*\b(?:tak|tidak)\b)",
                text,
            )
        )
        accepted = loyalty_topic and bool(
            re.search(
                r"\b(?:use|apply|redeem|yes|join)\b|使用|要用|兑换|加入|\b(?:guna|boleh|nak|mahu)\b",
                text,
            )
        )

        # A short yes/no is only attributable to loyalty when a real loyalty
        # check happened on an earlier customer turn. We deliberately do not
        # infer consent from a generic "yes" otherwise because it may be the
        # booking confirmation instead.
        if state.loyalty_offer_shown_turn is not None and state.loyalty_offer_shown_turn < state.turn_counter:
            if text in {"no", "no thanks", "no need", "skip", "不用", "不要", "不需要", "跳过", "tak", "tidak", "tak nak", "tidak mahu"}:
                declined = True
            elif text in {"yes", "yes please", "要", "可以", "use it", "使用", "ya", "boleh", "nak"}:
                accepted = True

        if declined:
            state.loyalty_decision = "declined"
        elif accepted:
            state.loyalty_decision = "accepted"
        else:
            return
        state.verified_facts["loyalty_decision"] = {
            "value": state.loyalty_decision,
            "source": "customer_message",
            "turn": state.turn_counter,
        }

    # PDPA/GDPR-style "delete my data" requests have no self-service tool —
    # there is no create_pet-style write path that could safely automate
    # cross-table personal-data erasure, and building one is a much bigger,
    # riskier feature than this needs. Escalate to a human (HITL) instead:
    # detect the request deterministically (never rely on the LLM alone to
    # recognize and correctly route something this consequential) and save
    # a staff enquiry with a distinct reason, same mechanism as every other
    # handoff_required path in this file.
    _DATA_DELETION_RE = re.compile(
        r"\bdelete\s+(?:all\s+)?(?:my\s+)?(?:account|data|profile|information|records?|personal\s+data)\b"
        r"|\bremove\s+(?:all\s+)?(?:my\s+)?(?:account|data|profile|information)\b"
        r"|\b(?:close|deactivate)\s+my\s+account\b"
        r"|\bforget\s+(?:about\s+)?me\b"
        r"|删除.{0,6}(?:我的)?(?:资料|帐号|账号|帳號|数据|資料|个人信息|個人資料)"
        r"|注销.{0,4}(?:账号|帳號)"
        r"|清除.{0,6}我的.{0,6}(?:资料|数据|信息)"
        r"|padam\s+(?:semua\s+)?(?:data|akaun|maklumat)\s+saya"
        r"|hapus\s+(?:semua\s+)?(?:data|akaun|maklumat)\s+saya"
        r"|batalkan\s+akaun\s+saya",
        re.IGNORECASE,
    )

    @classmethod
    def _is_data_deletion_request(cls, user_message: str) -> bool:
        return bool(cls._DATA_DELETION_RE.search(user_message or ""))

    @staticmethod
    def _capture_explicit_daycare_duration(state, user_message: str) -> None:
        """Cache an exact customer-stated DAYCARE duration across side flows."""
        text = str(user_message or "").strip()
        if not text:
            return
        is_daycare_context = str(state.service_type or "").upper() == "DAYCARE" or bool(
            re.search(r"\bday\s*-?care\b|日托|日间托管|日間托管", text, re.IGNORECASE)
        )
        if not is_daycare_context:
            return
        duration = extract_duration_minutes(text)
        if not duration:
            return
        state.daycare_duration_minutes = duration
        state.verified_facts["daycare_duration_minutes"] = {
            "value": duration,
            "source": "customer_message",
            "turn": state.turn_counter,
        }

    @staticmethod
    def _cache_daycare_duration_from_selection(state, selection: dict | None) -> None:
        """Use duration only when the real structured option is exact."""
        if not isinstance(selection, dict):
            return
        duration = selection.get("duration_minutes")
        if not duration:
            return
        try:
            parsed = int(duration)
        except (TypeError, ValueError):
            return
        if not 0 < parsed <= 24 * 60:
            return
        state.daycare_duration_minutes = parsed
        state.verified_facts["daycare_duration_minutes"] = {
            "value": parsed,
            "source": "selected_catalogue_option",
            "label": selection.get("label") or selection.get("service_name"),
            "turn": state.turn_counter,
        }

    @staticmethod
    def _inject_cached_daycare_duration(state, tool_name: str, args: dict) -> dict:
        """Fill a dropped duration for availability/write calls, never an exact pickup."""
        if tool_name not in {"check_availability", "check_availability_range", "create_booking"}:
            return args
        service_type = str(args.get("service_type") or state.service_type or "").strip().upper()
        if service_type != "DAYCARE" or not state.daycare_duration_minutes:
            return args
        if args.get("duration_minutes") not in (None, ""):
            return args
        if tool_name != "check_availability_range" and str(args.get("check_out_time") or "").strip():
            return args
        return {**args, "duration_minutes": state.daycare_duration_minutes}

    # Mutating tools that INSERT a new row rather than idempotently update
    # one — a duplicate call with identical args is never a legitimate
    # second action (a genuinely different booking/redemption always differs
    # in at least one arg), so it's safe to treat as the "yes confirm" the
    # customer already said once, repeated by mistake (confirmed live: this
    # produced real duplicate bookings and a real double loyalty-point
    # deduction). See state.last_mutation.
    _DEDUPED_MUTATION_TOOLS = {"create_booking", "redeem_reward"}

    @staticmethod
    def _mutation_signature(tool_name: str, args: dict) -> str:
        normalized = {
            k: (round(v, 2) if isinstance(v, float) else v) for k, v in sorted(args.items())
        }
        return f"{tool_name}:{json.dumps(normalized, sort_keys=True, default=str)}"

    @staticmethod
    def _known_pet_by_id(state, pet_id) -> dict | None:
        """The state.known_pets entry matching pet_id, or None if pet_id is
        missing/blank or isn't actually one of this customer's real pets
        (i.e. the model hallucinated or mismatched it)."""
        if pet_id is None or str(pet_id).strip() == "":
            return None
        try:
            pet_id_int = int(pet_id)
        except (TypeError, ValueError):
            return None
        return next((p for p in state.known_pets if p.get("pet_id") == pet_id_int), None)

    def _run_tool(self, tool_call: dict, state=None, user_message: str = "", sibling_tool_names: frozenset = frozenset()):
        """Execute one tool call and return its raw result (dict). No state mutation here — safe to run off-thread.

        tool_call["args"] is overwritten in-place (via the finally block
        below) with whatever args actually ended up being used, once this
        returns — every deterministic override above can change args away
        from what the model originally requested, and invoke_with_trace's
        trace.append / _cache_last_mutation both read tool_call["args"]
        afterwards. Previously they read the model's original, pre-override
        args instead, so every tool_trace this whole project has shown was
        potentially misleading — it displayed what the model asked for, not
        what actually ran (confirmed live: a stale pet_id override silently
        executed against the wrong pet while the trace kept showing the
        model's correct, intended pet_id).
        """
        tool = TOOLS_BY_NAME.get(tool_call["name"])
        if tool is None:
            return {"error": f"UNKNOWN_TOOL:{tool_call['name']}"}
        args = dict(tool_call.get("args") or {})
        try:
            if state is not None:
                if tool_call["name"] in COMPANY_SCOPED_TOOL_NAMES or "company_id" in args:
                    # Tenant scope is request/session authority, never a model
                    # choice. Every company-scoped tool receives the company
                    # attached to this ConversationState even if the model
                    # omits, hallucinates, or reuses another tenant's ID.
                    args = {**args, "company_id": state.company_id}
                if tool_call["name"] == "create_customer":
                    # The inbound, validated chat identity is authoritative;
                    # registration must never use a model-invented phone.
                    args = {
                        **args,
                        "company_id": state.company_id,
                        "phone_number": state.phone_number,
                    }
                if tool_call["name"] in CUSTOMER_SCOPED_TOOL_NAMES:
                    # Also the one place check_availability's customer_id gets
                    # set for slot-hold keying (see app.context.slot_holds) —
                    # no separate special-case needed for that.
                    if state.customer_id is not None:
                        # state.customer_id is deterministically resolved (real
                        # phone lookup, or cached from this session's own
                        # create_customer call) and is authoritative — never
                        # trust whatever the model supplies instead, same
                        # reasoning as pet_id below.
                        args = {**args, "customer_id": state.customer_id}
                    elif tool_call["name"] != "check_availability":
                        # No real identity exists yet for this phone number.
                        # Confirmed live: for a brand-new customer, the model
                        # skipped ever calling create_customer and instead
                        # called create_pet/get_pets with a FABRICATED
                        # customer_id (it guessed "1") — which happened to be a
                        # different real customer, and the model went on to
                        # read and relay that stranger's real pet's private
                        # details (breed, allergy notes, vaccination status) to
                        # the new customer. Block any customer-scoped call
                        # until create_customer has actually run this session.
                        return {
                            "error": "CUSTOMER_NOT_RESOLVED",
                            "message": (
                                "No customer_id has been established for this phone number yet "
                                "— call create_customer with the customer's name first (their "
                                "phone number is already known from RUNTIME_CONTEXT, never "
                                "invent or reuse someone else's customer_id), then retry this "
                                "call with the real customer_id it returns."
                            ),
                        }
                if tool_call["name"] in ("get_booking_service_options", "create_booking"):
                    # state.pet_id is only the LAST resolved pet, not necessarily
                    # the one this turn is actually about — in a multi-pet
                    # conversation, once the customer moves on to a second pet
                    # without re-typing its exact name (e.g. "and what about my
                    # cat?"), state.pet_id/pet_type/pet_size still point at the
                    # FIRST pet. Blindly overriding here (the old behaviour)
                    # forced every later get_booking_service_options/
                    # create_booking call back onto that stale pet — confirmed
                    # live: asking about a second pet's grooming after the first
                    # kept silently returning the first pet's species/pricing
                    # data. Only fall back to the cached pet when the model's
                    # own pet_id is missing or isn't actually one of this
                    # customer's real pets (still guards the original case: a
                    # hallucinated/mismatched pet_id) — otherwise trust it.
                    known = self._known_pet_by_id(state, args.get("pet_id"))
                    if known is None and state.pet_id is not None:
                        args = {**args, "pet_id": state.pet_id}
                if tool_call["name"] == "create_booking":
                    if args.get("preferred_staff"):
                        state.preferred_staff = str(args["preferred_staff"])
                    elif state.preferred_staff:
                        args = {**args, "preferred_staff": state.preferred_staff}
                args = self._inject_cached_daycare_duration(state, tool_call["name"], args)
                if tool_call["name"] == "send_booking_confirmation" and args.get("booking_id") and not args.get("service_type"):
                    try:
                        requested_booking_id = int(args["booking_id"])
                    except (TypeError, ValueError):
                        requested_booking_id = None
                    known_bookings = [
                        state.last_created_booking,
                        state.latest_booking,
                    ]
                    matching_booking = next(
                        (
                            booking for booking in known_bookings
                            if booking
                            and booking.get("booking_id") == requested_booking_id
                            and (
                                booking.get("service_type")
                                or booking.get("last_service_type")
                            )
                        ),
                        None,
                    )
                    if matching_booking:
                        args = {
                            **args,
                            "service_type": (
                                matching_booking.get("service_type")
                                or matching_booking.get("last_service_type")
                            ),
                        }
                if (
                    tool_call["name"] in ("cancel_booking", "reschedule_booking")
                    and not args.get("booking_id")
                    and args.get("confirm_pet_name")
                    and state.pending_booking_confirmation
                    and state.pending_booking_confirmation.get("tool") == tool_call["name"]
                ):
                    # Companion bug to the one below: once the customer replies
                    # with just the pet's name to confirm, the model has been
                    # observed calling this tool again with confirm_pet_name set
                    # but WITHOUT re-passing the booking_id/service_type from the
                    # confirmation_required turn one message ago — which makes it
                    # re-resolve from scratch and hit "ambiguous, multiple active
                    # bookings" again, even though the specific target was
                    # already identified and confirmed. Re-inject both from the
                    # cached confirmation_required result instead.
                    args = {
                        **args,
                        "booking_id": state.pending_booking_confirmation["booking_id"],
                        "service_type": state.pending_booking_confirmation.get("service_type") or args.get("service_type"),
                    }
                if tool_call["name"] in ("cancel_booking", "reschedule_booking") and args.get("booking_id"):
                    # cancel_booking/reschedule_booking's service_type parameter
                    # defaults to "GROOMING" when omitted — confirmed live: after
                    # an "ambiguous, multiple active bookings" result, the model
                    # picked the right booking_id from the candidates (each
                    # candidate carries its own real service_type) but called
                    # this tool again WITHOUT passing service_type, silently
                    # defaulting to GROOMING. get_booking_by_id then looked in
                    # the wrong table for a real DAYCARE booking_id and reported
                    # "booking could not be found" — a real, cancelable booking
                    # the customer just confirmed was unreachable. state.offered_options
                    # (populated from that same candidates list — see
                    # _cache_offered_options) still has the real service_type
                    # for this booking_id; trust that over the model's default.
                    try:
                        target_booking_id = int(args["booking_id"])
                    except (TypeError, ValueError):
                        target_booking_id = None
                    if target_booking_id is not None:
                        match = next(
                            (
                                opt for opt in state.offered_options
                                if opt.get("booking_id") == target_booking_id and opt.get("service_type")
                            ),
                            None,
                        )
                        if match is not None:
                            args = {**args, "service_type": match["service_type"]}
                if tool_call["name"] == "retrieve_policy":
                    # Same problem, one layer up: the model can call retrieve_policy
                    # directly (bypassing the pet_id-aware bundling inside
                    # get_booking_service_options) and skip pet_type/pet_size
                    # entirely — fill those in from the last resolved pet so
                    # species filtering is never silently skipped. The pet most
                    # recently resolved from the real roster is authoritative;
                    # otherwise preserve the dog/cat species inferred by the LLM
                    # from the customer's breed wording.
                    #
                    # Previously gated on service_type=="grooming" specifically,
                    # so a grooming question where the model omitted
                    # service_type entirely (a valid call — the tool's own
                    # signature allows it unset for a general/ambiguous
                    # enquiry) skipped this backfill outright. Not narrowed to
                    # any one service_type now: CompanyRAGRetriever.search only
                    # ever DROPS chunks explicitly tagged for the other
                    # species (app/rag/retriever.py) — species-neutral chunks
                    # tagged "all"/untagged are never affected — so passing a
                    # known pet_type here is safe regardless of which policy
                    # topic is being asked about, never just grooming.
                    authoritative_species = str(state.pet_type or "").strip().lower()
                    if authoritative_species not in ("dog", "cat"):
                        model_species = str(args.get("pet_type") or "").strip().lower()
                        authoritative_species = model_species if model_species in ("dog", "cat") else ""
                    if authoritative_species:
                        # pet_type is the species filter; service_type remains the
                        # business category (grooming). Always override a model
                        # mismatch so a Samoyed can never receive cat packages.
                        args = {**args, "pet_type": authoritative_species}
                    if not args.get("pet_size") and state.pet_size:
                        args = {**args, "pet_size": state.pet_size}

                args = self._strip_unconfirmed_pet_name(args, tool_call["name"], user_message)
                date_rejection = self._reject_unverified_date(state, tool_call["name"], args)
                if date_rejection:
                    return date_rejection
                if tool_call["name"] == "create_pet":
                    breed_rejection = self._reject_unconfirmed_breed(state, args, user_message)
                    if breed_rejection:
                        return breed_rejection
                    height_rejection = self._reject_unconfirmed_height(state, args, user_message)
                    if height_rejection:
                        return height_rejection
                if tool_call["name"] == "redeem_reward":
                    if "create_booking" in sibling_tool_names:
                        # Batched together in the same model response — confirmed
                        # live this is exactly when redeem_reward gets a
                        # fabricated/missing payment_id (create_booking's real
                        # one doesn't exist yet at dispatch time; they run
                        # concurrently, and even sequentially, this call's own
                        # args were built before create_booking's result existed).
                        # Reject and let the model retry redeem_reward alone,
                        # on its own next round, once create_booking's real
                        # result is actually in its context.
                        return {
                            "error": "REDEEM_BEFORE_BOOKING_RESULT",
                            "message": (
                                "redeem_reward was called in the same batch as create_booking — "
                                "its real payment_id doesn't exist yet at this point. Wait for "
                                "create_booking's result, then call redeem_reward by itself, using "
                                "the real payment_id it returned."
                            ),
                        }
                    payment_id_rejection = self._reject_unverified_payment_id(state, args)
                    if payment_id_rejection:
                        return payment_id_rejection
                    coupon_rejection = self._reject_mismatched_coupon(state, args, user_message)
                    if coupon_rejection:
                        return coupon_rejection
                loyalty_ready = (
                    state.loyalty_decision in {"accepted", "declined"}
                    or (
                        state.loyalty_offer_shown_turn is not None
                        and state.loyalty_offer_shown_turn < state.turn_counter
                    )
                )
                if tool_call["name"] == "create_booking" and not loyalty_ready:
                    return {
                        "error": "LOYALTY_OFFER_PENDING",
                        "message": (
                            "Before finalizing this booking, check the customer's loyalty "
                            "status — call get_loyalty_balance and/or check_coupon_eligibility "
                            "now. If they're a member with a coupon they can afford, offer it; "
                            "if not a member, briefly offer to join (register_loyalty_member "
                            "if they say yes). Then END YOUR REPLY HERE with that question — "
                            "do not also call create_booking in this same reply. Only retry "
                            "create_booking once the customer has actually answered you on "
                            "their NEXT message (whether they accept, decline, or ignore the "
                                "offer, proceed with the booking either way at that point). If the "
                                "customer has already explicitly accepted or declined loyalty in "
                                "their current message, record that choice and proceed without "
                                "forcing an extra turn. This check only blocks an unresolved offer."
                        ),
                    }
                if tool_call["name"] == "register_loyalty_member" and args.get("confirmed"):
                    if not (
                        state.register_preview_turn is not None and state.register_preview_turn < state.turn_counter
                    ):
                        # The model can fabricate confirmed=true in the same
                        # reply as the preview instead of genuinely waiting for
                        # the customer's next message to say yes (same bypass
                        # class as confirm_pet_name) — force it back to a
                        # preview unless the first confirmation_required
                        # genuinely happened on an earlier turn.
                        args = {**args, "confirmed": False}
                if tool_call["name"] in self._DEDUPED_MUTATION_TOOLS:
                    signature = self._mutation_signature(tool_call["name"], args)
                    last = state.last_mutation
                    if last and last.get("tool") == tool_call["name"] and last.get("signature") == signature:
                        return {
                            **last["result"],
                            "duplicate_suppressed": True,
                            "message": (
                                "This exact request already completed successfully earlier "
                                "this conversation — not repeating it (a real second time "
                                "would double-book/double-spend). Treat this as confirmation "
                                "it's already done; if the customer wants something genuinely "
                                "different, ask what's different instead of confirming again."
                            ),
                        }
            try:
                return tool.invoke(args)
            except Exception as exc:  # tool/data-layer failure -> structured error, never crash the turn
                return {"error": str(exc)}
        finally:
            tool_call["args"] = args

    @staticmethod
    def _cache_single_pet(state, pets: list[dict]) -> None:
        """If the customer has exactly one pet, there's no ambiguity — cache it
        so pet_id/pet_type never need to be re-derived (or guessed as None) by
        the model on later turns."""
        if len(pets) == 1:
            state.pet_id = pets[0].get("pet_id")
            state.pet_type = pets[0].get("pet_type")
            state.pet_name = pets[0].get("pet_name")
            state.pet_size = pets[0].get("size")
            state.pet_breed = pets[0].get("breed")

    def _cache_resolved_pet(self, state, tool_name: str, result: dict, args: dict | None = None) -> None:
        """Deterministically remember which pet the conversation is about."""
        if tool_name in ("get_booking_service_options", "create_booking") and isinstance(result, dict):
            # These don't return pet data in their result (get_booking_service_options
            # has none at all; create_booking's is keyed by booking fields, not
            # pet_id) — but tool_call["args"] now holds whatever pet_id was
            # ACTUALLY executed (see _run_tool's finally block), so use that.
            # Without this, a multi-pet conversation that just correctly
            # looked up a SECOND pet (e.g. "and what about my cat?") would
            # still have state.pet_id pointing at the FIRST pet on the next
            # turn — confirmed live: the model then got confused about which
            # pet a later ambiguous "her"/"him" referred to.
            if result.get("status") in ("success", "duplicate_suppressed"):
                known = self._known_pet_by_id(state, (args or {}).get("pet_id"))
                if known is not None:
                    state.pet_id = known.get("pet_id")
                    state.pet_type = known.get("pet_type")
                    state.pet_name = known.get("pet_name")
                    state.pet_size = known.get("pet_size")
                    state.pet_breed = known.get("pet_breed")
            return
        if tool_name == "find_pet_by_name" and result.get("status") == "success":
            data = result.get("data") or {}
            if data.get("pet_id") is not None:
                state.pet_id = data.get("pet_id")
                state.pet_type = data.get("pet_type")
                state.pet_name = data.get("pet_name")
                state.pet_size = data.get("size")
                state.pet_breed = data.get("breed")
        elif tool_name == "get_pets" and result.get("status") == "success":
            pets = (result.get("data") or {}).get("pets") or []
            self._cache_single_pet(state, pets)
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
        elif tool_name == "create_pet" and result.get("status") == "success":
            # A brand-new pet just got a real pet_id — cache it the same way
            # create_customer's own result gets cached, otherwise nothing
            # ever writes this pet into state. It was reachable within the
            # same turn purely from the model's own short-term context (the
            # tool's own JSON output was still in the message history), but
            # that memory doesn't survive a later scenario switch (e.g.
            # GROOMING -> BOARDING) — the model then re-asks for the pet's
            # height as if it were never registered, even though it was.
            pet = (result.get("data") or {}).get("pet") or {}
            if pet.get("pet_id") is not None:
                state.pet_id = pet.get("pet_id")
                state.pet_type = pet.get("pet_type")
                state.pet_name = pet.get("pet_name")
                state.pet_size = pet.get("size")
                state.pet_breed = pet.get("breed")
                state.known_pets = state.known_pets + [
                    {
                        "pet_id": pet.get("pet_id"),
                        "pet_type": pet.get("pet_type"),
                        "pet_name": pet.get("pet_name"),
                        "pet_size": pet.get("size"),
                        "pet_breed": pet.get("breed"),
                    }
                ]

    @staticmethod
    def _cache_latest_booking(state, tool_name: str, result: dict) -> None:
        """Keep DB-fetched existing bookings visible after the tool turn."""
        if tool_name not in ("get_latest_booking", "get_booking_by_id", "reschedule_booking"):
            return
        if not isinstance(result, dict) or result.get("status") != "success":
            return
        data = result.get("data") or {}
        if data:
            state.latest_booking = dict(data)

    @staticmethod
    def _cache_offered_options(state, tool_name: str, result: dict) -> None:
        """
        Deterministic backstop for replies like "the second one"/"number 2".
        ConversationState already had an offered_options field for exactly
        this — it just sat unused, never populated or read anywhere, so
        ordinal references were left entirely to the model re-deriving them
        from raw conversation text. Populate it here whenever a tool call
        actually presents the customer a numbered list of real choices.
        """
        if tool_name not in (
            "get_booking_service_options",
            "check_availability",
            "check_availability_range",
            "cancel_booking",
            "reschedule_booking",
        ):
            return
        # retrieve_policy (and others) return list[dict], not dict — check
        # the type before .get(), a bare result.get("status") crashed the
        # whole turn the first time retrieve_policy ran in the same batch
        # of tool calls as one of these.
        if not isinstance(result, dict):
            return
        if tool_name in ("cancel_booking", "reschedule_booking"):
            # The model's own customer-facing summary of an "ambiguous"
            # candidates list has been observed truncating it (13 real
            # candidates shown as 2) — this is the real, complete list to
            # resolve "the second one"/"the daycare one" against regardless
            # of how much of it actually made it into the reply text.
            if result.get("status") == "ambiguous":
                candidates = (result.get("data") or {}).get("candidates") or []
                if candidates:
                    state.offered_options = [
                        {
                            "label": f"{c.get('pet_name')} — {c.get('package_name')} ({c.get('date')})",
                            **c,
                        }
                        for c in candidates
                    ]
            return
        range_success = tool_name == "check_availability_range" and isinstance(result.get("days"), list)
        if result.get("status") != "success" and not range_success:
            if result.get("status") in {"not_found", "error", "missing_information"}:
                state.offered_options = []
            return
        data = result.get("data") or {}
        if tool_name == "get_booking_service_options":
            options = data.get("service_options") or []
            state.offered_options = [
                {"label": opt.get("service_name") or opt.get("room_type"), **opt} for opt in options
            ]
        elif tool_name == "check_availability":
            slots = data.get("available_slots") or []
            state.offered_options = [{"label": slot, "slot": slot} for slot in slots]
        elif tool_name == "check_availability_range":
            days = result.get("days") or data.get("days") or []
            state.offered_options = [
                {
                    "label": f"{day.get('date')} {slot}",
                    "date": day.get("date"),
                    "weekday": day.get("weekday"),
                    "slot": slot,
                }
                for day in days
                for slot in (day.get("available_slots") or [])
            ]

    @staticmethod
    def _cache_pending_booking_confirmation(state, tool_name: str, result: dict, args: dict) -> None:
        """Remember which specific booking cancel_booking/reschedule_booking
        is currently targeting — see ConversationState.pending_booking_confirmation.

        Not just for the "confirmation_required" case: confirmed live that a
        reschedule_booking call can also get pre-execution-rejected for an
        unrelated reason (UNVERIFIED_DATE, thrown by _reject_unverified_date
        in _run_tool BEFORE the real tool ever runs) while still carrying a
        perfectly real, correctly-identified booking_id in its args. The
        model then spent the rest of that turn re-resolving the date and
        never retried the actual tool call — and on the NEXT turn, having
        never spoken the numeric booking_id out loud in its own reply text,
        it had nothing left to recover it from and called the tool again
        with booking_id blank, hitting "ambiguous, multiple active bookings"
        again despite the target already being unambiguous a turn earlier.
        So: cache args["booking_id"] whenever one is present, regardless of
        outcome, EXCEPT when the result itself proves it doesn't actually
        identify a real, unambiguous booking (ambiguous/not_found) or the
        action already finished (success) — both of those should clear it
        instead, so a stale target can never leak into a later, different
        booking's flow."""
        if tool_name not in ("cancel_booking", "reschedule_booking"):
            return
        if not isinstance(result, dict):
            return
        status = result.get("status")
        if status == "confirmation_required":
            data = result.get("data") or {}
            if data.get("booking_id") is not None:
                state.pending_booking_confirmation = {
                    "tool": tool_name,
                    "booking_id": data.get("booking_id"),
                    "service_type": data.get("service_type"),
                }
            return
        if status in ("success", "ambiguous", "not_found"):
            state.pending_booking_confirmation = None
            return
        booking_id = args.get("booking_id")
        if booking_id:
            try:
                booking_id_int = int(booking_id)
            except (TypeError, ValueError):
                booking_id_int = None
            if booking_id_int is not None:
                state.pending_booking_confirmation = {
                    "tool": tool_name,
                    "booking_id": booking_id_int,
                    "service_type": args.get("service_type"),
                }

    @staticmethod
    def _cache_resolved_date(state, tool_name: str, result: dict) -> None:
        """
        Record every date resolve_datetime actually returns this session —
        the whitelist _reject_unverified_date checks tool calls against.
        Persists across turns (state, not a per-turn local), so a date
        resolved several turns ago (e.g. check-in, confirmed earlier) is
        still valid to reuse later (e.g. at create_booking time) without
        re-resolving it.
        """
        if tool_name != "resolve_datetime" or not isinstance(result, dict):
            return
        for value in (result.get("date"), *((result.get("date_range") or {}).values())):
            if value and value not in state.resolved_dates:
                state.resolved_dates.append(value)

    @staticmethod
    def _cache_known_coupons(state, tool_name: str, result: dict) -> None:
        """Record check_coupon_eligibility's real coupons — see
        ConversationState.known_coupons."""
        if tool_name != "check_coupon_eligibility" or not isinstance(result, dict):
            return
        if result.get("status") != "success":
            return
        coupons = (result.get("data") or {}).get("eligible_coupons") or []
        if coupons:
            state.known_coupons = [
                {
                    "coupon_id": c.get("coupon_id"),
                    "reward_name": c.get("reward_name"),
                    "discount_value": c.get("discount_value"),
                }
                for c in coupons
            ]

    @staticmethod
    def _tool_result_status(result) -> str:
        if isinstance(result, list):
            return "success"
        if not isinstance(result, dict):
            return "error"
        if result.get("status"):
            return str(result["status"])
        if result.get("error"):
            return "error"
        if result.get("found") is False:
            return "not_found"
        return "success"

    @staticmethod
    def _compact_evidence_result(result, max_chars: int = 1800):
        """Bound cross-turn evidence size and remove internal delivery fields."""
        def sanitize(value, depth: int = 0):
            if depth > 3:
                return "[nested data omitted]"
            if isinstance(value, dict):
                return {
                    key: sanitize(item, depth + 1)
                    for key, item in value.items()
                    if not str(key).startswith("_internal_")
                }
            if isinstance(value, list):
                return [sanitize(item, depth + 1) for item in value[:8]]
            if isinstance(value, str) and len(value) > 500:
                return value[:500] + "…"
            return value

        cleaned = sanitize(result)
        encoded = json.dumps(cleaned, ensure_ascii=False, default=str)
        if len(encoded) <= max_chars:
            return cleaned
        return {"preview": encoded[:max_chars] + "…", "truncated": True}

    @classmethod
    def _update_agent_evidence(cls, state, tool_name: str, result) -> None:
        """Record observed facts, never private model reasoning or guesses."""
        status = cls._tool_result_status(result)
        evidence = {
            "turn": state.turn_counter,
            "tool": tool_name,
            "status": status,
            "result": cls._compact_evidence_result(result),
        }
        state.recent_tool_evidence = (
            state.recent_tool_evidence + [evidence]
        )[-MAX_RECENT_TOOL_EVIDENCE:]

        if isinstance(result, dict):
            data = result.get("data") if isinstance(result.get("data"), dict) else result
            missing = data.get("missing_fields") or result.get("missing_fields") or []
            if status == "missing_information" and isinstance(missing, list):
                state.missing_information_by_tool[tool_name] = [str(item) for item in missing]
            elif status == "success":
                state.missing_information_by_tool.pop(tool_name, None)
            state.missing_information = list(dict.fromkeys(
                item
                for items in state.missing_information_by_tool.values()
                for item in items
            ))

        fact_names = {
            "resolve_datetime": "resolved_datetime",
            "get_pets": "customer_pets",
            "find_pet_by_name": "resolved_pet",
            "get_latest_booking": "latest_booking",
            "get_last_completed_booking": "last_completed_booking",
            "get_booking_by_id": "resolved_booking",
            "get_booking_service_options": "service_options",
            "retrieve_policy": "policy_knowledge",
            "check_availability": "availability",
            "check_availability_range": "availability_range",
            "get_loyalty_balance": "loyalty_balance",
            "check_coupon_eligibility": "coupon_eligibility",
            "get_payment_history": "payment_history",
            "get_redemption_history": "redemption_history",
            "send_booking_confirmation": "confirmation_delivery",
            "create_customer": "resolved_customer",
            "create_pet": "resolved_pet",
            "update_pet_vaccination": "vaccination_update",
            "create_booking": "created_booking",
            "cancel_booking": "cancelled_booking",
            "reschedule_booking": "rescheduled_booking",
            "redeem_reward": "redemption_request",
            "register_loyalty_member": "loyalty_membership",
        }
        fact_name = fact_names.get(tool_name)
        if fact_name and status in {"success", "confirmation_required", "ambiguous", "not_found"}:
            state.verified_facts[fact_name] = evidence

        if tool_name in {
            "create_booking", "cancel_booking", "reschedule_booking",
            "redeem_reward", "send_booking_confirmation",
        }:
            state.completion_status = "action_succeeded" if status == "success" else "action_incomplete"

    @staticmethod
    def _set_objective_from_scenario(state, scenario_name: str | None) -> None:
        if not scenario_name:
            state.objective = None
            return
        try:
            state.objective = load_scenario(scenario_name).get("goal")
        except ValueError:
            state.objective = None

    # Tools whose successful execution completes one concrete action. Clear
    # scenario routing afterwards so a later customer request is not trapped
    # behind stale tool gating; cached evidence/booking facts remain available,
    # and the model may freely continue with another requested booking.
    # A successful mutation is stronger evidence than the model's separately
    # declared scenario and safely releases scenario routing afterwards.
    _SCENARIO_CONFIRMING_TOOLS = {
        "create_booking": "MAKE_BOOKING",
        "cancel_booking": "CANCEL_BOOKING",
        "reschedule_booking": "RESCHEDULE_BOOKING",
        "redeem_reward": "LOYALTY_QUERY",
    }

    _LOYALTY_TOOL_NAMES = {
        "get_loyalty_balance",
        "check_coupon_eligibility",
        "register_loyalty_member",
        "redeem_reward",
    }

    @classmethod
    def _cache_loyalty_offer_shown(cls, state, tool_name: str) -> None:
        if tool_name in cls._LOYALTY_TOOL_NAMES:
            state.loyalty_offer_shown_turn = state.turn_counter

    @staticmethod
    def _cache_register_preview(state, tool_name: str, result: dict) -> None:
        if tool_name == "register_loyalty_member" and isinstance(result, dict) and result.get("status") == "confirmation_required":
            state.register_preview_turn = state.turn_counter

    @classmethod
    def _cache_last_mutation(cls, state, tool_name: str, args: dict, result: dict) -> None:
        """Record the last successful create_booking/redeem_reward call so an
        identical repeat is recognized as a duplicate confirm, not a new
        action — see _DEDUPED_MUTATION_TOOLS. Cleared on failure so a retry
        after a real error is never blocked."""
        if tool_name not in cls._DEDUPED_MUTATION_TOOLS:
            return
        if isinstance(result, dict) and result.get("status") == "success" and not result.get("duplicate_suppressed"):
            state.last_mutation = {
                "tool": tool_name,
                "signature": cls._mutation_signature(tool_name, args),
                "result": result,
            }
        elif isinstance(result, dict) and result.get("status") not in ("success",):
            state.last_mutation = None

    @classmethod
    def _sync_scenario_from_tool_call(cls, state, tool_name: str, result: dict) -> None:
        """
        Release scenario routing after a real action succeeds. Cached evidence
        remains, so the agent can continue another explicitly requested action
        without being trapped in the completed scenario.
        """
        confirmed = cls._SCENARIO_CONFIRMING_TOOLS.get(tool_name)
        if confirmed and isinstance(result, dict) and result.get("status") == "success":
            state.active_scenario = None
            state.current_step = None
            state.service_type = None
            state.objective = None
            state.offered_options = []
            state.missing_information = []
            state.missing_information_by_tool = {}

    _ORDINAL_WORDS = {
        "first": 1, "1st": 1,
        "second": 2, "2nd": 2,
        "third": 3, "3rd": 3,
        "fourth": 4, "4th": 4,
        "fifth": 5, "5th": 5,
        "第一": 1, "第一个": 1,
        "第二": 2, "第二个": 2,
        "第三": 3, "第三个": 3,
        "第四": 4, "第四个": 4,
        "第五": 5, "第五个": 5,
        # Malay (Bahasa Malaysia) — see time_normalization.py's Malay period
        # words for why this business's own market can't be left English/
        # Chinese-only in every deterministic guardrail.
        "pertama": 1,
        "kedua": 2,
        "ketiga": 3,
        "keempat": 4,
        "kelima": 5,
    }

    @classmethod
    def _detect_ordinal_index(cls, text: str) -> int | None:
        """1-indexed position referenced by phrases like "the second one",
        "number 2", "option 2", "第二个" — deliberately narrow patterns only,
        so an unrelated number in the message (a price, a duration) is never
        misread as a list-position reference."""
        lowered = (text or "").strip().lower()
        match = re.search(r"\b(?:number|option|choice)\s*#?\s*(\d+)\b", lowered)
        if match:
            return int(match.group(1))
        for word, idx in cls._ORDINAL_WORDS.items():
            # \b (word boundary) doesn't work for CJK text — consecutive
            # Chinese characters are all "word" chars to re, so "第一个吧"
            # never has a boundary after "个". Plain substring containment
            # is the correct check for the Chinese entries instead.
            if any(ord(ch) > 0x2E80 for ch in word):
                if word in lowered:
                    return idx
            elif re.search(rf"\b{re.escape(word)}\b", lowered):
                return idx
        return None

    def _resolve_ordinal_reference(self, state, user_message: str) -> dict | None:
        if not state.offered_options:
            return None
        idx = self._detect_ordinal_index(user_message)
        if idx is None or idx < 1 or idx > len(state.offered_options):
            return None
        return state.offered_options[idx - 1]

    _BATCH_DEPENDENCIES = {
        "check_availability": {"resolve_datetime"},
        "check_availability_range": {"resolve_datetime"},
        "create_pet": {"create_customer"},
        "get_booking_service_options": {"create_pet", "find_pet_by_name", "get_pets"},
        "create_booking": {
            "create_customer", "create_pet", "find_pet_by_name",
            "get_booking_service_options", "resolve_datetime",
            "check_availability", "check_availability_range",
            "update_pet_vaccination", "get_loyalty_balance",
            "check_coupon_eligibility",
        },
        "cancel_booking": {"get_latest_booking", "get_booking_by_id"},
        "reschedule_booking": {
            "get_latest_booking", "get_booking_by_id", "resolve_datetime",
            "check_availability", "check_availability_range",
        },
        "redeem_reward": {"create_booking", "check_coupon_eligibility"},
    }

    @classmethod
    def _ordered_tool_calls(cls, tool_calls: list[dict]) -> tuple[list[dict], bool]:
        """Stable topological order for dependencies emitted in one response."""
        names = {call["name"] for call in tool_calls}
        remaining = list(enumerate(tool_calls))
        completed_names: set[str] = set()
        ordered: list[dict] = []
        has_dependency = False

        while remaining:
            progressed = False
            for position, (original_index, call) in enumerate(remaining):
                required = cls._BATCH_DEPENDENCIES.get(call["name"], set()) & names
                if call["name"] != "update_conversation_state" and "update_conversation_state" in names:
                    required = required | {"update_conversation_state"}
                if required:
                    has_dependency = True
                if required <= completed_names:
                    ordered.append(call)
                    completed_names.add(call["name"])
                    remaining.pop(position)
                    progressed = True
                    break
            if not progressed:
                # A malformed/cyclic batch should still run predictably; the
                # normal tool guardrails will reject invalid arguments.
                ordered.extend(call for _, call in remaining)
                break
        return ordered, has_dependency

    @staticmethod
    def _response_requests_customer_input(content: str) -> bool:
        text = (content or "").strip().lower()
        return bool(
            re.search(
                r"[?？]|\b(?:what|which|when|where|who|could you|can you|please provide|please confirm)\b"
                r"|请问|哪一|什么时候|几点|可以告诉|请提供|请确认|请回复",
                text,
            )
        )

    @staticmethod
    def _trace_has_successful_mutation(trace: list[dict]) -> bool:
        for item in trace:
            if item.get("tool") not in {"create_booking", "cancel_booking", "reschedule_booking", "redeem_reward"}:
                continue
            try:
                result = json.loads(item.get("result") or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(result, dict) and result.get("status") == "success":
                return True
        return False

    @staticmethod
    def _trace_has_successful_document_delivery(trace: list[dict]) -> bool:
        for item in trace:
            if item.get("tool") not in {
                "create_booking", "reschedule_booking", "send_booking_confirmation"
            }:
                continue
            try:
                result = json.loads(item.get("result") or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(result, dict) or result.get("status") != "success":
                continue
            data = result.get("data") or {}
            delivery_status = str(
                data.get("delivery_status")
                or data.get("confirmation_delivery_status")
                or ""
            )
            if delivery_status in {"sent", "sent_console"}:
                return True
        return False

    @staticmethod
    def _trace_has_document_delivery_attempt(trace: list[dict]) -> bool:
        return any(
            item.get("tool") in {
                "create_booking", "reschedule_booking", "send_booking_confirmation"
            }
            for item in trace
        )

    @classmethod
    def _needs_tool_repair(
        cls,
        state,
        user_message: str,
        response_content: str,
        trace: list[dict],
    ) -> bool:
        """Narrow backstop for unsupported live claims; never a rigid router."""
        text = (user_message or "").lower()
        answer = (response_content or "").lower()
        action_claim = bool(
            re.search(
                r"\b(?:booked|booking confirmed|cancelled|canceled|rescheduled|redeemed)\b"
                r"|预约.{0,8}(?:成功|确认)|已经.{0,8}(?:取消|改期|预约)|兑换.{0,8}(?:成功|提交)",
                answer,
            )
        )
        if action_claim and not cls._trace_has_successful_mutation(trace):
            return True

        delivery_claim = bool(re.search(
            r"(?:confirmation|document|slip|pdf).{0,30}\b(?:sent|resent|delivered)\b"
            r"|\b(?:sent|resent|delivered)\b.{0,30}(?:confirmation|document|slip|pdf)"
            r"|(?:确认单|确认文件|文件|PDF).{0,12}(?:已发送|重发|送达)",
            answer,
            re.IGNORECASE,
        ))
        if delivery_claim and not cls._trace_has_successful_document_delivery(trace):
            # A failed/unknown external send must not be repeated merely to
            # repair prose. The final-response guard below replaces the false
            # success statement with an honest delivery failure instead.
            return not cls._trace_has_document_delivery_attempt(trace)

        confirmation_document_intent = bool(re.search(
            r"\b(?:booking\s+confirmation|confirmation\s+(?:slip|document|pdf)|my\s+confirmation)\b"
            r"|\b(?:send|resend|where).{0,24}(?:confirmation|slip|pdf)\b"
            r"|预约确认单|确认单|确认文件",
            text,
            re.IGNORECASE,
        ))
        if confirmation_document_intent and not (
            "send_booking_confirmation" in {
                item.get("tool") for item in trace
            }
            or cls._trace_has_successful_document_delivery(trace)
        ):
            # The resend tool can resolve the latest booking itself. Do not
            # accept an unnecessary request for booking details as progress.
            return True

        # If the draft is asking for genuinely missing customer information,
        # that is valid agent progress and should never be forced into a tool.
        if cls._response_requests_customer_input(response_content):
            return False

        live_intent = bool(
            re.search(
                r"\b(?:book|booking|availability|available|slot|price|cost|package|room|"
                r"grooming|daycare|boarding|add-?on|recommend|suggest|"
                r"booking\s+confirmation|confirmation\s+(?:slip|document|pdf)|"
                r"cancel|reschedule|voucher|coupon|loyalty|points?|payment|policy)\b"
                r"|预约|预订|空位|时段|价格|多少钱|配套|房型|美容|日托|寄宿|附加服务|"
                r"预约确认单|确认单|确认文件|推荐|建议|取消|改期|优惠券|积分|付款|政策",
                text,
            )
        )
        if not live_intent:
            return False

        facts = state.verified_facts or {}
        called_tools = {
            item.get("tool") for item in trace
            if item.get("tool") and item.get("tool") != "update_conversation_state"
        }

        if confirmation_document_intent and not (
            "send_booking_confirmation" in called_tools
            or cls._trace_has_successful_document_delivery(trace)
        ):
            return True

        availability_intent = bool(re.search(
            r"\b(?:availability|available|slot)\b|空位|时段",
            text,
        ))
        if availability_intent and not (
            facts.get("availability")
            or facts.get("availability_range")
            or called_tools & {"check_availability", "check_availability_range"}
        ):
            return True

        catalogue_intent = bool(re.search(
            r"\b(?:price|cost|package|room|grooming|daycare|boarding|add-?on|recommend|suggest)\b|"
            r"价格|多少钱|配套|房型|美容|日托|寄宿|附加服务|推荐|建议",
            text,
        ))
        if catalogue_intent and not (
            facts.get("service_options")
            or facts.get("policy_knowledge")
            or called_tools & {"get_booking_service_options", "retrieve_policy"}
        ):
            return True

        policy_intent = bool(re.search(r"\bpolicy\b|政策", text))
        if policy_intent and not (
            facts.get("policy_knowledge") or "retrieve_policy" in called_tools
        ):
            return True

        loyalty_intent = bool(re.search(
            r"\b(?:loyalty|points?|voucher|coupon)\b|积分|点数|优惠券",
            text,
        ))
        if loyalty_intent and not (
            facts.get("loyalty_balance")
            or facts.get("coupon_eligibility")
            or called_tools & {
                "get_loyalty_balance", "check_coupon_eligibility",
                "redeem_reward", "register_loyalty_member",
            }
        ):
            return True

        payment_intent = bool(re.search(r"\bpayment\b|付款", text))
        if payment_intent and not (
            facts.get("payment_history") or "get_payment_history" in called_tools
        ):
            return True

        booking_lookup_intent = bool(re.search(
            r"\b(?:booking status|my booking|cancel|reschedule)\b|"
            r"我的预约|预约状态|取消|改期",
            text,
        ))
        if booking_lookup_intent and not (
            facts.get("latest_booking")
            or facts.get("resolved_booking")
            or called_tools & {
                "get_latest_booking", "get_booking_by_id",
                "cancel_booking", "reschedule_booking",
            }
        ):
            return True

        # Reusing the right cached evidence is valid; unrelated tool activity
        # is not. These explicit matches are intentionally about evidence
        # categories, not a prescribed tool sequence.
        if re.search(r"\b(?:price|cost|package|room)\b|价格|多少钱|配套|房型", text):
            if facts.get("service_options") or facts.get("policy_knowledge"):
                return False
        if re.search(
            r"\b(?:grooming|daycare|boarding|add-?on|recommend|suggest)\b|"
            r"美容|日托|寄宿|附加服务|推荐|建议",
            text,
        ):
            if facts.get("service_options") or facts.get("policy_knowledge"):
                return False
        if re.search(r"\b(?:policy)\b|政策", text) and facts.get("policy_knowledge"):
            return False
        if re.search(r"\b(?:loyalty|points?|voucher|coupon)\b|积分|点数|优惠券", text):
            if facts.get("loyalty_balance") or facts.get("coupon_eligibility"):
                return False
        if re.search(r"\bpayment\b|付款", text) and facts.get("payment_history"):
            return False
        if re.search(r"\b(?:booking status|my booking)\b|我的预约|预约状态", text):
            if facts.get("latest_booking") or facts.get("resolved_booking"):
                return False
        # update_conversation_state is bookkeeping, not evidence for a live
        # business claim. Any other real tool observation is enough to let the
        # model decide naturally from its result.
        return not called_tools

    @staticmethod
    def _save_escalation_message(company_id, customer_id: int | None, user_message: str, handoff_reason: str | None) -> None:
        """
        Write and verify a row in Supabase `messages` so a human-required
        moment (handoff_required=True on a tool result) actually surfaces on
        the staff Enquiries dashboard, instead of only ever reaching the
        customer as reply wording ("our team will follow up") with nothing
        anywhere a staff member watching the dashboard would see. reply_date
        is left unset so it renders as "pending" (see web/common.js's
        normalizeChatMessage/renderEnquiryPendingTable).
        """
        from app.db.escalations import save_staff_enquiry

        save_staff_enquiry(company_id, customer_id, user_message, handoff_reason)

    @staticmethod
    def _cache_cancelled_booking(state, result: dict) -> None:
        """
        Remember what was just cancelled this session, in enough detail to
        rebook it without another lookup — "book it again" right after a
        cancellation means THIS booking, and get_last_completed_booking
        can't find it (it only matches booking_date < today; a just-cancelled
        FUTURE booking's date is still in the future).
        """
        if result.get("status") != "success":
            return
        data = result.get("data") or {}
        state.last_cancelled_booking = {
            "booking_id": data.get("booking_id"),
            "service_type": data.get("service_type"),
            "package_name": data.get("package_name") or data.get("service_name") or data.get("room_type"),
            "pet_id": data.get("pet_id"),
            "date": data.get("booking_date") or data.get("check_in_date"),
            "time": data.get("booking_time") or data.get("check_in_time"),
            "price": data.get("price") or data.get("price_per_night"),
        }

    @staticmethod
    def _cache_created_booking(state, result: dict) -> None:
        """
        Remember what was just booked this session so later turns still have
        it even though _resolve_identity only prefetches "latest_booking" on
        the session's first message (see its docstring) — without this, the
        model has no booking context at all from turn 2 onward unless it
        decides, unprompted, to re-call get_latest_booking/get_booking_by_id.
        """
        if result.get("status") != "success":
            return
        data = result.get("data") or {}
        state.last_created_booking = {
            "booking_id": data.get("booking_id"),
            "service_type": data.get("service_type"),
            "package_name": data.get("package_name") or data.get("service_name") or data.get("room_type"),
            "pet_id": data.get("pet_id"),
            "pet_name": data.get("pet_name"),
            "date": data.get("booking_date") or data.get("check_in_date"),
            "booking_date": data.get("booking_date") or data.get("check_in_date"),
            "time": data.get("booking_time") or data.get("check_in_time"),
            "check_out_date": data.get("check_out_date"),
            "check_out_time": data.get("check_out_time"),
            "price": data.get("price") or data.get("total_price") or data.get("price_per_night"),
            "booking_status": data.get("booking_status"),
        }
        state.latest_booking = dict(state.last_created_booking)

    _GREETING_LEAD_WORDS = (
        "hi", "hello", "hey", "welcome", "good morning", "good afternoon",
        "good evening", "早", "你好", "嗨", "早上好", "下午好", "晚上好",
        # Malay (Bahasa Malaysia) — without these, a model reply that greets
        # a Malay-speaking customer in Malay isn't recognized as already
        # greeted, and _ensure_first_message_greeting/_strip_redundant_greeting
        # would layer an English/Chinese greeting on top of it.
        "hai", "selamat pagi", "selamat tengah hari", "selamat petang",
        "selamat malam", "selamat datang",
    )

    @classmethod
    def _looks_already_greeted(cls, content: str, first_name: str | None) -> bool:
        """Heuristic: does the model's own reply already open with a greeting?"""
        head = (content or "").strip().lower()[:40]
        if any(head.startswith(word) for word in cls._GREETING_LEAD_WORDS):
            return True
        if first_name and first_name.strip().lower() in head:
            return True
        return False

    @classmethod
    def _strip_leading_greeting_sentence(cls, content: str, first_name: str | None) -> str:
        """
        Remove the model's own opening greeting sentence/line, if it wrote
        one but omitted a required identity element, so the minimal fallback
        can replace it without producing two greetings. Complete natural
        greetings are preserved by _ensure_first_message_greeting.
        """
        stripped = (content or "").strip()
        if not stripped:
            return stripped
        newline_idx = stripped.find("\n")
        sentence_match = re.match(r"^(.*?[.!?。！？])(\s+|$)", stripped, re.DOTALL)
        sentence_end = sentence_match.end(1) if sentence_match else None
        # Prefer whichever boundary (newline vs sentence-ending punctuation)
        # comes FIRST. A greeting with no punctuation before its own newline
        # (e.g. "Hi Farah welcome back\nHow can I help?") has no [.!?] until
        # the real question mark at the very end — blindly matching
        # sentence-first would swallow that whole question into "the
        # greeting" and strip it away entirely.
        if newline_idx != -1 and (sentence_end is None or newline_idx < sentence_end):
            first_part, rest = stripped[:newline_idx], stripped[newline_idx:].lstrip("\n")
        elif sentence_match:
            first_part, rest = sentence_match.group(1), stripped[sentence_match.end() :]
        else:
            first_part, rest = stripped, ""
        if cls._looks_already_greeted(first_part, first_name):
            return rest.strip()
        return stripped

    def _ensure_first_message_greeting(
        self, response, customer: dict, company_context: dict, user_message: str = ""
    ):
        """
        Enforce greeting elements without templating the rest of the reply.

        The model owns tone, wording, and whether verified booking history is
        relevant to the customer's message. Code only supplies a short missing
        greeting/name or greeting/company prefix. This avoids deterministic
        upsell/history sentences being injected into unrelated first turns.
        """
        content = (response.content or "").strip()
        chinese = bool(re.search(r"[\u3400-\u9fff]", user_message or ""))
        if customer.get("found") is False:
            company_name = (company_context or {}).get("company_name") or "us"
            head = content[:180].lower()
            greeting_complete = (
                self._looks_already_greeted(content, None)
                and str(company_name).strip().lower() in head
            )
            if greeting_complete:
                greeting = ""
            else:
                if self._looks_already_greeted(content, None):
                    content = self._strip_leading_greeting_sentence(content, None)
                greeting = (
                    f"你好，欢迎来到 {company_name}！"
                    if chinese else f"Hello and welcome to {company_name}!"
                )
        elif customer.get("found") is True:
            name = customer.get("first_name") or "there"
            head = content[:180].lower()
            greeting_complete = (
                self._looks_already_greeted(content, name)
                and str(name).strip().lower() in head
            )
            if greeting_complete:
                greeting = ""
            else:
                if self._looks_already_greeted(content, name):
                    content = self._strip_leading_greeting_sentence(content, name)
                greeting = f"你好，{name}！" if chinese else f"Hello {name}!"
        else:
            # Identity lookup unavailable is neither an existing nor a new
            # customer. Do not insert a misleading name/company welcome.
            greeting = "你好！" if chinese else "Hello!"

        additions = [part for part in (greeting, content) if part]
        return response.model_copy(update={"content": "\n\n".join(additions).strip()})

    def _strip_redundant_greeting(self, response, customer: dict):
        """
        Flip side of _ensure_first_message_greeting: on turns after the
        first the prompt says not to re-greet, but RUNTIME_CONTEXT.customer
        carries the same name/pet data every turn and the model has been
        observed opening with a fresh "Hi {name}, welcome back!" again
        anyway — confirmed live on a session's second turn. Only strips a
        short, blank-line-separated leading greeting paragraph (the
        consistent shape observed); leaves content alone if the model
        didn't structure it that way, rather than risk mangling a reply
        that happens to start with the customer's name for another reason.
        """
        content = response.content or ""
        first_name = customer.get("first_name")
        if not self._looks_already_greeted(content, first_name):
            return response
        parts = content.split("\n\n", 1)
        if len(parts) == 2 and len(parts[0]) <= 100:
            return response.model_copy(update={"content": parts[1].lstrip()})
        return response

    @staticmethod
    def _strip_internal_links(response):
        """
        The customer already gets the real PDF as a WhatsApp document
        attachment (send_whatsapp_document, called inside
        generate_and_send_booking_confirmation/invoice) — the model should
        never construct its own link to it. The system prompt already says
        never to surface any _internal_-prefixed field, but this leaked
        through anyway (confirmed live: the model pasted the full
        _internal_confirmation_url — a private, signed Supabase Storage
        URL — as a clickable markdown link in its reply). Same "don't trust
        the model for this" reasoning as everywhere else in this file: drop
        any line containing a Supabase Storage signed-URL rather than rely
        on prompt wording alone.
        """
        content = response.content or ""
        dangling_link_line = re.compile(
            r"\b(?:link\s+below|download\s+(?:it\s+)?(?:using|from)\s+the\s+link)\b"
            r"|(?:以下|下面).{0,8}(?:链接|連結)|(?:下载|下載).{0,8}(?:链接|連結)",
            re.IGNORECASE,
        )
        cleaned = "\n".join(
            line for line in content.split("\n")
            if "/storage/v1/object/sign/" not in line
            and not dangling_link_line.search(line)
        )
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if cleaned == content.strip():
            return response
        return response.model_copy(update={"content": cleaned})

    @staticmethod
    def _ground_document_delivery_response(response, user_message: str, trace: list[dict]):
        """Never let prose turn a failed document dispatch into fake success."""
        document_intent = bool(re.search(
            r"\b(?:confirmation|document|slip|pdf)\b|确认单|确认文件|文件",
            user_message or "",
            re.IGNORECASE,
        ))
        if not document_intent or PawfectOrchestrator._trace_has_successful_document_delivery(trace):
            return response

        attempts = [
            item for item in trace
            if item.get("tool") in {
                "create_booking", "reschedule_booking", "send_booking_confirmation"
            }
        ]
        if not attempts:
            return response
        try:
            result = json.loads(attempts[-1].get("result") or "{}")
        except (TypeError, json.JSONDecodeError):
            return response
        # Missing/ambiguous/not-found results need the model's natural request
        # for clarification. Only replace claims around an actual delivery
        # failure that has been marked for staff follow-up.
        if not isinstance(result, dict) or not result.get("handoff_required"):
            return response
        chinese = bool(re.search(r"[\u3400-\u9fff]", user_message or ""))
        truthful = (
            "这次确认单没有成功发送。我已经把文件发送问题记录给员工跟进；请不要使用上面的空白链接提示。"
            if chinese else
            "The confirmation document was not delivered successfully. I've logged the "
            "delivery problem for staff follow-up; please disregard any earlier blank link wording."
        )
        return response.model_copy(update={"content": truthful})

    def _apply_tool_result_state(self, state, tool_call: dict, result) -> None:
        """Apply one observed result immediately so dependent calls can use it."""
        tool_name = tool_call["name"]
        args = tool_call.get("args") or {}

        if tool_name == "update_conversation_state" and isinstance(result, dict) and not result.get("error"):
            previous_scenario = state.active_scenario
            state.active_scenario = result.get("active_scenario")
            state.current_step = result.get("current_step")
            if result.get("service_type"):
                state.service_type = result.get("service_type")
            elif state.active_scenario != "MAKE_BOOKING":
                state.service_type = None
            if str(state.service_type or "").upper() != "DAYCARE":
                state.daycare_duration_minutes = None
                state.verified_facts.pop("daycare_duration_minutes", None)
            if previous_scenario != state.active_scenario:
                state.offered_options = []
                state.missing_information = []
                state.missing_information_by_tool = {}
            self._set_objective_from_scenario(state, state.active_scenario)
            state.completion_status = "in_progress" if state.active_scenario else None

        self._cache_resolved_pet(state, tool_name, result, args)
        self._cache_latest_booking(state, tool_name, result)
        self._cache_offered_options(state, tool_name, result)
        self._cache_pending_booking_confirmation(state, tool_name, result, args)
        self._cache_resolved_date(state, tool_name, result)
        self._cache_known_coupons(state, tool_name, result)
        self._cache_last_mutation(state, tool_name, args, result)
        self._cache_loyalty_offer_shown(state, tool_name)
        self._cache_register_preview(state, tool_name, result)

        if tool_name == "create_customer" and isinstance(result, dict) and result.get("status") == "success":
            state.customer_id = result.get("data", {}).get("customer_id")
            state.customer_name = result.get("data", {}).get("full_name")
            state.customer_address = result.get("data", {}).get("address")

        if tool_name == "cancel_booking" and isinstance(result, dict):
            self._cache_cancelled_booking(state, result)
            if result.get("status") == "success":
                cancelled = dict(result.get("data") or {})
                if cancelled:
                    state.latest_booking = cancelled
            if (
                state.last_created_booking
                and result.get("status") == "success"
                and state.last_created_booking.get("booking_id") == (result.get("data") or {}).get("booking_id")
            ):
                state.last_created_booking = None
        elif tool_name == "create_booking" and isinstance(result, dict) and result.get("status") == "success":
            state.last_cancelled_booking = None
            state.preferred_staff = None
            state.daycare_duration_minutes = None
            state.verified_facts.pop("daycare_duration_minutes", None)
            self._cache_created_booking(state, result)
            payment_id = (result.get("data") or {}).get("payment_id")
            if payment_id is not None:
                state.last_created_payment_id = payment_id
        elif tool_name == "redeem_reward" and isinstance(result, dict) and result.get("status") == "success":
            state.last_created_payment_id = None

        self._sync_scenario_from_tool_call(state, tool_name, result)
        self._update_agent_evidence(state, tool_name, result)

    def invoke_with_trace(self, company_context: dict, state, user_message: str):
        """Evidence-driven tool loop with flexible planning and full trace."""
        state.turn_counter += 1
        self._capture_explicit_loyalty_decision(state, user_message)
        self._capture_explicit_daycare_duration(state, user_message)
        is_first_message = not state.history
        customer = self._resolve_identity(company_context["company_id"], state)
        self._match_named_pet(state, user_message)
        if not customer.get("pets") and state.known_pets:
            # customer["pets"] only comes prefetched on the first message of a
            # session — on later turns the model is supposed to call
            # get_pets itself if it needs pet data, but has been observed
            # just claiming "I don't have your pet details" instead. Since
            # the roster is already cached from turn 1, always surface it so
            # there's never a turn where the model plausibly believes no pet
            # data exists.
            customer = {**customer, "pets": state.known_pets}
        if not customer.get("latest_booking") and state.last_created_booking:
            # Same reasoning as the pets fallback above: "latest_booking" only
            # comes prefetched on the session's first message (see
            # _resolve_identity's docstring) — every later turn otherwise has
            # zero booking context unless the model decides, unprompted, to
            # re-call get_latest_booking/get_booking_by_id. Confirmed to
            # cause the model to ask for booking details again, or claim none
            # exist, moments after successfully creating one earlier this
            # same session. state.last_created_booking is cheap (no DB call)
            # session-local memory for exactly that gap.
            customer = {
                **customer,
                "latest_booking": state.last_created_booking,
                "booking_context_status": "available",
            }
        if customer.get("found"):
            state.verified_facts["resolved_customer"] = {
                "source": "runtime_identity",
                "customer_id": state.customer_id,
            }
        if state.known_pets:
            state.verified_facts["customer_pets"] = {
                "source": "runtime_identity",
                "pets": state.known_pets,
            }
        if state.latest_booking:
            state.verified_facts["latest_booking"] = {
                "source": "runtime_identity",
                "booking": state.latest_booking,
            }
        if state.active_scenario and not state.objective:
            self._set_objective_from_scenario(state, state.active_scenario)

        resolved_selection = self._resolve_ordinal_reference(state, user_message)
        self._cache_daycare_duration_from_selection(state, resolved_selection)
        bound_scenario = state.active_scenario
        bound_tools = _tools_for_scenario(bound_scenario)
        model = self._base_model.bind_tools(bound_tools)
        messages = self.build_messages(
            company_context,
            customer,
            state,
            user_message,
            resolved_selection,
            bound_tools,
        )
        trace: list[dict] = []
        escalation_saved = False
        escalation_failed = False
        repair_used = False
        force_tool_once = False

        if customer.get("found") is None:
            # state.customer_id is guaranteed None here — this branch is only
            # reached when the phone lookup itself failed, so no real
            # customer_id was ever resolved this turn (see _resolve_identity).
            # save_staff_enquiry requires a real customer_id (the `messages`
            # table's escalation row is keyed to one) and always raises
            # without it — traced statically, not observed live: every
            # identity-lookup failure is guaranteed to hit this codepath,
            # throw, and leave NO staff-visible record at all, with only the
            # generic exception text (not even the phone number) reaching
            # the server log. Skip the guaranteed-failing
            # insert attempt and log the one piece of identifying information
            # that does exist (the phone number) directly instead, so this is
            # at least discoverable in server/Cloud Logging even though no
            # dashboard row can be created for an unconfirmed identity.
            escalation_failed = True
            logging.getLogger(__name__).error(
                "Staff follow-up needed but could not be logged to the dashboard "
                "(no resolved customer_id — identity lookup failed): "
                "company_id=%s phone_number=%s message=%r",
                company_context.get("company_id"),
                state.phone_number,
                user_message,
            )

        if customer.get("found") and self._is_data_deletion_request(user_message):
            try:
                self._save_escalation_message(
                    company_context.get("company_id"),
                    state.customer_id,
                    user_message,
                    "DATA_DELETION_REQUEST",
                )
                escalation_saved = True
            except Exception as exc:
                escalation_failed = True
                logging.getLogger(__name__).exception(
                    "Could not persist data-deletion escalation for customer_id=%s: %s",
                    state.customer_id,
                    exc,
                )

        def apply_and_escalate(tool_call: dict, result) -> None:
            nonlocal escalation_saved, escalation_failed
            if isinstance(result, dict) and result.get("handoff_required") and not escalation_saved:
                try:
                    self._save_escalation_message(
                        company_context.get("company_id"),
                        state.customer_id,
                        user_message,
                        result.get("handoff_reason"),
                    )
                    escalation_saved = True
                except Exception as exc:
                    escalation_failed = True
                    logging.getLogger(__name__).exception(
                        "Could not persist staff escalation for customer_id=%s: %s",
                        state.customer_id,
                        exc,
                    )
            self._apply_tool_result_state(state, tool_call, result)

        for iteration_index in range(MAX_TOOL_ITERATIONS):
            if force_tool_once:
                # One narrow repair pass for a live-data/action draft that had
                # no evidence. Exclude the bookkeeping-only state tool so this
                # forced call must observe or act on real business data.
                repair_tools = [
                    tool for tool in ALL_TOOLS if tool.name != "update_conversation_state"
                ]
                bound_tools = repair_tools
                model = self._base_model.bind_tools(repair_tools, tool_choice="required")
                # Sentinel guarantees the following iteration returns to the
                # normal auto tool-choice binding after this one repair call.
                bound_scenario = "__FORCED_TOOL_REPAIR__"
                force_tool_once = False
            elif state.active_scenario != bound_scenario:
                # A tool call earlier in THIS SAME turn (update_conversation_state,
                # or a scenario-syncing tool like create_booking/cancel_booking
                # succeeding) switched active_scenario — model was bound to the
                # OLD scenario's tool set above, so a tool the new scenario
                # needs (e.g. create_booking right after switching into
                # MAKE_BOOKING) isn't even a callable function yet this turn.
                # Confirmed live: the model would say "Sure, let's get that
                # booked" without actually calling create_booking, only
                # managing to call it on the customer's NEXT message once
                # invoke_with_trace re-entered and rebuilt the binding fresh.
                # Rebuilding here closes that gap within the same turn too.
                bound_scenario = state.active_scenario
                bound_tools = _tools_for_scenario(bound_scenario)
                model = self._base_model.bind_tools(bound_tools)

            self._refresh_runtime_message(
                messages,
                company_context,
                customer,
                state,
                resolved_selection,
                bound_tools,
            )
            response = model.invoke(messages)
            messages.append(response)

            if not response.tool_calls:
                if (
                    not repair_used
                    and self._needs_tool_repair(
                        state, user_message, response.content, trace
                    )
                ):
                    # The draft is not shown to the customer. Give the agent one
                    # chance to ground the answer, without prescribing a fixed
                    # tool sequence or a customer-facing response template.
                    messages.pop()
                    messages.append((
                        "system",
                        "The previous draft attempted to answer or claim completion for "
                        "a live business request without sufficient tool evidence. Choose "
                        "the most useful real-data/action tool now, observe its result, then "
                        "continue naturally. Do not invent missing arguments; if the result "
                        "shows customer input is required, ask only for that missing input.",
                    ))
                    repair_used = True
                    force_tool_once = True
                    continue

                final_customer = self._customer_context_for_state(customer, state)
                response = self._ground_document_delivery_response(
                    response, user_message, trace
                )
                if is_first_message:
                    response = self._ensure_first_message_greeting(
                        response, final_customer, company_context, user_message
                    )
                else:
                    response = self._strip_redundant_greeting(response, final_customer)
                response = self._strip_internal_links(response)
                if escalation_failed:
                    warning = (
                        "刚才无法把人工跟进请求写入系统；如果事情紧急，请直接联系员工。"
                        if re.search(r"[\u3400-\u9fff]", user_message or "") else
                        "I couldn't lodge the staff follow-up in our system just now. "
                        "Please contact the team directly if this is urgent."
                    )
                    response = response.model_copy(
                        update={"content": f"{response.content.rstrip()}\n\n{warning}"}
                    )
                state.history.append({"role": "human", "content": user_message})
                state.history.append({"role": "ai", "content": response.content})
                state.history = state.history[-MAX_HISTORY_TURNS * 2 :]
                return response, trace

            tool_calls = response.tool_calls
            sibling_tool_names = frozenset(tc["name"] for tc in tool_calls)
            parallel_safe_tools = {
                "get_pets", "find_pet_by_name", "get_latest_booking",
                "get_last_completed_booking", "get_booking_by_id",
                "get_booking_service_options", "resolve_datetime",
                "check_availability", "check_availability_range",
                "get_loyalty_balance", "check_coupon_eligibility",
                "get_payment_history", "get_redemption_history", "retrieve_policy",
            }

            def run_timed(tool_call):
                started_at = time_module.perf_counter()
                queued_at = submitted_at.get(tool_call["id"], batch_started_at)
                queue_wait_ms = round((started_at - queued_at) * 1000, 1)
                result = self._run_tool(tool_call, state, user_message, sibling_tool_names)
                duration_ms = round((time_module.perf_counter() - started_at) * 1000, 1)
                return result, duration_ms, queue_wait_ms

            def await_tool_future(future, tool_call):
                """Bound request latency even if one worker-side tool stalls."""
                timeout_origin = (
                    batch_started_at
                    if execution_mode == "parallel"
                    else submitted_at.get(tool_call["id"], batch_started_at)
                )
                remaining_seconds = max(
                    0.0,
                    TOOL_CALL_TIMEOUT_SECONDS - (time_module.perf_counter() - timeout_origin),
                )
                try:
                    return future.result(timeout=remaining_seconds)
                except FutureTimeoutError:
                    # Python cannot forcibly stop a thread already inside a
                    # third-party socket call. cancel() prevents queued work;
                    # the timeout still releases this customer request while
                    # lower-layer HTTP timeouts finish the worker in the
                    # background instead of hanging the agent loop forever.
                    future.cancel()
                    outcome_unknown = tool_call["name"] in MUTATING_TOOL_NAMES
                    return (
                        {
                            "status": "error",
                            "error_code": (
                                "TOOL_TIMEOUT_OUTCOME_UNKNOWN"
                                if outcome_unknown else "TOOL_TIMEOUT"
                            ),
                            "recoverable": not outcome_unknown,
                            "message": (
                                f"{tool_call['name']} did not return within "
                                f"{TOOL_CALL_TIMEOUT_SECONDS:g} seconds. "
                                + (
                                    "Its write outcome is unknown: do not repeat the action or claim "
                                    "success; staff must verify the record first."
                                    if outcome_unknown else
                                    "Do not claim success; retry once if useful or ask the customer "
                                    "to try again."
                                )
                            ),
                            "handoff_required": outcome_unknown,
                            "handoff_reason": (
                                "TOOL_TIMEOUT_OUTCOME_UNKNOWN" if outcome_unknown else None
                            ),
                        },
                        round((time_module.perf_counter() - batch_started_at) * 1000, 1),
                        0.0,
                    )
                except Exception as exc:
                    logging.getLogger(__name__).exception(
                        "Tool worker failed for %s: %s", tool_call["name"], exc
                    )
                    return (
                        {
                            "status": "error",
                            "error_code": "TOOL_WORKER_ERROR",
                            "recoverable": False,
                            "message": "The tool worker failed before returning a usable result.",
                            "handoff_required": True,
                            "handoff_reason": "TOOL_WORKER_ERROR",
                        },
                        round((time_module.perf_counter() - batch_started_at) * 1000, 1),
                        0.0,
                    )

            ordered_calls, has_dependency = self._ordered_tool_calls(tool_calls)
            unsafe_parallel_tools = sorted(
                {tc["name"] for tc in tool_calls if tc["name"] not in parallel_safe_tools}
            )
            run_in_parallel = (
                len(tool_calls) > 1
                and not unsafe_parallel_tools
                and not has_dependency
            )
            batch_started_at = time_module.perf_counter()
            submitted_at: dict[str, float] = {}
            records_by_id: dict[str, dict] = {}
            if run_in_parallel:
                execution_mode = "parallel"
                parallel_block_reason = None
                futures = {}
                for tool_call in tool_calls:
                    submitted_at[tool_call["id"]] = time_module.perf_counter()
                    futures[tool_call["id"]] = _TOOL_EXECUTOR.submit(run_timed, tool_call)
                for execution_order, tool_call in enumerate(tool_calls, start=1):
                    future = futures[tool_call["id"]]
                    result, duration_ms, queue_wait_ms = await_tool_future(future, tool_call)
                    apply_and_escalate(tool_call, result)
                    records_by_id[tool_call["id"]] = {
                        "tool_call": tool_call,
                        "result": result,
                        "duration_ms": duration_ms,
                        "queue_wait_ms": queue_wait_ms,
                        "execution_order": execution_order,
                    }
            else:
                execution_mode = "sequential"
                parallel_block_reason = (
                    "single_call_in_iteration"
                    if len(tool_calls) == 1
                    else (
                        "contains_dependency_chain"
                        if has_dependency
                        else f"contains_non_parallel_safe_tools:{','.join(unsafe_parallel_tools)}"
                    )
                )
                for execution_order, tool_call in enumerate(ordered_calls, start=1):
                    # Use the same process-lifetime pool for sequential calls.
                    # Previously these ran directly on the request thread, so
                    # the configured timeout only protected parallel reads and
                    # a single database/tool call could still hang forever.
                    submitted_at[tool_call["id"]] = time_module.perf_counter()
                    future = _TOOL_EXECUTOR.submit(run_timed, tool_call)
                    result, duration_ms, queue_wait_ms = await_tool_future(future, tool_call)
                    # Crucial agentic behavior: a dependent call later in this
                    # same batch observes IDs/dates/state produced by this call.
                    apply_and_escalate(tool_call, result)
                    records_by_id[tool_call["id"]] = {
                        "tool_call": tool_call,
                        "result": result,
                        "duration_ms": duration_ms,
                        "queue_wait_ms": queue_wait_ms,
                        "execution_order": execution_order,
                    }
            batch_duration_ms = round((time_module.perf_counter() - batch_started_at) * 1000, 1)

            # ToolMessage order follows the model's original tool_calls array,
            # even when execution was dependency-reordered internally.
            for tool_call in tool_calls:
                record = records_by_id[tool_call["id"]]
                result = record["result"]
                content = json.dumps(result, ensure_ascii=False, default=str)
                trace.append({
                    "iteration": iteration_index + 1,
                    "tool_call_id": tool_call["id"],
                    "tool": tool_call["name"],
                    # _run_tool applies the server-side tenant/customer overrides
                    # in-place, so these are the arguments that were actually
                    # executed rather than merely the model's proposed values.
                    "args": tool_call["args"],
                    "batch_size": len(tool_calls),
                    "batch_duration_ms": batch_duration_ms,
                    "execution_mode": execution_mode,
                    "execution_order": record["execution_order"],
                    "parallel_block_reason": parallel_block_reason,
                    "executor_max_workers": TOOL_EXECUTOR_MAX_WORKERS,
                    "queue_wait_ms": record["queue_wait_ms"],
                    "duration_ms": record["duration_ms"],
                    "result": content,
                })
                messages.append(ToolMessage(content=content, tool_call_id=tool_call["id"]))

        raise ToolLoopError(
            "Tool-calling loop did not converge within MAX_TOOL_ITERATIONS",
            trace,
        )

    def invoke(self, company_context: dict | None, state, user_message: str):
        """
        Run one customer turn end-to-end: resolve identity, build
        prompt+context, call GPT-4o-mini with a scenario-scoped tool set,
        execute any requested tool calls, feed results back, and repeat
        until the model returns a final natural-language reply.
        """
        if company_context is None:
            company_context = get_company_config(state.company_id)

        response, _trace = self.invoke_with_trace(company_context, state, user_message)
        return response
