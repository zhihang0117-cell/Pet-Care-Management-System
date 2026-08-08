import pytest

from app.db.schema_preflight import REQUIRED_POSTGREST_PATHS, verify_required_supabase_schema


class _Response:
    def __init__(self, document):
        self.document = document

    def raise_for_status(self):
        return None


    def json(self):
        return self.document


def _get_for(document, captured):
    def get(url, *, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _Response(document)

    return get


def test_schema_preflight_is_read_only_and_accepts_required_capabilities():
    captured = {}
    verify_required_supabase_schema(
        supabase_url="https://example.supabase.co/",
        service_role_key="secret-test-key",
        get=_get_for(
            {"paths": {path: {"post": {}} for path in REQUIRED_POSTGREST_PATHS}},
            captured,
        ),
    )

    assert captured["url"] == "https://example.supabase.co/rest/v1/"
    assert captured["timeout"] == 10
    assert captured["headers"]["Accept"] == "application/openapi+json"


def test_schema_preflight_lists_missing_capability_and_migration_guide():
    paths = {path: {"post": {}} for path in REQUIRED_POSTGREST_PATHS}
    paths.pop("/rpc/create_booking_idempotent")
    with pytest.raises(RuntimeError, match="create_booking_idempotent.*backend/sql/README.md"):
        verify_required_supabase_schema(
            supabase_url="https://example.supabase.co",
            service_role_key="secret-test-key",
            get=_get_for({"paths": paths}, {}),
        )
