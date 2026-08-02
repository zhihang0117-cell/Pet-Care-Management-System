"""
Upload generated PDFs (booking confirmations, invoices) to Supabase Storage.

Unlike business-assets (logos — public, per backend/sql/
enquiry_refund_logo_migration.sql), these contain a real customer's booking/
payment details, so the bucket is PRIVATE and callers get back a short-lived
signed URL, not a public one.
"""

from __future__ import annotations

import logging

from app.db.supabase_client import get_supabase_client

CUSTOMER_DOCUMENTS_BUCKET = "customer-documents"
SIGNED_URL_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days — long enough for a customer to open a WhatsApp link late


def _ensure_bucket(client) -> None:
    try:
        buckets = client.storage.list_buckets()
        if any(getattr(b, "name", None) == CUSTOMER_DOCUMENTS_BUCKET or (isinstance(b, dict) and b.get("name") == CUSTOMER_DOCUMENTS_BUCKET) for b in buckets):
            return
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not list PDF storage buckets: %s", exc)
    try:
        client.storage.create_bucket(
            CUSTOMER_DOCUMENTS_BUCKET,
            options={"public": False, "file_size_limit": 10 * 1024 * 1024, "allowed_mime_types": ["application/pdf"]},
        )
    except Exception as exc:
        # Race with another process creating it, or already exists under a
        # client version whose list_buckets() shape didn't match above —
        # either way, the upload below is the real source of truth.
        logging.getLogger(__name__).warning("Could not create PDF storage bucket (upload will verify): %s", exc)


def upload_customer_document(company_id: int, category: str, filename: str, pdf_bytes: bytes) -> str:
    """
    category: "booking-confirmations" or "invoices". Returns a signed URL
    valid for SIGNED_URL_TTL_SECONDS. Overwrites (upsert) so regenerating
    the same document (e.g. a reschedule) replaces the old file at the same
    path instead of accumulating stale copies.
    """
    client = get_supabase_client()
    _ensure_bucket(client)
    object_path = f"{company_id}/{category}/{filename}"
    client.storage.from_(CUSTOMER_DOCUMENTS_BUCKET).upload(
        object_path,
        pdf_bytes,
        {"content-type": "application/pdf", "upsert": "true"},
    )
    signed = client.storage.from_(CUSTOMER_DOCUMENTS_BUCKET).create_signed_url(
        object_path, SIGNED_URL_TTL_SECONDS
    )
    document_url = signed.get("signedURL") or signed.get("signedUrl") or ""
    if not document_url:
        raise RuntimeError("Supabase Storage did not return a signed document URL")
    return document_url
