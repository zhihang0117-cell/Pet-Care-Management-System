"""Download business documents from Supabase Storage for production processing."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.services.supabase_store import get_supabase_client


def download_storage_docx(storage_bucket: str, storage_path: str) -> Path:
    bucket = str(storage_bucket or "").strip()
    path = str(storage_path or "").strip().lstrip("/")
    if not bucket:
        raise ValueError("storage_bucket is required")
    if not path:
        raise ValueError("storage_path is required")
    if not path.lower().endswith(".docx"):
        raise ValueError("storage_path must point to a .docx file")

    client = get_supabase_client()
    payload = client.storage.from_(bucket).download(path)
    if not payload:
        raise ValueError(f"Could not download object: {bucket}/{path}")

    suffix = Path(path).suffix or ".docx"
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temp_file.write(payload)
    temp_file.flush()
    temp_file.close()
    return Path(temp_file.name)
