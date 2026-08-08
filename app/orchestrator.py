from __future__ import annotations
import json
import logging
import os
import re
import time as time_module
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo

from langchain_core.messages import ToolMessage
from langchain_openai import ChatOpenAI

from app.agent.confirmation_policy import (
    AFFIRMATIVE_RE,
    NEGATIVE_RE,
    confirmation_intent,
)
from app.agent.booking_authorization import (
    ADD_ON_REFERENCE_RE,
    customer_stated_value,
    recent_customer_text,
    reject_unconfirmed_optional_booking_fields,
)
from app.agent.evidence_policy import (
    policy_evidence_matches,
    service_options_evidence_matches,
)
from app.agent.pet_resolution import (
    CAT_WORDS_RE,
    DOG_WORDS_RE,
    known_pet_by_id,
    match_named_pet,
    stated_species,
)
from app.agent.response_grounding import (
    ground_booking_preview_response,
    ground_document_delivery_response,
    ground_membership_response,
)
from app.context.company import get_company_config
from app.db.relational_provider import get_relational_repository
from app.prompts.system_prompt import SYSTEM_PROMPT
from app.scenarios.loader import load_scenario
from app.tools.customer_tools import (
    _extract_daycare_catalogue_options,
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
from app.db.time_normalization import extract_duration_minutes, extract_time_range

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


def _sanitize_model_value(value):
    """Remove server-only fields before state/tool data reaches the model."""
    if isinstance(value, dict):
        return {
            key: _sanitize_model_value(item)
            for key, item in value.items()
            if not str(key).startswith("_internal_")
        }
    if isinstance(value, list):
        return [_sanitize_model_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_model_value(item) for item in value)
    return value

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
    "get_booking_service_options",
    "check_availability",
    "check_availability_range",
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

# Read-only calls whose result is stable enough to reuse within a single
# customer turn. Successful, empty, and failed results are all remembered:
# repeating the same failed Supabase/RAG call nine times cannot create new
# evidence and only turns one dependency outage into an internal_error loop.
# The cache remains turn-local so availability/account data refresh next turn.
CACHEABLE_READ_TOOL_NAMES = {
    "get_pets",
    "find_pet_by_name",
    "get_latest_booking",
    "get_last_completed_booking",
    "get_booking_by_id",
    "get_booking_service_options",
    "resolve_datetime",
    "check_availability",
    "check_availability_range",
    "get_loyalty_balance",
    "check_coupon_eligibility",
    "get_payment_history",
    "get_redemption_history",
    "retrieve_policy",
}

# Read-only tools available in every scenario (and when no scenario is active
# yet).  This is the fail-closed capability baseline: no unknown or unset
# scenario can create/update data or send a document.
CORE_TOOL_NAMES = {
    "resolve_datetime",
    "retrieve_policy",
    "get_booking_service_options",
    "get_pets",
    "get_latest_booking",
    "get_last_completed_booking",
    "get_payment_history",
    "get_redemption_history",
    "update_conversation_state",
    "get_booking_by_id",
    "get_loyalty_balance",
    "check_coupon_eligibility",
}

# Every write/external side effect is explicitly granted by one scenario.
SCENARIO_TOOL_NAMES = {
    "MAKE_BOOKING": {
        "create_customer", "create_pet", "update_pet_vaccination",
        "find_pet_by_name", "check_availability", "check_availability_range", "create_booking",
        "register_loyalty_member",
    },
    "CANCEL_BOOKING": {"get_booking_by_id", "cancel_booking"},
    "RESCHEDULE_BOOKING": {"get_booking_by_id", "check_availability", "check_availability_range", "reschedule_booking"},
    "LOYALTY_QUERY": {"get_loyalty_balance", "check_coupon_eligibility", "redeem_reward"},
    "MEMBER": {"create_customer", "register_loyalty_member"},
    "PAYMENT_QUERY": set(),
    "ENQUIRY": set(),
    "BOOKING_DOCUMENT": {"send_booking_confirmation"},
    "POLICY_QUERY": set(),
}

# Keep enough room for catalogue, availability, preview and final write repair
# turns without letting a malformed model plan loop indefinitely.
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
    allowed = CORE_TOOL_NAMES | SCENARIO_TOOL_NAMES.get(active_scenario or "", set())
    if active_scenario == "MAKE_BOOKING":
        # Keep retrieve_policy available for a genuine side question without
        # forcing the booking scenario to be discarded. Catalogue/pricing is
        # still handled by get_booking_service_options; the prompt and runtime
        # guard below reject using retrieve_policy as a duplicate price tool.
        # "Like last time" has its own completed-history tool. An upcoming
        # latest booking is a competing, semantically wrong candidate here.
        allowed.discard("get_latest_booking")
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
                _TOOL_EXECUTOR.submit(
                    repo.list_customer_pets, int(company_id), int(state.customer_id)
                )
                if hydrate_pets else None
            )
            booking_future = (
                _TOOL_EXECUTOR.submit(
                    repo.get_latest_booking, int(company_id), int(state.customer_id)
                )
                if hydrate_booking else None
            )
            prefetch_deadline = time_module.monotonic() + TOOL_CALL_TIMEOUT_SECONDS

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
                    logging.getLogger(__name__).exception("Pet prefetch failed: %s", exc)
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
                    logging.getLogger(__name__).exception("Booking prefetch failed: %s", exc)
            else:
                booking_result = None

            if pets_result is not None:
                if pets_result.get("status") == "success":
                    pets = (pets_result.get("data") or {}).get("pets", [])
                    state.pets_context_status = "available"
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

    @staticmethod
    def _match_named_pet(state, user_message: str) -> None:
        match_named_pet(state, user_message)

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
        state_dict = _sanitize_model_value(
            {k: v for k, v in state.__dict__.items() if k != "history"}
        )
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

    _DIRECT_DATE_WORD_RE = re.compile(
        r"\b(?:today|tomorrow|week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"hari\s+ini|esok|minggu|isnin|selasa|rabu|khamis|jumaat|sabtu|ahad)\b"
        r"|今天|今日|明天|后天|後天|星期[一二三四五六日天1-7]|周[一二三四五六日天1-7]|週[一二三四五六日天1-7]",
        re.IGNORECASE,
    )
    _OPERATIONAL_DATE_RE = re.compile(
        r"\b(?:book|booking|reserve|appointment|available|availability|slot|cancel|reschedule)\b"
        r"|预约|預約|订位|訂位|空位|取消|改期|安排|有位",
        re.IGNORECASE,
    )

    _REPEAT_BOOKING_RE = re.compile(
        r"\b(?:like|same\s+as|repeat)\s+(?:the\s+)?last\s+time\b|"
        r"\b(?:my\s+)?usual\b|"
        r"和上次一样|跟上次一样|照上次|照旧|同上次",
        re.IGNORECASE,
    )

    @classmethod
    def _is_repeat_booking_request(cls, user_message: str) -> bool:
        return bool(cls._REPEAT_BOOKING_RE.search(str(user_message or "")))

    @staticmethod
    def _explicit_service_type(user_message: str) -> str:
        text = str(user_message or "")
        if re.search(r"\b(?:groom(?:ing)?|bath(?:ing)?)\b|美容|洗澡|洗护", text, re.IGNORECASE):
            return "GROOMING"
        if re.search(r"\bday\s*care\b|日托|托管", text, re.IGNORECASE):
            return "DAYCARE"
        if re.search(r"\bboard(?:ing)?\b|寄宿|住宿", text, re.IGNORECASE):
            return "BOARDING"
        return ""

    @staticmethod
    def _policy_query_is_catalogue_only(query: object) -> bool:
        """Reject using policy RAG as a second catalogue/price lookup.

        Policy terms that happen to mention a fee remain valid; only a query
        whose vocabulary is purely packages/menu/prices is redirected.
        """
        text = str(query or "")
        catalogue = bool(re.search(
            r"\b(?:price|pricing|package|menu|service option|rate)\b|"
            r"价格|價錢|价钱|套餐|服务项目|服務項目|"
            r"\b(?:harga|pakej|senarai perkhidmatan)\b",
            text,
            re.IGNORECASE,
        ))
        policy = bool(re.search(
            r"\b(?:policy|requirement|rule|term|cancel|refund|deposit|vaccin|"
            r"late|no[ -]?show|eligib|prohibit|allow|fee)\b|"
            r"政策|规定|規定|要求|取消|退款|押金|疫苗|迟到|遲到|"
            r"\b(?:polisi|syarat|peraturan|batal|bayaran balik|deposit|vaksin)\b",
            text,
            re.IGNORECASE,
        ))
        return catalogue and not policy

    @staticmethod
    def _normalized_option_label(value: object) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()

    @classmethod
    def _cache_repeat_booking_template(
        cls, state, tool_name: str, result: dict, args: dict
    ) -> None:
        """Bind repeat history to current catalogue evidence in application code."""
        if not isinstance(result, dict):
            return
        if tool_name == "get_last_completed_booking":
            if result.get("found") is not True:
                state.repeat_booking_template = None
                return
            add_on = str(result.get("add_on") or "").strip()
            if add_on in {"-", "—", "–"}:
                add_on = ""
            state.repeat_booking_template = {
                "booking_id": result.get("booking_id"),
                "pet_id": result.get("pet_id"),
                "pet_name": result.get("pet_name"),
                "service_type": (
                    result.get("last_service_type")
                    or result.get("service_type")
                    or args.get("service_type")
                ),
                "historical_package_name": (
                    result.get("package_name") or result.get("selected_variant")
                ),
                "historical_price": result.get("price"),
                "historical_add_on": add_on,
                "historical_add_on_price": result.get("add_on_price"),
                "catalogue_validated": False,
            }
            return

        if tool_name != "get_booking_service_options" or result.get("status") != "success":
            return
        template = state.repeat_booking_template
        if not template:
            return
        requested_service = str(args.get("service_type") or "").strip().upper()
        if requested_service != str(template.get("service_type") or "").strip().upper():
            return
        if str(args.get("pet_id") or "") != str(template.get("pet_id") or ""):
            return

        package_label = cls._normalized_option_label(template.get("historical_package_name"))
        current_options = [
            option
            for option in state.verified_service_options
            if str(option.get("service_type") or "").upper() == requested_service
            and str(option.get("pet_id") or "") == str(template.get("pet_id") or "")
        ]
        main_match = next(
            (
                option for option in current_options
                if str(option.get("selection_kind") or "service") != "add_on"
                and cls._normalized_option_label(
                    option.get("service_name") or option.get("room_type")
                ) == package_label
            ),
            None,
        )
        add_on_label = cls._normalized_option_label(template.get("historical_add_on"))
        add_on_match = None
        if add_on_label:
            add_on_match = next(
                (
                    option for option in current_options
                    if str(option.get("selection_kind") or "") == "add_on"
                    and cls._normalized_option_label(option.get("service_name")) == add_on_label
                ),
                None,
            )

        state.repeat_booking_template = {
            **template,
            "catalogue_validated": bool(main_match and (not add_on_label or add_on_match)),
            "package_name": (main_match or {}).get("service_name"),
            "price": (main_match or {}).get("price"),
            "add_on": (add_on_match or {}).get("service_name") if add_on_match else "",
            "add_on_price": (add_on_match or {}).get("price") if add_on_match else 0,
        }

    @classmethod
    def _next_repeat_booking_tool(
        cls, state, user_message: str, trace: list[dict]
    ) -> str | None:
        """Return one missing read needed to finish a scoped repeat request."""
        if not cls._is_repeat_booking_request(user_message):
            return None
        called = {item.get("tool") for item in trace}
        if state.active_scenario != "MAKE_BOOKING":
            return "update_conversation_state"
        if "get_last_completed_booking" not in called:
            return "get_last_completed_booking"
        if not state.repeat_booking_template:
            return None
        if "get_booking_service_options" not in called:
            return "get_booking_service_options"
        if not state.repeat_booking_template.get("catalogue_validated"):
            return None
        resolved = state.current_datetime_resolution or {}
        if not resolved.get("date") and not resolved.get("date_range"):
            return None
        availability_tool = (
            "check_availability_range" if resolved.get("date_range") else "check_availability"
        )
        if availability_tool not in called:
            return availability_tool
        return None

    @classmethod
    def _ground_direct_datetime_response(cls, response, state, user_message: str):
        """Use deterministic output for a short, non-operational date question.

        Operational messages keep the model's natural response, but their
        writes still use the same resolution through the date whitelist.
        """
        resolved = state.current_datetime_resolution
        text = str(user_message or "").strip()
        if (
            not isinstance(resolved, dict)
            or len(text) > 60
            or not cls._DIRECT_DATE_WORD_RE.search(text)
            or cls._OPERATIONAL_DATE_RE.search(text)
            or cls._is_staff_handoff_request(text)
        ):
            return response
        value = resolved.get("date")
        date_range = resolved.get("date_range") or {}
        chinese = bool(re.search(r"[\u3400-\u9fff]", text))
        if value:
            try:
                weekday = datetime.fromisoformat(value).strftime("%A")
            except ValueError:
                weekday = ""
            if chinese:
                chinese_weekday = {
                    "Monday": "星期一", "Tuesday": "星期二", "Wednesday": "星期三",
                    "Thursday": "星期四", "Friday": "星期五", "Saturday": "星期六",
                    "Sunday": "星期日",
                }.get(weekday, weekday)
                content = f"日期是 {value}（{chinese_weekday}）。"
            else:
                content = f"The date is {value} ({weekday})."
            return response.model_copy(update={"content": content})
        if date_range.get("start") and date_range.get("end"):
            content = (
                f"日期范围是 {date_range['start']} 至 {date_range['end']}。"
                if chinese else
                f"The date range is {date_range['start']} through {date_range['end']}."
            )
            return response.model_copy(update={"content": content})
        return response

    @staticmethod
    def _ground_latest_availability_response(
        response, user_message: str, trace: list[dict], state=None
    ):
        """Render choices from the latest availability result, never model prose.

        This is intentionally limited to turns whose last substantive tool is
        an availability read. A later booking/price/policy action keeps its own
        response, while a slot-selection turn gets a deterministic allow-list
        so an unavailable time cannot leak into customer-facing text.
        """
        latest = None
        for item in reversed(trace):
            tool_name = item.get("tool")
            if tool_name in {"resolve_datetime", "update_conversation_state"}:
                continue
            if tool_name not in {"check_availability", "check_availability_range"}:
                return response
            latest = item
            break
        if latest is None:
            return response

        raw_result = latest.get("result")
        try:
            result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
        except (TypeError, ValueError, json.JSONDecodeError):
            return response
        if not isinstance(result, dict):
            return response

        chinese = bool(re.search(r"[\u3400-\u9fff]", str(user_message or "")))
        malay = bool(re.search(
            r"\b(?:saya|boleh|pukul|masa|tempah|ambil|hantar)\b",
            str(user_message or ""),
            re.IGNORECASE,
        ))

        def with_repeat_context(content: str) -> str:
            template = getattr(state, "repeat_booking_template", None) if state else None
            repeat_evidence_in_this_turn = PawfectOrchestrator._is_repeat_booking_request(
                user_message
            ) or any(
                item.get("tool") == "get_last_completed_booking" for item in trace
            )
            if (
                not repeat_evidence_in_this_turn
                or not template
                or not template.get("catalogue_validated")
            ):
                return content
            pet_name = template.get("pet_name") or "your pet"
            package_name = template.get("package_name")
            price = template.get("price")
            add_on = template.get("add_on")
            price_text = f"RM{float(price):g}" if price not in (None, "") else ""
            add_on_text = f" + {add_on}" if add_on else ""
            if chinese:
                summary = (
                    f"已按 {pet_name} 上次的选择锁定当前仍可预约的"
                    f"「{package_name}」{add_on_text}"
                    f"{f'（{price_text}）' if price_text else ''}。"
                )
            else:
                summary = (
                    f"I matched {pet_name}'s last visit to the currently bookable "
                    f"{package_name}{add_on_text}"
                    f"{f' ({price_text})' if price_text else ''}."
                )
            return f"{summary}\n\n{content}"

        def display_time(value: object) -> str:
            text = str(value or "")
            return text[:5] if re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", text) else text

        status = str(result.get("status") or "").lower()
        is_range_success = (
            latest.get("tool") == "check_availability_range"
            and isinstance(result.get("days"), list)
        )
        if status == "missing_information":
            missing = (result.get("data") or {}).get("missing_fields") or []
            field_labels = {
                "room_type": "房型",
                "check_out_date": "退房日期",
                "check_in_time": "check-in/drop-off 时间",
                "duration_minutes": "服务时长",
                "check_out_time_or_duration_minutes": "pickup 时间或服务时长",
            }
            readable = "、".join(field_labels.get(str(field), str(field)) for field in missing)
            if chinese:
                content = f"还需要确认{readable or '完整预约条件'}后才能可靠提供时间；目前不会显示任何未经验证的时段。"
            elif malay:
                content = "Maklumat tempahan belum lengkap, jadi saya tidak akan menawarkan sebarang masa yang belum disahkan. Sila lengkapkan butiran yang diminta dahulu."
            else:
                content = "The booking details are incomplete, so I won’t offer any unverified times. Please provide the missing booking details first."
            return response.model_copy(update={"content": with_repeat_context(content)})
        if status != "success" and not is_range_success:
            if chinese:
                content = "目前无法可靠确认可用时段，因此不会提供任何时间选择。请更换预约条件后让我重新检查，或请员工协助确认。"
            elif malay:
                content = "Saya tidak dapat mengesahkan masa yang tersedia dengan yakin sekarang, jadi tiada pilihan masa akan ditawarkan. Cuba syarat lain atau minta staf menyemak."
            else:
                content = "I can’t reliably verify availability right now, so I won’t offer any time choices. Change a booking condition so I can check again, or ask staff to verify."
            return response.model_copy(update={"content": with_repeat_context(content)})

        if latest.get("tool") == "check_availability_range":
            days = result.get("days") or (result.get("data") or {}).get("days") or []
            verified_days = [
                (
                    str(day.get("date") or ""),
                    [display_time(slot) for slot in day.get("available_slots") or []],
                )
                for day in days
                if day.get("available_slots")
            ]
            if verified_days:
                lines = [f"- {date_value}: {', '.join(slots)}" for date_value, slots in verified_days]
                if chinese:
                    content = "完整校验后，目前可选择的时段只有：\n" + "\n".join(lines) + "\n请从以上时段中选择。"
                elif malay:
                    content = "Selepas semakan penuh, hanya masa berikut tersedia:\n" + "\n".join(lines) + "\nSila pilih daripada masa di atas."
                else:
                    content = "After a complete availability check, these are the available times:\n" + "\n".join(lines) + "\nPlease choose only from the times above."
            elif chinese:
                content = "完整校验后，这个日期范围目前没有能满足全部条件的可用时段。你可以更换日期或其他预约条件，我会重新检查。"
            elif malay:
                content = "Selepas semakan penuh, tiada masa dalam julat tarikh ini yang memenuhi semua syarat. Beri tarikh atau syarat lain untuk saya semak semula."
            else:
                content = "After a complete availability check, no times in this date range satisfy all booking conditions. Give me another date or condition and I’ll check again."
            return response.model_copy(update={"content": with_repeat_context(content)})

        data = result.get("data") or {}
        check_out_mode = str(data.get("selection_target") or "").upper() == "CHECK_OUT"
        key = "available_check_out_times" if check_out_mode else "available_slots"
        choices = [display_time(slot) for slot in data.get(key) or []]
        if choices:
            joined = ", ".join(choices)
            if chinese:
                target = "pickup/check-out" if check_out_mode else "check-in/drop-off"
                content = f"完整校验后，目前可选择的 {target} 时间只有：{joined}。请从这些时间中选择。"
            elif malay:
                target = "pickup/check-out" if check_out_mode else "check-in/drop-off"
                content = f"Selepas semakan penuh, hanya masa {target} ini tersedia: {joined}. Sila pilih daripada masa ini."
            else:
                target = "pickup/check-out" if check_out_mode else "check-in/drop-off"
                content = f"After a complete availability check, the only available {target} times are: {joined}. Please choose from these times."
        elif chinese:
            target = "pickup/check-out" if check_out_mode else "check-in/drop-off"
            content = f"完整校验后，目前没有能满足全部条件的 {target} 时间。你可以更换日期、服务时长、房型或员工偏好，我会重新检查。"
        elif malay:
            target = "pickup/check-out" if check_out_mode else "check-in/drop-off"
            content = f"Selepas semakan penuh, tiada masa {target} yang memenuhi semua syarat. Beri tarikh atau syarat lain untuk saya semak semula."
        else:
            target = "pickup/check-out" if check_out_mode else "check-in/drop-off"
            content = f"After a complete availability check, no {target} time satisfies all booking conditions. Give me another date or condition and I’ll check again."
        return response.model_copy(update={"content": with_repeat_context(content)})

    @staticmethod
    def _ground_daycare_recommendation_response(
        response, user_message: str, trace: list[dict], state
    ):
        """Recommend DAYCARE options from structured duration/price evidence."""
        latest = None
        for item in reversed(trace):
            if item.get("tool") in {"resolve_datetime", "update_conversation_state"}:
                continue
            if item.get("tool") != "get_booking_service_options":
                return response
            latest = item
            break
        if latest is None or str((latest.get("args") or {}).get("service_type") or "").upper() != "DAYCARE":
            return response
        try:
            result = json.loads(latest.get("result") or "{}")
        except (TypeError, json.JSONDecodeError):
            return response
        if not isinstance(result, dict) or result.get("status") != "success":
            return response
        options = [
            option for option in (result.get("data") or {}).get("service_options") or []
            if isinstance(option, dict)
        ]
        if not options:
            return response

        recommendation_requested = bool(re.search(
            r"\b(?:recommend|suggest|best|suitable|which\s+(?:one|package)|"
            r"which.{0,30}daycare|what.{0,30}daycare)\b|"
            r"推荐|推薦|建议|建議|适合|適合|哪个好|哪個好",
            user_message or "",
            re.IGNORECASE,
        ))
        booking_daycare = (
            state.active_scenario == "MAKE_BOOKING"
            and str(state.service_type or "").upper() == "DAYCARE"
        )
        if not recommendation_requested and not booking_daycare:
            return response

        duration = getattr(state, "daycare_duration_minutes", None)
        chinese = bool(re.search(r"[\u3400-\u9fff]", user_message or ""))
        if not duration:
            content = (
                "为了推荐正确的日托套餐，请告诉我预计托管多久，或提供接送时间。确认时长后我会比较适用套餐和实际总价。"
                if chinese else
                "To recommend the right daycare package, how long will your pet stay, "
                "or what are the drop-off and pickup times? I’ll compare only the applicable "
                "packages and their actual totals."
            )
            return response.model_copy(update={"content": content})

        ranked: list[tuple[int, float, dict]] = []
        for option in options:
            try:
                rate = float(option.get("price"))
            except (TypeError, ValueError):
                continue
            exact = option.get("duration_minutes")
            minimum = option.get("min_duration_minutes")
            maximum = option.get("max_duration_minutes")
            unit = str(option.get("pricing_unit") or "flat").casefold()
            eligible = False
            rank = 9
            if exact not in (None, ""):
                eligible = int(exact) == int(duration)
                rank = 0
            elif minimum not in (None, "") or maximum not in (None, ""):
                eligible = True
                if minimum not in (None, ""):
                    eligible = eligible and (
                        duration > int(minimum)
                        if option.get("min_duration_exclusive")
                        else duration >= int(minimum)
                    )
                if maximum not in (None, ""):
                    eligible = eligible and (
                        duration < int(maximum)
                        if option.get("max_duration_exclusive")
                        else duration <= int(maximum)
                    )
                rank = 1
            elif unit == "hour":
                eligible = True
                rank = 2
            if not eligible:
                continue
            total = rate * duration / 60 if unit == "hour" else rate
            ranked.append((rank, total, option))

        if not ranked:
            return response
        ranked.sort(key=lambda item: (item[0], item[1], str(item[2].get("service_name") or "")))
        recommendations = ranked[:2]
        duration_text = (
            f"{duration // 60:g} 小时" if duration % 60 == 0 else f"{duration} 分钟"
        ) if chinese else (
            f"{duration // 60:g} hours" if duration % 60 == 0 else f"{duration} minutes"
        )
        rendered = [
            f"{item[2].get('service_name')} — RM{item[1]:g}"
            for item in recommendations
        ]
        if chinese:
            content = f"按 {duration_text} 的托管时长，最适合的是：{rendered[0]}。"
            if len(rendered) > 1:
                content += f" 另一个适用选择是 {rendered[1]}。"
            content += "请选择其中一个；确定后我再按完整时长检查接送时段。"
        else:
            content = f"For a {duration_text} stay, the best direct match is {rendered[0]}."
            if len(rendered) > 1:
                content += f" Another applicable option is {rendered[1]}."
            content += " Choose one, then I’ll check drop-off and pickup availability for the full stay."
        return response.model_copy(update={"content": content})

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
        None if some number in height_text actually appears (as a real
        number, not just any digit) somewhere in what the customer has
        actually said this session; otherwise a rejection dict. create_pet
        requiring height_text doesn't stop the model from simply inventing a
        plausible number instead of really asking — confirmed live
        (height_cm=30 sent with no customer message ever containing that
        number, back when this took an already-converted cm value). Checked
        against the raw number in height_text rather than a converted cm
        value, since e.g. "24 inches" only ever appears as "24" in what the
        customer actually typed. This can't verify the number is TRUE, only
        that it was actually said by the customer rather than fabricated
        wholesale.
        """
        height_text = args.get("height_text")
        if not height_text:
            return None
        stated_numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(height_text))]
        if not stated_numbers:
            return None
        # state.history is already capped to MAX_HISTORY_TURNS complete
        # human/AI pairs. Slicing its last 8 entries meant only about four
        # customer answers survived validation, which is shorter than the
        # customer+pet profile flow itself and caused earlier breed/species
        # answers to be rejected at the final create_pet call.
        recent_human_text = " ".join(
            [user_message or ""]
            + [t["content"] for t in state.history if t.get("role") == "human"]
        )
        mentioned_numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", recent_human_text)]
        if any(abs(n - s) <= 2 for n in mentioned_numbers for s in stated_numbers):
            return None
        return {
            "error": "UNCONFIRMED_HEIGHT",
            "message": (
                f"height_text ({height_text!r}) does not match any number the customer "
                "has actually said this conversation. Do not invent/estimate a height — "
                "ask the customer directly for their pet's height (any unit is fine — cm, "
                "inches, feet; a rough estimate is fine if they don't know exactly), then "
                "retry create_pet with their exact wording."
            ),
        }

    _DOG_WORDS_RE = DOG_WORDS_RE
    _CAT_WORDS_RE = CAT_WORDS_RE

    @classmethod
    def _stated_species(cls, user_message: str) -> str | None:
        return stated_species(user_message)

    @classmethod
    def _reject_species_mismatch(cls, state, args: dict, user_message: str) -> dict | None:
        """None unless the customer's current message states a species that
        contradicts the specific pet this tool call is about to use.

        Confirmed live: a customer asked about "Anjing" (Malay for dog)
        pricing while their only registered pet (Milo) is a real Cat — the
        model reflexively reused Milo's pet_id (system prompt: "when exactly
        one known pet exists, use it without asking which pet") and silently
        answered with Milo's cat pricing instead of noticing the mismatch.
        get_booking_service_options has no way to be asked for a species'
        pricing independent of a specific registered pet_id, so the model
        had no better tool call available either way — the fix is to force
        it to ask instead of silently guessing which pet/species is meant.

        Covers any tool call carrying a resolved pet_id (a DB lookup for
        that pet's real species), plus retrieve_policy calls backfilled from
        the session's cached state.pet_type a few lines above — both are the
        same failure shape: a known species silently overriding what the
        customer just said this turn.
        """
        stated = cls._stated_species(user_message)
        if stated is None:
            return None

        known_species = ""
        pet_label = "the pet on file"
        pet_id = args.get("pet_id")
        if pet_id:
            if state.customer_id is None:
                return {
                    "error": "PET_OWNERSHIP_UNVERIFIED",
                    "message": (
                        "A pet_id cannot be used until the customer identity is resolved. "
                        "Resolve/create the customer and their pet first."
                    ),
                }
            # Identity hydration and create_pet both maintain an owned roster.
            # Reuse that authoritative application state instead of issuing a
            # second, differently-scoped Supabase query from the orchestrator.
            # This keeps ownership in one boundary and also prevents lookup
            # exceptions here from escaping before the normal tool error path.
            known_pet = cls._known_pet_by_id(state, pet_id)
            if known_pet is None:
                return {
                    "error": "PET_NOT_OWNED_BY_CUSTOMER",
                    "message": (
                        "The supplied pet_id does not belong to the resolved customer. "
                        "Use the customer's verified pet roster; do not retry with guessed IDs."
                    ),
                }
            known_species = str(known_pet.get("pet_type") or "").strip().lower()
            pet_label = known_pet.get("pet_name") or pet_label
        elif args.get("pet_type"):
            known_species = str(args.get("pet_type") or "").strip().lower()
            pet_label = "the pet already on file"

        if known_species not in ("dog", "cat") or known_species == stated:
            return None

        return {
            "error": "SPECIES_MISMATCH",
            "message": (
                f"The customer's message says {stated!r}, but this call is about to use "
                f"{pet_label}'s real species ({known_species!r}). Do not silently answer "
                f"with {pet_label}'s species/pricing — ask the customer directly whether "
                f"they mean {pet_label} or a different pet before calling this tool again."
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

        generic_species = {
            "dog", "cat", "canine", "feline", "犬", "狗", "猫", "貓",
            "anjing", "kucing",
        }
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
            + [turn["content"] for turn in state.history if turn.get("role") == "human"]
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
    def _recent_customer_text(
        state, user_message: str, *, since_turn: int | None = None
    ) -> str:
        return recent_customer_text(state, user_message, since_turn=since_turn)

    @staticmethod
    def _canonicalize_explicit_breed_answer(args: dict, user_message: str) -> dict:
        """Repair only unambiguous mixed/unknown answers from this turn.

        The model has repeatedly mapped "I don't know the breed" to "mixed",
        or sent the species as breed after the customer answered "mixed". Both
        lose the customer's meaning and then trigger a guard rejection loop.
        Current-turn wording is authoritative enough to canonicalize these two
        special answers without guessing an actual breed.
        """
        text = str(user_message or "").casefold()
        mixed_answer = bool(
            re.search(
                r"\b(?:mixed\s*breed|cross\s*breed|crossbreed|mongrel|kacukan)\b"
                r"|混种|混種|米克斯|串串",
                text,
            )
        )
        unknown_answer = bool(
            re.search(
                r"(?:\b(?:don['’]?t|do\s+not|not\s+sure|unsure|unknown|no\s+idea)\b"
                r".{0,24}\b(?:breed|kind)\b)"
                r"|(?:\b(?:breed|kind)\b.{0,24}"
                r"\b(?:don['’]?t\s+know|do\s+not\s+know|not\s+sure|unknown)\b)"
                r"|(?:(?:不知道|不清楚|不确定|不確定|不晓得|不曉得).{0,8}(?:品种|品種))"
                r"|(?:(?:品种|品種).{0,8}(?:不知道|不清楚|不确定|不確定|不晓得|不曉得))"
                r"|(?:\b(?:tak\s+tahu|tidak\s+tahu|kurang\s+pasti)\b.{0,24}"
                r"\b(?:baka|breed)\b)",
                text,
            )
        )
        if mixed_answer:
            return {**args, "breed": "mixed"}
        if unknown_answer:
            return {**args, "breed": "unknown"}
        return args

    @staticmethod
    def _customer_stated_value(value: object, customer_text: str) -> bool:
        return customer_stated_value(value, customer_text)

    @classmethod
    def _reject_unconfirmed_registration_fields(
        cls, state, tool_name: str, args: dict, user_message: str
    ) -> dict | None:
        """Block profile fields the model invented instead of collecting."""
        customer_text = cls._recent_customer_text(state, user_message)
        if tool_name == "create_customer":
            full_name = str(args.get("full_name") or "").strip()
            if not cls._customer_stated_value(full_name, customer_text):
                return {
                    "error": "UNCONFIRMED_CUSTOMER_NAME",
                    "message": (
                        "full_name was not stated by the customer in this conversation. "
                        "Ask for their name and preserve exactly what they provide."
                    ),
                }
            address = str(args.get("address") or "").strip()
            if address and address != "-" and not cls._customer_stated_value(address, customer_text):
                return {
                    "error": "UNCONFIRMED_CUSTOMER_ADDRESS",
                    "message": "address was not stated by the customer; omit it instead of inventing one.",
                }
            return None

        if tool_name == "create_pet":
            pet_name = str(args.get("pet_name") or "").strip()
            if not cls._customer_stated_value(pet_name, customer_text):
                return {
                    "error": "UNCONFIRMED_PET_NAME",
                    "message": "pet_name was not stated by the customer; ask for it instead of inventing one.",
                }
            pet_type = str(args.get("pet_type") or "").strip().casefold()
            species_words = {
                "dog": cls._DOG_WORDS_RE,
                "cat": cls._CAT_WORDS_RE,
            }
            if pet_type in species_words and not species_words[pet_type].search(customer_text.casefold()):
                return {
                    "error": "UNCONFIRMED_PET_TYPE",
                    "message": "pet_type was not stated by the customer; ask whether the pet is a cat or dog.",
                }
        return None

    @classmethod
    def _reject_unconfirmed_vaccination_expiry(
        cls, state, args: dict, user_message: str
    ) -> dict | None:
        value = str(args.get("vaccination_expiry_text") or "").strip()
        customer_text = cls._recent_customer_text(state, user_message)
        if value and cls._customer_stated_value(value, customer_text):
            return None
        return {
            "error": "UNCONFIRMED_VACCINATION_EXPIRY",
            "message": (
                "vaccination_expiry_text must be the customer's own date wording. "
                "Ask for the expiry date and pass their wording through unchanged."
            ),
        }

    @classmethod
    def _reject_unconfirmed_optional_booking_fields(
        cls, state, args: dict, user_message: str
    ) -> dict | None:
        return reject_unconfirmed_optional_booking_fields(
            state,
            args,
            user_message,
            detect_ordinal_index=cls._detect_ordinal_index,
            normalize_option_label=cls._normalized_option_label,
        )

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
            return {
                "error": "NO_VERIFIED_COUPON",
                "message": (
                    "No eligible coupon has been observed from check_coupon_eligibility. "
                    "Fetch eligibility and use the customer's exact selected coupon."
                ),
            }
        try:
            coupon_id = int(args.get("coupon_id"))
        except (TypeError, ValueError):
            return {
                "error": "UNVERIFIED_COUPON_ID",
                "message": "coupon_id must be an exact ID from the verified eligible coupon list.",
            }
        chosen = next((c for c in state.known_coupons if c.get("coupon_id") == coupon_id), None)
        if chosen is None:
            return {
                "error": "UNVERIFIED_COUPON_ID",
                "message": f"coupon_id {coupon_id!r} is not in the verified eligible coupon list.",
            }
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
            return {
                "error": "NO_VERIFIED_PAYMENT_ID",
                "message": (
                    "No payment_id from a successful create_booking exists in this session. "
                    "The AI may only redeem against the exact payment it just created; do not guess an older ID."
                ),
            }
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
    def _strip_unconfirmed_pet_name(
        state, args: dict, tool_name: str, user_message: str
    ) -> dict:
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
        pending = state.pending_booking_confirmation
        # Even if the customer's initial action request contains the pet's
        # name ("cancel Milo's booking"), that is target identification, not
        # confirmation of a preview they have not seen yet. Only a later
        # customer turn may authorize the already-previewed target/change.
        if (
            not pending
            or pending.get("tool") != tool_name
            or pending.get("preview_turn") is None
            or int(pending.get("preview_turn") or 0) != state.turn_counter - 1
        ):
            return {**args, "confirm_pet_name": ""}
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
            r"\b(?:loyalty|voucher|coupon|points?|member(?:ship)?|register|enrol|sign\s*up)\b|"
            r"积分|点数|优惠券|礼券|会员|會員|注册|註冊|"
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
                r"\b(?:use|apply|redeem|yes|join|register|enrol|sign\s*up)\b|"
                r"使用|要用|兑换|加入|注册|註冊|成为会员|成為會員|"
                r"\b(?:guna|boleh|nak|mahu|daftar)\b",
                text,
            )
        )

        # A short yes/no is only attributable to loyalty when a real loyalty
        # check happened on an earlier customer turn. We deliberately do not
        # infer consent from a generic "yes" otherwise because it may be the
        # booking confirmation instead.
        if (
            state.loyalty_offer_shown_turn is not None
            and state.loyalty_offer_shown_turn == state.turn_counter - 1
        ):
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

    @classmethod
    def _capture_explicit_add_on_decision(cls, state, user_message: str) -> None:
        """Record a short decline only when the preceding reply asked about add-ons."""
        if not state.offered_add_on_options:
            return
        previous_reply = next(
            (
                str(turn.get("content") or "")
                for turn in reversed(state.history)
                if turn.get("role") == "ai"
            ),
            "",
        )
        if not cls._ADD_ON_REFERENCE_RE.search(previous_reply):
            return
        if cls._confirmation_intent(user_message) != "negative":
            return
        state.verified_facts["add_on_decision"] = {
            "value": "declined",
            "source": "customer_message",
            "turn": state.turn_counter,
        }

    @classmethod
    def _tools_for_turn(cls, state, user_message: str) -> list:
        """Expose optional loyalty capabilities only while they are relevant."""
        tools = _tools_for_scenario(state.active_scenario)
        if state.active_scenario in {"MEMBER", "LOYALTY_QUERY"}:
            return tools

        text = str(user_message or "")
        explicit_loyalty_topic = bool(re.search(
            r"\b(?:loyalty|voucher|coupon|points?|member(?:ship)?|register|enrol|sign\s*up)\b|"
            r"积分|点数|优惠券|礼券|会员|會員|注册|註冊|"
            r"\b(?:baucar|kupon|ganjaran|keahlian|daftar)\b",
            text,
            re.IGNORECASE,
        ))
        recent_offer_reply = (
            state.loyalty_offer_shown_turn is not None
            and state.loyalty_offer_shown_turn == state.turn_counter - 1
        )
        has_current_pending_confirmation = any(
            isinstance(pending, dict)
            and int(pending.get("preview_turn") or 0) == state.turn_counter - 1
            for name, pending in state.pending_actions.items()
            if name in {"register_loyalty_member", "redeem_reward"}
        )
        if explicit_loyalty_topic or recent_offer_reply or has_current_pending_confirmation:
            return tools

        loyalty_names = {
            "get_loyalty_balance",
            "check_coupon_eligibility",
            "register_loyalty_member",
            "redeem_reward",
        }
        return [tool for tool in tools if tool.name not in loyalty_names]

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

    _STAFF_HANDOFF_RE = re.compile(
        r"\b(?:talk|speak|chat|contact|connect|transfer|escalate|complain|complaint)\b.{0,35}"
        r"\b(?:human|person|staff|agent|manager|vet|veterinarian)\b"
        r"|\b(?:human|staff|agent|manager|vet|veterinarian)\b.{0,35}"
        r"\b(?:talk|speak|contact|help|follow\s*up|call\s*me)\b"
        r"|(?:我要|想找|请找|請找|转接|轉接|联系|聯繫|投诉|投訴).{0,10}(?:人工|员工|員工|客服|经理|經理|兽医|獸醫)"
        r"|(?:人工|员工|員工|客服|经理|經理|兽医|獸醫).{0,10}(?:联系|聯繫|跟进|跟進|处理|處理|回复|回覆)"
        r"|\b(?:nak|mahu|boleh)\b.{0,30}\b(?:cakap|hubungi|jumpa)\b.{0,20}\b(?:staf|pegawai|pengurus|doktor\s+haiwan)\b",
        re.IGNORECASE,
    )

    @classmethod
    def _is_staff_handoff_request(cls, user_message: str) -> bool:
        return bool(cls._STAFF_HANDOFF_RE.search(user_message or ""))

    _DOCUMENT_REQUEST_RE = re.compile(
        r"\b(?:send|resend|share|download|where(?:'s|\s+is)|didn'?t\s+receive|not\s+received)\b.{0,35}"
        r"\b(?:confirmation|slip|document|pdf)\b"
        r"|(?:发送|發送|重发|重發|再发|再發|没收到|沒收到|在哪里|在哪裡).{0,12}(?:确认单|確認單|确认书|確認書|单据|單據|文件|PDF)"
        r"|\b(?:hantar|hantar\s+semula|tak\s+terima|tidak\s+terima)\b.{0,30}\b(?:pengesahan|dokumen|slip|pdf)\b",
        re.IGNORECASE,
    )

    @classmethod
    def _is_document_request(cls, user_message: str) -> bool:
        return bool(cls._DOCUMENT_REQUEST_RE.search(user_message or ""))

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
            time_range = extract_time_range(text)
            duration = time_range[2] if time_range else None
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
        if tool_name == "register_loyalty_member":
            args = {k: v for k, v in args.items() if k != "confirmed"}
        if tool_name in {"cancel_booking", "reschedule_booking"}:
            # The typed pet name authorizes an already-previewed change; it is
            # not itself part of the target booking/date/time payload.
            args = {k: v for k, v in args.items() if k != "confirm_pet_name"}
        normalized = {
            k: (round(v, 2) if isinstance(v, float) else v) for k, v in sorted(args.items())
        }
        return f"{tool_name}:{json.dumps(normalized, sort_keys=True, default=str)}"

    _AFFIRMATIVE_RE = AFFIRMATIVE_RE
    _NEGATIVE_RE = NEGATIVE_RE
    _ADD_ON_REFERENCE_RE = ADD_ON_REFERENCE_RE

    @classmethod
    def _confirmation_intent(cls, user_message: str) -> str | None:
        return confirmation_intent(user_message)

    @classmethod
    def _pending_scenario_matches(cls, state, tool_name: str, pending: dict) -> bool:
        scenario = pending.get("scenario")
        if scenario is not None:
            return scenario == state.active_scenario
        # Compatibility for previews created by the previous deployment.
        # Still fail closed unless the active scenario owns this exact tool.
        return cls._SCENARIO_CONFIRMING_TOOLS.get(tool_name) == state.active_scenario

    @classmethod
    def _pending_confirmation_tool(cls, state, user_message: str) -> str | None:
        """Return the exact pending write that a standalone ``yes`` confirms.

        Confirmation is application state, not a planning decision for the
        model.  Without this guard the model can answer a standalone ``yes``
        with prose (including a fresh greeting) and never call the pending
        mutation at all.  Prefer the action owned by the active scenario and
        fail closed when more than one unrelated action is pending.
        """
        if cls._confirmation_intent(user_message) != "affirmative":
            return None

        preferred = {
            "MAKE_BOOKING": "create_booking",
            "LOYALTY_QUERY": "redeem_reward",
            "MEMBER": "register_loyalty_member",
        }.get(state.active_scenario)
        candidates = []
        for tool_name in ("create_booking", "redeem_reward", "register_loyalty_member"):
            pending = state.pending_actions.get(tool_name)
            args = pending.get("args") if isinstance(pending, dict) else None
            if (
                isinstance(args, dict)
                and not args.get("truncated")
                and int(pending.get("preview_turn") or 0) == state.turn_counter - 1
                and cls._pending_scenario_matches(state, tool_name, pending)
            ):
                candidates.append(tool_name)

        if preferred in candidates:
            return preferred
        return candidates[0] if len(candidates) == 1 else None

    @classmethod
    def _authorize_pending_action(cls, state, tool_name: str, args: dict, user_message: str) -> dict | None:
        """Return None only when an exact preview was affirmatively confirmed
        on a later customer turn. Otherwise store/retain the preview and return
        a non-executing result for the model to explain."""
        signature = cls._mutation_signature(tool_name, args)
        pending = state.pending_actions.get(tool_name)
        intent = cls._confirmation_intent(user_message)
        if tool_name == "register_loyalty_member" and intent is None:
            membership_reply = re.sub(
                r"[\s.!?,，。！？]+", " ", str(user_message or "").strip()
            ).strip()
            if re.fullmatch(
                r"(?:register(?: me)?|sign me up|join(?: the)? membership|"
                r"注册|註冊|加入会员|加入會員|成为会员|成為會員)",
                membership_reply,
                re.IGNORECASE,
            ):
                # A direct response to an already-presented membership offer is
                # the user's explicit consent for this action only.  Keep these
                # phrases out of the global affirmative matcher so that
                # "register" can never confirm a booking or another mutation.
                intent = "affirmative"
        if pending and pending.get("signature") == signature:
            if intent == "negative":
                state.pending_actions.pop(tool_name, None)
                return {
                    "status": "cancelled_by_customer",
                    "error_code": "ACTION_DECLINED",
                    "message": "The customer declined this pending action. Do not execute it.",
                }
            if (
                intent == "affirmative"
                and int(pending.get("preview_turn") or 0) == state.turn_counter - 1
                and cls._pending_scenario_matches(state, tool_name, pending)
            ):
                return None
            if int(pending.get("preview_turn") or 0) != state.turn_counter:
                # The old preview is not executable, but this tool result will
                # show the exact payload again now. Renew it for one turn.
                state.pending_actions[tool_name] = {
                    "signature": signature,
                    "args": cls._compact_evidence_result(args, max_chars=1200),
                    "preview_turn": state.turn_counter,
                    "scenario": state.active_scenario,
                }
                pending = state.pending_actions[tool_name]
            return {
                "status": "confirmation_required",
                "error_code": "EXPLICIT_CONFIRMATION_REQUIRED",
                "data": {"action": tool_name, "preview": pending.get("args")},
                "message": (
                    "This exact action is only previewed. Execute it only after the customer "
                    "replies with a standalone affirmative confirmation on the immediately following turn."
                ),
            }

        state.pending_actions[tool_name] = {
            "signature": signature,
            "args": cls._compact_evidence_result(args, max_chars=1200),
            "preview_turn": state.turn_counter,
            "scenario": state.active_scenario,
        }
        return {
            "status": "confirmation_required",
            "error_code": "ACTION_PREVIEW_CREATED",
            "data": {"action": tool_name, "preview": state.pending_actions[tool_name]["args"]},
            "message": (
                "No write occurred. Present these exact details to the customer and ask for "
                "a standalone confirmation. Retry with identical arguments only after their immediately following reply."
            ),
        }

    @staticmethod
    def _normalize_clock(value) -> str:
        text = str(value or "").strip()
        match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", text)
        return f"{int(match.group(1)):02d}:{match.group(2)}" if match else text.casefold()

    @classmethod
    def _reject_unverified_booking_payload(
        cls, state, args: dict, confirmed_preview_turn: int | None = None
    ) -> dict | None:
        service_type = str(args.get("service_type") or "").strip().upper()
        pet_id = args.get("pet_id")
        package = str(args.get("package_name") or "").strip().casefold()
        try:
            price = round(float(args.get("price")), 2)
        except (TypeError, ValueError):
            price = None

        requested_time = cls._normalize_clock(args.get("time"))
        requested_check_out_time = cls._normalize_clock(args.get("check_out_time"))
        try:
            requested_duration = int(args.get("duration_minutes"))
        except (TypeError, ValueError):
            requested_duration = None
        if service_type == "DAYCARE" and requested_duration is None:
            def clock_minutes(value: str) -> int | None:
                match = re.fullmatch(r"(\d{2}):(\d{2})", value)
                if not match:
                    return None
                return int(match.group(1)) * 60 + int(match.group(2))

            start_minutes = clock_minutes(requested_time)
            end_minutes = clock_minutes(requested_check_out_time)
            if start_minutes is not None and end_minutes is not None and end_minutes > start_minutes:
                requested_duration = end_minutes - start_minutes

        candidates = [
            option for option in state.verified_service_options
            if str(option.get("service_type") or "").upper() == service_type
            and (not option.get("pet_id") or str(option.get("pet_id")) == str(pet_id))
            and str(option.get("service_name") or option.get("room_type") or "").strip().casefold() == package
        ]
        def option_price_matches(option: dict) -> bool:
            if price is None or option.get("price") in (None, ""):
                return False
            expected = float(option["price"])
            if (
                service_type == "DAYCARE"
                and str(option.get("pricing_unit") or "").casefold() == "hour"
            ):
                if requested_duration is None or requested_duration <= 0:
                    return False
                expected *= requested_duration / 60
            return abs(expected - price) <= 0.01

        chosen = next((option for option in candidates if option_price_matches(option)), None)
        if chosen is None:
            return {
                "error": "UNVERIFIED_SERVICE_OPTION",
                "message": (
                    "package_name/price is not an exact option observed from "
                    "get_booking_service_options for this service and pet. Fetch the catalogue, "
                    "let the customer select an option, and reuse its exact name and price."
                ),
            }

        if service_type == "DAYCARE" and requested_duration is not None:
            exact = chosen.get("duration_minutes")
            minimum = chosen.get("min_duration_minutes")
            maximum = chosen.get("max_duration_minutes")
            applicable = True
            if exact not in (None, ""):
                applicable = requested_duration == int(exact)
            if minimum not in (None, ""):
                applicable = applicable and (
                    requested_duration > int(minimum)
                    if chosen.get("min_duration_exclusive")
                    else requested_duration >= int(minimum)
                )
            if maximum not in (None, ""):
                applicable = applicable and (
                    requested_duration < int(maximum)
                    if chosen.get("max_duration_exclusive")
                    else requested_duration <= int(maximum)
                )
            if not applicable:
                return {
                    "error": "PACKAGE_NOT_APPLICABLE_FOR_DURATION",
                    "message": (
                        "The selected daycare package does not cover the complete visit "
                        "duration. Fetch the catalogue and use the applicable duration tier."
                    ),
                }

        if args.get("add_on"):
            add_on_name = str(args.get("add_on") or "").strip().casefold()
            try:
                add_on_price = round(float(args.get("add_on_price")), 2)
            except (TypeError, ValueError):
                add_on_price = None
            valid_add_on = any(
                str(option.get("service_type") or "").upper() == service_type
                and str(option.get("selection_kind") or "") == "add_on"
                and str(option.get("service_name") or "").strip().casefold() == add_on_name
                and add_on_price is not None
                and option.get("price") not in (None, "")
                and abs(float(option["price"]) - add_on_price) <= 0.01
                for option in state.verified_service_options
            )
            if not valid_add_on:
                return {
                    "error": "UNVERIFIED_ADD_ON",
                    "message": "The add-on name/price was not observed as an exact catalogue option.",
                }

        requested_date = str(args.get("date") or "")
        requested_room = str(
            args.get("package_name") if service_type == "BOARDING" else args.get("room_type") or ""
        ).strip().casefold()
        requested_check_out_date = str(args.get("check_out_date") or "").strip()
        requested_preferred_staff = str(args.get("preferred_staff") or "").strip().casefold()

        def slot_matches_constraints(slot: dict) -> bool:
            try:
                verified_this_turn = int(slot.get("verified_turn")) == int(state.turn_counter)
            except (TypeError, ValueError):
                verified_this_turn = False
            if not verified_this_turn and confirmed_preview_turn is not None:
                try:
                    verified_this_turn = (
                        int(slot.get("verified_turn")) == int(confirmed_preview_turn)
                    )
                except (TypeError, ValueError):
                    verified_this_turn = False
            if (
                not verified_this_turn
                or str(slot.get("service_type") or "").upper() != service_type
                or str(slot.get("date") or "") != requested_date
                or cls._normalize_clock(slot.get("time")) != requested_time
            ):
                return False
            if requested_preferred_staff != str(slot.get("preferred_staff") or "").strip().casefold():
                return False
            if service_type == "BOARDING":
                return (
                    bool(requested_room)
                    and str(slot.get("room_type") or "").strip().casefold() == requested_room
                    and bool(requested_check_out_date)
                    and str(slot.get("check_out_date") or "") == requested_check_out_date
                    and cls._normalize_clock(slot.get("check_out_time")) == requested_check_out_time
                )
            if service_type == "DAYCARE":
                try:
                    checked_duration = int(slot.get("duration_minutes"))
                except (TypeError, ValueError):
                    return False
                return (
                    requested_duration is not None
                    and checked_duration == requested_duration
                    and (
                        not requested_check_out_time
                        or cls._normalize_clock(slot.get("check_out_time")) == requested_check_out_time
                    )
                )
            return True

        slot_verified = any(
            slot_matches_constraints(slot)
            for slot in state.verified_availability_slots
        )
        if not slot_verified:
            return {
                "error": "UNVERIFIED_AVAILABILITY_SLOT",
                "message": (
                    "This exact service/date/time was not returned as available by "
                    "check_availability with the same room/stay, duration, and staff constraints. "
                    "Verify the complete request and let the customer select the observed slot."
                ),
            }
        return None

    @staticmethod
    def _availability_args_for_booking(args: dict) -> dict:
        """Build an exact fresh availability probe from a proposed booking."""
        service_type = str(args.get("service_type") or "").strip().upper()
        return {
            "company_id": args.get("company_id"),
            "service_type": service_type,
            "date": args.get("date") or "",
            "time": args.get("time") or "",
            "room_type": (
                args.get("package_name") if service_type == "BOARDING"
                else args.get("room_type") or ""
            ),
            "check_out_date": args.get("check_out_date") or "",
            "customer_id": args.get("customer_id") or "",
            "pet_id": args.get("pet_id") or "",
            "exclude_booking_id": "",
            "duration_minutes": args.get("duration_minutes"),
            "check_out_time": args.get("check_out_time") or "",
            "preferred_staff": args.get("preferred_staff") or "",
            "selection_target": "CHECK_IN",
            "check_in_time": "",
        }

    @classmethod
    def _availability_result_contains_booking(cls, result: dict, booking_args: dict) -> bool:
        if not isinstance(result, dict) or result.get("status") != "success":
            return False
        requested = cls._normalize_clock(booking_args.get("time"))
        return any(
            cls._normalize_clock(slot) == requested
            for slot in (result.get("data") or {}).get("available_slots") or []
        )

    def _customer_selected_booking_time(
        self, state, args: dict, user_message: str
    ) -> bool:
        """True only when this turn explicitly selects the proposed time."""
        requested = self._normalize_clock(args.get("time"))
        resolved = state.current_datetime_resolution or {}
        if requested and self._normalize_clock(resolved.get("time")) == requested:
            return True
        selected = self._resolve_ordinal_reference(state, user_message)
        return bool(
            selected
            and requested
            and self._normalize_clock(selected.get("slot")) == requested
        )

    @staticmethod
    def _known_pet_by_id(state, pet_id) -> dict | None:
        return known_pet_by_id(state, pet_id)

    def _run_tool(
        self,
        tool_call: dict,
        state=None,
        user_message: str = "",
        sibling_tool_names: frozenset = frozenset(),
        successful_read_results: dict[str, object] | None = None,
        successful_read_lock: Lock | None = None,
        authoritative_args: dict | None = None,
    ):
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
        args = dict(
            authoritative_args
            if authoritative_args is not None
            else (tool_call.get("args") or {})
        )
        # Membership belongs to an existing customer row.  The model has been
        # observed calling create_customer after the customer accepted a
        # membership offer, which can only return "Customer already exists"
        # and caused an endless registration/booking loop. Redirect that exact
        # mistake to the idempotent membership tool using server-resolved
        # identity. If a prior offer was actually shown and accepted, that is
        # already the required two-turn consent; otherwise request a preview.
        if (
            state is not None
            and tool_call.get("name") == "create_customer"
            and state.customer_id is not None
            and (
                state.active_scenario == "MEMBER"
                or state.loyalty_decision == "accepted"
            )
        ):
            accepted_prior_offer = bool(
                state.loyalty_decision == "accepted"
                and state.loyalty_offer_shown_turn is not None
                and state.loyalty_offer_shown_turn < state.turn_counter
            )
            tool_call["name"] = "register_loyalty_member"
            args = {
                "company_id": state.company_id,
                "customer_id": state.customer_id,
                "confirmed": accepted_prior_offer,
            }
            if accepted_prior_offer:
                state.pending_actions["register_loyalty_member"] = {
                    "signature": self._mutation_signature(
                        "register_loyalty_member", args
                    ),
                    "args": dict(args),
                    "preview_turn": state.loyalty_offer_shown_turn,
                    "scenario": state.active_scenario,
                }

        tool = TOOLS_BY_NAME.get(tool_call["name"])
        if tool is None:
            return {"error": f"UNKNOWN_TOOL:{tool_call['name']}"}
        confirmed_preview_turn: int | None = None
        try:
            if state is not None:
                allowed_names = {candidate.name for candidate in _tools_for_scenario(state.active_scenario)}
                if (
                    tool_call["name"] in MUTATING_TOOL_NAMES
                    and tool_call["name"] not in allowed_names
                ):
                    return {
                        "error": "TOOL_NOT_ALLOWED_FOR_SCENARIO",
                        "message": (
                            f"{tool_call['name']} is not authorized while active_scenario is "
                            f"{state.active_scenario!r}. Declare the customer's exact scenario first."
                        ),
                    }
                if (
                    tool_call["name"] == "update_conversation_state"
                    and state.active_scenario
                    and state.active_scenario != "POLICY_QUERY"
                    and args.get("active_scenario") == "POLICY_QUERY"
                ):
                    # Reads do not need a scenario switch. Return the state
                    # that actually remains active so the model and refreshed
                    # RUNTIME_CONTEXT cannot disagree about the main goal.
                    return {
                        "active_scenario": state.active_scenario,
                        "current_step": state.current_step,
                        "service_type": state.service_type,
                        "note": (
                            "Policy is a side question. Keep the active goal, call "
                            "retrieve_policy, answer it, then continue the original flow."
                        ),
                    }
                if tool_call["name"] in COMPANY_SCOPED_TOOL_NAMES or "company_id" in args:
                    # Tenant scope is request/session authority, never a model
                    # choice. Every company-scoped tool receives the company
                    # attached to this ConversationState even if the model
                    # omits, hallucinates, or reuses another tenant's ID.
                    args = {**args, "company_id": state.company_id}
                if (
                    tool_call["name"] == "retrieve_policy"
                    and state.active_scenario == "MAKE_BOOKING"
                    and self._policy_query_is_catalogue_only(args.get("query"))
                ):
                    return {
                        "status": "error",
                        "error_code": "USE_CATALOGUE_TOOL",
                        "message": (
                            "This is a catalogue/pricing lookup, not a policy side question. "
                            "Use get_booking_service_options once and continue the booking flow."
                        ),
                    }
                if tool_call["name"] == "create_customer":
                    # The inbound, validated chat identity is authoritative;
                    # registration must never use a model-invented phone.
                    args = {
                        **args,
                        "company_id": state.company_id,
                        "phone_number": state.phone_number,
                    }
                    registration_rejection = self._reject_unconfirmed_registration_fields(
                        state, tool_call["name"], args, user_message
                    )
                    if registration_rejection:
                        return registration_rejection
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
                    elif tool_call["name"] not in {
                        "check_availability",
                        "check_availability_range",
                        # General catalogue enquiries do not require a customer.
                        # Only the pet-specific form requires ownership, which
                        # the tool itself rejects when customer_id is absent.
                        "get_booking_service_options",
                    }:
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
                if tool_call["name"] in {
                    "create_booking", "redeem_reward", "register_loyalty_member"
                }:
                    pending_action = state.pending_actions.get(tool_call["name"])
                    pending_args = (
                        pending_action.get("args")
                        if isinstance(pending_action, dict)
                        else None
                    )
                    if (
                        self._confirmation_intent(user_message) == "affirmative"
                        and isinstance(pending_args, dict)
                        and not pending_args.get("truncated")
                        and int(pending_action.get("preview_turn") or 0) == state.turn_counter - 1
                        and self._pending_scenario_matches(
                            state, tool_call["name"], pending_action
                        )
                    ):
                        # A confirmation authorizes the exact payload shown to
                        # the customer. Never let the model rebuild, omit, or
                        # alter it after the customer says "correct"/"confirm".
                        # This also prevents membership confirmation from
                        # silently dropping confirmed=true.
                        if tool_call["name"] == "create_booking":
                            confirmed_preview_turn = int(pending_action["preview_turn"])
                        args = {
                            **pending_args,
                            "company_id": state.company_id,
                            "customer_id": state.customer_id,
                        }
                if tool_call["name"] == "get_last_completed_booking":
                    # A repeat template is always scoped by application-held
                    # identity and the service explicitly named by the
                    # customer. Never permit the model to broaden this to all
                    # pets/services (the old API did exactly that and returned
                    # Milo's daycare for a grooming repeat request).
                    if state.pet_id is not None:
                        args = {**args, "pet_id": state.pet_id}
                    repeat_service = (
                        self._explicit_service_type(user_message)
                        or str(state.service_type or "").strip().upper()
                    )
                    if repeat_service:
                        args = {**args, "service_type": repeat_service}
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
                    if len(state.known_pets) > 1:
                        cached_pet = self._known_pet_by_id(state, state.pet_id)
                        model_pet = self._known_pet_by_id(state, args.get("pet_id"))
                        # A pet explicitly resolved from this customer message
                        # is authoritative. During later turns of the same flow,
                        # an omitted/same pet_id may safely reuse that selection.
                        # A different real roster ID is not permission by itself:
                        # the model can guess one, so reject instead of silently
                        # switching pets or silently forcing the stale cached ID.
                        if state.pet_selected_turn == state.turn_counter:
                            known = cached_pet
                        elif cached_pet is not None and args.get("pet_id") in (None, ""):
                            known = cached_pet
                        elif (
                            cached_pet is not None
                            and model_pet is not None
                            and model_pet.get("pet_id") == cached_pet.get("pet_id")
                        ):
                            known = model_pet
                        else:
                            known = None
                        if known is None:
                            return {
                                "status": "missing_information",
                                "error_code": "PET_SELECTION_REQUIRED",
                                "message": (
                                    "Several pets are registered and the current message did not "
                                    "unambiguously select the pet represented by this tool call. "
                                    "Ask which pet; do not choose or switch pet_id yourself."
                                ),
                            }
                        args = {**args, "pet_id": known.get("pet_id")}
                    else:
                        known = self._known_pet_by_id(state, args.get("pet_id"))
                        if known is None and state.pet_id is not None:
                            args = {**args, "pet_id": state.pet_id}
                            known = self._known_pet_by_id(state, state.pet_id)
                    if tool_call["name"] == "create_booking" and known is not None:
                        # pet_name is display data, but allowing the model to
                        # mismatch it with a real pet_id produces false
                        # confirmations and can poison downstream documents.
                        args = {**args, "pet_name": known.get("pet_name")}
                        if state.service_type:
                            args = {**args, "service_type": state.service_type}
                    if (
                        tool_call["name"] == "get_booking_service_options"
                        and self._is_repeat_booking_request(user_message)
                    ):
                        repeat_service = (
                            self._explicit_service_type(user_message)
                            or str(state.service_type or "").strip().upper()
                        )
                        if repeat_service:
                            args = {**args, "service_type": repeat_service}
                if tool_call["name"] in {"check_availability", "check_availability_range"}:
                    # Availability must include the selected pet so a second
                    # service cannot be offered over that pet's active booking.
                    if len(state.known_pets) > 1:
                        known = self._known_pet_by_id(state, state.pet_id)
                        if known is None:
                            return {
                                "status": "missing_information",
                                "error_code": "PET_SELECTION_REQUIRED",
                                "message": (
                                    "Several pets are registered and none was selected for this "
                                    "booking. Ask which pet before checking availability."
                                ),
                            }
                        args = {**args, "pet_id": known.get("pet_id")}
                    elif state.pet_id is not None:
                        known = self._known_pet_by_id(state, args.get("pet_id"))
                        args = {**args, "pet_id": (known or {}).get("pet_id") or state.pet_id}
                if (
                    tool_call["name"] in {"check_availability", "check_availability_range"}
                    and self._is_repeat_booking_request(user_message)
                ):
                    repeat_service = (
                        self._explicit_service_type(user_message)
                        or str(state.service_type or "").strip().upper()
                    )
                    resolved = state.current_datetime_resolution or {}
                    time_filter = resolved.get("time") or resolved.get("period") or ""
                    if tool_call["name"] == "check_availability":
                        args = {
                            **args,
                            "service_type": repeat_service or args.get("service_type"),
                            "date": resolved.get("date") or args.get("date"),
                            "time": time_filter or args.get("time") or "",
                        }
                    else:
                        date_range = resolved.get("date_range") or {}
                        args = {
                            **args,
                            "service_type": repeat_service or args.get("service_type"),
                            "start_date": date_range.get("start") or args.get("start_date"),
                            "end_date": date_range.get("end") or args.get("end_date"),
                            "time": time_filter or args.get("time") or "",
                        }
                if tool_call["name"] == "create_booking":
                    optional_field_rejection = self._reject_unconfirmed_optional_booking_fields(
                        state, args, user_message
                    )
                    if optional_field_rejection:
                        return optional_field_rejection
                    if args.get("preferred_staff"):
                        state.preferred_staff = str(args["preferred_staff"])
                    elif state.preferred_staff:
                        args = {**args, "preferred_staff": state.preferred_staff}
                if tool_call["name"] == "update_pet_vaccination":
                    if state.pet_id is not None:
                        args = {**args, "pet_id": state.pet_id}
                    vaccination_rejection = self._reject_unconfirmed_vaccination_expiry(
                        state, args, user_message
                    )
                    if vaccination_rejection:
                        return vaccination_rejection
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
                if (
                    tool_call["name"] == "reschedule_booking"
                    and args.get("confirm_pet_name")
                    and state.pending_booking_confirmation
                    and state.pending_booking_confirmation.get("tool") == "reschedule_booking"
                ):
                    expected_signature = state.pending_booking_confirmation.get("change_signature")
                    if (
                        expected_signature
                        and self._mutation_signature("reschedule_booking", args)
                        != expected_signature
                    ):
                        return {
                            "error": "RESCHEDULE_DETAILS_CHANGED",
                            "message": (
                                "The booking/date/time details differ from the change the customer "
                                "was shown. No write occurred. Preview the new exact details and "
                                "ask for confirmation again."
                            ),
                        }
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

                args = self._strip_unconfirmed_pet_name(
                    state, args, tool_call["name"], user_message
                )
                date_rejection = self._reject_unverified_date(state, tool_call["name"], args)
                if date_rejection:
                    return date_rejection
                species_rejection = self._reject_species_mismatch(state, args, user_message)
                if species_rejection:
                    return species_rejection
                if (
                    tool_call["name"] == "send_booking_confirmation"
                    and not self._is_document_request(user_message)
                ):
                    return {
                        "error": "EXPLICIT_DOCUMENT_REQUEST_REQUIRED",
                        "message": (
                            "The current customer message does not explicitly request a booking "
                            "confirmation document/slip. Do not send or resend one."
                        ),
                    }
                if tool_call["name"] == "create_pet":
                    args = self._canonicalize_explicit_breed_answer(args, user_message)
                    registration_rejection = self._reject_unconfirmed_registration_fields(
                        state, tool_call["name"], args, user_message
                    )
                    if registration_rejection:
                        return registration_rejection
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
                if tool_call["name"] == "create_booking":
                    booking_rejection = self._reject_unverified_booking_payload(
                        state, args, confirmed_preview_turn=confirmed_preview_turn
                    )
                    if booking_rejection:
                        if (
                            booking_rejection.get("error") == "UNVERIFIED_AVAILABILITY_SLOT"
                            and self._customer_selected_booking_time(
                                state, args, user_message
                            )
                        ):
                            booking_rejection = {
                                **booking_rejection,
                                "_internal_required_tool": "check_availability",
                                "_internal_required_args": self._availability_args_for_booking(args),
                                "_internal_retry_args": dict(args),
                            }
                        return booking_rejection
                    confirmation_rejection = self._authorize_pending_action(
                        state, tool_call["name"], args, user_message
                    )
                    if confirmation_rejection:
                        return confirmation_rejection
                if tool_call["name"] == "redeem_reward":
                    confirmation_rejection = self._authorize_pending_action(
                        state, tool_call["name"], args, user_message
                    )
                    if confirmation_rejection:
                        return confirmation_rejection
                if tool_call["name"] == "register_loyalty_member" and args.get("confirmed"):
                    confirmation_rejection = self._authorize_pending_action(
                        state, tool_call["name"], args, user_message
                    )
                    if confirmation_rejection:
                        return confirmation_rejection
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
            read_signature = None
            if (
                tool_call["name"] in CACHEABLE_READ_TOOL_NAMES
                and successful_read_results is not None
            ):
                read_signature = self._mutation_signature(tool_call["name"], args)
                if successful_read_lock is None:
                    cached_result = successful_read_results.get(read_signature)
                else:
                    with successful_read_lock:
                        cached_result = successful_read_results.get(read_signature)
                if cached_result is not None:
                    if isinstance(cached_result, dict):
                        return {
                            **cached_result,
                            "_internal_duplicate_read_suppressed": True,
                            "duplicate_suppressed": True,
                            "message": (
                                "This identical read already succeeded in the current customer "
                                "turn. Its result above is the authoritative cached result. Do "
                                "not call this or another equivalent read again; answer the "
                                "customer from the available evidence or ask for genuinely "
                                "missing customer input."
                            ),
                        }
                    return {
                        "status": self._tool_result_status(cached_result),
                        "data": cached_result,
                        "_internal_duplicate_read_suppressed": True,
                        "duplicate_suppressed": True,
                        "message": (
                            "This identical read already succeeded in the current customer "
                            "turn. Use this cached result and do not call it again."
                        ),
                    }
            try:
                result = tool.invoke(args)
                if read_signature is not None:
                    if successful_read_lock is None:
                        successful_read_results.setdefault(read_signature, result)
                    else:
                        with successful_read_lock:
                            successful_read_results.setdefault(read_signature, result)
                return result
            except Exception as exc:  # tool/data-layer failure -> structured error, never crash the turn
                result = {"status": "error", "error": str(exc)}
                if read_signature is not None:
                    if successful_read_lock is None:
                        successful_read_results.setdefault(read_signature, result)
                    else:
                        with successful_read_lock:
                            successful_read_results.setdefault(read_signature, result)
                return result
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
                    state.pet_selected_turn = state.turn_counter
            return
        if tool_name == "find_pet_by_name" and result.get("status") == "success":
            data = result.get("data") or {}
            if data.get("pet_id") is not None:
                state.pet_id = data.get("pet_id")
                state.pet_type = data.get("pet_type")
                state.pet_name = data.get("pet_name")
                state.pet_size = data.get("size")
                state.pet_breed = data.get("breed")
                state.pet_selected_turn = state.turn_counter
        elif tool_name == "get_pets":
            if result.get("status") == "success":
                pets = (result.get("data") or {}).get("pets") or []
                state.pets_context_status = "available"
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
            else:
                state.pets_context_status = "unavailable"
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
                state.pet_selected_turn = state.turn_counter
                state.known_pets = state.known_pets + [
                    {
                        "pet_id": pet.get("pet_id"),
                        "pet_type": pet.get("pet_type"),
                        "pet_name": pet.get("pet_name"),
                        "pet_size": pet.get("size"),
                        "pet_breed": pet.get("breed"),
                    }
                ]
                state.pets_context_status = "available"

    @staticmethod
    def _cache_latest_booking(state, tool_name: str, result: dict) -> None:
        """Keep DB-fetched existing bookings visible after the tool turn."""
        if tool_name not in ("get_latest_booking", "get_booking_by_id", "reschedule_booking"):
            return
        if not isinstance(result, dict):
            return
        if tool_name == "get_latest_booking":
            if result.get("status") == "not_found":
                state.latest_booking = None
                state.booking_context_status = "not_found"
                return
            if result.get("status") != "success":
                state.booking_context_status = "unavailable"
                return
            state.booking_context_status = (
                "available" if result.get("data") else "not_found"
            )
        elif result.get("status") != "success":
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
            state.offered_add_on_options = []
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
                state.offered_add_on_options = []
            return
        data = result.get("data") or {}
        if tool_name == "get_booking_service_options":
            state.offered_options = [
                {"label": opt.get("service_name") or opt.get("room_type"), **opt}
                for opt in (data.get("service_options") or [])
            ]
            state.offered_add_on_options = [
                {"label": opt.get("service_name") or opt.get("room_type"), **opt}
                for opt in (data.get("add_on_options") or [])
            ]
        elif tool_name == "check_availability":
            state.offered_add_on_options = []
            if str(data.get("selection_target") or "").upper() == "CHECK_OUT":
                slots = data.get("available_check_out_times") or []
                state.offered_options = [
                    {
                        "label": slot,
                        "slot": slot,
                        "selection_target": "CHECK_OUT",
                        "check_in_time": data.get("check_in_time"),
                    }
                    for slot in slots
                ]
            else:
                slots = data.get("available_slots") or []
                state.offered_options = [{"label": slot, "slot": slot} for slot in slots]
        elif tool_name == "check_availability_range":
            state.offered_add_on_options = []
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

    @classmethod
    def _cache_booking_evidence(cls, state, tool_name: str, result: dict, args: dict) -> None:
        """Cache only catalogue/availability values returned by real tools."""
        if not isinstance(result, dict):
            return
        service_type = str(
            args.get("service_type") or (result.get("data") or {}).get("service_type")
            or result.get("service_type") or ""
        ).upper()
        if tool_name == "get_booking_service_options" and result.get("status") == "success":
            data = result.get("data") or {}
            options = list(data.get("service_options") or []) + list(data.get("add_on_options") or [])
            # customer_tools already converts RAG rows into these structured
            # lists. Parse raw evidence only as a compatibility fallback for
            # older tool results, never append both representations.
            if not options:
                selected_pet = next(
                    (
                        pet for pet in state.known_pets
                        if str(pet.get("pet_id") or "") == str(args.get("pet_id") or "")
                    ),
                    None,
                )
                parsed_services, parsed_add_ons = _extract_daycare_catalogue_options(
                    result.get("detailed_pricing_by_size") or [],
                    pet_size=(selected_pet or {}).get("pet_size") or "",
                )
                options = parsed_services + parsed_add_ons
            deduped_options: list[dict] = []
            seen_options: set[tuple[str, float | None, str]] = set()
            for option in options:
                if not isinstance(option, dict):
                    continue
                try:
                    normalized_price = round(float(option.get("price")), 2)
                except (TypeError, ValueError):
                    normalized_price = None
                key = (
                    cls._normalized_option_label(
                        option.get("service_name") or option.get("room_type")
                    ),
                    normalized_price,
                    str(option.get("selection_kind") or "service").casefold(),
                )
                if key in seen_options:
                    continue
                seen_options.add(key)
                deduped_options.append(option)
            options = deduped_options
            state.verified_service_options = [
                option for option in state.verified_service_options
                if not (
                    str(option.get("service_type") or "").upper() == service_type
                    and str(option.get("pet_id") or "") == str(args.get("pet_id") or "")
                )
            ]
            state.verified_service_options.extend(
                {
                    **option,
                    "service_type": service_type,
                    "pet_id": args.get("pet_id"),
                }
                for option in options
                if isinstance(option, dict)
            )
            return

        new_slots: list[dict] = []
        if tool_name == "check_availability" and result.get("status") == "success":
            data = result.get("data") or {}
            if str(data.get("selection_target") or "").upper() == "CHECK_OUT":
                check_in_time = data.get("check_in_time") or args.get("check_in_time") or ""

                def minutes(value: object) -> int | None:
                    normalized = PawfectOrchestrator._normalize_clock(value)
                    match = re.fullmatch(r"(\d{2}):(\d{2})", normalized)
                    if not match:
                        return None
                    return int(match.group(1)) * 60 + int(match.group(2))

                checkin_minutes = minutes(check_in_time)
                for pickup in data.get("available_check_out_times") or []:
                    pickup_minutes = minutes(pickup)
                    duration = None
                    if (
                        service_type == "DAYCARE"
                        and checkin_minutes is not None
                        and pickup_minutes is not None
                        and pickup_minutes > checkin_minutes
                    ):
                        duration = pickup_minutes - checkin_minutes
                    new_slots.append({
                        "service_type": service_type,
                        "verified_turn": state.turn_counter,
                        "date": data.get("booking_date") or args.get("date"),
                        "time": check_in_time,
                        "room_type": data.get("room_type") or args.get("room_type") or "",
                        "check_out_date": data.get("check_out_date") or args.get("check_out_date") or "",
                        "check_out_time": pickup,
                        "duration_minutes": duration,
                        "preferred_staff": data.get("preferred_staff") or args.get("preferred_staff") or "",
                    })
            else:
                for slot in data.get("available_slots") or []:
                    new_slots.append({
                        "service_type": service_type,
                        "verified_turn": state.turn_counter,
                        "date": data.get("booking_date") or args.get("date"),
                        "time": slot,
                        "room_type": data.get("room_type") or args.get("room_type") or "",
                        "check_out_date": data.get("check_out_date") or args.get("check_out_date") or "",
                        "check_out_time": data.get("check_out_time") or args.get("check_out_time") or "",
                        "duration_minutes": data.get("service_duration_minutes") or args.get("duration_minutes"),
                        "preferred_staff": data.get("preferred_staff") or args.get("preferred_staff") or "",
                    })
        elif tool_name == "check_availability_range":
            days = result.get("days") or (result.get("data") or {}).get("days") or []
            for day in days:
                for slot in day.get("available_slots") or []:
                    new_slots.append({
                        "service_type": service_type,
                        "verified_turn": state.turn_counter,
                        "date": day.get("date"),
                        "time": slot,
                        "room_type": args.get("room_type") or "",
                        "check_out_date": "",
                        "check_out_time": "",
                        "duration_minutes": args.get("duration_minutes"),
                        "preferred_staff": args.get("preferred_staff") or "",
                    })
        if new_slots:
            keys = {
                (
                    slot["service_type"], slot["date"], str(slot["time"]),
                    str(slot["room_type"]), str(slot.get("check_out_date") or ""),
                    str(slot.get("check_out_time") or ""),
                    str(slot.get("duration_minutes") or ""),
                    str(slot.get("preferred_staff") or ""),
                )
                for slot in new_slots
            }
            state.verified_availability_slots = [
                slot for slot in state.verified_availability_slots
                if (
                    str(slot.get("service_type")), str(slot.get("date")), str(slot.get("time")),
                    str(slot.get("room_type") or ""), str(slot.get("check_out_date") or ""),
                    str(slot.get("check_out_time") or ""), str(slot.get("duration_minutes") or ""),
                    str(slot.get("preferred_staff") or ""),
                ) not in keys
            ] + new_slots

    @classmethod
    def _cache_pending_booking_confirmation(cls, state, tool_name: str, result: dict, args: dict) -> None:
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
                    "preview_turn": state.turn_counter,
                }
                if tool_name == "reschedule_booking":
                    preview_args = {
                        **args,
                        "booking_id": data.get("booking_id"),
                        "service_type": data.get("service_type") or args.get("service_type"),
                    }
                    state.pending_booking_confirmation["change_signature"] = cls._mutation_signature(
                        tool_name, preview_args
                    )
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
                if tool_name == "reschedule_booking":
                    state.pending_booking_confirmation["change_signature"] = cls._mutation_signature(
                        tool_name, {**args, "booking_id": booking_id_int}
                    )

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
        # Empty is authoritative too. Retaining an older non-empty list after
        # a fresh successful empty lookup lets a later flow redeem a stale or
        # now-ineligible coupon.
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
            return "success" if result else "not_found"
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
    def _update_agent_evidence(
        cls, state, tool_name: str, result, args: dict | None = None
    ) -> None:
        """Record observed facts, never private model reasoning or guesses."""
        status = cls._tool_result_status(result)
        evidence = {
            "turn": state.turn_counter,
            "tool": tool_name,
            "status": status,
            # Evidence without its input scope is not reusable evidence.  A
            # grooming catalogue cannot support a later boarding answer, and a
            # food-policy retrieval cannot support cancellation terms.
            "args": cls._compact_evidence_result(args or {}, max_chars=1200),
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
            if tool_name in {"retrieve_policy", "get_booking_service_options"} and status == "not_found":
                state.verified_facts.pop(fact_name, None)
            else:
                state.verified_facts[fact_name] = evidence
        elif fact_name and status == "error" and tool_name in {
            "retrieve_policy", "get_booking_service_options"
        }:
            state.verified_facts.pop(fact_name, None)

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
        "register_loyalty_member": "MEMBER",
        "send_booking_confirmation": "BOOKING_DOCUMENT",
    }

    _LOYALTY_TOOL_NAMES = {
        "get_loyalty_balance",
        "check_coupon_eligibility",
        "register_loyalty_member",
        "redeem_reward",
    }

    @classmethod
    def _cache_loyalty_offer_presented(
        cls, state, response_content: str, trace: list[dict]
    ) -> None:
        """Mark an offer only when the customer-facing reply actually shows it.

        A successful loyalty lookup is internal evidence, not proof that the
        customer saw an offer. The old tool-call-time marker let a same-turn
        lookup silently satisfy the next-turn booking gate even when the model
        never mentioned loyalty or coupons in its reply.
        """
        if not re.search(
            r"\b(?:loyalty|voucher|coupon|points?|member(?:ship)?)\b|"
            r"积分|点数|优惠券|礼券|会员|"
            r"\b(?:baucar|kupon|ganjaran|keahlian)\b",
            str(response_content or ""),
            re.IGNORECASE,
        ):
            return

        for item in trace:
            if item.get("tool") not in cls._LOYALTY_TOOL_NAMES:
                continue
            try:
                result = json.loads(item.get("result") or "{}")
            except (TypeError, ValueError):
                continue
            if cls._tool_result_status(result) in {"success", "confirmation_required"}:
                state.loyalty_offer_shown_turn = state.turn_counter
                return

    @classmethod
    def _cache_register_preview(cls, state, tool_name: str, result: dict, args: dict) -> None:
        if tool_name == "register_loyalty_member" and isinstance(result, dict) and result.get("status") == "confirmation_required":
            executable_args = {**args, "confirmed": True}
            state.pending_actions[tool_name] = {
                "signature": cls._mutation_signature(tool_name, executable_args),
                "args": cls._compact_evidence_result(executable_args, max_chars=1200),
                "preview_turn": state.turn_counter,
                "scenario": state.active_scenario,
            }

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
                "result": _sanitize_model_value(result),
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
        if (
            confirmed
            and confirmed == state.active_scenario
            and isinstance(result, dict)
            and result.get("status") == "success"
        ):
            state.active_scenario = None
            state.current_step = None
            state.service_type = None
            state.objective = None
            state.offered_options = []
            state.offered_add_on_options = []
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
        options = (
            state.offered_add_on_options
            if self._ADD_ON_REFERENCE_RE.search(str(user_message or ""))
            else state.offered_options
        )
        if not options:
            return None
        idx = self._detect_ordinal_index(user_message)
        if idx is None or idx < 1 or idx > len(options):
            return None
        return options[idx - 1]

    _BATCH_DEPENDENCIES = {
        "get_last_completed_booking": {"find_pet_by_name", "get_pets"},
        "check_availability": {
            "resolve_datetime", "get_last_completed_booking", "get_booking_service_options"
        },
        "check_availability_range": {
            "resolve_datetime", "get_last_completed_booking", "get_booking_service_options"
        },
        "create_pet": {"create_customer"},
        "get_booking_service_options": {
            "create_pet", "find_pet_by_name", "get_pets", "get_last_completed_booking"
        },
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
                r"|请问|哪一|什么时候|几点|可以告诉|请提供|请确认|请回复"
                # "boleh" alone is a plain modal verb ("can/may") used in
                # ordinary statements ("saya boleh bantu"), not a question
                # marker — only "bolehkah" (the -kah interrogative particle)
                # reliably signals a question the way the other words here do.
                r"|\b(?:apa|bila|di\s*mana|siapa|bolehkah|sila\s+(?:beritahu|sahkan|berikan))\b",
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
    def _trace_has_successful_availability(cls, trace: list[dict]) -> bool:
        """Only a successful current-turn read can support offered times."""
        for item in trace:
            if item.get("tool") not in {"check_availability", "check_availability_range"}:
                continue
            raw_result = item.get("result")
            try:
                result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(result, dict):
                continue
            if item.get("tool") == "check_availability_range" and isinstance(
                result.get("days"), list
            ):
                return True
            if cls._tool_result_status(result) == "success":
                return True
        return False

    @classmethod
    def _successful_trace_tools(cls, trace: list[dict]) -> set[str]:
        """Tool names with an actually successful result, not merely a call."""
        successful: set[str] = set()
        for item in trace:
            tool_name = item.get("tool")
            if not tool_name:
                continue
            raw_result = item.get("result")
            try:
                result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if cls._tool_result_status(result) == "success":
                successful.add(str(tool_name))
        return successful

    @classmethod
    def _service_options_evidence_matches(cls, state, user_message: str) -> bool:
        return service_options_evidence_matches(
            state, user_message, cls._explicit_service_type
        )

    @staticmethod
    def _policy_evidence_matches(state, user_message: str) -> bool:
        return policy_evidence_matches(state, user_message)

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
        successful_tools = cls._successful_trace_tools(trace)
        scoped_service_options = cls._service_options_evidence_matches(state, user_message)
        scoped_policy_knowledge = cls._policy_evidence_matches(state, user_message)
        action_claim = bool(
            re.search(
                r"\b(?:booked|booking confirmed|cancelled|canceled|rescheduled|redeemed)\b"
                r"|预约.{0,8}(?:成功|确认)|已经.{0,8}(?:取消|改期|预约)|兑换.{0,8}(?:成功|提交)"
                r"|tempahan.{0,8}(?:berjaya|disahkan)"
                r"|telah.{0,8}(?:dibatalkan|dijadualkan\s+semula|ditempah)"
                r"|penebusan.{0,8}(?:berjaya|dihantar)",
                answer,
            )
        )
        if action_claim and not cls._trace_has_successful_mutation(trace):
            return True

        delivery_claim = bool(re.search(
            r"(?:confirmation|document|slip|pdf).{0,30}\b(?:sent|resent|delivered)\b"
            r"|\b(?:sent|resent|delivered)\b.{0,30}(?:confirmation|document|slip|pdf)"
            r"|(?:确认单|确认文件|文件|PDF).{0,12}(?:已发送|重发|送达)"
            r"|(?:pengesahan|dokumen|slip|pdf).{0,30}(?:dihantar(?:\s+semula)?)"
            r"|dihantar(?:\s+semula)?.{0,30}(?:pengesahan|dokumen|slip|pdf)",
            answer,
            re.IGNORECASE,
        ))
        if delivery_claim and not cls._trace_has_successful_document_delivery(trace):
            # A failed/unknown external send must not be repeated merely to
            # repair prose. The final-response guard below replaces the false
            # success statement with an honest delivery failure instead.
            return not cls._trace_has_document_delivery_attempt(trace)

        # Confirmed live: get_loyalty_balance only returns points_balance/tier
        # — there is no generic points-to-RM conversion rate anywhere in this
        # data model, discounts only exist per real coupon (coupon.points_
        # required / "discount_value (RM)"). The model still fabricated
        # "1 point = RM1" and a specific deductible amount from the balance
        # number alone, having called get_loyalty_balance but never
        # check_coupon_eligibility (the tool that actually returns real
        # coupons). Checked here (before the customer-input early return
        # below) because this exact response also ends in a follow-up
        # question ("Would you like to redeem...?"), which would otherwise
        # make _response_requests_customer_input short-circuit the whole
        # function before ever reaching the loyalty_intent check further
        # down — a false claim immediately followed by a question must still
        # be caught.
        coupon_value_claim = bool(re.search(
            r"\d+\s*points?\s*=\s*rm|point.{0,15}=.{0,10}rm\s?\d|"
            r"redeem.{0,30}(?:full\s+amount|rm\s?\d)|deduct.{0,20}(?:up\s+to\s+)?rm\s?\d|"
            r"cover.{0,20}(?:the\s+)?(?:entire|full).{0,20}cost"
            r"|积分.{0,10}(?:等于|=).{0,10}(?:rm|令吉)|抵扣.{0,10}(?:rm|令吉)\s?\d"
            r"|mata.{0,10}=.{0,10}rm|tolak.{0,10}rm\s?\d",
            answer,
            re.IGNORECASE,
        ))
        if coupon_value_claim and "check_coupon_eligibility" not in successful_tools:
            return True

        # Same bypass shape as coupon_value_claim above, for prices instead
        # of coupon math: the prompt's own "never invent... prices" rule has
        # no enforcement behind it. Deliberately conservative — requires ANY
        # tool that could legitimately have produced a real RM figure to be
        # completely absent from the trace, rather than tying the claim to
        # one specific tool the way coupon_value_claim does, since a price
        # can legitimately come from several different sources (a live
        # catalogue quote, a just-created booking's real total, an existing
        # booking/payment being read back, a policy document mentioning a
        # fee) — and, same as catalogue_intent/policy_intent/etc. further
        # down, a fact already verified and cached from an earlier turn
        # counts too; re-quoting an already-verified RM50 must not force a
        # redundant re-call. Only fires when NEITHER this turn's trace NOR
        # any cached fact could account for it — the response naming a
        # concrete RM figure with nothing anywhere to back it means Grade A
        # confidence it's invented, at the cost of not catching every
        # possible price hallucination.
        price_value_claim = bool(re.search(r"\brm\s?\d", answer, re.IGNORECASE))
        price_evidence_tools = {
            "get_booking_service_options", "create_booking", "cancel_booking",
            "reschedule_booking", "get_latest_booking", "get_last_completed_booking",
            "get_booking_by_id", "get_payment_history", "check_availability",
            "check_availability_range", "retrieve_policy",
        }
        price_evidence_facts = {
            "latest_booking", "resolved_booking",
            "payment_history", "availability", "availability_range",
        }
        if price_value_claim and not (
            successful_tools & price_evidence_tools
            or scoped_service_options
            or scoped_policy_knowledge
            or any((state.verified_facts or {}).get(key) for key in price_evidence_facts)
        ):
            return True

        # Same bypass shape again, for slot/availability claims — confirmed
        # live via this exact function: "10 AM is available." with no
        # check_availability call anywhere was already caught by
        # availability_intent further down, but only when the response had
        # no trailing question; appending "Would you like to book it?" made
        # _response_requests_customer_input's early return skip past
        # availability_intent entirely and silently accept the same
        # fabricated slot.
        availability_value_claim = bool(re.search(
            r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s+is\s+available|"
            r"available\s+(?:at|on)\s+\d|"
            r"\bslots?\s+(?:is|are)\s+available\b|"
            r"有空位|有档期|时段.{0,5}有空",
            answer,
            re.IGNORECASE,
        ))
        if availability_value_claim and not cls._trace_has_successful_availability(trace):
            return True

        # HEALTH BOUNDARY in the system prompt says "Only after success may
        # the updated status be treated as recorded" — this had zero
        # enforcement behind it at all (unlike action_claim above, which
        # covers booking/cancel/reschedule/redeem specifically and doesn't
        # include vaccination wording). Confirmed live: with an empty trace,
        # "I have recorded your vaccination as updated!" passed straight
        # through.
        vaccination_claim = bool(re.search(
            r"vaccinat(?:ed|ion).{0,20}(?:recorded|updated|verified|confirmed|on\s+file)|"
            r"(?:recorded|updated|noted).{0,20}vaccinat(?:ed|ion)|"
            r"疫苗.{0,10}(?:已更新|已记录|已确认)",
            answer,
            re.IGNORECASE,
        ))
        if vaccination_claim and "update_pet_vaccination" not in successful_tools:
            return True

        # Same bypass shape, for booking-status lookups: "what's the status
        # of my booking?" answered with a specific status ("Your booking is
        # confirmed for tomorrow") never matches action_claim above — "is
        # confirmed" isn't the adjacent "booking confirmed" phrase, because
        # this is a status readback, not a just-performed action — and,
        # positioned after the early return the same way availability/coupon/
        # price used to be, would let the model report a fabricated status
        # the moment the reply also asks a follow-up question.
        booking_status_claim = bool(re.search(
            r"\b(?:your|the)\s+booking\s+(?:is|status\s+is|has\s+been)\s*(?:currently\s+)?"
            r"(?:confirmed|cancelled|canceled|rescheduled|completed|pending|paid|no[- ]show)\b|"
            r"\bbooking\s+status\s*(?:is|:)\s*\w+|"
            r"您的预约(?:状态)?(?:是|为|已)(?:确认|取消|改期|完成|待处理)|"
            r"预约状态[:：]\s*\S+|"
            r"status\s+tempahan\s+(?:anda\s+)?(?:ialah|adalah)\s*\w+",
            answer,
            re.IGNORECASE,
        ))
        if booking_status_claim and not (
            successful_tools & {
                "get_latest_booking", "get_booking_by_id",
                "cancel_booking", "reschedule_booking", "create_booking",
            }
            or any(
                (state.verified_facts or {}).get(key)
                for key in ("latest_booking", "resolved_booking", "created_booking")
            )
        ):
            return True

        # Same bypass shape, for policy answers: a definitive allowed/
        # required/prohibited-style answer to a policy question, with no
        # retrieve_policy evidence anywhere, is as fabricatable as a price or
        # availability claim. Deliberately narrower than the price/
        # availability checks — no bare "yes"/"no" (far too broad on its
        # own) — and gated on the customer's own message actually asking
        # about policy, so an unrelated "not allowed" elsewhere in a reply
        # never triggers this.
        policy_question = bool(re.search(r"\bpolicy\b|政策|\bpolisi\b", text))
        policy_claim = policy_question and bool(re.search(
            r"\ballowed\b|\bnot\s+allowed\b|\brequired\b|\bmandatory\b|\bprohibited\b|"
            r"\bpolicy\s+(?:is|requires|allows)\b|"
            r"必须|不允许|允许|禁止|规定是|政策(?:是|规定)|"
            r"\bmesti\b|\bdibenarkan\b|\btidak\s+dibenarkan\b|\bpolisi\s+(?:ialah|memerlukan)\b",
            answer,
            re.IGNORECASE,
        ))
        if policy_claim and not (
            scoped_policy_knowledge
            or "retrieve_policy" in successful_tools
        ):
            return True

        confirmation_document_intent = bool(re.search(
            r"\b(?:booking\s+confirmation|confirmation\s+(?:slip|document|pdf)|my\s+confirmation)\b"
            r"|\b(?:send|resend|where).{0,24}(?:confirmation|slip|pdf)\b"
            r"|预约确认单|确认单|确认文件"
            r"|pengesahan\s+tempahan|slip\s+pengesahan|dokumen\s+pengesahan"
            r"|(?:hantar(?:kan)?|di\s*mana).{0,24}pengesahan",
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
                r"预约确认单|确认单|确认文件|推荐|建议|取消|改期|优惠券|积分|付款|政策"
                r"|\b(?:tempah(?:an)?|kekosongan|harga|kos|pakej|bilik|penginapan|"
                r"tambahan|cadang(?:kan)?|batal(?:kan)?|tukar\s+tarikh|jadual\s+semula|"
                r"baucar|kupon|mata\s+ganjaran|bayar(?:an)?|polisi)\b",
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
            r"\b(?:availability|available|slot)\b|空位|时段|\bkekosongan\b",
            text,
        ))
        if availability_intent and not cls._trace_has_successful_availability(trace):
            return True

        catalogue_intent = bool(re.search(
            r"\b(?:price|cost|package|room|grooming|daycare|boarding|add-?on|recommend|suggest)\b|"
            r"价格|多少钱|配套|房型|美容|日托|寄宿|附加服务|推荐|建议|"
            r"\b(?:harga|kos|pakej|bilik|penginapan|tambahan|cadang(?:kan)?)\b",
            text,
        ))
        if catalogue_intent and not (
            scoped_service_options
            or scoped_policy_knowledge
            or successful_tools & {"get_booking_service_options", "retrieve_policy"}
        ):
            return True

        policy_intent = bool(re.search(r"\bpolicy\b|政策|\bpolisi\b", text))
        if policy_intent and not (
            scoped_policy_knowledge or "retrieve_policy" in successful_tools
        ):
            return True

        loyalty_intent = bool(re.search(
            r"\b(?:loyalty|points?|voucher|coupon)\b|积分|点数|优惠券|"
            r"\b(?:baucar|kupon|mata\s+ganjaran)\b",
            text,
        ))
        if loyalty_intent and not (
            facts.get("loyalty_balance")
            or facts.get("coupon_eligibility")
            or successful_tools & {
                "get_loyalty_balance", "check_coupon_eligibility",
                "redeem_reward", "register_loyalty_member",
            }
        ):
            return True

        payment_intent = bool(re.search(r"\bpayment\b|付款|\bbayar(?:an)?\b", text))
        if payment_intent and not (
            facts.get("payment_history") or "get_payment_history" in successful_tools
        ):
            return True

        booking_lookup_intent = bool(re.search(
            r"\b(?:booking status|my booking|cancel|reschedule)\b|"
            r"我的预约|预约状态|取消|改期|"
            r"\btempahan\s+saya\b|\bstatus\s+tempahan\b|\bbatal(?:kan)?\b|\btukar\s+tarikh\b",
            text,
        ))
        if booking_lookup_intent and not (
            facts.get("latest_booking")
            or facts.get("resolved_booking")
            or successful_tools & {
                "get_latest_booking", "get_booking_by_id",
                "cancel_booking", "reschedule_booking",
            }
        ):
            return True

        # Reusing the right cached evidence is valid; unrelated tool activity
        # is not. These explicit matches are intentionally about evidence
        # categories, not a prescribed tool sequence.
        if re.search(
            r"\b(?:price|cost|package|room)\b|价格|多少钱|配套|房型|"
            r"\b(?:harga|kos|pakej|bilik)\b",
            text,
        ):
            if scoped_service_options or scoped_policy_knowledge:
                return False
        if re.search(
            r"\b(?:grooming|daycare|boarding|add-?on|recommend|suggest)\b|"
            r"美容|日托|寄宿|附加服务|推荐|建议|"
            r"\b(?:penginapan|tambahan|cadang(?:kan)?)\b",
            text,
        ):
            if scoped_service_options or scoped_policy_knowledge:
                return False
        if re.search(r"\b(?:policy)\b|政策|\bpolisi\b", text) and scoped_policy_knowledge:
            return False
        if re.search(
            r"\b(?:loyalty|points?|voucher|coupon)\b|积分|点数|优惠券|"
            r"\b(?:baucar|kupon|mata\s+ganjaran)\b",
            text,
        ):
            if facts.get("loyalty_balance") or facts.get("coupon_eligibility"):
                return False
        if re.search(r"\bpayment\b|付款|\bbayar(?:an)?\b", text) and facts.get("payment_history"):
            return False
        if re.search(
            r"\b(?:booking status|my booking)\b|我的预约|预约状态|"
            r"\btempahan\s+saya\b|\bstatus\s+tempahan\b",
            text,
        ):
            if facts.get("latest_booking") or facts.get("resolved_booking"):
                return False
        # update_conversation_state is bookkeeping, not evidence for a live
        # business claim. Any other real tool observation is enough to let the
        # model decide naturally from its result.
        return not successful_tools

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
        state.booking_context_status = "available"

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
            and not re.search(r"\b_internal_[A-Za-z0-9_]+\b", line)
            and not dangling_link_line.search(line)
        )
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if cleaned == content.strip():
            return response
        return response.model_copy(update={"content": cleaned})

    @staticmethod
    def _ground_document_delivery_response(response, user_message: str, trace: list[dict]):
        return ground_document_delivery_response(
            response,
            user_message,
            trace,
            trace_has_successful_delivery=(
                PawfectOrchestrator._trace_has_successful_document_delivery
            ),
        )

    @staticmethod
    def _ground_membership_response(response, user_message: str, trace: list[dict], state=None):
        return ground_membership_response(response, user_message, trace, state)

    @staticmethod
    def _ground_booking_preview_response(response, user_message: str, trace: list[dict]):
        return ground_booking_preview_response(response, user_message, trace)

    @staticmethod
    def _ground_unavailable_profile_claims(response, user_message: str, customer: dict):
        """Never let a failed profile read become a verified-empty claim."""
        content = str(response.content or "")
        false_no_pet_claim = bool(re.search(
            r"\b(?:no|don't|do not|doesn't|does not|haven't|have not)\b.{0,35}"
            r"\bpets?\b|没有.{0,8}宠物|未.{0,8}(?:登记|注册).{0,8}宠物",
            content,
            re.IGNORECASE,
        ))
        false_no_booking_claim = bool(re.search(
            r"\b(?:no|don't|do not|doesn't|does not|haven't|have not)\b.{0,40}"
            r"\bbookings?\b|没有.{0,8}预约|查不到.{0,8}预约",
            content,
            re.IGNORECASE,
        ))
        pets_failed = customer.get("pets_context_status") == "unavailable"
        booking_failed = customer.get("booking_context_status") == "unavailable"
        if not ((pets_failed and false_no_pet_claim) or (booking_failed and false_no_booking_claim)):
            return response

        chinese = bool(re.search(r"[\u3400-\u9fff]", user_message or ""))
        truthful = (
            "目前无法读取您的客户、宠物或预约资料，因此我不能判断记录为空。请稍后再试。"
            if chinese else
            "I couldn't read your customer, pet, or booking profile just now, so I can't "
            "truthfully say that no record exists. Please try again shortly."
        )
        return response.model_copy(update={"content": truthful})

    def _apply_tool_result_state(self, state, tool_call: dict, result) -> None:
        """Apply one observed result immediately so dependent calls can use it."""
        tool_name = tool_call["name"]
        args = tool_call.get("args") or {}

        if tool_name == "update_conversation_state" and isinstance(result, dict) and not result.get("error"):
            previous_scenario = state.active_scenario
            requested_scenario = result.get("active_scenario")
            preserve_main_goal_for_side_question = bool(
                previous_scenario == "MAKE_BOOKING"
                and requested_scenario in {"POLICY_QUERY", "MEMBER"}
            )
            if not preserve_main_goal_for_side_question:
                state.active_scenario = requested_scenario
                state.current_step = result.get("current_step")
                if result.get("service_type"):
                    state.service_type = result.get("service_type")
                elif state.active_scenario != "MAKE_BOOKING":
                    state.service_type = None
                if str(state.service_type or "").upper() != "DAYCARE":
                    state.daycare_duration_minutes = None
                    state.verified_facts.pop("daycare_duration_minutes", None)
                if previous_scenario != state.active_scenario:
                    if state.active_scenario == "MAKE_BOOKING":
                        state.booking_flow_started_turn = state.turn_counter
                    elif previous_scenario == "MAKE_BOOKING":
                        state.booking_flow_started_turn = None
                    state.pending_actions = {}
                    state.pending_booking_confirmation = None
                    state.offered_options = []
                    state.offered_add_on_options = []
                    state.missing_information = []
                    state.missing_information_by_tool = {}
                    if (
                        state.active_scenario == "MAKE_BOOKING"
                        and len(state.known_pets) > 1
                        and state.pet_selected_turn != state.turn_counter
                    ):
                        state.pet_id = None
                        state.pet_type = None
                        state.pet_name = None
                        state.pet_size = None
                        state.pet_breed = None
                if previous_scenario == "MAKE_BOOKING" and state.active_scenario != "MAKE_BOOKING":
                    state.preferred_staff = None
                    state.loyalty_decision = None
                    state.loyalty_offer_shown_turn = None
                    state.repeat_booking_template = None
                    state.pending_actions.pop("create_booking", None)
                    state.verified_availability_slots = []
                self._set_objective_from_scenario(state, state.active_scenario)
                state.completion_status = "in_progress" if state.active_scenario else None

        self._cache_resolved_pet(state, tool_name, result, args)
        self._cache_latest_booking(state, tool_name, result)
        self._cache_offered_options(state, tool_name, result)
        self._cache_booking_evidence(state, tool_name, result, args)
        self._cache_repeat_booking_template(state, tool_name, result, args)
        self._cache_pending_booking_confirmation(state, tool_name, result, args)
        self._cache_resolved_date(state, tool_name, result)
        self._cache_known_coupons(state, tool_name, result)
        self._cache_last_mutation(state, tool_name, args, result)
        self._cache_register_preview(state, tool_name, result, args)

        if tool_name == "create_customer" and isinstance(result, dict) and result.get("status") == "success":
            state.customer_id = result.get("data", {}).get("customer_id")
            state.customer_name = result.get("data", {}).get("full_name")
            state.customer_address = result.get("data", {}).get("address")

        if tool_name == "cancel_booking" and isinstance(result, dict):
            self._cache_cancelled_booking(state, result)
            if result.get("status") == "success":
                # A cancelled row is not the customer's latest active booking.
                # Force a fresh lookup next turn so another active booking can
                # become latest instead of exposing stale cancelled context.
                state.latest_booking = None
                state.booking_context_status = "unavailable"
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
            state.loyalty_decision = None
            state.loyalty_offer_shown_turn = None
            state.repeat_booking_template = None
            state.booking_flow_started_turn = None
            state.verified_facts.pop("daycare_duration_minutes", None)
            self._cache_created_booking(state, result)
            payment_id = (result.get("data") or {}).get("payment_id")
            if payment_id is not None:
                state.last_created_payment_id = payment_id
            if len(state.known_pets) > 1:
                state.pet_id = None
                state.pet_type = None
                state.pet_name = None
                state.pet_size = None
                state.pet_breed = None
                state.pet_selected_turn = None
        elif tool_name == "redeem_reward" and isinstance(result, dict) and result.get("status") == "success":
            state.last_created_payment_id = None

        if isinstance(result, dict) and result.get("status") == "success":
            state.pending_actions.pop(tool_name, None)

        self._sync_scenario_from_tool_call(state, tool_name, result)
        self._update_agent_evidence(state, tool_name, result, args)

    def invoke_with_trace(self, company_context: dict, state, user_message: str):
        """Evidence-driven tool loop with flexible planning and full trace."""
        state.turn_counter += 1
        if state.active_scenario == "MAKE_BOOKING" and state.booking_flow_started_turn is None:
            # Safe migration path for an in-flight session created before this
            # field existed: start the authorization boundary now rather than
            # trusting unrelated older history.
            state.booking_flow_started_turn = state.turn_counter
        # A confirmation only authorizes the preview from the immediately
        # preceding customer turn. Drop anything older before routing a bare
        # "yes" so an unrelated later reply cannot execute stale work.
        state.pending_actions = {
            name: pending
            for name, pending in state.pending_actions.items()
            if int((pending or {}).get("preview_turn") or 0) >= state.turn_counter - 1
        }
        pending_booking = state.pending_booking_confirmation
        if (
            isinstance(pending_booking, dict)
            and pending_booking.get("preview_turn") is not None
            and int(pending_booking.get("preview_turn") or 0) < state.turn_counter - 1
        ):
            state.pending_booking_confirmation = None
        if self._is_repeat_booking_request(user_message):
            state.repeat_booking_template = None
            explicit_service = self._explicit_service_type(user_message)
            if explicit_service:
                state.service_type = explicit_service
        try:
            deterministic_datetime = resolve_datetime.invoke({"text": user_message})
        except Exception as exc:
            deterministic_datetime = {"ambiguous": True, "error": str(exc)}
            logging.getLogger(__name__).exception("Deterministic datetime preprocessing failed: %s", exc)
        if isinstance(deterministic_datetime, dict) and not deterministic_datetime.get("ambiguous"):
            state.current_datetime_resolution = deterministic_datetime
            self._cache_resolved_date(state, "resolve_datetime", deterministic_datetime)
            state.verified_facts["current_datetime_resolution"] = {
                "source": "deterministic_preprocessing",
                "turn": state.turn_counter,
                "result": self._compact_evidence_result(deterministic_datetime),
            }
        else:
            state.current_datetime_resolution = None
            state.verified_facts.pop("current_datetime_resolution", None)
        self._capture_explicit_loyalty_decision(state, user_message)
        self._capture_explicit_add_on_decision(state, user_message)
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
        bound_tools = self._tools_for_turn(state, user_message)
        model = self._base_model.bind_tools(bound_tools, strict=True)
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
        forced_repeat_tools: set[str] = set()
        force_repeat_tool_once: str | None = None
        force_pending_tool_once = self._pending_confirmation_tool(state, user_message)
        force_exact_tool_once: tuple[str, dict] | None = None
        retry_booking_after_availability: dict | None = None
        booking_availability_repair_used = False
        force_final_after_duplicate_tool = False
        successful_read_results: dict[str, object] = {}
        successful_read_lock = Lock()
        failed_mutation_results: dict[str, dict] = {}

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

        if (
            customer.get("found")
            and not escalation_saved
            and self._is_staff_handoff_request(user_message)
        ):
            try:
                self._save_escalation_message(
                    company_context.get("company_id"),
                    state.customer_id,
                    user_message,
                    "CUSTOMER_REQUESTED_STAFF_HANDOFF",
                )
                escalation_saved = True
                state.verified_facts["staff_enquiry"] = {
                    "source": "deterministic_staff_handoff",
                    "turn": state.turn_counter,
                    "saved": True,
                }
            except Exception as exc:
                escalation_failed = True
                logging.getLogger(__name__).exception(
                    "Could not persist explicit staff enquiry for customer_id=%s: %s",
                    state.customer_id,
                    exc,
                )

        def apply_and_escalate(tool_call: dict, result) -> None:
            nonlocal escalation_saved, escalation_failed
            if tool_call["name"] in CACHEABLE_READ_TOOL_NAMES:
                read_signature = self._mutation_signature(
                    tool_call["name"], tool_call.get("args") or {}
                )
                # Also captures executor timeouts/worker failures, which are
                # produced outside _run_tool and otherwise could be retried
                # repeatedly in the same customer turn.
                with successful_read_lock:
                    successful_read_results.setdefault(read_signature, result)
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
            if (
                tool_call["name"] in MUTATING_TOOL_NAMES
                and isinstance(result, dict)
                and result.get("status") == "error"
                and result.get("handoff_required")
                and not result.get("_internal_duplicate_mutation_suppressed")
            ):
                failed_mutation_results[tool_call["name"]] = dict(result)
            self._apply_tool_result_state(state, tool_call, result)
            if (
                tool_call["name"] in MUTATING_TOOL_NAMES
                and self._tool_result_status(result) == "success"
            ):
                # A write can make earlier reads stale (for example,
                # create_booking changes get_latest_booking/availability).
                # Preserve deduplication only while no successful side effect
                # has intervened in this customer turn.
                with successful_read_lock:
                    successful_read_results.clear()

        for iteration_index in range(MAX_TOOL_ITERATIONS):
            active_authoritative_tool: str | None = None
            active_authoritative_args: dict | None = None
            if force_final_after_duplicate_tool:
                # A read was repeated after reaching a stable result, or a
                # mutation was repeated after a non-recoverable failure.
                # Remove tool schemas for one pass so the model must converge
                # on a customer response instead of exhausting the loop.
                bound_tools = []
                model = self._base_model
                bound_scenario = "__FORCED_FINAL_AFTER_DUPLICATE_READ__"
                force_final_after_duplicate_tool = False
                repair_used = True
            elif force_pending_tool_once:
                # A standalone affirmative on a later turn is an explicit
                # transition for the exact server-cached preview. Bind only
                # that mutation and require the call; the LLM cannot turn the
                # confirmation into a greeting or choose a different action.
                required_name = force_pending_tool_once
                required_tool = TOOLS_BY_NAME.get(required_name)
                force_pending_tool_once = None
                if required_tool is None:
                    continue
                bound_tools = [required_tool]
                model = self._base_model.bind_tools(
                    bound_tools, tool_choice="required", strict=True
                )
                bound_scenario = "__FORCED_PENDING_CONFIRMATION__"
            elif force_exact_tool_once:
                # Repair a stale cross-turn slot selection with a fresh exact
                # availability read, then retry the identical booking draft.
                # The LLM supplies only the tool call envelope; application
                # code owns every argument copied from the rejected draft.
                required_name, required_args = force_exact_tool_once
                force_exact_tool_once = None
                required_tool = TOOLS_BY_NAME.get(required_name)
                if required_tool is None:
                    continue
                active_authoritative_tool = required_name
                active_authoritative_args = required_args
                bound_tools = [required_tool]
                model = self._base_model.bind_tools(
                    bound_tools, tool_choice="required", strict=True
                )
                bound_scenario = "__FORCED_EXACT_BOOKING_REPAIR__"
            elif force_repeat_tool_once:
                # The repeat-booking path has a small, evidence-defined read
                # chain. Bind only the one missing tool so a general required
                # choice cannot select an overlapping lookup or policy tool.
                required_name = force_repeat_tool_once
                required_tool = TOOLS_BY_NAME.get(required_name)
                force_repeat_tool_once = None
                if required_tool is None:
                    continue
                bound_tools = [required_tool]
                model = self._base_model.bind_tools(
                    bound_tools, tool_choice="required", strict=True
                )
                bound_scenario = "__FORCED_REPEAT_BOOKING_READ__"
            elif force_tool_once:
                # One narrow repair pass for a live-data/action draft that had
                # no evidence. Exclude the bookkeeping-only state tool so this
                # forced call must observe or act on real business data.
                repair_tools = [
                    tool for tool in self._tools_for_turn(state, user_message)
                    if tool.name != "update_conversation_state"
                ]
                if not repair_tools:
                    repair_used = True
                    force_tool_once = False
                    continue
                bound_tools = repair_tools
                model = self._base_model.bind_tools(
                    repair_tools, tool_choice="required", strict=True
                )
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
                bound_tools = self._tools_for_turn(state, user_message)
                model = self._base_model.bind_tools(bound_tools, strict=True)

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
                required_repeat_tool = self._next_repeat_booking_tool(
                    state, user_message, trace
                )
                if (
                    required_repeat_tool
                    and required_repeat_tool not in forced_repeat_tools
                ):
                    # Discard the premature catalogue/confirmation draft and
                    # finish the next concrete evidence step. This is what
                    # prevents "like last time" from stopping at a full menu
                    # when a date period was already supplied.
                    messages.pop()
                    messages.append((
                        "system",
                        f"This is a scoped repeat-booking request. Call only "
                        f"{required_repeat_tool} now. Use the authoritative pet, "
                        "service, repeat template, and deterministic date/period "
                        "from RUNTIME_CONTEXT; do not browse unrelated services or policy.",
                    ))
                    forced_repeat_tools.add(required_repeat_tool)
                    force_repeat_tool_once = required_repeat_tool
                    continue
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
                response = self._ground_direct_datetime_response(
                    response,
                    state,
                    user_message,
                )
                response = self._ground_latest_availability_response(
                    response,
                    user_message,
                    trace,
                    state,
                )
                response = self._ground_daycare_recommendation_response(
                    response,
                    user_message,
                    trace,
                    state,
                )
                response = self._ground_document_delivery_response(
                    response, user_message, trace
                )
                response = self._ground_membership_response(
                    response, user_message, trace, state
                )
                response = self._ground_booking_preview_response(
                    response, user_message, trace
                )
                response = self._ground_unavailable_profile_claims(
                    response, user_message, final_customer
                )
                if is_first_message:
                    response = self._ensure_first_message_greeting(
                        response, final_customer, company_context, user_message
                    )
                else:
                    response = self._strip_redundant_greeting(response, final_customer)
                response = self._strip_internal_links(response)
                self._cache_loyalty_offer_presented(state, response.content, trace)
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
                state.history.append({
                    "role": "human", "content": user_message, "turn": state.turn_counter
                })
                state.history.append({
                    "role": "ai", "content": response.content, "turn": state.turn_counter
                })
                state.history = state.history[-MAX_HISTORY_TURNS * 2 :]
                return response, trace

            tool_calls = response.tool_calls
            sibling_tool_names = frozenset(tc["name"] for tc in tool_calls)
            parallel_safe_tools = CACHEABLE_READ_TOOL_NAMES

            def run_timed(tool_call):
                started_at = time_module.perf_counter()
                queued_at = submitted_at.get(tool_call["id"], batch_started_at)
                queue_wait_ms = round((started_at - queued_at) * 1000, 1)
                cached_failure = failed_mutation_results.get(tool_call["name"])
                if cached_failure is not None:
                    result = {
                        **cached_failure,
                        "_internal_duplicate_mutation_suppressed": True,
                        "message": (
                            "This mutation already returned a non-recoverable operational "
                            "failure during this customer turn. Do not call it again; explain "
                            "the failure and continue without claiming success."
                        ),
                    }
                else:
                    result = self._run_tool(
                        tool_call,
                        state,
                        user_message,
                        sibling_tool_names,
                        successful_read_results,
                        successful_read_lock,
                        (
                            active_authoritative_args
                            if tool_call["name"] == active_authoritative_tool
                            else None
                        ),
                    )
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
            read_signatures = [
                self._mutation_signature(
                    tool_call["name"],
                    {
                        **dict(tool_call.get("args") or {}),
                        **(
                            {"company_id": state.company_id}
                            if tool_call["name"] in COMPANY_SCOPED_TOOL_NAMES
                            else {}
                        ),
                        **(
                            {"customer_id": state.customer_id}
                            if (
                                tool_call["name"] in CUSTOMER_SCOPED_TOOL_NAMES
                                and state.customer_id is not None
                            )
                            else {}
                        ),
                    },
                )
                for tool_call in tool_calls
                if tool_call["name"] in CACHEABLE_READ_TOOL_NAMES
            ]
            has_duplicate_read_call = len(read_signatures) != len(set(read_signatures))
            unsafe_parallel_tools = sorted(
                {tc["name"] for tc in tool_calls if tc["name"] not in parallel_safe_tools}
            )
            run_in_parallel = (
                len(tool_calls) > 1
                and not unsafe_parallel_tools
                and not has_dependency
                and not has_duplicate_read_call
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
                        else (
                            "contains_duplicate_read_calls"
                            if has_duplicate_read_call
                            else f"contains_non_parallel_safe_tools:{','.join(unsafe_parallel_tools)}"
                        )
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
                model_content = json.dumps(
                    _sanitize_model_value(result), ensure_ascii=False, default=str
                )
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
                messages.append(ToolMessage(content=model_content, tool_call_id=tool_call["id"]))

            # A customer naturally selects a slot on the turn after it was
            # shown. The old guard required availability evidence from this
            # same turn, rejected that normal selection, and let the model
            # loop through catalogue/create_booking again. Recheck the exact
            # complete constraints once, then retry the exact original draft.
            if not booking_availability_repair_used:
                stale_booking_record = next(
                    (
                        record for record in records_by_id.values()
                        if record["tool_call"]["name"] == "create_booking"
                        and isinstance(record["result"], dict)
                        and record["result"].get("error") == "UNVERIFIED_AVAILABILITY_SLOT"
                        and isinstance(record["result"].get("_internal_required_args"), dict)
                        and isinstance(record["result"].get("_internal_retry_args"), dict)
                    ),
                    None,
                )
                if stale_booking_record is not None:
                    repair_result = stale_booking_record["result"]
                    retry_booking_after_availability = repair_result["_internal_retry_args"]
                    force_exact_tool_once = (
                        "check_availability", repair_result["_internal_required_args"]
                    )
                    booking_availability_repair_used = True
                    messages.append((
                        "system",
                        "The customer selected a previously offered slot. Recheck that exact "
                        "slot now with every service/date/duration/staff constraint before "
                        "retrying the unchanged booking preview.",
                    ))
                    continue

            if (
                active_authoritative_tool == "check_availability"
                and retry_booking_after_availability is not None
            ):
                availability_record = next(
                    (
                        record for record in records_by_id.values()
                        if record["tool_call"]["name"] == "check_availability"
                    ),
                    None,
                )
                if (
                    availability_record is not None
                    and self._availability_result_contains_booking(
                        availability_record["result"], retry_booking_after_availability
                    )
                ):
                    force_exact_tool_once = (
                        "create_booking", retry_booking_after_availability
                    )
                    retry_booking_after_availability = None
                    messages.append((
                        "system",
                        "The exact customer-selected slot is freshly available. Retry the "
                        "unchanged booking draft now so it can enter confirmation preview.",
                    ))
                    continue
                retry_booking_after_availability = None

            if any(
                isinstance(record["result"], dict)
                and (
                    record["result"].get("_internal_duplicate_read_suppressed")
                    or record["result"].get("_internal_duplicate_mutation_suppressed")
                )
                for record in records_by_id.values()
            ):
                messages.append((
                    "system",
                    "A tool call already reached a result that must not be repeated during "
                    "this customer turn, and its cached result is present above. Tool access is disabled "
                    "for the next response. Give the customer a natural final answer from "
                    "the available evidence, or ask only for genuinely missing customer "
                    "input. Do not request or claim another tool call.",
                ))
                force_final_after_duplicate_tool = True

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
