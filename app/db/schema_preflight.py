"""Read-only checks for database capabilities required by the deployed app."""

from __future__ import annotations

import os

import requests


REQUIRED_POSTGREST_PATHS = frozenset({"/rpc/register_loyalty_member_atomic"})


def missing_required_paths(openapi_document: dict) -> list[str]:
    """Return required PostgREST endpoints absent from an OpenAPI document."""
    paths = openapi_document.get("paths")
    available = set(paths) if isinstance(paths, dict) else set()
    return sorted(REQUIRED_POSTGREST_PATHS - available)


def verify_required_supabase_schema(
    *,
    supabase_url: str | None = None,
    service_role_key: str | None = None,
    get=requests.get,
) -> None:
    """Fail startup when a required migration is missing from PostgREST.

    Fetching the OpenAPI document is read-only. In particular, this never
    invokes a mutation RPC merely to test whether it exists.
    """
    base_url = (supabase_url or os.getenv("SUPABASE_URL", "")).strip().rstrip("/")
    key = (service_role_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")).strip()
    if not base_url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for schema preflight"
        )

    response = get(
        f"{base_url}/rest/v1/",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/openapi+json",
        },
        timeout=10,
    )
    response.raise_for_status()
    document = response.json()

    missing = missing_required_paths(document)
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            "Required Supabase schema capabilities are missing from the PostgREST "
            f"schema cache: {joined}. Apply backend/sql/loyalty_member_registration_migration.sql "
            "before deploying."
        )
