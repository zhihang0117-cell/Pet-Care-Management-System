"""
Temporary structured logging for /chat session-pollution investigation.

One identifiable log block per request. Never logs API keys or credentials.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("pawfect.chat")


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def _session_snapshot(session) -> dict[str, Any]:
    if session is None:
        return {}
    if hasattr(session, "to_dict"):
        return dict(session.to_dict())
    return {
        key: getattr(session, key, None)
        for key in (
            "phone_number",
            "customer_id",
            "customer_name",
            "existing_customer",
            "pending_action",
            "sub_flow",
            "last_service_type",
            "pet_name",
            "pet_type",
            "pet_size",
            "pet_height",
            "service_package",
            "selected_package",
            "selected_addons",
            "add_on_service",
            "add_on_type",
            "price_context",
            "booking_creation_flow",
            "missing_fields",
            "booking_missing_snapshot",
            "collected_entities",
            "draft_booking_payload",
            "greeted_this_session",
        )
    }


def _intent_snapshot(intent_json: dict | None) -> dict[str, Any]:
    intent_json = dict(intent_json or {})
    entities = dict(intent_json.get("entities") or {})
    return {
        "main_intent": intent_json.get("main_intent"),
        "scenario_intent": intent_json.get("scenario_intent"),
        "service_type": intent_json.get("service_type"),
        "sub_flow": intent_json.get("sub_flow"),
        "next_action": intent_json.get("next_action"),
        "database_action": intent_json.get("database_action"),
        "retrieval_needed": intent_json.get("retrieval_needed"),
        "retrieval_source": list(intent_json.get("retrieval_source") or []),
        "standalone_service_info": intent_json.get("standalone_service_info"),
        "booking_supporting_info_needed": intent_json.get("booking_supporting_info_needed"),
        "booking_supporting_service_info": intent_json.get("booking_supporting_service_info"),
        "supporting_info_type": intent_json.get("supporting_info_type"),
        "price_enquiry_incomplete": intent_json.get("price_enquiry_incomplete"),
        "price_enquiry_complete": intent_json.get("price_enquiry_complete"),
        "ask_package_after_info": intent_json.get("ask_package_after_info"),
        "missing_information": list(intent_json.get("missing_information") or []),
        "entities": entities,
        "confidence": intent_json.get("confidence"),
    }


def _selected_package(session, intent_json: dict | None) -> str:
    intent_json = intent_json or {}
    entities = dict(intent_json.get("entities") or {})
    for source in (
        entities.get("selected_package"),
        getattr(session, "selected_package", "") if session is not None else "",
        (getattr(session, "collected_entities", {}) or {}).get("selected_package")
        if session is not None
        else "",
        entities.get("service_package"),
        getattr(session, "service_package", "") if session is not None else "",
    ):
        value = str(source or "").strip()
        if value:
            return value
    return ""


def _selected_addons(session, intent_json: dict | None) -> list[str]:
    if session is not None:
        raw = getattr(session, "selected_addons", None)
        if isinstance(raw, list) and raw:
            return [str(item).strip() for item in raw if str(item).strip()]
    intent_json = intent_json or {}
    entities = dict(intent_json.get("entities") or {})
    raw_addons = entities.get("selected_addons")
    if isinstance(raw_addons, list):
        return [str(item).strip() for item in raw_addons if str(item).strip()]
    return []


def _requested_price_item(session, intent_json: dict | None) -> str:
    intent_json = intent_json or {}
    entities = dict(intent_json.get("entities") or {})
    parts: list[str] = []

    package = _selected_package(session, intent_json)
    if package:
        parts.append(f"package={package}")

    for key, label in (
        ("add_on_service", "add_on"),
        ("add_on_type", "add_on_type"),
    ):
        value = str(
            entities.get(key)
            or (getattr(session, key, "") if session is not None else "")
            or (getattr(session, "collected_entities", {}) or {}).get(key)
            if session is not None
            else ""
        ).strip()
        if value:
            parts.append(f"{label}={value}")

    price_context = str(
        entities.get("price_context")
        or (getattr(session, "price_context", "") if session is not None else "")
        or intent_json.get("supporting_info_type")
        or ""
    ).strip()
    if price_context:
        parts.append(f"price_context={price_context}")

    supporting = str(intent_json.get("supporting_info_type") or "").strip()
    if supporting and supporting not in price_context:
        parts.append(f"supporting_info_type={supporting}")

    return "; ".join(parts)


def _chunk_summaries(rag_context: list | None) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for chunk in list(rag_context or []):
        if not isinstance(chunk, dict):
            continue
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        summaries.append(
            {
                "chunk_id": chunk.get("chunk_id"),
                "main_header": metadata.get("main_header") or chunk.get("main_header"),
                "sub_header": metadata.get("sub_header") or chunk.get("sub_header"),
                "section_title": metadata.get("section_title") or chunk.get("section_title"),
                "service_type": metadata.get("service_type") or chunk.get("service_type"),
            }
        )
    return summaries


def _retrieval_filters(rag_debug: dict | None) -> dict[str, Any]:
    rag_debug = dict(rag_debug or {})
    return {
        "filter_tenant_sent": rag_debug.get("filter_tenant_sent"),
        "filter_company_id_sent": rag_debug.get("filter_company_id_sent"),
        "filter_metadata_sent": rag_debug.get("filter_metadata_sent"),
        "detected_service_type": rag_debug.get("detected_service_type"),
        "required_service_type": rag_debug.get("required_service_type"),
        "retrieval_mode": rag_debug.get("retrieval_mode"),
        "query_rewrite_used": rag_debug.get("query_rewrite_used"),
        "rewritten_query": rag_debug.get("rewritten_query"),
    }


class ChatRequestTrace:
    """Accumulates one request's debug fields; emits a single log block at end."""

    def __init__(self, *, session_key: str, message: str, session_before: dict) -> None:
        self.session_key = session_key
        self.message = message
        self.session_before = session_before
        self.intent_result: dict[str, Any] = {}
        self.session_after_merge: dict[str, Any] = {}
        self.retrieval_query: str = ""
        self.retrieval_filters: dict[str, Any] = {}
        self.retrieved_chunks: list[dict[str, Any]] = []
        self.selected_package: str = ""
        self.selected_addons: list[str] = []
        self.requested_price_item: str = ""
        self.missing_information: list[str] = []
        self.next_action: str = ""
        self.route: str = ""
        self.reply_preview: str = ""

    def log_pre_intent(self) -> None:
        logger.info(
            "\n=== CHAT REQUEST START ===\n"
            "session_key: %s\n"
            "message: %s\n"
            "session_before:\n%s\n"
            "=== CHAT CHECKPOINT: pre_intent ===",
            self.session_key,
            self.message,
            _safe_json(self.session_before),
        )

    def set_post_intent(self, intent_json: dict, session) -> None:
        self.intent_result = _intent_snapshot(intent_json)
        self.session_after_merge = _session_snapshot(session)
        self.missing_information = list(self.intent_result.get("missing_information") or [])
        self.next_action = str(self.intent_result.get("next_action") or "")
        self.selected_package = _selected_package(session, intent_json)
        self.selected_addons = _selected_addons(session, intent_json)
        self.requested_price_item = _requested_price_item(session, intent_json)

    def log_pre_retrieval(self) -> None:
        logger.info(
            "\n=== CHAT CHECKPOINT: pre_retrieval ===\n"
            "session_key: %s\n"
            "intent_result:\n%s\n"
            "session_after_merge:\n%s\n"
            "selected_package: %s\n"
            "selected_addons: %s\n"
            "requested_price_item: %s\n"
            "missing_information: %s\n"
            "next_action: %s",
            self.session_key,
            _safe_json(self.intent_result),
            _safe_json(self.session_after_merge),
            self.selected_package or "(none)",
            _safe_json(self.selected_addons),
            self.requested_price_item or "(none)",
            _safe_json(self.missing_information),
            self.next_action or "(none)",
        )

    def set_retrieval(
        self,
        *,
        retrieval_query: str,
        rag_debug: dict | None,
        rag_context: list | None,
        rewritten_query: str = "",
        query_rewrite_used: bool = False,
    ) -> None:
        self.retrieval_query = str(retrieval_query or "")
        self.retrieval_filters = _retrieval_filters(rag_debug)
        if rewritten_query:
            self.retrieval_filters["rewritten_query"] = rewritten_query
        if query_rewrite_used:
            self.retrieval_filters["query_rewrite_used"] = query_rewrite_used
        self.retrieved_chunks = _chunk_summaries(rag_context)

    def set_post_response(
        self,
        *,
        intent_json: dict,
        session,
        route: str,
        reply: str,
    ) -> None:
        self.route = route
        self.reply_preview = str(reply or "")[:400]
        self.missing_information = list(intent_json.get("missing_information") or [])
        self.next_action = str(intent_json.get("next_action") or "")
        self.selected_package = _selected_package(session, intent_json)
        self.selected_addons = _selected_addons(session, intent_json)
        self.requested_price_item = _requested_price_item(session, intent_json)
        self.session_after_merge = _session_snapshot(session)

    def log_end(self) -> None:
        logger.info(
            "\n=== CHAT REQUEST START ===\n"
            "session_key: %s\n"
            "message: %s\n"
            "session_before:\n%s\n"
            "intent_result:\n%s\n"
            "session_after_merge:\n%s\n"
            "retrieval_query: %s\n"
            "retrieval_filters:\n%s\n"
            "retrieved_chunks:\n%s\n"
            "selected_package: %s\n"
            "selected_addons: %s\n"
            "requested_price_item: %s\n"
            "missing_information: %s\n"
            "next_action: %s\n"
            "route: %s\n"
            "reply_preview: %s\n"
            "=== CHAT REQUEST END ===",
            self.session_key,
            self.message,
            _safe_json(self.session_before),
            _safe_json(self.intent_result),
            _safe_json(self.session_after_merge),
            self.retrieval_query or "(none)",
            _safe_json(self.retrieval_filters),
            _safe_json(self.retrieved_chunks),
            self.selected_package or "(none)",
            _safe_json(self.selected_addons),
            self.requested_price_item or "(none)",
            _safe_json(self.missing_information),
            self.next_action or "(none)",
            self.route or "(none)",
            self.reply_preview or "(none)",
        )


def log_chat_stage(stage: str, **fields: Any) -> None:
    logger.info("chat_%s %s", stage, json.dumps(fields, ensure_ascii=False, default=str))


def log_chat_intent(intent_json: dict | None, *, message: str = "") -> None:
    log_chat_stage("intent", message=message, **_intent_snapshot(intent_json))


def log_chat_session(session) -> None:
    log_chat_stage("session", **_session_snapshot(session))


def log_chat_retrieval(
    *,
    retrieval_query: str = "",
    route: str = "",
    rag_used: bool = False,
    chunk_count: int = 0,
    rag_error: str | None = None,
) -> None:
    log_chat_stage(
        "retrieval",
        route=route,
        retrieval_query=retrieval_query,
        rag_used=rag_used,
        chunk_count=chunk_count,
        rag_error=rag_error or "",
    )


def log_chat_response(*, route: str = "", provider: str = "", reply_preview: str = "") -> None:
    log_chat_stage("response", route=route, provider=provider, reply_preview=reply_preview[:240])
