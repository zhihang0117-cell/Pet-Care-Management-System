"""
Minimal HTTP entrypoint for the LangChain harness in app/.

app/orchestrator.py is a plain importable library (no server assumptions).
This file just exposes it over HTTP so the eval console (frontend/eval_console.html)
and, eventually, a WhatsApp webhook can call it.
"""

from __future__ import annotations

import os
import hmac
import json
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

# Governs two things that are fine wide-open for local/eval-console testing
# but must not ship that way: the /debug/* endpoints (no auth at all today)
# and whether /chat's error response includes the real exception text.
# Defaults closed; local eval can opt in explicitly.
DEBUG_MODE = os.getenv("PAWFECT_DEBUG_MODE", "false").strip().lower() not in ("false", "0", "no")

# The /documents/* endpoints below are only ever called server-to-server by
# the Node dashboard (backend/src/lib/aiBackend.js, which sends this same
# header) after it PATCHes a booking/payment/redemption — never by a browser.
# Cloud Run/Render both deploy this service with --allow-unauthenticated, so
# without this check anyone with the URL could trigger invoice generation or
# WhatsApp notices for arbitrary company/payment/booking ids. /chat is
# uses its own X-Chat-Key boundary because it is a separate ingress surface.
_INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "").strip()
_CHAT_API_KEY = os.getenv("CHAT_API_KEY", "").strip()


