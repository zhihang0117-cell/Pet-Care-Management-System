"""
Relational repository factory.

Trimmed from the original ai-backend relational_provider.py: the
mock/JSON-fixture provider branch belonged to offline testing for the old
intent/router pipeline and is dropped here — this harness always talks to
the real Supabase-backed repository.
"""

from __future__ import annotations

from .relational_repository import RelationalRepository

_repository: RelationalRepository | None = None


def get_relational_repository(*, force_new: bool = False) -> RelationalRepository:
    global _repository
    if _repository is not None and not force_new:
        return _repository

    from .supabase_relational_repository import SupabaseRelationalRepository

    _repository = SupabaseRelationalRepository()
    return _repository


def reset_relational_repository() -> None:
    global _repository
    _repository = None
