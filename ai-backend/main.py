import logging
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from customer_context import apply_request_context_to_intent
from customer_identity import resolve_customer_at_request_start
from intent_schema import (
    apply_message_pattern_overrides,
    build_human_handoff_intent,
    is_explicit_human_handoff_request,
    is_medical_diagnosis_handoff_request,
    normalize_intent_result,
)
from response_generator import EXPLICIT_HUMAN_HANDOFF_REPLY, generate_final_response, handoff_reply_for_reason, resolve_turn_handoff
from router import route_intent
from database_service import execute_database_action, get_database_provider
from llm_service import detect_intent
from rag_service import retrieve_rag_context
from response_data_sanitizer import sanitize_response_data
from relational_tool_calling import (
    execute_relational_tool_calls,
    plan_relational_tool_calls,
)
from booking_flow import (
    apply_booking_collection_rules,
    apply_booking_confirmation_safety,
    apply_booking_entry_rules,
)
from booking_service_info import apply_mixed_booking_service_info_rules, apply_standalone_service_info_rules
from session_continuation import (
    apply_read_only_entity_rules,
    apply_session_booking_creation_flag,
    apply_session_identity,
    handle_session_before_routing,
    update_session_after_turn,
)
from session_store import (
    clear_session_for_phone,
    get_or_create_session,
    get_session_key,
    session_exists,
    update_session_from_availability_check,
    update_session_from_last_booking,
    update_session_intent,
)
from runtime_debug import get_runtime_debug_payload, infer_next_question, log_chat_runtime_state, log_startup_info
from chat_logging import (
    ChatRequestTrace,
    log_chat_intent,
    log_chat_response,
    log_chat_retrieval,
    log_chat_session,
    log_chat_stage,
)

logger = logging.getLogger("pawfect.chat")

_DEFAULT_DATABASE_RESULT = {
    "action": "unknown_database_action",
    "status": "not_applicable",
    "data": {},
    "error": None,
}

_RAG_ROUTES = frozenset(
    {"CALL_KNOWLEDGE_RAG", "CALL_RAG_AND_DATABASE", "CALL_RAG_THEN_ASK_MISSING_INFO"}
)
_DATABASE_ROUTES = frozenset({"CALL_DATABASE", "CALL_RAG_AND_DATABASE"})


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    log_startup_info()
    yield


