"""
Sending a generated document/notice to the customer over WhatsApp.

Three provider modes are implemented:

- (unset): "not_configured" — honest no-op, nothing is sent anywhere.
- WHATSAPP_PROVIDER=console: a TEST STAND-IN, not a real channel. It writes
  into an in-memory per-phone "outbox" (see OUTBOX below) that
  main.py's /debug/outbox endpoint and frontend/eval_console.html read from
  — lets you exercise the entire close-the-loop pipeline (booking confirmed
  -> PDF generated -> "sent") end to end without needing real WhatsApp
  credentials. It is NOT a substitute for a real integration and must not
  be enabled outside local testing.

- WHATSAPP_PROVIDER=meta_cloud_api: sends text/documents through Meta's
  /{phone-number-id}/messages Graph endpoint using WHATSAPP_ACCESS_TOKEN and
  WHATSAPP_PHONE_NUMBER_ID.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict

import requests

# In-memory only (like ConversationMemory) — resets on server restart, and
# only meaningful with WHATSAPP_PROVIDER=console. Keyed by phone_number,
# newest last. Capped per phone so a long test session doesn't grow forever.
OUTBOX: dict[str, list[dict]] = defaultdict(list)
_MAX_OUTBOX_PER_PHONE = 50


def _record_outbox(phone_number: str, entry: dict) -> None:
    entries = OUTBOX[phone_number]
    entries.append({**entry, "timestamp": time.time()})
    if len(entries) > _MAX_OUTBOX_PER_PHONE:
        del entries[: len(entries) - _MAX_OUTBOX_PER_PHONE]


def get_outbox(phone_number: str) -> list[dict]:
    return list(OUTBOX.get(phone_number, []))


def clear_outbox(phone_number: str) -> None:
    OUTBOX.pop(phone_number, None)


def _meta_config() -> tuple[str, str] | None:
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    if not token or not phone_number_id:
        return None
    return token, phone_number_id


def _send_meta(phone_number: str, payload: dict) -> dict:
    config = _meta_config()
    if config is None:
        return {
            "status": "not_configured",
            "message": "Meta WhatsApp credentials are incomplete.",
            "phone_number": phone_number,
        }
    token, phone_number_id = config
    version = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v23.0").strip() or "v23.0"
    base_url = os.getenv("WHATSAPP_GRAPH_API_BASE_URL", "https://graph.facebook.com").rstrip("/")
    recipient = "".join(ch for ch in str(phone_number or "") if ch.isdigit())
    body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": recipient, **payload}
    try:
        response = requests.post(
            f"{base_url}/{version}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        response_payload = response.json() if response.content else {}
        response.raise_for_status()
        message_ids = [item.get("id") for item in response_payload.get("messages", []) if item.get("id")]
        return {
            "status": "sent",
            "provider": "meta_cloud_api",
            "phone_number": phone_number,
            "message_ids": message_ids,
        }
    except Exception as exc:
        detail = ""
        if "response_payload" in locals():
            detail = str(response_payload.get("error", {}).get("message") or "")
        return {
            "status": "error",
            "provider": "meta_cloud_api",
            "phone_number": phone_number,
            "error": detail or str(exc),
        }


def send_whatsapp_document(phone_number: str, document_url: str, caption: str) -> dict:
    provider = os.getenv("WHATSAPP_PROVIDER", "").strip().lower()
    if provider == "console":
        _record_outbox(phone_number, {"kind": "document", "document_url": document_url, "caption": caption})
        return {
            "status": "sent_console",
            "message": "Delivered to the local test outbox (WHATSAPP_PROVIDER=console) — not a real WhatsApp send.",
            "phone_number": phone_number,
            "document_url": document_url,
        }
    if not provider:
        return {
            "status": "not_configured",
            "message": (
                "No WHATSAPP_PROVIDER configured — the document was generated and "
                "stored, but nothing was actually sent over WhatsApp."
            ),
            "phone_number": phone_number,
            "document_url": document_url,
        }
    if provider == "meta_cloud_api":
        filename = document_url.split("?", 1)[0].rsplit("/", 1)[-1] or "pawfect-document.pdf"
        return _send_meta(
            phone_number,
            {
                "type": "document",
                "document": {"link": document_url, "caption": caption, "filename": filename},
            },
        )
    raise NotImplementedError(f"WHATSAPP_PROVIDER={provider!r} has no send implementation yet")


def send_whatsapp_text(phone_number: str, message: str) -> dict:
    """Same idea as send_whatsapp_document, for a plain text notice (booking
    status reminders, redemption/refund decisions) with no PDF attached."""
    provider = os.getenv("WHATSAPP_PROVIDER", "").strip().lower()
    if provider == "console":
        _record_outbox(phone_number, {"kind": "text", "text": message})
        return {
            "status": "sent_console",
            "message": "Delivered to the local test outbox (WHATSAPP_PROVIDER=console) — not a real WhatsApp send.",
            "phone_number": phone_number,
            "text": message,
        }
    if not provider:
        return {
            "status": "not_configured",
            "message": (
                "No WHATSAPP_PROVIDER configured — this notice text was generated "
                "but nothing was actually sent over WhatsApp."
            ),
            "phone_number": phone_number,
            "text": message,
        }
    if provider == "meta_cloud_api":
        return _send_meta(phone_number, {"type": "text", "text": {"preview_url": False, "body": message}})
    raise NotImplementedError(f"WHATSAPP_PROVIDER={provider!r} has no send implementation yet")