def _resolve_chat_company(x_chat_key: str = Header(default="")) -> int:
    """
    Authenticate /chat AND derive which company it's for from the presented
    key — never from a client-supplied company_id field, which would be the
    same header-trust vulnerability that was removed from the old /api/llm
    surface on the Node side. Two credential shapes are accepted:

    1. A per-company key row in company_chat_key (see backend/sql/
       chat_api_key_migration.sql) — resolves to that row's real
       company_id. This is what actually enables one deployment to safely
       serve more than one company.
    2. The legacy single CHAT_API_KEY env var — resolves to
       RELATIONAL_COMPANY_ID, exactly like every /chat call worked before
       this existed. Kept so an existing single-company deployment (or one
       that hasn't run the migration yet) needs no changes.

    Fails closed in production if neither credential is configured at all;
    local eval-console testing may opt into no-auth via PAWFECT_DEBUG_MODE.
    """
    from app.db.customer_context import get_relational_company_id, resolve_company_id_from_chat_key

    presented = x_chat_key.strip()
    if presented:
        company_id = resolve_company_id_from_chat_key(presented)
        if company_id is not None:
            return company_id

    if not _CHAT_API_KEY:
        if DEBUG_MODE:
            return get_relational_company_id()
        raise HTTPException(status_code=503, detail="CHAT_API_KEY is not configured on this service")
    if not hmac.compare_digest(presented, _CHAT_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Chat-Key")
    return get_relational_company_id()


class _SlidingWindowLimiter:
    def __init__(self):
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            # Opportunistic cleanup prevents unbounded growth from one-off keys.
            if len(self._events) > 10_000:
                stale = [k for k, q in self._events.items() if not q or q[-1] <= cutoff]
                for stale_key in stale[:2_000]:
                    self._events.pop(stale_key, None)
            return True


_CHAT_LIMITER = _SlidingWindowLimiter()


def _enforce_chat_rate_limit(request: Request, phone_number: str) -> None:
    ip = request.client.host if request.client else "unknown"
    def configured_limit(name: str, default: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError:
            return default
        return value if value > 0 else default

    ip_limit = configured_limit("CHAT_RATE_LIMIT_PER_IP_PER_MINUTE", 120)
    phone_limit = configured_limit("CHAT_RATE_LIMIT_PER_PHONE_PER_MINUTE", 30)
    if not _CHAT_LIMITER.check(f"ip:{ip}", ip_limit) or not _CHAT_LIMITER.check(
        f"phone:{phone_number}", phone_limit
    ):
        raise HTTPException(status_code=429, detail="Too many chat requests; please retry shortly")


def _require_internal_key(x_internal_key: str = Header(default="")) -> None:
    if not _INTERNAL_API_KEY:
        # Fails closed rather than silently allowing unauthenticated access —
        # set INTERNAL_API_KEY (see render.yaml) before deploying.
        raise HTTPException(status_code=503, detail="INTERNAL_API_KEY is not configured on this service")
    if not hmac.compare_digest(x_internal_key.strip(), _INTERNAL_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Internal-Key")

from app.context.memory import ConversationMemory
from app.context.company import get_company_config
from app.db.customer_context import get_relational_company_id
from app.orchestrator import PawfectOrchestrator
from app.documents.company_profile import get_billing_profile
from app.documents.pdf_builder import build_booking_confirmation_pdf, build_invoice_pdf
from app.documents.service import generate_and_send_invoice
from app.documents.dispatch import send_whatsapp_text, get_outbox, clear_outbox
from app.documents.notifications import (
    build_booking_status_notice,
    build_payment_refund_notice,
    build_redemption_decision_notice,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # This API is stateless (phone_number-keyed, no cookies) — allow_credentials
    # was never needed and, combined with allow_origins=["*"], is a
    # combination browsers already reject/flag per the CORS spec.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_orchestrator: PawfectOrchestrator | None = None
_memory = ConversationMemory()


@app.on_event("startup")
def _warm_up_embedding_model() -> None:
    """
    Load (and JIT-warm) the local sentence-transformers embedding model
    before this process accepts any real traffic, instead of paying that
    cost on whichever customer's message happens to trigger the first
    retrieve_policy/get_booking_service_options call. get_embedding_model()
    already caches the loaded model at module scope (app/rag/embeddings.py),
    so this just moves that one-time cost to server startup — where a slow
    first request doesn't read as "the bot is broken" to a real customer.
    Only relevant for EMBEDDING_PROVIDER=sentence_transformers (the
    default); the OpenAI embeddings path has no local model to warm.
    Never allowed to fail startup — if the model can't load here, the first
    real RAG call will surface that same error immediately after, same as
    before this existed.
    """
    if os.getenv("EMBEDDING_PROVIDER", "sentence_transformers").strip().lower() == "openai":
        return
    try:
        from app.rag.embeddings import embed_query

        embed_query("warmup")
        logging.getLogger(__name__).info("Embedding model warmed up at startup.")
    except Exception:
        logging.getLogger(__name__).exception(
            "Embedding model warmup failed — first real RAG call will pay this cost instead."
        )


def _agent_state_summary(state) -> dict:
    """Small, non-sensitive progress snapshot for the authenticated eval console."""
    return {
        "objective": state.objective,
        "active_scenario": state.active_scenario,
        "service_type": state.service_type,
        "current_step": state.current_step,
        "completion_status": state.completion_status,
        "missing_information": list(state.missing_information),
        "verified_fact_keys": sorted(state.verified_facts),
        "recent_evidence_count": len(state.recent_tool_evidence),
    }


def _documents_from_trace(trace: list[dict]) -> list[dict]:
    """Typed attachment contract for the authenticated eval console."""
    documents: list[dict] = []
    seen_urls: set[str] = set()
    for item in trace or []:
        if item.get("tool") not in {
            "create_booking", "reschedule_booking", "send_booking_confirmation"
        }:
            continue
        raw_result = item.get("result")
        try:
            result = raw_result if isinstance(raw_result, dict) else json.loads(raw_result or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            continue
        document_url = str(
            data.get("_internal_document_url")
            or data.get("_internal_confirmation_url")
            or ""
        ).strip()
        if not document_url or document_url in seen_urls:
            continue
        seen_urls.add(document_url)
        delivery_status = str(
            data.get("delivery_status")
            or data.get("confirmation_delivery_status")
            or ""
        )
        service_type = str(data.get("service_type") or "").strip().upper()
        booking_id = data.get("booking_id")
        reference = "-".join(
            part for part in (service_type, str(booking_id) if booking_id is not None else "") if part
        )
        documents.append({
            "kind": "document",
            "document_type": "booking_confirmation",
            "label": (
                f"Booking confirmation {reference}" if reference else "Booking confirmation"
            ),
            "url": document_url,
            "delivery_status": delivery_status,
            "delivered": delivery_status in {"sent", "sent_console"},
            "source_tool": item.get("tool"),
        })
    return documents


def _get_orchestrator() -> PawfectOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PawfectOrchestrator()
    return _orchestrator


class ChatRequest(BaseModel):
    message: str
    phone_number: str = ""


class ClearSessionRequest(BaseModel):
    phone_number: str = ""


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest, http_request: Request, resolved_company_id: int = Depends(_resolve_chat_company)):
    """
    System prompt + conversation context -> GPT-4o-mini -> approved tools
    (Supabase/RAG/availability) looped until the model returns a final reply.

    Returns tool_trace + latency_ms in addition to the reply so the eval
    console can show what happened per turn.
    """
    phone_number = request.phone_number.strip()
    if not phone_number:
        return {"error": "phone_number is required"}
    from app.db.customer_context import canonical_phone_number

    try:
        phone_number = canonical_phone_number(phone_number)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _enforce_chat_rate_limit(http_request, phone_number)

    company_id = str(resolved_company_id)
    # Two near-simultaneous messages from one phone must see each other's
    # state in order. This protects the current single-process deployment;
    # Redis/DB locking is still required before multi-worker scaling.
    with _memory.session_lock(phone_number, company_id):
        return _chat_locked(request, phone_number, company_id)


def _chat_locked(request: ChatRequest, phone_number: str, company_id: str):
    state = _memory.get(phone_number, company_id)
    started_at = time.perf_counter()
    trace = []
    try:
        # Company-context reads are part of the same protected agent run. A DB
        # failure here must reach the customer fallback/staff escalation rather
        # than escaping as an unhandled FastAPI 500 before the try block.
        company_context = get_company_config(company_id)
        response, trace = _get_orchestrator().invoke_with_trace(company_context, state, request.message)
    except Exception as exc:
        import traceback

        traceback.print_exc()  # server-side log only — the customer never sees this
        # A blank reply here means a real WhatsApp customer gets literally
        # nothing back (confirmed: MAX_TOOL_ITERATIONS and any unhandled
        # exception both used to return reply=""). Always give them
        # something human, and still save state so the TTL/turn_counter
        # bookkeeping this turn isn't silently lost.
        _memory.save(state)
        fallback_reply = (
            "Sorry, I'm having a technical hiccup on my end right now — "
            "I've flagged this for our team to look into. Please try "
            "again in a moment, or a staff member will follow up with you."
        )
        escalation_saved = False
        if state.customer_id is None:
            # save_staff_enquiry requires a real customer_id (the `messages`
            # escalation row is keyed to one) and always raises without it —
            # this branch is reached whenever the failure above happened
            # before identity was ever resolved this turn. Calling it anyway
            # only produced a second, silently-swallowed exception with the
            # phone number never logged anywhere. Log the one piece of
            # identifying information that does exist instead of attempting
            # a write that cannot succeed.
            fallback_reply = (
                "Sorry, I'm having a technical hiccup and couldn't lodge a staff follow-up "
                "in our system. Please contact the team directly if this is urgent."
            )
            logging.getLogger(__name__).error(
                "Staff follow-up needed but could not be logged to the dashboard "
                "(no resolved customer_id): company_id=%s phone_number=%s message=%r",
                company_id,
                phone_number,
                request.message,
            )
        else:
            try:
                from app.db.escalations import save_staff_enquiry

                save_staff_enquiry(company_id, state.customer_id, request.message, "AI_BACKEND_ERROR")
                escalation_saved = True
            except Exception as escalation_exc:
                fallback_reply = (
                    "Sorry, I'm having a technical hiccup and couldn't lodge a staff follow-up "
                    "in our system. Please contact the team directly if this is urgent."
                )
                logging.getLogger(__name__).exception(
                    "Could not persist AI_BACKEND_ERROR escalation for customer_id=%s: %s",
                    state.customer_id,
                    escalation_exc,
                )
        delivery = send_whatsapp_text(phone_number, fallback_reply)
        partial_trace = getattr(exc, "trace", trace)
        return {
            "reply": fallback_reply,
            "phone_number": phone_number,
            "company_id": company_id,
            "customer_id": state.customer_id,
            # Preserve calls completed before MAX_TOOL_ITERATIONS or another
            # late agent-loop failure; an empty list now genuinely means no
            # traced tool completed before the error.
            "tool_trace": partial_trace,
            "documents": _documents_from_trace(partial_trace),
            "latency_ms": round((time.perf_counter() - started_at) * 1000, 1),
            # Full exception text only in debug mode (eval console testing) —
            # a real caller of this endpoint in production shouldn't get
            # internal error/schema details back in the response body, even
            # though the "reply" text was already generic either way.
            "error": str(exc) if DEBUG_MODE else "internal_error",
            "staff_escalation_saved": escalation_saved,
            "delivery": delivery,
            "agent_state": _agent_state_summary(state),
        }
    latency_ms = round((time.perf_counter() - started_at) * 1000, 1)
    _memory.save(state)

    delivery = send_whatsapp_text(phone_number, response.content)
    delivery_escalation_saved = False
    if delivery.get("status") not in {"sent", "sent_console"} and state.customer_id is not None:
        try:
            from app.db.escalations import save_staff_enquiry

            save_staff_enquiry(company_id, state.customer_id, request.message, "WHATSAPP_DELIVERY_ERROR")
            delivery_escalation_saved = True
        except Exception as exc:
            # The API response still exposes the real delivery failure; never
            # replace it with a fake sent status if this secondary alert also fails.
            logging.getLogger(__name__).exception("Could not persist WhatsApp delivery escalation: %s", exc)
    return {
        "reply": response.content,
        "phone_number": phone_number,
        "company_id": company_id,
        "customer_id": state.customer_id,
        "tool_trace": trace,
        "documents": _documents_from_trace(trace),
        "latency_ms": latency_ms,
        "error": None,
        "delivery": delivery,
        "delivery_escalation_saved": delivery_escalation_saved,
        "agent_state": _agent_state_summary(state),
    }


def _require_debug_mode() -> None:
    # These three endpoints have no auth of their own at all — fine for
    # local/eval-console testing, not fine left reachable in production
    # (clear-session/clear-outbox are real writes; outbox can read whatever
    # a phone number's simulated WhatsApp history contains). Set
    # PAWFECT_DEBUG_MODE=false to turn all three off.
    if not DEBUG_MODE:
        raise HTTPException(status_code=404, detail="Not found")


@app.post("/debug/clear-session")
def clear_session(request: ClearSessionRequest):
    """Local testing only: reset in-memory conversation state for a phone number."""
    _require_debug_mode()
    phone = request.phone_number.strip()
    cleared = _memory.clear(phone)
    return {"status": "success", "cleared": cleared, "phone_number": phone}


@app.get("/debug/outbox")
def debug_outbox(phone_number: str):
    """
    Local testing only: the simulated WhatsApp outbox for one phone number
    (see app.documents.dispatch — only populated when WHATSAPP_PROVIDER=
    console). Lets frontend/eval_console.html show what "would have been
    sent" — booking confirmations, invoices, status/redemption/refund
    notices — without needing real WhatsApp credentials.
    """
    _require_debug_mode()
    return {"phone_number": phone_number, "entries": get_outbox(phone_number.strip())}


@app.post("/debug/clear-outbox")
def debug_clear_outbox(request: ClearSessionRequest):
    """Local testing only: clear the simulated WhatsApp outbox for one phone number."""
    _require_debug_mode()
    clear_outbox(request.phone_number.strip())
    return {"status": "success", "phone_number": request.phone_number}


class ProcessDocumentRequest(BaseModel):
    company_id: int
    document_id: str
    document_type: Literal["policies", "service_information", "business_flow_booking", "veterinary"]
    service_type: Literal["grooming", "boarding", "daycare", "general"] = "general"
    storage_bucket: str
    storage_path: str


@app.post("/api/documents/process", dependencies=[Depends(_require_internal_key)])
def api_documents_process(request: ProcessDocumentRequest):
    """
    Called by the staff dashboard right after it uploads/replaces a company
    policy document to Supabase Storage (see backend/src/routes/companies.js
    POST/PUT /me/documents) — downloads the .docx, runs section-aware
    chunking + rule-based metadata tagging, embeds with BGE-Large, and
    atomically replaces this document's chunks in chunks_bge_large so RAG
    retrieval (app/rag/retriever.py) picks up the new content.
    """
    from app.documents.vector_store import process_document_from_storage

    try:
        return process_document_from_storage(
            company_id=request.company_id,
            document_id=request.document_id,
            document_type=request.document_type,
            service_type=request.service_type,
            storage_bucket=request.storage_bucket,
            storage_path=request.storage_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class InvoiceRequest(BaseModel):
    company_id: int
    payment_id: int
    send: bool = True


@app.post("/documents/invoice", dependencies=[Depends(_require_internal_key)])
def documents_invoice(request: InvoiceRequest):
    """
    Called by the staff dashboard right after it marks a payment 'Paid' (see
    backend/src/routes/payments.js mark-paid) — generates the invoice
    PDF, stores it, and attempts to send it over WhatsApp (see
    app.documents.dispatch — returns "not_configured" until a real WhatsApp
    provider is wired in). This is a distinct HTTP call rather than an
    in-process function because the transition that triggers it happens in
    a separate Node server, not this one.

    Also called (with send=False) by the dashboard's View/Print Invoice
    buttons (backend/src/routes/payments.js GET /:id/invoice) to fetch a
    fresh signed URL for an already-paid payment's invoice without
    re-sending it over WhatsApp.
    """
    from app.db.supabase_client import get_supabase_client

    client = get_supabase_client()
    payment_rows = (
        client.table("payment")
        .select("*")
        .eq("company_id", request.company_id)
        .eq("payment_id", request.payment_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not payment_rows:
        return {"status": "error", "error": f"payment_id {request.payment_id} not found"}
    payment = payment_rows[0]
    if str(payment.get("status") or "").strip().casefold() != "paid":
        return {
            "status": "error",
            "error": "INVOICE_REQUIRES_PAID_PAYMENT",
            "message": "An invoice can only be generated for a payment whose status is Paid.",
        }

    booking = {}
    for table, id_col, date_col in (
        ("grooming_booking", "grooming_booking_id", "booking_date"),
        ("daycare_booking", "daycare_booking_id", "booking_date"),
        ("boarding_booking", "boarding_booking_id", "check_in_date"),
    ):
        rows = (
            client.table(table)
            .select("*")
            .eq("company_id", request.company_id)
            .eq("payment_id", request.payment_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if rows:
            booking = {**rows[0], "booking_id": rows[0].get(id_col)}
            break

    return generate_and_send_invoice(request.company_id, payment, booking, send=request.send)


def _seed_notice_into_history(company_id: int | str, phone_number: str, message: str) -> None:
    """
    So that if the customer replies to this outbound notice, the next /chat
    turn already has it in state.history as an "ai" turn — e.g. a reply to
    the No Show notice ("sorry, can we reschedule?") reads naturally in
    context instead of the model having no idea what's being responded to.
    """
    from app.db.customer_context import canonical_phone_number

    try:
        canonical_phone = canonical_phone_number(phone_number)
    except ValueError:
        canonical_phone = phone_number.strip()
    tenant = str(company_id)
    from contextlib import nullcontext

    lock_context = (
        _memory.session_lock(canonical_phone, tenant)
        if hasattr(_memory, "session_lock")
        else nullcontext()
    )
    with lock_context:
        state = _memory.get(canonical_phone, tenant)
        state.history.append({"role": "ai", "content": message})
        _memory.save(state)


class BookingStatusNoticeRequest(BaseModel):
    company_id: int
    service_type: str
    booking_id: int
    old_status: str
    new_status: str


@app.post("/documents/booking-status-notice", dependencies=[Depends(_require_internal_key)])
def documents_booking_status_notice(request: BookingStatusNoticeRequest):
    """Called by the staff dashboard right after a booking's status is
    PATCHed (see backend/src/routes/bookings.js) — reminds the
    customer (Scheduled->Pending), asks about a no-show (Pending->No Show),
    or invites pickup (Pending->Done). No-op for any other transition."""
    notice = build_booking_status_notice(
        request.company_id, request.service_type, request.booking_id, request.old_status, request.new_status
    )
    if notice is None:
        return {"status": "no_notice_needed"}
    _seed_notice_into_history(request.company_id, notice["phone_number"], notice["message"])
    send_result = send_whatsapp_text(notice["phone_number"], notice["message"])
    return {"status": "success", "message": notice["message"], "send_result": send_result}


class RedemptionNoticeRequest(BaseModel):
    company_id: int
    redemption_id: int
    status: str


@app.post("/documents/redemption-notice", dependencies=[Depends(_require_internal_key)])
def documents_redemption_notice(request: RedemptionNoticeRequest):
    """Called by the staff dashboard right after a redemption is
    Approved/Rejected (see backend/src/routes/redemptions.js)."""
    notice = build_redemption_decision_notice(request.company_id, request.redemption_id, request.status)
    if notice is None:
        return {"status": "no_notice_needed"}
    _seed_notice_into_history(request.company_id, notice["phone_number"], notice["message"])
    send_result = send_whatsapp_text(notice["phone_number"], notice["message"])
    return {"status": "success", "message": notice["message"], "send_result": send_result}


class RefundNoticeRequest(BaseModel):
    company_id: int
    payment_id: int


@app.post("/documents/refund-notice", dependencies=[Depends(_require_internal_key)])
def documents_refund_notice(request: RefundNoticeRequest):
    """Called by the staff dashboard right after a payment is refunded (see
    backend/src/routes/payments.js /:id/refund)."""
    notice = build_payment_refund_notice(request.company_id, request.payment_id)
    if notice is None:
        return {"status": "no_notice_needed"}
    _seed_notice_into_history(request.company_id, notice["phone_number"], notice["message"])
    send_result = send_whatsapp_text(notice["phone_number"], notice["message"])
    return {"status": "success", "message": notice["message"], "send_result": send_result}


_SAMPLE_BOOKING = {
    "booking_id": 100001,
    "service_type": "BOARDING",
    "package_name": "Mars Room",
    "pet_name": "Milo",
    "pet_id": None,
    "staff_name": "Sarah Wong",
    "check_in_date": "2026-08-20",
    "check_in_time": "10:30:00",
    "check_out_date": "2026-08-22",
    "check_out_time": "10:30:00",
    "total_price": 156,
    "booking_status": "Pending",
    "created_date": "2026-08-01",
}
_SAMPLE_PAYMENT = {
    "payment_id": 100001,
    "service": "Mars Room",
    "base_price": 176,
    "add_ons": "",
    "final_amount": 156,
    "payment_method": "Cash",
    "date": "2026-08-01",
    "status": "Paid",
}
_SAMPLE_LOYALTY = {"points_balance": 307, "tier": "Silver"}
_SAMPLE_REDEMPTION = {"points_spent": 200, "reward_name": "RM20 Voucher"}


@app.get("/documents/preview/booking-confirmation")
def preview_booking_confirmation():
    """
    Renders the booking-confirmation template with the CURRENT company's
    real logo/name/address (get_billing_profile) but sample booking data —
    lets staff see the actual template design without needing a real
    booking. Returns the raw PDF for the browser to render inline.
    """
    company_id = get_relational_company_id()
    profile = get_billing_profile(company_id)
    pdf_bytes = build_booking_confirmation_pdf(profile, _SAMPLE_BOOKING, "Sample Customer", _SAMPLE_LOYALTY)
    return Response(content=pdf_bytes, media_type="application/pdf")


@app.get("/documents/preview/invoice")
def preview_invoice():
    """Same idea as preview_booking_confirmation, for the invoice template."""
    company_id = get_relational_company_id()
    profile = get_billing_profile(company_id)
    pdf_bytes = build_invoice_pdf(
        profile, _SAMPLE_PAYMENT, _SAMPLE_BOOKING, "Sample Customer", _SAMPLE_LOYALTY, _SAMPLE_REDEMPTION
    )
    return Response(content=pdf_bytes, media_type="application/pdf")
