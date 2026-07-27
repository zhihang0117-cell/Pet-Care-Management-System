"""
Final response generator for Pawfect backend.

Uses the Final Response Prompt (OpenAI) after routing, RAG filtering, and database lookup.
Falls back to rule-based replies when the response LLM is unavailable.
"""

import json
import os
import re

from dotenv import load_dotenv

from llm_api_error import (
    LlmApiError,
    LlmFinalResponseError,
    LlmStageError,
    build_final_response_error,
    build_llm_api_error,
    is_eval_strict_mode,
    log_llm_api_error,
    log_llm_stage_error,
)
from llm_call_logging import log_llm_call
from prompts import FINAL_RESPONSE_PROMPT
from testing_mode import should_reraise_on_error

EXPLICIT_HUMAN_HANDOFF_REPLY = (
    "I'll pass this conversation to our team so someone can assist you."
)

RAG_NO_CONTEXT_HANDOFF_REPLY = (
    "I'm unable to confirm that information from our current service records. "
    "I'll pass this to our team to assist you."
)

_HANDOFF_REPLY_BY_REASON = {
    "EXPLICIT_HUMAN_REQUEST": EXPLICIT_HUMAN_HANDOFF_REPLY,
    "MEDICAL_OUT_OF_SCOPE": EXPLICIT_HUMAN_HANDOFF_REPLY,
    "CUSTOMER_NOT_FOUND": (
        "I couldn't verify those details in our records. "
        "I'll pass this to our team to assist you."
    ),
    "BOOKING_NOT_FOUND": (
        "I couldn't find a matching booking in our records. "
        "I'll pass this to our team for further checking."
    ),
    "PET_NOT_FOUND": (
        "I couldn't verify those details in our records. "
        "I'll pass this to our team to assist you."
    ),
    "LOYALTY_ACCOUNT_NOT_FOUND": (
        "I couldn't find a loyalty account linked to your details. "
        "I'll ask our team to check this for you."
    ),
    "RAG_CONTEXT_NOT_FOUND": RAG_NO_CONTEXT_HANDOFF_REPLY,
    "RAG_RETRIEVAL_ERROR": RAG_NO_CONTEXT_HANDOFF_REPLY,
    "DATABASE_ERROR": (
        "I'm unable to confirm that from our records right now. "
        "I'll pass this to our team to assist you."
    ),
}

_BOOKING_LOOKUP_ACTIONS = frozenset(
    {
        "check_booking_status",
        "check_last_booking",
        "get_latest_booking_by_customer_id",
        "cancel_booking",
        "reschedule_booking",
        "update_booking",
    }
)

_LOYALTY_LOOKUP_ACTIONS = frozenset(
    {
        "check_loyalty_points",
        "check_membership_status",
        "check_loyalty_account",
        "redeem_reward",
    }
)

_PET_LOOKUP_ACTIONS = frozenset({"get_pet_by_customer_and_name", "get_pets_by_customer_id"})

_CUSTOMER_LOOKUP_ACTIONS = frozenset({"check_customer_by_phone", "create_customer", "update_customer"})

_RAG_ROUTE_NAMES = frozenset({"CALL_KNOWLEDGE_RAG", "CALL_RAG_THEN_ASK_MISSING_INFO", "CALL_RAG_AND_DATABASE"})


def handoff_reply_for_reason(handoff_reason: str) -> str:
    reason = str(handoff_reason or "").strip()
    return _HANDOFF_REPLY_BY_REASON.get(reason, EXPLICIT_HUMAN_HANDOFF_REPLY)


def database_handoff_reason(database_result: dict | None, scenario_intent: str = "") -> str:
    result = database_result or {}
    action = str(result.get("action") or "").strip()
    status = str(result.get("status") or "").strip()
    data_found = result.get("data_found")
    if status == "error":
        return "DATABASE_ERROR"
    if data_found is False or status == "not_found":
        if action in _BOOKING_LOOKUP_ACTIONS or scenario_intent in {
            "VIEW_BOOKING_STATUS",
            "REPEAT_LAST_BOOKING",
            "CANCEL_BOOKING",
            "RESCHEDULE_BOOKING",
        }:
            return "BOOKING_NOT_FOUND"
        if action in _LOYALTY_LOOKUP_ACTIONS or scenario_intent in {
            "CHECK_LOYALTY_POINTS",
            "CHECK_MEMBERSHIP_STATUS",
            "LOYALTY_ACCOUNT_INQUIRY",
            "REDEEM_REWARD",
        }:
            return "LOYALTY_ACCOUNT_NOT_FOUND"
        if action in _PET_LOOKUP_ACTIONS:
            return "PET_NOT_FOUND"
        if action in _CUSTOMER_LOOKUP_ACTIONS:
            return "CUSTOMER_NOT_FOUND"
    return ""


def should_handoff_for_database(database_result: dict | None, scenario_intent: str = "") -> bool:
    scenario = str(scenario_intent or "").strip()
    if scenario in {"CUSTOMER_GREETING", "COLLECT_CUSTOMER_NAME", "MAKE_BOOKING", "CHECK_AVAILABILITY"}:
        return False
    if scenario == "ASK_MISSING_INFO":
        return False
    reason = database_handoff_reason(database_result, scenario_intent=scenario)
    return bool(reason)


def rag_handoff_reason(rag_result: dict | None, route: str = "") -> str:
    route = str(route or "").strip()
    if route not in _RAG_ROUTE_NAMES:
        return ""
    # Supplementary RAG during booking, or policy+DB combined routes: DB/session
    # grounding and missing-info collection may proceed without retrieved context.
    if route in {"CALL_RAG_THEN_ASK_MISSING_INFO", "CALL_RAG_AND_DATABASE"}:
        return ""
    result = rag_result or {}
    if result.get("rag_error"):
        return "RAG_RETRIEVAL_ERROR"
    if not result.get("rag_reliable"):
        return "RAG_CONTEXT_NOT_FOUND"
    return ""


def resolve_turn_handoff(
    *,
    user_message: str,
    intent_json: dict,
    route_result: dict,
    database_result: dict | None,
    rag_result: dict | None,
) -> tuple[bool, str]:
    del user_message
    explicit = str(intent_json.get("handoff_reason") or "").strip()
    if explicit:
        return True, explicit
    db_result = database_result or {}
    if db_result.get("handoff_required"):
        return True, str(db_result.get("handoff_reason") or "DATABASE_ERROR")
    route = str(route_result.get("route") or "").strip()
    if route == "HUMAN_HANDOFF":
        return True, str(route_result.get("reason") or "UNKNOWN")
    scenario = str(intent_json.get("scenario_intent") or "").strip()
    db_reason = database_handoff_reason(database_result, scenario)
    if db_reason and should_handoff_for_database(database_result, scenario):
        return True, db_reason
    rag_reason = rag_handoff_reason(rag_result, route)
    if rag_reason:
        return True, rag_reason
    return False, ""


