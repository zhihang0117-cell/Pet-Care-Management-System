"""Shared Supabase client for relational database actions."""

from __future__ import annotations

import os

from dotenv import load_dotenv

_client = None


def get_supabase_client():
    """Return a cached Supabase client using service-role credentials."""
    global _client
    if _client is not None:
        return _client

    load_dotenv(override=not os.getenv("_EVAL_OVERRIDE_ACTIVE"))
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set for relational DB access")

    from supabase import create_client

    _client = create_client(supabase_url, supabase_key)
    return _client


def reset_supabase_client() -> None:
    """Clear cached client (mainly for tests)."""
    global _client
    _client = None
