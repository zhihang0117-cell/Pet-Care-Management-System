"""Relational provider factory — RELATIONAL_PROVIDER=mock|supabase."""

from __future__ import annotations

import os

from dotenv import load_dotenv

from relational_repository import RelationalRepository

_repository: RelationalRepository | None = None


def get_relational_provider() -> str:
    load_dotenv(override=not os.getenv("_EVAL_OVERRIDE_ACTIVE"))
    explicit = os.getenv("RELATIONAL_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    return os.getenv("DATABASE_PROVIDER", "mock").strip().lower()


def get_relational_repository(*, force_new: bool = False) -> RelationalRepository:
    global _repository
    if _repository is not None and not force_new:
        return _repository
    provider = get_relational_provider()
    if provider == "supabase":
        from supabase_relational_repository import SupabaseRelationalRepository

        _repository = SupabaseRelationalRepository()
    else:
        from mock_relational_repository import MockRelationalRepository

        _repository = MockRelationalRepository()
    return _repository


def reset_relational_repository() -> None:
    global _repository
    _repository = None