app = FastAPI(lifespan=_app_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    customer_id: str = ""
    phone_number: str = ""


class ClearSessionRequest(BaseModel):
    phone_number: str = ""


@app.get("/")
def read_root():
    return {"message": "Pawfect backend environment is working"}


@app.post("/debug/clear-session")
def debug_clear_session(request: ClearSessionRequest):
    """Local testing only: clear in-memory conversation state for a phone number."""
    from testing_mode import is_testing_mode

    if not is_testing_mode() and os.getenv("ENVIRONMENT_NAME", "development") != "development":
        return {"status": "forbidden", "error": "debug endpoints disabled"}
    phone = str(request.phone_number or "").strip()
    cleared = clear_session_for_phone(phone)
    return {"status": "success", "cleared": cleared, "phone_number": phone}


@app.get("/debug/runtime")
def debug_runtime(phone_number: str = Query(default="")):
    """Local testing only: active process, source path, and session state."""
    return get_runtime_debug_payload(phone_number)


_DEFAULT_HANDOFF_REPLY = (
    "Sorry, I'm having a little trouble right now. "
    "I'll connect you with our team to help you shortly."
)


def _build_chat_error_response(request: ChatRequest, exc: Exception, *, error_id: str = "") -> dict:
    log_chat_stage(
        "error",
        error_type=type(exc).__name__,
        error=str(exc),
        error_id=error_id or "(none)",
    )
    request_phone = str(request.phone_number or "").strip()
    session = get_or_create_session(request_phone)
    return {
        "reply": _DEFAULT_HANDOFF_REPLY,
        "customer_id": session.customer_id if session.customer_id is not None else "",
        "phone_number": request_phone,
        "session_context": sanitize_response_data(session.to_dict()),
        "database_provider": get_database_provider(),
        "message": request.message,
        "intent_json": {},
        "llm_provider_used": "error_fallback",
        "final_response_provider_used": "error_fallback",
        "route_result": {"route": "HUMAN_HANDOFF", "reason": "unhandled_error"},
        "rag_used": False,
        "rag_provider_config": os.getenv("RAG_PROVIDER", "mock"),
        "rag_provider_used": "none",
        "rag_error": str(exc),
        "rag_debug": None,
        "rag_context": [],
        "retrieval_query": request.message,
        "rewritten_query": "",
        "query_rewrite_used": False,
        "database_used": False,
        "database_result": _DEFAULT_DATABASE_RESULT,
        "identity_result": {"status": "error", "error": str(exc), "error_id": error_id or ""},
        "handoff_required": True,
        "handoff_reason": "DATABASE_ERROR",
    }


def _build_early_handoff_response(
    request: ChatRequest,
    session,
    *,
    handoff_reason: str,
    llm_provider_used: str = "handoff_template",
) -> dict:
    intent_json = build_human_handoff_intent(request.message, handoff_reason=handoff_reason)
    route_result = {"route": "HUMAN_HANDOFF", "reason": handoff_reason}
    return {
        "reply": handoff_reply_for_reason(handoff_reason),
        "customer_id": session.customer_id if session.customer_id is not None else "",
        "phone_number": str(request.phone_number or "").strip(),
        "session_context": sanitize_response_data(session.to_dict()),
        "database_provider": get_database_provider(),
        "message": request.message,
        "intent_json": intent_json,
        "llm_provider_used": llm_provider_used,
        "final_response_provider_used": "handoff_template",
        "route_result": route_result,
        "rag_used": False,
        "rag_provider_config": os.getenv("RAG_PROVIDER", "mock"),
        "rag_provider_used": "none",
        "rag_error": None,
        "rag_debug": None,
        "rag_context": [],
        "retrieval_query": request.message,
        "rewritten_query": "",
        "query_rewrite_used": False,
        "database_used": False,
        "database_result": _DEFAULT_DATABASE_RESULT,
        "identity_result": {"status": "not_applicable"},
        "handoff_required": True,
        "handoff_reason": handoff_reason,
    }


@app.post("/chat")
def chat(request: ChatRequest):
    from testing_mode import is_testing_mode, log_unhandled_exception, new_error_id

    try:
        return _process_chat(request)
    except Exception as exc:
        error_id = new_error_id()
        log_unhandled_exception(
            logger,
            exc,
            error_id=error_id,
            phone=request.phone_number,
            message=request.message,
        )
        if is_testing_mode():
            raise
        return _build_chat_error_response(request, exc, error_id=error_id)


def _process_chat(request: ChatRequest):
    request_phone = str(request.phone_number or "").strip()
    request_customer_id = str(request.customer_id or "").strip()
    session_key = get_session_key(request_phone)
    session_existed_before = session_exists(request_phone)
    session = get_or_create_session(request_phone)
    session.enable_runtime_guard()

    customer_name_before = str(getattr(session, "customer_name", "") or "").strip()
    pet_type_before = str(getattr(session, "pet_type", "") or "").strip()
    step_before = str(getattr(session, "current_step", "") or "").strip()

    request_trace = ChatRequestTrace(
        session_key=session_key,
        message=request.message,
        session_before=dict(session.to_dict()),
    )
    request_trace.log_pre_intent()

    if is_explicit_human_handoff_request(request.message):
        return _build_early_handoff_response(session=session, request=request, handoff_reason="EXPLICIT_HUMAN_REQUEST")
    if is_medical_diagnosis_handoff_request(request.message):
        return _build_early_handoff_response(session=session, request=request, handoff_reason="MEDICAL_OUT_OF_SCOPE")

    identity_result = resolve_customer_at_request_start(
        session, request_phone, request_customer_id, request.message
    )

    intent_result = detect_intent(request.message)
    intent_json = dict(intent_result.get("intent_json") or {})
    intent_json["_session_greeted_before_turn"] = bool(
        getattr(session, "greeted_this_session", False)
    )
    intent_json = apply_message_pattern_overrides(request.message, intent_json)
    intent_json = normalize_intent_result(intent_json)
    llm_provider_used = str(intent_result.get("provider_used") or "unknown")

    intent_json = apply_request_context_to_intent(
        intent_json,
        phone_number=request_phone,
        customer_id=request_customer_id,
    )
    intent_json = apply_read_only_entity_rules(intent_json, request.message)
    intent_json = apply_session_identity(session, intent_json)
    apply_session_booking_creation_flag(session, request.message, intent_json)

    intent_json, session = handle_session_before_routing(session, request.message, intent_json)
    intent_json = apply_booking_entry_rules(session, intent_json, request.message)
    intent_json = apply_mixed_booking_service_info_rules(session, intent_json, request.message)
    intent_json = apply_standalone_service_info_rules(session, intent_json, request.message)
    intent_json = apply_booking_collection_rules(session, intent_json, request.message)
    intent_json = apply_booking_confirmation_safety(session, intent_json, request.message)
    session = update_session_intent(session, intent_json)

    request_trace.set_post_intent(intent_json, session)
    request_trace.log_pre_retrieval()

    route_result = route_intent(intent_json)
    route = str(route_result.get("route") or "").strip()
    invoke_rag = route in _RAG_ROUTES
    invoke_database = route in _DATABASE_ROUTES

    rag_route = route if invoke_rag else "SKIP_RAG"
    rag_result = retrieve_rag_context(request.message, intent_json, rag_route, session=session)
    rag_context = list(rag_result.get("rag_context") or [])
    rag_used = bool(rag_result.get("rag_used"))
    rag_provider_used = rag_result.get("rag_provider_used")
    rag_provider_config = rag_result.get("rag_provider_config")
    rag_error = rag_result.get("rag_error")
    rag_debug = rag_result.get("rag_debug")
    retrieval_query = rag_result.get("retrieval_query", request.message)
    rewritten_query = rag_result.get("rewritten_query", "")
    query_rewrite_used = bool(rag_result.get("query_rewrite_used", False))

    request_trace.set_retrieval(
        retrieval_query=retrieval_query,
        rag_debug=rag_debug,
        rag_context=rag_context,
        rewritten_query=rewritten_query,
        query_rewrite_used=query_rewrite_used,
    )
    log_chat_retrieval(
        retrieval_query=retrieval_query,
        route=route,
        rag_used=rag_used,
        chunk_count=len(rag_context),
        rag_error=rag_error,
    )

    database_result = _DEFAULT_DATABASE_RESULT
    scenario_intent = str(intent_json.get("scenario_intent") or "").strip()

    if scenario_intent == "CUSTOMER_GREETING":
        database_result = identity_result
        entities = dict(intent_json.get("entities") or {})
        if session.customer_name and not str(entities.get("customer_name", "")).strip():
            entities["customer_name"] = session.customer_name
        intent_json["entities"] = entities
        if session.existing_customer:
            intent_json["customer_status"] = "EXISTING_CUSTOMER"
        elif request_phone:
            intent_json["customer_status"] = "NEW_CUSTOMER"
    elif scenario_intent == "COLLECT_CUSTOMER_NAME" and identity_result.get("status") == "not_found":
        database_result = identity_result
    elif invoke_database:
        tool_calls = (
            []
            if scenario_intent in {"CHECK_COUPON_ELIGIBILITY", "GET_BOOKING_SERVICE_OPTIONS"}
            else plan_relational_tool_calls(request.message, intent_json)
        )
        database_result = execute_relational_tool_calls(
            tool_calls,
            intent_json=intent_json,
            customer_id=request_customer_id,
            phone_number=request_phone,
            session=session,
            user_message=request.message,
        )
        if not database_result:
            database_result = execute_database_action(
                intent_json,
                customer_id=request_customer_id,
                phone_number=request_phone,
                session=session,
                user_message=request.message,
            )

    if database_result.get("action") == "check_available_slots" and database_result.get("status") == "success":
        session = update_session_from_availability_check(session, intent_json, database_result)

    if scenario_intent == "REPEAT_LAST_BOOKING" and database_result.get("action") == "check_last_booking":
        session = update_session_from_last_booking(session, database_result)

    if intent_json.get("returning_customer_booking_entry"):
        from database_service import fetch_latest_booking_for_entry

        last_booking_result = fetch_latest_booking_for_entry(session)
        intent_json["booking_entry_last_booking"] = last_booking_result
        if last_booking_result.get("status") == "success":
            session.last_booking_snapshot = dict(last_booking_result.get("data") or {})
        else:
            session.last_booking_snapshot = {}
        if database_result.get("status") == "not_applicable":
            database_result = last_booking_result

    session = update_session_after_turn(session, intent_json, route_result, database_result)
    database_used = database_result["status"] != "not_applicable"

    handoff_required, handoff_reason = resolve_turn_handoff(
        user_message=request.message,
        intent_json=intent_json,
        route_result=route_result,
        database_result=database_result,
        rag_result=rag_result,
    )
    if handoff_required:
        intent_json["handoff_reason"] = handoff_reason
        route_result = {"route": "HUMAN_HANDOFF", "reason": handoff_reason}

    # Keep staff identifiers available for internal booking/availability work,
    # but never expose them to the response LLM or the public chat payload.
    response_database_result = sanitize_response_data(database_result)

    final_response_result = generate_final_response(
        user_message=request.message,
        intent_json=intent_json,
        route_result=route_result,
        rag_context=rag_context,
        database_result=response_database_result,
        required_service_type=(rag_debug or {}).get("required_service_type"),
        session=session,
        identity_result=identity_result,
    )
    reply = final_response_result["reply"]
    final_response_provider_used = final_response_result["final_response_provider_used"]

    current_step = str(getattr(session, "current_step", "") or intent_json.get("current_step") or "").strip()
    missing_fields = list(getattr(session, "missing_fields", []) or intent_json.get("missing_information") or [])
    next_question = infer_next_question(intent_json, reply)

    log_chat_runtime_state(
        raw_phone=request_phone,
        normalized_phone=session_key,
        session_key=session_key,
        session_existed_before=session_existed_before,
        customer_name_before=customer_name_before,
        customer_name_after=str(getattr(session, "customer_name", "") or "").strip(),
        pet_type_before=pet_type_before,
        pet_type_after=str(getattr(session, "pet_type", "") or "").strip(),
        current_step=current_step,
        missing_fields=missing_fields,
        next_question=next_question,
        message=request.message,
    )
    log_chat_response(route=route, provider=str(final_response_provider_used or ""), reply_preview=reply)
    request_trace.set_post_response(intent_json=intent_json, session=session, route=route, reply=reply)
    request_trace.log_end()

    resolved_customer_id = session.customer_id
    if resolved_customer_id is None and request_customer_id:
        resolved_customer_id = request_customer_id

    return {
        "reply": reply,
        "customer_id": resolved_customer_id if resolved_customer_id is not None else "",
        "phone_number": request_phone,
        "session_context": sanitize_response_data(session.to_dict()),
        "database_provider": get_database_provider(),
        "message": request.message,
        "intent_json": intent_json,
        "llm_provider_used": llm_provider_used,
        "final_response_provider_used": final_response_provider_used,
        "route_result": route_result,
        "rag_used": rag_used,
        "rag_provider_config": rag_provider_config,
        "rag_provider_used": rag_provider_used,
        "rag_error": rag_error,
        "rag_debug": rag_debug,
        "rag_context": rag_context,
        "retrieval_query": retrieval_query,
        "rewritten_query": rewritten_query,
        "query_rewrite_used": query_rewrite_used,
        "database_used": database_used,
        "database_result": response_database_result,
        "identity_result": identity_result,
        "handoff_required": handoff_required,
        "handoff_reason": handoff_reason or "",
    }