def enrich_database_result(database_result: dict | None) -> dict:
    result = dict(database_result or {})
    if "success" not in result:
        status = str(result.get("status") or "").strip()
        result["success"] = status in {"success", "not_found", "missing_information"}
    if "data_found" not in result:
        status = str(result.get("status") or "").strip()
        result["data_found"] = status == "success" and bool(result.get("data"))
    if "handoff_required" not in result:
        result["handoff_required"] = False
    if "handoff_reason" not in result:
        result["handoff_reason"] = None
    return result


_MEDICAL_HANDOFF_PATTERNS = [
    r"\bfever\b",
    r"\bmedicine\b",
    r"\bmedication\b",
    r"\btreat(?:ment)?\b",
    r"\bdiagnos",
    r"\bemergency\b",
    r"\bsick\b",
    r"\bill(?:ness)?\b",
    r"\bprescrib",
    r"\bdosage\b",
    r"\bantibiotic",
    r"\bvomit",
    r"\bdiarrh",
    r"\bpain\b",
    r"\binjur",
    r"\bwound\b",
    r"\bbleed",
]


def strip_fragment_opening(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    prefix = re.compile(
        r"^(?:Sure\s*[!.]?\s*😊?\s*|Of course\s*[!.]?\s*😊?\s*|No problem\s*[!.]?\s*😊?\s*|Thanks(?: for clarifying)?\.\s*|Got it\.\s*)+",
        re.I,
    )
    previous = None
    while cleaned != previous:
        previous = cleaned
        cleaned = prefix.sub("", cleaned, count=1).strip()
    return cleaned


def validate_final_reply(
    reply: str,
    plan=None,
    intent_json: dict | None = None,
    session=None,
) -> str:
    del plan, session
    intent_json = intent_json or {}
    text = str(reply or "").strip()
    if not text:
        return text
    for pattern in (
        re.compile(r"would you like to proceed with a booking\?", re.I),
        re.compile(r"would you like to make a booking\?", re.I),
    ):
        if not intent_json.get("offer_booking_transition"):
            text = pattern.sub("", text).strip()
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_REPEATED_GREETING_PREFIX = re.compile(
    r"^\s*(?:hi|hello|hey)"
    r"(?:\s+[A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,2})?"
    r"\s*[,!]\s*(?:😊|👋|🐾)?\s*(?:\n+|\s+)",
    re.IGNORECASE,
)

_GREETING_REPLY_PREFIX = re.compile(r"^\s*(?:hi|hello|hey)\b", re.IGNORECASE)


def add_first_turn_greeting(reply: str, intent_json: dict, session=None) -> str:
    """Welcome the customer once at the start of every new conversation."""
    text = str(reply or "").strip()
    if not text or session is None:
        return text
    if intent_json.get("_session_greeted_before_turn") is not False:
        return text
    if str(intent_json.get("scenario_intent") or "").strip() == "CUSTOMER_GREETING":
        return text

    session.greeted_this_session = True
    if _GREETING_REPLY_PREFIX.match(text):
        return text

    name = str(getattr(session, "customer_name", "") or "").strip()
    if bool(getattr(session, "existing_customer", False)) and name:
        greeting = f"Hi {name}, welcome back to Pawfect! 😊"
    elif bool(getattr(session, "existing_customer", False)):
        greeting = "Hi, welcome back to Pawfect! 😊"
    else:
        greeting = "Hi, welcome to Pawfect! 😊"
    return f"{greeting}\n\n{text}"


def strip_repeated_session_greeting(reply: str, intent_json: dict) -> str:
    """Remove an LLM-added salutation after this session was already greeted."""
    text = str(reply or "").strip()
    if not text:
        return text
    if not intent_json.get("_session_greeted_before_turn"):
        return text
    if str(intent_json.get("scenario_intent") or "").strip() == "CUSTOMER_GREETING":
        return text
    return _REPEATED_GREETING_PREFIX.sub("", text, count=1).strip()


def compose_missing_info_reply(next_question: str, *, scenario: str = "", acknowledgement_style: str = "") -> str:
    del scenario, acknowledgement_style
    return strip_fragment_opening(str(next_question or "")).strip()


_BOOKING_CTA_PATTERNS = (
    re.compile(r"(?:^|\n)\s*Would you like to make a booking\?\s*$", re.I | re.M),
    re.compile(r"(?:^|\n)\s*Would you like to proceed with a booking\?\s*$", re.I | re.M),
    re.compile(r"(?:^|\n)\s*Would you like to book(?: this service| with us)?\?\s*$", re.I | re.M),
)


def strip_unwanted_booking_cta(reply: str, *, offer_booking_transition: bool = False) -> str:
    text = str(reply or "").strip()
    if not text or offer_booking_transition:
        return text
    updated = text
    for pattern in _BOOKING_CTA_PATTERNS:
        updated = pattern.sub("", updated)
    return re.sub(r"\n{3,}", "\n\n", updated).strip()


def apply_response_flags(
    intent_json: dict,
    session=None,
    user_message: str = "",
    route_result: dict | None = None,
) -> dict:
    del session, user_message, route_result
    intent_json["user_has_explicit_booking_intent"] = bool(
        intent_json.get("booking_supporting_info_needed") or intent_json.get("booking_creation_flow")
    )
    intent_json["offer_booking_transition"] = False
    return intent_json


def finalize_customer_reply(
    reply: str,
    intent_json: dict,
    session=None,
    user_message: str = "",
    route_result: dict | None = None,
) -> str:
    del user_message, route_result
    apply_response_flags(intent_json)
    text = strip_repeated_session_greeting(reply, intent_json)
    text = strip_unwanted_booking_cta(text, offer_booking_transition=False)
    text = add_first_turn_greeting(text, intent_json, session=session)
    return validate_final_reply(text, intent_json=intent_json)


def _normalize_whatsapp_formatting(reply: str) -> str:
    """Ensure bullets and sections use real line breaks for WhatsApp display."""
    text = (reply or "").strip()
    if not text:
        return text

    text = re.sub(r"(?<!\n)\s*•\s*", "\n• ", text)
    text = re.sub(r"([:.!?😊])\s*\n•", r"\1\n\n•", text)
    text = re.sub(
        r"(•[^\n]+)\s+((?:Can you|Could you|Would you|May I)\b[^.?\n]*)",
        r"\1\n\n\2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\.\s+((?:Can you|Could you|Would you|May I)\b[^.?\n]*)",
        r".\n\n\1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\.\s+(Please confirm[^.?\n]*)",
        r".\n\n\1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _chunk_service_type(chunk: dict) -> str:
    metadata = chunk.get("metadata") or {}
    return str(metadata.get("service_type", "")).strip().lower()


def _chunks_matching_service(chunks: list[dict], required_service_type: str | None) -> list[dict]:
    if not required_service_type:
        return chunks
    return [
        chunk
        for chunk in chunks
        if _chunk_service_type(chunk) == required_service_type
    ]


def _is_medical_or_emergency_query(user_message: str) -> bool:
    message = user_message.lower().strip()
    return any(re.search(pattern, message) for pattern in _MEDICAL_HANDOFF_PATTERNS)


def _is_price_query(user_message: str) -> bool:
    message = user_message.lower().strip()
    return any(
        re.search(pattern, message)
        for pattern in [r"\bprice\b", r"\bhow much\b", r"\bcost\b", r"\bfee\b"]
    )


def _is_payment_or_consent_only_context(chunks: list[dict]) -> bool:
    if not chunks:
        return False

    combined = " ".join(chunk.get("text", "") for chunk in chunks).lower()
    payment_consent_signals = (
        "payment term",
        "consent",
        "deposit",
        "refund policy",
        "cancellation fee",
        "terms and conditions",
    )
    package_signals = (
        "basic grooming",
        "full grooming",
        "nano spa",
        "grooming package",
        "grooming service",
        "service information",
    )
    has_payment = any(signal in combined for signal in payment_consent_signals)
    has_package = any(signal in combined for signal in package_signals)
    return has_payment and not has_package


def _is_addon_only_context(chunks: list[dict]) -> bool:
    if not chunks:
        return False

    combined = " ".join(chunk.get("text", "") for chunk in chunks).lower()
    has_addon_signal = any(
        keyword in combined
        for keyword in ["add-on", "add on", "addon", "nail clipping", "dental scaling"]
    )
    has_main_package_signal = any(
        keyword in combined
        for keyword in ["package", "main grooming", "full grooming", "basic grooming service"]
    )
    return has_addon_signal and not has_main_package_signal


def _build_decision_json(
    intent_json: dict,
    route_result: dict,
    required_service_type: str | None = None,
) -> str:
    decision = {
        "intent_json": intent_json,
        "route_result": route_result,
        "required_service_type": required_service_type,
        "offer_booking_transition": bool(intent_json.get("offer_booking_transition")),
        "user_has_explicit_booking_intent": bool(
            intent_json.get("user_has_explicit_booking_intent")
        ),
    }
    return json.dumps(decision, indent=2, ensure_ascii=False)


def _finalize_reply(
    reply: str,
    intent_json: dict,
    session=None,
    user_message: str = "",
    route_result: dict | None = None,
    conv_ctx=None,
) -> str:
    del conv_ctx
    finalized = finalize_customer_reply(
        reply,
        intent_json,
        session=session,
        user_message=user_message,
        route_result=route_result,
    )
    normalized = _normalize_whatsapp_formatting(finalized)
    return validate_final_reply(normalized, intent_json=intent_json, session=session)


def _return_final_response(
    reply: str,
    intent_json: dict,
    *,
    session=None,
    user_message: str = "",
    route_result: dict | None = None,
    provider_used: str,
    model_used: str,
    provider_logged: str,
    base_url: str = "",
    conv_ctx=None,
) -> dict:
    return {
        "reply": _finalize_reply(
            reply,
            intent_json,
            session=session,
            user_message=user_message,
            route_result=route_result,
            conv_ctx=conv_ctx,
        ),
        "final_response_provider_used": provider_used,
        "final_response_model_used": model_used,
        "final_response_provider_logged": provider_logged,
        "final_response_base_url_used": base_url,
    }


def _format_database_result(database_result: dict) -> str:
    if not database_result or database_result.get("status") == "not_applicable":
        return "No database result available."
    return json.dumps(database_result, indent=2, ensure_ascii=False)


def _format_retrieved_context(
    final_context_chunks: list[dict],
    required_service_type: str | None = None,
) -> str:
    """
    Format only final_context_chunks for the Final Response Prompt.

    These chunks are already bad-filtered, service-filtered, and top-ranked.
    """
    matching_chunks = _chunks_matching_service(final_context_chunks, required_service_type)
    chunks = matching_chunks or final_context_chunks

    if not chunks:
        return "No retrieved information available."

    parts: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        text = _normalize_text(chunk.get("text", ""))
        if not text:
            continue

        metadata = chunk.get("metadata") or {}
        service_type = metadata.get("service_type") or "unknown"
        section_title = metadata.get("section_title") or metadata.get("sub_header") or ""
        header = f"[Source {index}"
        if service_type:
            header += f" | service: {service_type}"
        if section_title:
            header += f" | section: {section_title}"
        header += "]"
        parts.append(f"{header}\n{text}")

    return "\n\n".join(parts) if parts else "No retrieved information available."


def _build_greeting_reply(database_result: dict, session=None) -> str:
    """Fixed greeting template that welcomes at most once per session."""
    status = str(database_result.get("status") or "").strip()
    data = database_result.get("data") or {}
    action = str(database_result.get("action") or "").strip()
    already_greeted = bool(getattr(session, "greeted_this_session", False))

    if already_greeted:
        if status == "not_found":
            return "May I have your name so I can continue helping you?"
        if status == "missing_information":
            return "May I have your phone number so I can check your account?"
        return "What can I help you with today?"

    if session is not None:
        session.greeted_this_session = True

    if status == "success" and action == "check_customer_by_phone":
        name = str(data.get("full_name") or data.get("customer_name") or "").strip()
        if name:
            return f"Hi {name}, welcome back to Pawfect! 😊 What can I help you with today?"
        return "Hi, welcome back to Pawfect! 😊 What can I help you with today?"

    if status == "not_found":
        return (
            "Hi, welcome to Pawfect! 😊 "
            "May I have your name first so we can assist you better?"
        )

    if status == "missing_information":
        return (
            "Hi, welcome to Pawfect! 😊 "
            "May I have your phone number so we can check whether you already have an account with us?"
        )

    return "Hi, welcome to Pawfect! 😊 How can I help you today?"


def _build_coupon_eligibility_reply(
    database_result: dict,
    intent_json: dict,
    session=None,
) -> str:
    """Natural, fully grounded coupon answer with an optional booking continuation."""
    if str(database_result.get("status") or "").strip() != "success":
        return "I couldn't check your coupon eligibility right now. I'll get our team to assist you."

    data = dict(database_result.get("data") or {})
    balance = int(data.get("points_balance") or 0)
    eligible = list(data.get("eligible_coupons") or [])
    sections = [f"You currently have {balance} loyalty points 😊"]

    if eligible:
        lines = []
        for coupon in eligible[:5]:
            name = str(coupon.get("reward_name") or coupon.get("reward_type") or "Coupon").strip()
            required = int(coupon.get("points_required") or 0)
            discount = coupon.get("discount_value")
            detail = f" — RM{discount} off" if discount not in (None, "") else ""
            lines.append(f"• {name}: {required} points{detail}")
        sections.append("You have enough points for:\n\n" + "\n".join(lines))
    else:
        next_coupon = data.get("next_coupon") or {}
        if next_coupon:
            name = str(
                next_coupon.get("reward_name") or next_coupon.get("reward_type") or "the next coupon"
            ).strip()
            short = int(next_coupon.get("points_short") or 0)
            required = int(next_coupon.get("points_required") or 0)
            sections.append(
                f"You don't have enough points for a coupon yet. "
                f"{name} requires {required} points, so you need {short} more."
            )
        else:
            sections.append("There aren't any active coupons available for redemption right now.")

    if intent_json.get("coupon_eligibility_with_booking"):
        from booking_flow import build_missing_field_reply

        missing = list(intent_json.get("deferred_booking_missing") or [])
        continuation = build_missing_field_reply(missing, session, intent_json)
        sections.append(
            "I can also continue with your booking."
            + (f"\n\n{continuation}" if continuation else "")
        )

    return "\n\n".join(section for section in sections if section).strip()


def _build_booking_service_options_reply(database_result: dict, session=None) -> str:
    """Present verified service-catalogue choices before booking availability."""
    if str(database_result.get("status") or "").strip() != "success":
        return "I couldn't load the service choices right now. I'll get our team to assist you."
    data = dict(database_result.get("data") or {})
    service_type = str(data.get("service_type") or "").strip().lower()
    options = list(data.get("service_options") or [])
    if not options:
        return f"I couldn't find active {service_type or 'service'} options right now. I'll get our team to assist you."
    displayed_options = options if service_type == "boarding" else options[:6]
    lines = [
        f"{index}. {item.get('service_name')}"
        + (f" — {item.get('price_display')}" if item.get("price_display") else "")
        + (
            f" · up to {item.get('capacity')} pet"
            f"{'s' if int(item.get('capacity') or 0) != 1 else ''}"
            if service_type == "boarding" and item.get("capacity")
            else ""
        )
        for index, item in enumerate(displayed_options, start=1)
    ]
    pet_name = str(getattr(session, "pet_name", "") or "").strip()
    pet_phrase = f" for {pet_name}" if pet_name else ""
    return (
        f"For {service_type}{pet_phrase}, these are the available "
        f"{'room types' if service_type == 'boarding' else 'service options'}:\n\n"
        + "\n".join(lines)
        + "\n\nChoose one and send your preferred date in the same message. "
        "For example: “Option 2, 3 August.”"
    )


def _enforce_service_option_conversation_contract(reply: str, session=None) -> str:
    """Keep LLM wording natural while enforcing the date-before-time workflow."""
    text = str(reply or "").strip()
    text = re.sub(
        r"\b(?:your\s+)?preferred\s+date\s+(?:and|&)\s+(?:your\s+)?(?:preferred\s+)?time\b",
        "your preferred date",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bdate\s+(?:and|&)\s+time\b",
        "date",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"^(?:I can help(?: you)? with that|I'd be happy to help(?: with that)?)[^\n]*\n{2,}",
        "",
        text,
        count=1,
        flags=re.I,
    )
    if re.match(r"^Hi,\s*welcome\s+to\s+Pawfect", text, re.I):
        text = re.sub(
            r"\n{2,}(?:I can help(?: you)? with that|I'd be happy to help(?: with that)?)[^\n]*\n{2,}",
            "\n\n",
            text,
            count=1,
            flags=re.I,
        )
    is_new_customer = session is not None and not bool(
        getattr(session, "existing_customer", False)
    )
    customer_name = str(getattr(session, "customer_name", "") or "").strip()
    if (
        is_new_customer
        and not customer_name
        and not re.search(r"\b(?:your|customer)\s+(?:full\s+)?name\b", text, re.I)
    ):
        text, direct_name_insertions = re.subn(
            r"\bMay I have\s+(?:your\s+)?pet(?:'s)?\s+name\b",
            "May I have your name, your pet's name",
            text,
            count=1,
            flags=re.I,
        )
        if not direct_name_insertions:
            text, direct_name_insertions = re.subn(
                r"\bprovide\s+(?:me\s+with\s+)?(?:your\s+)?pet(?:'s)?\s+name\b",
                "provide your name, your pet's name",
                text,
                count=1,
                flags=re.I,
            )
        if direct_name_insertions:
            customer_name = "__requested__"
        details_intro = list(
            re.finditer(
                r"(?:provide|send|have|need)[^\n]{0,100}\bdetails?\b|\bdetails?\s+to\s+proceed\b",
                text,
                re.I,
            )
        )
        search_start = details_intro[-1].end() if details_intro else len(text)
        first_bullet = re.search(r"(?m)^[•*-]\s+", text[search_start:])
        if first_bullet and not customer_name:
            insert_at = search_start + first_bullet.start()
            text = (
                text[:insert_at]
                + "• Your name\n"
                + text[insert_at:]
            )

    if session is not None and bool(getattr(session, "existing_customer", False)):
        pets = list(getattr(session, "customer_pets", []) or [])
        selected_pet = str(getattr(session, "pet_name", "") or "").strip()
        if len(pets) > 1 and not selected_pet:
            # Replace an open-ended pet-name request with registered choices.
            text = re.sub(
                r"(?:^|\n{2,})[^\n]*(?:provide|tell|share|which|what)[^\n]*"
                r"\bpet(?:'s)?\s+name\b[^\n]*(?=$|\n{2,})",
                "",
                text,
                count=1,
                flags=re.I,
            ).strip()
            names: list[str] = []
            for pet in pets:
                name = str(pet.get("pet_name") or "").strip()
                if not name or name in names:
                    continue
                names.append(name)
            last_pet = str(
                (getattr(session, "last_booking_snapshot", {}) or {}).get("pet_name")
                or ""
            ).strip()
            recommendation = ""
            if last_pet and last_pet.lower() in {name.lower() for name in names}:
                recommendation = (
                    f"I'd recommend {last_pet}, since your latest booking was for {last_pet}. "
                )
            if names:
                if len(names) == 2:
                    pet_choices = f"{names[0]} or {names[1]}"
                else:
                    pet_choices = ", ".join(names[:-1]) + f", or {names[-1]}"
                text += (
                    "\n\n"
                    + recommendation
                    + f"Would you like to make the booking for {pet_choices}? "
                    "You can include the service option and preferred date in the same reply."
                )
    text = re.sub(r"(?m)^([•*-]\s+)your preferred date\b", r"\1Preferred date", text, flags=re.I)
    return text.strip()


def _format_service_type_label(service_type: str) -> str:
    text = str(service_type or "").strip()
    if not text or text.upper() == "UNKNOWN":
        return "your previous service"
    return text.replace("_", " ").title()


def _build_repeat_last_booking_reply(database_result: dict, intent_json: dict | None = None) -> str:
    """Fixed WhatsApp templates for REPEAT_LAST_BOOKING (no LLM)."""
    from booking_flow import format_last_booking_sentence

    intent_json = intent_json or {}
    status = str(database_result.get("status") or "").strip()
    data = database_result.get("data") or {}

    if status == "success" and intent_json.get("last_service_inquiry_only"):
        summary = format_last_booking_sentence(data)
        if summary:
            return f"{summary} Would you like to book the same service again?"
        return "Would you like to book the same service again?"

    if status == "success":
        summary = format_last_booking_sentence(data)
        if summary:
            return f"{summary} What date and time would you like to book it again?"
        return "What date and time would you like to book it again?"

    if status == "not_found":
        return handoff_reply_for_reason("BOOKING_NOT_FOUND")

    if status == "missing_information":
        return (
            "Sure 😊 May I have your phone number so we can check your previous booking?"
        )

    return handoff_reply_for_reason("BOOKING_NOT_FOUND")


def _build_handoff_reply(user_message: str, *, handoff_reason: str = "") -> str:
    reason = str(handoff_reason or "").strip()
    if reason:
        return handoff_reply_for_reason(reason)
    if _is_medical_or_emergency_query(user_message):
        return handoff_reply_for_reason("MEDICAL_OUT_OF_SCOPE")
    return EXPLICIT_HUMAN_HANDOFF_REPLY


def _build_missing_info_reply(intent_json: dict, session=None) -> str:
    missing_information = intent_json.get("missing_information", [])
    scenario_intent = intent_json.get("scenario_intent", "UNKNOWN")

    if (
        scenario_intent == "SERVICE_INFORMATION"
        and intent_json.get("standalone_service_info")
        and missing_information
    ):
        from booking_service_info import build_price_missing_field_reply

        return compose_missing_info_reply(build_price_missing_field_reply(session, list(missing_information)))

    if scenario_intent in {"MAKE_BOOKING", "CHECK_AVAILABILITY"} and missing_information:
        from booking_flow import build_booking_missing_info_reply

        if scenario_intent == "MAKE_BOOKING" or any(
            field in missing_information
            for field in (
                "repeat_or_new_service_choice",
                "service_type",
                "pet_name",
                "preferred_date",
                "preferred_time",
            )
        ):
            return build_booking_missing_info_reply(intent_json, session)

    if scenario_intent == "MAKE_BOOKING":
        lines = [
            "Sure 😊 May I have these details to help with the booking?",
            "• Service type",
            "• Preferred date and time",
            "• Pet type and size/height",
            "• Pet name",
        ]
        if missing_information:
            lines = [
                "Sure 😊 May I have these details to help with the booking?",
                *[f"• {field.replace('_', ' ')}" for field in missing_information],
            ]
        return "\n".join(lines)

    if scenario_intent == "CHECK_AVAILABILITY" and missing_information == ["preferred_time"]:
        return "What time do you prefer?"

    if missing_information:
        return (
            "Sure 😊 May I have a few more details?\n"
            + "\n".join(f"• {field.replace('_', ' ')}" for field in missing_information)
        )

    return "Sure 😊 May I have a few more details so I can help you?"


def _build_rule_based_reply(
    user_message: str,
    intent_json: dict,
    route_result: dict,
    final_context_chunks: list[dict],
    database_result: dict,
    required_service_type: str | None = None,
    session=None,
) -> str:
    route = route_result.get("route", "")
    scenario_intent = intent_json.get("scenario_intent", "UNKNOWN")

    if route == "HUMAN_HANDOFF":
        return _build_handoff_reply(
            user_message,
            handoff_reason=str(intent_json.get("handoff_reason") or route_result.get("reason") or ""),
        )

    if route == "ASK_MISSING_INFO":
        return _build_missing_info_reply(intent_json, session)

    if route in {"CALL_KNOWLEDGE_RAG", "CALL_RAG_THEN_ASK_MISSING_INFO"}:
        from booking_service_info import append_service_info_follow_up, build_mixed_booking_rag_reply

        def _finalize_service_rag_reply(reply) -> str:
            if intent_json.get("booking_interruption"):
                return str(reply or "").strip()
            if route == "CALL_RAG_THEN_ASK_MISSING_INFO" and not intent_json.get("standalone_service_info"):
                return build_mixed_booking_rag_reply(reply, session, intent_json)
            if isinstance(reply, dict):
                return append_service_info_follow_up(reply, session, intent_json, user_message=user_message)
            return append_service_info_follow_up(reply, session, intent_json, user_message=user_message)

        service_key = str(
            required_service_type or intent_json.get("service_type") or ""
        ).strip().lower()
        is_grooming_price_enquiry = service_key == "grooming" and (
            intent_json.get("standalone_service_info") or intent_json.get("price_enquiry_incomplete")
        )

        matching_chunks = _chunks_matching_service(final_context_chunks, required_service_type)
        if required_service_type and not matching_chunks and not is_grooming_price_enquiry:
            if _is_price_query(user_message):
                reply = (
                    f"I don't have the exact {required_service_type} price here yet. "
                    "May I know your pet's type and height/size so our team can confirm it for you?"
                )
            else:
                reply = (
                    f"I don't have the exact {required_service_type} details here yet, "
                    "but I can help check with the team."
                )
            return _finalize_service_rag_reply(reply)

        chunks = matching_chunks or final_context_chunks
        if not chunks:
            from booking_service_info import grooming_package_list_fallback, is_package_list_question

            if is_package_list_question(user_message):
                reply = grooming_package_list_fallback(required_service_type or intent_json.get("service_type"))
            else:
                reply = "I don't have the exact detail here, but I can help check with the team."
            return _finalize_service_rag_reply(reply)

        if required_service_type == "grooming" and _is_payment_or_consent_only_context(chunks):
            reply = (
                "I can help check the grooming package details, but I don't have the "
                "exact package information here."
            )
            return _finalize_service_rag_reply(reply)

        combined_text = "\n\n".join(
            _normalize_text(chunk.get("text", "")) for chunk in chunks if chunk.get("text")
        )
        if not combined_text:
            reply = "I don't have the exact detail here, but I can help check with the team."
            return _finalize_service_rag_reply(reply)

        if is_grooming_price_enquiry:
            from booking_service_info import build_grooming_price_response_plan

            plan = build_grooming_price_response_plan(combined_text, session, intent_json, chunks)
            return _finalize_service_rag_reply(plan)

        if service_key == "grooming" and _is_addon_only_context(chunks):
            reply = (
                "Grooming package price may depend on your pet's size and package.\n"
                f"{combined_text}\n\n"
                "May I know your pet's type and height/size so the exact package price can be confirmed?"
            )
            return _finalize_service_rag_reply(reply)

        if _is_price_query(user_message) and intent_json.get("pet_size", "UNKNOWN") == "UNKNOWN":
            from booking_service_info import get_price_enquiry_context

            ctx = get_price_enquiry_context(session) if session is not None else {}
            if not (ctx.get("pet_height") or ctx.get("pet_size")):
                reply = (
                    f"{combined_text}\n\n"
                    "May I know your pet is a dog or cat, and the height/size?"
                )
            else:
                reply = combined_text
        else:
            reply = combined_text

        return _finalize_service_rag_reply(reply)

    if route == "CALL_RAG_AND_DATABASE":
        chunks = _chunks_matching_service(final_context_chunks[:2], required_service_type)
        chunks = chunks or final_context_chunks[:2]
        chunk_text = "\n\n".join(
            _normalize_text(chunk.get("text", "")) for chunk in chunks if chunk.get("text")
        )
        data = database_result.get("data", {})
        booking_id = data.get("booking_id", "your booking")
        current_status = data.get("current_status", "confirmed")
        next_step = data.get("next_step", "Please let me know if you'd like to continue.")

        if chunk_text:
            return (
                f"I can help with that 😊\n\n"
                f"{chunk_text}\n\n"
                f"Your booking {booking_id} is currently {current_status}.\n"
                f"{next_step}"
            )
        return (
            f"I can help with that 😊\n\n"
            f"Your booking {booking_id} is currently {current_status}.\n"
            f"{next_step}"
        )

    if route == "CALL_DATABASE":
        data = database_result.get("data", {})
        action = str(database_result.get("action") or "").strip()
        status = str(database_result.get("status") or "").strip()

        if action == "create_booking":
            if status == "success" and database_result.get("data_found") and data.get("verified"):
                booking_id = data.get("booking_id", "")
                booking_status = data.get("booking_status", "Pending")
                return (
                    f"Your booking request has been created with status {booking_status}. "
                    f"Reference ID: {booking_id}."
                )
            if database_result.get("handoff_required") or status != "success":
                return handoff_reply_for_reason(str(database_result.get("handoff_reason") or "DATABASE_ERROR"))
            return (
                f"I couldn't create the booking right now: "
                f"please check the details and try again."
            )

        if action == "cancel_booking":
            if status == "success" and data.get("verified"):
                booking_status = data.get("booking_status", "Cancelled")
                booking_id = data.get("booking_id", "")
                return f"Your booking {booking_id} has been cancelled. Status: {booking_status}."
            if database_result.get("handoff_required") or status != "success":
                return handoff_reply_for_reason(str(database_result.get("handoff_reason") or "DATABASE_ERROR"))
            return "I couldn't cancel the booking. I'll pass this to our team to assist you."

        if action == "reschedule_booking":
            if status == "success" and data.get("verified"):
                return (
                    f"Your booking {data.get('booking_id', '')} has been rescheduled to "
                    f"{data.get('booking_date', 'N/A')} at {data.get('booking_time', data.get('check_in_time', 'N/A'))}."
                )
            if database_result.get("handoff_required") or status != "success":
                return handoff_reply_for_reason(str(database_result.get("handoff_reason") or "DATABASE_ERROR"))
            return "I couldn't reschedule the booking. I'll pass this to our team to assist you."

        if action == "redeem_reward":
            if status == "success" and data.get("verified"):
                return (
                    f"Redeemed {data.get('points_redeemed', 0)} points successfully. "
                    f"Your new balance is {data.get('points_balance', 0)} points."
                )
            if database_result.get("handoff_required") or status != "success":
                return handoff_reply_for_reason(str(database_result.get("handoff_reason") or "DATABASE_ERROR"))
            return "I couldn't redeem the points. I'll pass this to our team to assist you."

        if scenario_intent == "VIEW_BOOKING_STATUS":
            if database_result.get("data_found") is False or database_result.get("status") == "not_found":
                return handoff_reply_for_reason("BOOKING_NOT_FOUND")
            return (
                f"Your booking is {data.get('booking_status', 'confirmed')}.\n"
                f"Service: {data.get('service_type', 'N/A')}\n"
                f"Date: {data.get('booking_date', 'N/A')}\n"
                f"Time: {data.get('booking_time', 'N/A')}"
            )

        if scenario_intent == "CHECK_LOYALTY_POINTS":
            if database_result.get("data_found") is False or database_result.get("status") == "not_found":
                return handoff_reply_for_reason("LOYALTY_ACCOUNT_NOT_FOUND")
            return f"You currently have {data.get('loyalty_points', 0)} loyalty points 😊"

        if scenario_intent == "CHECK_AVAILABILITY":
            slots = ", ".join(data.get("available_slots", []))
            return (
                f"Available slots for {data.get('service_type', 'the selected service')}: "
                f"{slots}."
            )

        if scenario_intent == "REDEEM_REWARD":
            return (
                f"You have {data.get('available_points', 0)} points available. "
                "Would you like to proceed with redemption?"
            )

        if scenario_intent == "CHECK_MEMBERSHIP_STATUS":
            if database_result.get("data_found") is False or database_result.get("status") == "not_found":
                return handoff_reply_for_reason("LOYALTY_ACCOUNT_NOT_FOUND")
            return f"Your membership status is {data.get('membership_status', 'unknown')}."

        if scenario_intent == "LOYALTY_ACCOUNT_INQUIRY":
            if database_result.get("data_found") is False or database_result.get("status") == "not_found":
                return handoff_reply_for_reason("LOYALTY_ACCOUNT_NOT_FOUND")
            return (
                f"You have {data.get('loyalty_points', 0)} loyalty points. "
                f"Membership status: {data.get('membership_status', 'unknown')}."
            )

        if scenario_intent == "CUSTOMER_GREETING":
            return _build_greeting_reply(database_result, session)

        if scenario_intent == "REPEAT_LAST_BOOKING":
            return _build_repeat_last_booking_reply(database_result, intent_json)

        if scenario_intent == "CONFIRM_BOOKING":
            from booking_draft import build_booking_confirmed_reply, build_draft_confirmation_reply

            if str(database_result.get("status") or "").strip() == "success":
                reply = build_booking_confirmed_reply(database_result, session)
            elif getattr(session, "draft_booking_payload", None):
                reply = build_draft_confirmation_reply(session)
            else:
                reply = (
                    f"I couldn't confirm the booking: "
                    f"{database_result.get('error') or 'please check your details and try again.'}"
                )
            return reply

        if scenario_intent == "BOOKING_CONFIRMATION_ORPHAN":
            from booking_draft import build_orphan_confirmation_reply

            return build_orphan_confirmation_reply()

        return "I'll check that with our team and get back to you shortly."

    return "How can I help you with Pawfect grooming, daycare, or boarding today?"


def _generate_llm_final_response(
    customer_message: str,
    decision_json: str,
    retrieved_context: str,
    database_result_text: str,
) -> str:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")

    runtime = log_llm_call("final_response")
    model = runtime["model"]
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None

    prompt = FINAL_RESPONSE_PROMPT.format(
        customer_message=customer_message,
        decision_json=decision_json,
        retrieved_context=retrieved_context,
        database_result=database_result_text,
    )

    client = OpenAI(api_key=api_key, base_url=base_url)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": customer_message},
            ],
            temperature=0.4,
        )
    except Exception as exc:
        if should_reraise_on_error():
            if is_eval_strict_mode():
                api_error = build_llm_api_error(stage="final_response", exc=exc, attempt=1)
                log_llm_api_error(api_error)
                raise api_error from exc
            raise
        raise

    content = response.choices[0].message.content
    if not content:
        if is_eval_strict_mode():
            stage_error = build_final_response_error(
                ValueError("OpenAI returned an empty final response"),
                attempt=1,
            )
            log_llm_stage_error(stage_error)
            raise stage_error
        raise ValueError("OpenAI returned an empty final response")

    return content.strip(), runtime


