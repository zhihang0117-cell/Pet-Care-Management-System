from __future__ import annotations
import json
import logging
import os
import re
import time as time_module
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo

from langchain_core.messages import ToolMessage
from langchain_openai import ChatOpenAI

from app.agent.guardrails import (
    ADD_ON_REFERENCE_RE,
    AFFIRMATIVE_RE,
    CAT_WORDS_RE,
    DOG_WORDS_RE,
    NEGATIVE_RE,
    SCENARIO_CONFIRMING_TOOLS,
    apply_repeat_availability_filters,
    apply_scenario_update,
    availability_args_for_booking,
    availability_result_contains_booking,
    canonicalize_explicit_breed_answer,
    confirmation_intent,
    correct_change_service_type,
    customer_selected_booking_time,
    customer_stated_value,
    infer_document_service_type,
    known_pet_by_id,
    match_named_pet,
    normalize_clock,
    policy_evidence_matches,
    recent_customer_text,
    reject_changed_reschedule,
    reject_mismatched_coupon,
    reject_species_mismatch,
    reject_unconfirmed_breed,
    reject_unconfirmed_height,
    reject_unconfirmed_optional_booking_fields,
    reject_unconfirmed_registration_fields,
    reject_unconfirmed_vaccination_expiry,
    reject_unverified_booking_payload,
    reject_unverified_date,
    reject_unverified_payment_id,
    restore_pending_change_target,
    scope_availability_pet,
    scope_catalogue_or_booking_pet,
    scope_policy_pet,
    scope_repeat_history,
    service_options_evidence_matches,
    set_objective_from_scenario,
    stated_species,
    strip_unconfirmed_pet_name,
    sync_scenario_from_tool_call,
)
from app.agent.tool_loop import (
    BATCH_DEPENDENCIES,
    apply_authoritative_scope,
    apply_datetime_resolution,
    availability_result,
    await_tool_future_result,
    batch_has_duplicate_suppression,
    begin_turn_state,
    booking_availability_repair,
    compact_evidence_result,
    enrich_customer_context,
    finalize_customer_response,
    fresh_available_times,
    invoke_with_turn_read_cache,
    mutation_signature,
    ordered_tool_calls,
    plan_tool_batch,
    record_runtime_identity_evidence,
    redirect_existing_customer_membership,
    response_requests_customer_input,
    restore_confirmed_pending_args,
    reused_failed_mutation,
    successful_trace_tools,
    tool_result_status,
    trace_has_document_delivery_attempt,
    trace_has_successful_availability,
    trace_has_successful_document_delivery,
    trace_has_successful_mutation,
)
from app.context.company import get_company_config
from app.context.runtime_context import (
    customer_context_for_state,
    resolve_identity,
    runtime_message,
)
from app.prompts.system_prompt import SYSTEM_PROMPT
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
from app.tools.text_formatting import contains_chinese
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
        """Instance-config adapter over app.context.runtime_context.resolve_identity.

        Kept as a real (overridable) method rather than calling the module
        function straight from invoke_with_trace: tests monkeypatch this
        exact seam to fake identity resolution without a real repository.
        """
        return resolve_identity(
            company_id,
            state,
            tool_executor=_TOOL_EXECUTOR,
            timeout_seconds=TOOL_CALL_TIMEOUT_SECONDS,
            cache_single_pet=self._cache_single_pet,
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
            runtime_message(
                company_context, customer, state, resolved_selection, available_tools,
                sanitize_model_value=_sanitize_model_value,
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
        messages[1] = runtime_message(
            company_context, customer, state, resolved_selection, available_tools,
            sanitize_model_value=_sanitize_model_value,
        )

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
        if not ADD_ON_REFERENCE_RE.search(previous_reply):
            return
        if confirmation_intent(user_message) != "negative":
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


    @classmethod
    def _pending_scenario_matches(cls, state, tool_name: str, pending: dict) -> bool:
        scenario = pending.get("scenario")
        if scenario is not None:
            return scenario == state.active_scenario
        # Compatibility for previews created by the previous deployment.
        # Still fail closed unless the active scenario owns this exact tool.
        return SCENARIO_CONFIRMING_TOOLS.get(tool_name) == state.active_scenario

    @classmethod
    def _pending_confirmation_tool(cls, state, user_message: str) -> str | None:
        """Return the exact pending write that a standalone ``yes`` confirms.

        Confirmation is application state, not a planning decision for the
        model.  Without this guard the model can answer a standalone ``yes``
        with prose (including a fresh greeting) and never call the pending
        mutation at all.  Prefer the action owned by the active scenario and
        fail closed when more than one unrelated action is pending.
        """
        if confirmation_intent(user_message) != "affirmative":
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
        signature = mutation_signature(tool_name, args)
        pending = state.pending_actions.get(tool_name)
        intent = confirmation_intent(user_message)
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
                renewed_pending = {
                    "signature": signature,
                    "args": compact_evidence_result(args, max_chars=1200),
                    "preview_turn": state.turn_counter,
                    "scenario": state.active_scenario,
                }
                if tool_name == "create_booking":
                    renewed_pending["idempotency_key"] = (
                        pending.get("idempotency_key") or uuid.uuid4().hex
                    )
                state.pending_actions[tool_name] = renewed_pending
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

        new_pending = {
            "signature": signature,
            "args": compact_evidence_result(args, max_chars=1200),
            "preview_turn": state.turn_counter,
            "scenario": state.active_scenario,
        }
        if tool_name == "create_booking":
            new_pending["idempotency_key"] = uuid.uuid4().hex
        state.pending_actions[tool_name] = new_pending
        return {
            "status": "confirmation_required",
            "error_code": "ACTION_PREVIEW_CREATED",
            "data": {"action": tool_name, "preview": state.pending_actions[tool_name]["args"]},
            "message": (
                "No write occurred. Present these exact details to the customer and ask for "
                "a standalone confirmation. Retry with identical arguments only after their immediately following reply."
            ),
        }

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
        if tool_call.get("name") == "create_booking":
            # Never trust a model-authored replay token. Confirmed booking
            # calls receive the server-generated token stored with the exact
            # pending preview.
            args.pop("idempotency_key", None)
        if state is not None:
            args = redirect_existing_customer_membership(
                tool_call,
                args,
                state,
                mutation_signature=mutation_signature,
            )

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
                args, scope_rejection = apply_authoritative_scope(
                    tool_call["name"],
                    args,
                    state,
                    company_scoped_tools=COMPANY_SCOPED_TOOL_NAMES,
                    customer_scoped_tools=CUSTOMER_SCOPED_TOOL_NAMES,
                )
                if scope_rejection:
                    return scope_rejection
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
                    registration_rejection = reject_unconfirmed_registration_fields(
                        state, tool_call["name"], args, user_message
                    )
                    if registration_rejection:
                        return registration_rejection
                args, confirmed_preview_turn = restore_confirmed_pending_args(
                    tool_call["name"],
                    args,
                    state,
                    user_message,
                    confirmation_intent=confirmation_intent,
                    pending_scenario_matches=self._pending_scenario_matches,
                )
                if tool_call["name"] == "get_last_completed_booking":
                    args = scope_repeat_history(
                        args,
                        state,
                        user_message,
                        explicit_service_type=self._explicit_service_type,
                    )
                args, pet_scope_rejection = scope_catalogue_or_booking_pet(
                    tool_call["name"],
                    args,
                    state,
                    user_message,
                    known_pet_by_id=known_pet_by_id,
                    is_repeat_booking_request=self._is_repeat_booking_request,
                    explicit_service_type=self._explicit_service_type,
                )
                if pet_scope_rejection:
                    return pet_scope_rejection
                if tool_call["name"] in {"check_availability", "check_availability_range"}:
                    args, availability_rejection = scope_availability_pet(
                        args,
                        state,
                        known_pet_by_id=known_pet_by_id,
                    )
                    if availability_rejection:
                        return availability_rejection
                if (
                    tool_call["name"] in {"check_availability", "check_availability_range"}
                    and self._is_repeat_booking_request(user_message)
                ):
                    args = apply_repeat_availability_filters(
                        tool_call["name"],
                        args,
                        state,
                        user_message,
                        explicit_service_type=self._explicit_service_type,
                    )
                if tool_call["name"] == "create_booking":
                    optional_field_rejection = reject_unconfirmed_optional_booking_fields(
                        state,
                        args,
                        user_message,
                        detect_ordinal_index=self._detect_ordinal_index,
                        normalize_option_label=self._normalized_option_label,
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
                    vaccination_rejection = reject_unconfirmed_vaccination_expiry(
                        state, args, user_message
                    )
                    if vaccination_rejection:
                        return vaccination_rejection
                args = self._inject_cached_daycare_duration(state, tool_call["name"], args)
                if tool_call["name"] == "send_booking_confirmation":
                    args = infer_document_service_type(args, state)
                args = restore_pending_change_target(tool_call["name"], args, state)
                args = correct_change_service_type(tool_call["name"], args, state)
                changed_reschedule = reject_changed_reschedule(
                    tool_call["name"],
                    args,
                    state,
                    mutation_signature=mutation_signature,
                )
                if changed_reschedule:
                    return changed_reschedule
                if tool_call["name"] == "retrieve_policy":
                    args = scope_policy_pet(args, state)

                args = strip_unconfirmed_pet_name(
                    state, args, tool_call["name"], user_message
                )
                date_rejection = reject_unverified_date(state, tool_call["name"], args)
                if date_rejection:
                    return date_rejection
                species_rejection = reject_species_mismatch(state, args, user_message)
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
                    args = canonicalize_explicit_breed_answer(args, user_message)
                    registration_rejection = reject_unconfirmed_registration_fields(
                        state, tool_call["name"], args, user_message
                    )
                    if registration_rejection:
                        return registration_rejection
                    breed_rejection = reject_unconfirmed_breed(state, args, user_message)
                    if breed_rejection:
                        return breed_rejection
                    height_rejection = reject_unconfirmed_height(state, args, user_message)
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
                    payment_id_rejection = reject_unverified_payment_id(state, args)
                    if payment_id_rejection:
                        return payment_id_rejection
                    coupon_rejection = reject_mismatched_coupon(state, args, user_message)
                    if coupon_rejection:
                        return coupon_rejection
                if tool_call["name"] == "create_booking":
                    booking_rejection = reject_unverified_booking_payload(
                        state, args, confirmed_preview_turn=confirmed_preview_turn
                    )
                    if booking_rejection:
                        if (
                            booking_rejection.get("error") == "UNVERIFIED_AVAILABILITY_SLOT"
                            and customer_selected_booking_time(
                                state, args, user_message,
                                resolve_ordinal_reference=self._resolve_ordinal_reference,
                            )
                        ):
                            booking_rejection = {
                                **booking_rejection,
                                "_internal_required_tool": "check_availability",
                                "_internal_required_args": availability_args_for_booking(args),
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
                    signature = mutation_signature(tool_call["name"], args)
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
            return invoke_with_turn_read_cache(
                tool,
                tool_call["name"],
                args,
                cacheable_read_tools=CACHEABLE_READ_TOOL_NAMES,
                successful_read_results=successful_read_results,
                successful_read_lock=successful_read_lock,
                mutation_signature=mutation_signature,
                tool_result_status=tool_result_status,
            )
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
                known = known_pet_by_id(state, (args or {}).get("pet_id"))
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
                    normalized = normalize_clock(value)
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
                    state.pending_booking_confirmation["change_signature"] = mutation_signature(
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
                    state.pending_booking_confirmation["change_signature"] = mutation_signature(
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


    @classmethod
    def _update_agent_evidence(
        cls, state, tool_name: str, result, args: dict | None = None
    ) -> None:
        """Record observed facts, never private model reasoning or guesses."""
        status = tool_result_status(result)
        evidence = {
            "turn": state.turn_counter,
            "tool": tool_name,
            "status": status,
            # Evidence without its input scope is not reusable evidence.  A
            # grooming catalogue cannot support a later boarding answer, and a
            # food-policy retrieval cannot support cancellation terms.
            "args": compact_evidence_result(args or {}, max_chars=1200),
            "result": compact_evidence_result(result),
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

    # Tools whose successful execution completes one concrete action. Clear
    # scenario routing afterwards so a later customer request is not trapped
    # behind stale tool gating; cached evidence/booking facts remain available,
    # and the model may freely continue with another requested booking.
    # A successful mutation is stronger evidence than the model's separately
    # declared scenario and safely releases scenario routing afterwards.
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
            if tool_result_status(result) in {"success", "confirmation_required"}:
                state.loyalty_offer_shown_turn = state.turn_counter
                return

    @classmethod
    def _cache_register_preview(cls, state, tool_name: str, result: dict, args: dict) -> None:
        if tool_name == "register_loyalty_member" and isinstance(result, dict) and result.get("status") == "confirmation_required":
            executable_args = {**args, "confirmed": True}
            state.pending_actions[tool_name] = {
                "signature": mutation_signature(tool_name, executable_args),
                "args": compact_evidence_result(executable_args, max_chars=1200),
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
                "signature": mutation_signature(tool_name, args),
                "result": _sanitize_model_value(result),
            }
        elif isinstance(result, dict) and result.get("status") not in ("success",):
            state.last_mutation = None

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
            if ADD_ON_REFERENCE_RE.search(str(user_message or ""))
            else state.offered_options
        )
        if not options:
            return None
        idx = self._detect_ordinal_index(user_message)
        if idx is None or idx < 1 or idx > len(options):
            return None
        return options[idx - 1]

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
        successful_tools = successful_trace_tools(trace)
        scoped_service_options = service_options_evidence_matches(
            state, user_message, cls._explicit_service_type
        )
        scoped_policy_knowledge = policy_evidence_matches(
            state, user_message, cls._explicit_service_type
        )
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
        if action_claim and not trace_has_successful_mutation(trace):
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
        if delivery_claim and not trace_has_successful_document_delivery(trace):
            # A failed/unknown external send must not be repeated merely to
            # repair prose. The final-response guard below replaces the false
            # success statement with an honest delivery failure instead.
            return not trace_has_document_delivery_attempt(trace)

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
        # make response_requests_customer_input short-circuit the whole
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
        # response_requests_customer_input's early return skip past
        # availability_intent entirely and silently accept the same
        # fabricated slot.
        #
        # Confirmed live: the ZERO-tool-call turn is not just a positive-slot
        # risk — "Unfortunately, there are no grooming slots available
        # tomorrow." with an empty trace (no check_availability call at all)
        # passed straight through, because every pattern above only matched
        # a positive "X is available" claim. A fabricated "nothing's
        # available" is exactly as false and costs a real booking, so the
        # negative phrasing needs the same backstop, not just the positive
        # one.
        # The negative side is deliberately broad — checked live against ~25
        # realistic human phrasings (casual/formal, EN/ZH, "we're full",
        # "don't have any openings", "all taken", "slots are gone", etc.)
        # rather than one exact template, because a real customer-facing
        # model reply never says "no slots available" verbatim; it paraphrases.
        availability_value_claim = bool(re.search(
            r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s+is\s+available|"
            r"available\s+(?:at|on)\s+\d|"
            # "slots are available", but also the linking-verb-less "slots
            # available" ("we have grooming slots available tomorrow") —
            # confirmed live: that exact phrasing, with zero check_availability
            # calls anywhere in the turn, slipped through because the old
            # pattern required "is"/"are" between the two words.
            r"\bslots?\s+(?:is|are\s+)?available\b|"
            r"\bwe\s+(?:do\s+)?have\s+(?:slots?|availability|openings?|vacanc(?:y|ies)|times?|appointments?|rooms?)\b|"
            r"\byes\b.{0,20}\b(?:slots?|availability|openings?)\b|"
            # negation word + availability noun: "no slots", "don't have any
            # openings", "nothing available", "zero vacancies".
            r"\b(?:no|not\s+any|zero|none|don'?t|doesn'?t|do\s+not|does\s+not)\b"
                r"(?:\s+\w+){0,3}?\s+(?:have\s+)?(?:any\s+)?"
                r"(?:slots?|openings?|vacanc(?:y|ies)|availability|times?|appointments?|rooms?|anything)\s*"
                r"(?:available|open|free|left)?\b|"
            r"\bnothing(?:'s|\s+is)?\s+(?:available|open|free)\b|"
            # availability noun + bad-state word, either order: "slots are
            # all taken", "all our slots are gone", "rooms are unavailable".
            r"\b(?:slots?|times?|appointments?|rooms?|openings?)\b.{0,20}\b(?:all\s+)?(?:taken|gone|full|unavailable)\b|"
            r"\b(?:fully|all|completely)[\s-]?booked\b|"
            r"\bbooked\s+up\b|\bbooked\s+solid\b|\ball\s+booked\b|"
            r"\bcan(?:not|'t)\s+(?:fit|accommodate|squeeze)\s+(?:you|anyone)?\b|"
            r"\bwe'?re\s+(?:fully\s+)?full\b|"
            r"有空位|有档期|时段.{0,5}有空|"
            r"没(?:有)?.{0,10}(?:空位|档期|时段|名额|空档|位置|位子)|(?:已经)?(?:满了|订满|约满|排满)",
            answer,
            re.IGNORECASE,
        ))
        if availability_value_claim and not trace_has_successful_availability(trace):
            return True

        # create_booking has no confirmation_required step of its own — the
        # "please confirm this booking" preview is composed entirely by the
        # model from whatever availability evidence it has, which can be
        # several turns old by the time an add-on/confirm step is reached.
        # Confirmed live: a preview stated "Check-in Time: 16:00", the
        # customer confirmed, and create_booking's own real check rejected
        # 16:00 as unavailable — that time was never backed by a fresh
        # check_availability call this turn, just carried forward from
        # memory. None of the claim patterns above catch this: it's a
        # summary line, not a sentence containing the word "available".
        # Checked before the response_requests_customer_input early return
        # below, same as the other claims — "Please confirm..." itself
        # matches that function's own trigger phrase and would otherwise
        # bypass this entirely.
        if (
            state.active_scenario == "MAKE_BOOKING"
            and "create_booking" not in successful_tools
            and re.search(
                r"please\s+confirm|reply\s+yes|confirm\s+(?:this|the)\s+booking|"
                r"确认(?:预约|这个预约|以上|吗)|回复.{0,4}(?:确认|yes)",
                answer,
                re.IGNORECASE,
            )
        ):
            stated_times = set(re.findall(r"\b\d{1,2}:\d{2}\b", answer))
            if stated_times and not stated_times & fresh_available_times(trace):
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
            or trace_has_successful_document_delivery(trace)
        ):
            # The resend tool can resolve the latest booking itself. Do not
            # accept an unnecessary request for booking details as progress.
            return True

        # If the draft is asking for genuinely missing customer information,
        # that is valid agent progress and should never be forced into a tool.
        if response_requests_customer_input(response_content):
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
            or trace_has_successful_document_delivery(trace)
        ):
            return True

        availability_intent = bool(re.search(
            r"\b(?:availability|available|slot)\b|空位|时段|\bkekosongan\b",
            text,
        ))
        if availability_intent and not trace_has_successful_availability(trace):
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
        chinese = contains_chinese(user_message)
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

    def _apply_tool_result_state(self, state, tool_call: dict, result) -> None:
        """Apply one observed result immediately so dependent calls can use it."""
        tool_name = tool_call["name"]
        args = tool_call.get("args") or {}

        if tool_name == "update_conversation_state":
            apply_scenario_update(state, result)

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

        sync_scenario_from_tool_call(state, tool_name, result)
        self._update_agent_evidence(state, tool_name, result, args)

    def invoke_with_trace(self, company_context: dict, state, user_message: str):
        """Evidence-driven tool loop with flexible planning and full trace."""
        begin_turn_state(
            state,
            user_message,
            is_repeat_booking_request=self._is_repeat_booking_request,
            explicit_service_type=self._explicit_service_type,
        )
        try:
            deterministic_datetime = resolve_datetime.invoke({"text": user_message})
        except Exception as exc:
            deterministic_datetime = {"ambiguous": True, "error": str(exc)}
            logging.getLogger(__name__).exception("Deterministic datetime preprocessing failed: %s", exc)
        apply_datetime_resolution(
            state,
            deterministic_datetime,
            cache_resolved_date=self._cache_resolved_date,
            compact_evidence_result=compact_evidence_result,
        )
        self._capture_explicit_loyalty_decision(state, user_message)
        self._capture_explicit_add_on_decision(state, user_message)
        self._capture_explicit_daycare_duration(state, user_message)
        is_first_message = not state.history
        customer = self._resolve_identity(company_context["company_id"], state)
        match_named_pet(state, user_message)
        customer = enrich_customer_context(customer, state)
        record_runtime_identity_evidence(customer, state)
        if state.active_scenario and not state.objective:
            set_objective_from_scenario(state, state.active_scenario)

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
                read_signature = mutation_signature(
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
                and tool_result_status(result) == "success"
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

                response = finalize_customer_response(
                    self,
                    response,
                    customer=customer,
                    company_context=company_context,
                    state=state,
                    user_message=user_message,
                    trace=trace,
                    is_first_message=is_first_message,
                    escalation_failed=escalation_failed,
                    max_history_turns=MAX_HISTORY_TURNS,
                    is_staff_handoff_request=self._is_staff_handoff_request,
                    is_repeat_booking_request=self._is_repeat_booking_request,
                    trace_has_successful_document_delivery=trace_has_successful_document_delivery,
                )
                return response, trace

            tool_calls = response.tool_calls
            sibling_tool_names = frozenset(tc["name"] for tc in tool_calls)
            parallel_safe_tools = CACHEABLE_READ_TOOL_NAMES

            def run_timed(tool_call):
                started_at = time_module.perf_counter()
                queued_at = submitted_at.get(tool_call["id"], batch_started_at)
                queue_wait_ms = round((started_at - queued_at) * 1000, 1)
                cached_failure = reused_failed_mutation(
                    tool_call["name"], failed_mutation_results
                )
                if cached_failure is not None:
                    result = cached_failure
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
                return await_tool_future_result(
                    future,
                    tool_call,
                    execution_mode=execution_mode,
                    batch_started_at=batch_started_at,
                    submitted_at=submitted_at,
                    timeout_seconds=TOOL_CALL_TIMEOUT_SECONDS,
                    mutating_tool_names=MUTATING_TOOL_NAMES,
                )

            (
                ordered_calls,
                run_in_parallel,
                execution_mode,
                parallel_block_reason,
            ) = plan_tool_batch(
                tool_calls,
                state,
                ordered_tool_calls=ordered_tool_calls,
                mutation_signature=mutation_signature,
                cacheable_read_tools=parallel_safe_tools,
                company_scoped_tools=COMPANY_SCOPED_TOOL_NAMES,
                customer_scoped_tools=CUSTOMER_SCOPED_TOOL_NAMES,
            )
            batch_started_at = time_module.perf_counter()
            submitted_at: dict[str, float] = {}
            records_by_id: dict[str, dict] = {}
            if run_in_parallel:
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
                repair = booking_availability_repair(records_by_id)
                if repair is not None:
                    required_args, retry_booking_after_availability = repair
                    force_exact_tool_once = (
                        "check_availability", required_args
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
                if availability_result_contains_booking(
                    availability_result(records_by_id),
                    retry_booking_after_availability,
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

            if batch_has_duplicate_suppression(records_by_id):
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
