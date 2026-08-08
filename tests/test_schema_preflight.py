import pytest

from app.db.schema_preflight import verify_required_supabase_schema


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


def test_schema_preflight_is_read_only_and_accepts_registration_rpc():
    captured = {}
    verify_required_supabase_schema(
        supabase_url="https://example.supabase.co/",
        service_role_key="secret-test-key",
        get=_get_for(
            {"paths": {"/rpc/register_loyalty_member_atomic": {"post": {}}}},
            captured,
        ),
    )

    assert captured["url"] == "https://example.supabase.co/rest/v1/"
    assert captured["timeout"] == 10
    assert captured["headers"]["Accept"] == "application/openapi+json"


def test_schema_preflight_fails_when_registration_migration_is_missing():
    with pytest.raises(RuntimeError, match="loyalty_member_registration_migration.sql"):
        verify_required_supabase_schema(
            supabase_url="https://example.supabase.co",
            service_role_key="secret-test-key",
            get=_get_for({"paths": {}}, {}),
        )