def generate_final_response(
    user_message: str,
    intent_json: dict,
    route_result: dict,
    rag_context: list,
    database_result: dict,
    required_service_type: str | None = None,
    session=None,
    identity_result: dict | None = None,
    conv_ctx=None,
) -> dict:
    """
    Generate the final WhatsApp customer reply.

    Uses Final Response Prompt when configured, with rule-based fallback.
    rag_context must already be final_context_chunks only.
    """
    load_dotenv(override=not os.getenv("_EVAL_OVERRIDE_ACTIVE"))

    apply_response_flags(
        intent_json,
        session=session,
        user_message=user_message,
        route_result=route_result,
    )

    handoff_reason = str(intent_json.get("handoff_reason") or "").strip()
    route = str(route_result.get("route") or "").strip()
    if handoff_reason or route == "HUMAN_HANDOFF":
        reply = _build_handoff_reply(
            user_message,
            handoff_reason=handoff_reason or str(route_result.get("reason") or ""),
        )
        return {
            "reply": _finalize_reply(
                reply,
                intent_json,
                session=session,
                user_message=user_message,
                route_result=route_result,
                conv_ctx=conv_ctx,
            ),
            "final_response_provider_used": "handoff_template",
            "final_response_model_used": "handoff_template",
            "final_response_provider_logged": "handoff_template",
            "final_response_base_url_used": "",
        }

    database_result = enrich_database_result(database_result)

    def _done(
        reply: str,
        *,
        provider_used: str,
        model_used: str,
        provider_logged: str,
        base_url: str = "",
    ) -> dict:
        return _return_final_response(
            reply,
            intent_json,
            session=session,
            user_message=user_message,
            route_result=route_result,
            provider_used=provider_used,
            model_used=model_used,
            provider_logged=provider_logged,
            base_url=base_url,
            conv_ctx=conv_ctx,
        )

    scenario_intent = str(intent_json.get("scenario_intent") or "").strip()
    if scenario_intent == "CUSTOMER_GREETING":
        reply = _build_greeting_reply(database_result, session)
        return _done(
            reply,
            provider_used="rule_based",
            model_used="greeting_template",
            provider_logged="greeting_template",
        )

    if scenario_intent == "REPEAT_LAST_BOOKING":
        reply = _build_repeat_last_booking_reply(database_result, intent_json)
        return _done(
            reply,
            provider_used="rule_based",
            model_used="repeat_last_booking_template",
            provider_logged="repeat_last_booking_template",
        )

    route = str(route_result.get("route") or "").strip()
    if route == "CALL_RAG_THEN_ASK_MISSING_INFO":
        reply = _build_rule_based_reply(
            user_message=user_message,
            intent_json=intent_json,
            route_result=route_result,
            final_context_chunks=rag_context,
            database_result=database_result,
            required_service_type=required_service_type,
            session=session,
        )
        return _done(
            reply,
            provider_used="rule_based",
            model_used="mixed_booking_service_info_template",
            provider_logged="mixed_booking_service_info_template",
        )

    if route == "ASK_MISSING_INFO" and intent_json.get("standalone_service_info"):
        reply = _build_missing_info_reply(intent_json, session)
        return _done(
            reply,
            provider_used="rule_based",
            model_used="standalone_service_price_template",
            provider_logged="standalone_service_price_template",
        )

    if route == "ASK_MISSING_INFO" and intent_json.get("sub_flow") == "booking_price_pending_info":
        from booking_flow import build_booking_missing_info_reply

        reply = build_booking_missing_info_reply(intent_json, session)
        return _done(
            reply,
            provider_used="rule_based",
            model_used="booking_price_pending_info_template",
            provider_logged="booking_price_pending_info_template",
        )

    if (
        session is not None
        and intent_json.get("slot_just_accepted")
        and getattr(session, "draft_booking_payload", None)
    ):
        from booking_draft import build_draft_confirmation_reply

        reply = build_draft_confirmation_reply(session)
        return _done(
            reply,
            provider_used="rule_based",
            model_used="booking_draft_confirmation_template",
            provider_logged="booking_draft_confirmation_template",
        )

    if route == "ASK_MISSING_INFO" and scenario_intent == "MAKE_BOOKING":
        if intent_json.get("new_customer_booking_collection") and not session.existing_customer:
            from booking_flow import build_new_customer_booking_collection_reply

            reply = build_new_customer_booking_collection_reply(session, intent_json, user_message)
            return _done(
                reply,
                provider_used="rule_based",
                model_used="new_customer_booking_collection_template",
                provider_logged="new_customer_booking_collection_template",
            )

        from booking_flow import build_booking_missing_info_reply

        reply = build_booking_missing_info_reply(intent_json, session)
        return _done(
            reply,
            provider_used="rule_based",
            model_used="booking_missing_info_template",
            provider_logged="booking_missing_info_template",
        )

    if route == "ASK_MISSING_INFO" and "repeat_or_new_service_choice" in list(
        intent_json.get("missing_information") or []
    ):
        from booking_flow import build_booking_missing_info_reply

        reply = build_booking_missing_info_reply(intent_json, session)
        return _done(
            reply,
            provider_used="rule_based",
            model_used="booking_missing_info_template",
            provider_logged="booking_missing_info_template",
        )

    if scenario_intent == "COLLECT_CUSTOMER_NAME":
        from booking_flow import build_collect_customer_name_reply

        missing = list(intent_json.get("missing_information") or [])
        if not missing and session is not None and str(getattr(session, "customer_name", "") or "").strip():
            name = str(session.customer_name).strip()
            reply = f"Thanks {name}! 😊 How can I help you today?"
        else:
            reply = build_collect_customer_name_reply(intent_json, session)
        return _done(
            reply,
            provider_used="rule_based",
            model_used="collect_customer_name_template",
            provider_logged="collect_customer_name_template",
        )

    from booking_draft import AWAIT_BOOKING_CONFIRMATION, build_draft_confirmation_reply

    if scenario_intent == "CONFIRM_BOOKING":
        from booking_draft import build_booking_confirmed_reply, build_draft_confirmation_reply

        if str(database_result.get("status") or "").strip() == "success":
            reply = build_booking_confirmed_reply(database_result, session)
        elif getattr(session, "draft_booking_payload", None):
            reply = build_draft_confirmation_reply(session)
        else:
            reply = (
                f"I couldn't confirm the booking: "
                f"{database_result.get('error') or 'please check your details and try again.'}"
            )
        return _done(
            reply,
            provider_used="rule_based",
            model_used="booking_confirmed_template",
            provider_logged="booking_confirmed_template",
        )

    if scenario_intent == "BOOKING_CONFIRMATION_ORPHAN":
        from booking_draft import build_orphan_confirmation_reply

        reply = build_orphan_confirmation_reply()
        return _done(
            reply,
            provider_used="rule_based",
            model_used="booking_orphan_confirm_template",
            provider_logged="booking_orphan_confirm_template",
        )

    response_plan = intent_json.get("response_plan") or {}
    awaiting_confirmation = bool(
        response_plan.get("booking_confirmation_ready")
        or intent_json.get("scenario_intent") == "CONFIRM_BOOKING"
    )
    if (
        session is not None
        and awaiting_confirmation
        and str(database_result.get("action") or "").strip() == "check_available_slots"
        and str(database_result.get("status") or "").strip() == "success"
        and getattr(session, "draft_booking_payload", None)
    ):
        reply = build_draft_confirmation_reply(session)
        return _done(
            reply,
            provider_used="rule_based",
            model_used="booking_draft_confirmation_template",
            provider_logged="booking_draft_confirmation_template",
        )

    if (
        scenario_intent == "GET_BOOKING_SERVICE_OPTIONS"
        and str(database_result.get("action") or "").strip() == "get_booking_service_options"
        and (
            not rag_context
            or (
                os.getenv("FINAL_RESPONSE_PROVIDER", "").strip().lower()
                or os.getenv("LLM_PROVIDER", "mock").strip().lower()
            )
            != "openai"
        )
    ):
        reply = _build_booking_service_options_reply(database_result, session)
        return _done(
            reply,
            provider_used="rule_based",
            model_used="booking_service_options_template",
            provider_logged="booking_service_options_template",
        )

    if (
        scenario_intent == "CHECK_COUPON_ELIGIBILITY"
        and str(database_result.get("action") or "").strip() == "check_coupon_eligibility"
    ):
        reply = _build_coupon_eligibility_reply(database_result, intent_json, session)
        return _done(
            reply,
            provider_used="rule_based",
            model_used="coupon_eligibility_template",
            provider_logged="coupon_eligibility_template",
        )

    if (
        scenario_intent == "CHECK_AVAILABILITY"
        and str(database_result.get("action") or "").strip() == "check_available_slots"
    ):
        from availability_service import build_availability_reply

        if session is not None and getattr(session, "draft_booking_payload", None):
            reply = build_draft_confirmation_reply(session)
            return _done(
                reply,
                provider_used="rule_based",
                model_used="booking_draft_confirmation_template",
                provider_logged="booking_draft_confirmation_template",
            )

        availability = dict((database_result.get("data") or {}).get("availability_result") or getattr(session, "availability_result", {}) or {})
        if availability:
            reply = build_availability_reply(session, availability, intent_json)
        elif str(database_result.get("status") or "").strip() == "success":
            reply = "I couldn't confirm slot availability yet. Would you like to try another time?"
        else:
            reply = "I couldn't check availability right now. Would you like to try another time?"
        return _done(
            reply,
            provider_used="rule_based",
            model_used="availability_check_template",
            provider_logged="availability_check_template",
        )

    decision_json = _build_decision_json(intent_json, route_result, required_service_type)
    retrieved_context = _format_retrieved_context(rag_context, required_service_type)
    database_result_text = _format_database_result(database_result)

    provider = os.getenv("FINAL_RESPONSE_PROVIDER", "").strip().lower()
    if not provider:
        provider = os.getenv("LLM_PROVIDER", "mock").strip().lower() or "mock"

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            if should_reraise_on_error():
                raise ValueError("OPENAI_API_KEY is not set for evaluation final response")
        elif api_key:
            try:
                reply, runtime = _generate_llm_final_response(
                    customer_message=user_message,
                    decision_json=decision_json,
                    retrieved_context=retrieved_context,
                    database_result_text=database_result_text,
                )
                from booking_service_info import append_service_info_follow_up, build_mixed_booking_rag_reply

                if route == "CALL_RAG_THEN_ASK_MISSING_INFO":
                    reply = build_mixed_booking_rag_reply(reply, session, intent_json)
                else:
                    reply = append_service_info_follow_up(reply, session, intent_json)
                if scenario_intent == "GET_BOOKING_SERVICE_OPTIONS":
                    reply = _enforce_service_option_conversation_contract(reply, session)
                return _done(
                    reply,
                    provider_used="openai",
                    model_used=runtime["model"],
                    provider_logged=runtime["provider"],
                    base_url=runtime["base_url"],
                )
            except LlmStageError:
                raise
            except Exception as exc:
                if should_reraise_on_error():
                    if is_eval_strict_mode():
                        if hasattr(exc, "status_code") or exc.__class__.__name__ in {
                            "APIStatusError",
                            "RateLimitError",
                            "APIConnectionError",
                            "APITimeoutError",
                        }:
                            api_error = build_llm_api_error(stage="final_response", exc=exc, attempt=1)
                            log_llm_api_error(api_error)
                            raise api_error from exc
                        stage_error = build_final_response_error(exc, attempt=1)
                        log_llm_stage_error(stage_error)
                        raise stage_error from exc
                    raise

    if is_eval_strict_mode():
        raise ValueError(
            f"Final response LLM unavailable during evaluation (provider={provider})"
        )

    reply = _build_rule_based_reply(
        user_message=user_message,
        intent_json=intent_json,
        route_result=route_result,
        final_context_chunks=rag_context,
        database_result=database_result,
        required_service_type=required_service_type,
        session=session,
    )
    return _done(
        reply,
        provider_used="rule_based",
        model_used="rule_based",
        provider_logged="rule_based",
    )
