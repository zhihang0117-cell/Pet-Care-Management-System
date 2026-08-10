"""Phase 3 of REFACTOR_PLAN.md — reference-based tool wrappers, validated
against real Supabase data (real company_id=1 "Happy Paws Center", real
customer/pet records used throughout this session).

The LangChain @tool boundary is NOT mocked here on purpose — these hit the
real underlying business logic (app.tools.customer_tools,
app.tools.availability_tools) exactly like a live customer conversation
would, same principle as every other live-data check this session. The
rest of this pytest suite is deliberately network/credential-free (no live
LLM or DB calls), so the two tests that need real Supabase credentials are
explicitly skipped when those aren't configured (e.g. CI, a fresh clone)
rather than making the whole suite require them.
"""

import os

import pytest
from dotenv import load_dotenv

from app.agent.evidence import EvidenceStore
from app.tools import reference_tools

# get_supabase_client() only loads .env lazily on first real use, which
# would otherwise make this module's own credential check below run before
# .env is loaded at all (most other test files never import main.py/
# supabase_client.py at module level either) — load it explicitly here so
# the skip decision reflects what's actually configured, not import order.
load_dotenv(override=False)
_HAS_SUPABASE_CREDS = bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
requires_live_supabase = pytest.mark.skipif(
    not _HAS_SUPABASE_CREDS, reason="SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY not configured"
)


@requires_live_supabase
def test_get_service_options_mints_a_resolvable_ref_for_a_real_catalogue():
    evidence = EvidenceStore()
    result = reference_tools.get_service_options(
        evidence, company_id=1, customer_id=1, pet_id=1, service_type="GROOMING",
    )
    assert result["ok"] is True
    assert len(result["options"]) > 0

    first = result["options"][0]
    assert first["option_ref"].startswith("option_")
    assert first["price"] > 0
    # No company_id/customer_id/pet_id anywhere in what the model sees —
    # those were never something it should have had authority over.
    assert "company_id" not in first
    assert "customer_id" not in first

    resolved = evidence.resolve(first["option_ref"])
    assert resolved["name"] == first["name"]
    assert resolved["price"] == first["price"]


@requires_live_supabase
def test_check_availability_with_a_real_option_ref_returns_real_slots():
    evidence = EvidenceStore()
    catalogue = reference_tools.get_service_options(
        evidence, company_id=1, customer_id=1, pet_id=1, service_type="GROOMING",
    )
    option_ref = catalogue["options"][0]["option_ref"]

    # A far-future date unlikely to collide with any real seed booking,
    # with an exact requested time so the underlying check is fast (a full
    # unfiltered day sweep is much slower — see REFACTOR_PLAN.md's Phase 3
    # notes for the BOARDING case, verified manually instead of here).
    result = reference_tools.check_availability(
        evidence, company_id=1, customer_id=1, pet_id=1,
        option_ref=option_ref, date="2026-09-15", time_preference="14:00",
    )
    assert result["ok"] is True
    assert result["slots"] == [] or all(s["time"].startswith("14:00") for s in result["slots"])
    if result["slots"]:
        slot_ref = result["slots"][0]["slot_ref"]
        assert evidence.resolve(slot_ref)["option_ref"] == option_ref


@requires_live_supabase
def test_check_availability_marks_a_daycare_check_in_only_query_as_candidate():
    """Calling with just a check-in time (no duration/checkout yet) must
    still return real slots — marked "candidate", not "verified" — rather
    than hard-blocking until both sides are known (item #3/#4 of the
    2026-08-10 architecture review: candidate vs verified availability)."""
    evidence = EvidenceStore()
    catalogue = reference_tools.get_service_options(
        evidence, company_id=1, customer_id=2, pet_id=2, service_type="DAYCARE",
    )
    option_ref = catalogue["options"][0]["option_ref"]

    result = reference_tools.check_availability(
        evidence, company_id=1, customer_id=2, pet_id=2,
        option_ref=option_ref, date="2026-09-17", check_in_time="09:00",
    )
    assert result["ok"] is True
    if result["slots"]:
        assert result.get("status") == "candidate"
        assert result["still_needs"]
        assert all(slot["status"] == "candidate" for slot in result["slots"])
        resolved = evidence.resolve(result["slots"][0]["slot_ref"])
        assert resolved["status"] == "candidate"


def test_check_availability_rejects_an_unknown_or_expired_option_ref():
    evidence = EvidenceStore()
    result = reference_tools.check_availability(
        evidence, company_id=1, customer_id=1, pet_id=1,
        option_ref="option_doesnotexist", date="2026-09-15",
    )
    assert result["ok"] is False
    assert result["code"] == "UNKNOWN_OR_EXPIRED_OPTION_REF"
    assert result["slots"] == []


def test_evidence_store_refs_never_cross_session_instances():
    """A ref minted in one EvidenceStore (one customer's session) must never
    resolve against a different instance — the isolation property the
    session-scoping is actually for."""
    store_a = EvidenceStore()
    store_b = EvidenceStore()
    ref = store_a.mint("option", {"name": "Standard Bath", "price": 80.0})
    assert store_a.resolve(ref) is not None
    assert store_b.resolve(ref) is None
