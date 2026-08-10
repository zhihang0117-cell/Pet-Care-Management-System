"""Phase 8 of REFACTOR_PLAN.md — app/agent/policy.py, the consolidated
"Check" layer. See that module's docstring for why this is NOT a port of
app/agent/guardrails.py: most of what that file polices is now structurally
impossible under the reference-based tool design, and this module is only
the small set of checks that survive that shift.
"""

from app.agent import policy
from app.agent.evidence import EvidenceStore


class _FakeState:
    def __init__(self, turn_counter: int, pending_mutation_target: dict | None = None):
        self.turn_counter = turn_counter
        self.pending_mutation_target = pending_mutation_target


# ---------------------------------------------------------------------------
# authorize_confirm — the shared gate confirm_booking/confirm_membership
# both call (previously duplicated near-identically as
# app.agent.runtime._authorize_confirm_booking/_authorize_confirm_membership).
# ---------------------------------------------------------------------------


def test_authorize_confirm_allows_a_ref_that_does_not_resolve_through():
    """An invalid/expired ref is not an authority problem — that's
    confirm_booking/confirm_membership's own UNKNOWN_OR_EXPIRED_*_REF to
    report, not this gate's job."""
    evidence = EvidenceStore()
    rejection = policy.authorize_confirm(
        "preview_doesnotexist", evidence=evidence, state=_FakeState(turn_counter=4),
        user_message="yes",
    )
    assert rejection is None


def test_authorize_confirm_is_blocked_without_a_genuine_affirmative():
    evidence = EvidenceStore()
    ref = evidence.mint("preview", {"booking_args": {}, "minted_turn": 3})
    rejection = policy.authorize_confirm(
        ref, evidence=evidence, state=_FakeState(turn_counter=4),
        user_message="what about a different room instead?",
    )
    assert rejection is not None
    assert rejection["code"] == "CONFIRMATION_REQUIRED"


def test_authorize_confirm_is_blocked_in_the_same_turn_the_ref_was_minted():
    """The core safety property this gate exists for: a preview/member ref
    minted THIS turn cannot be confirmed THIS turn, even with a perfectly
    affirmative message — confirmation must be a later, genuinely separate
    customer turn, never something the model fabricates for itself within
    one exchange."""
    evidence = EvidenceStore()
    ref = evidence.mint("preview", {"booking_args": {}})
    evidence.resolve(ref)["minted_turn"] = 5

    rejection = policy.authorize_confirm(
        ref, evidence=evidence, state=_FakeState(turn_counter=5), user_message="yes",
    )
    assert rejection is not None
    assert rejection["code"] == "CONFIRMATION_MUST_BE_A_LATER_TURN"


def test_authorize_confirm_is_allowed_on_a_genuinely_later_turn_with_a_real_affirmative():
    evidence = EvidenceStore()
    ref = evidence.mint("preview", {"booking_args": {}})
    evidence.resolve(ref)["minted_turn"] = 5

    rejection = policy.authorize_confirm(
        ref, evidence=evidence, state=_FakeState(turn_counter=6), user_message="yes go ahead",
    )
    assert rejection is None


def test_authorize_confirm_works_identically_for_a_member_ref():
    """Same gate, same behavior — confirm_membership's member_ref needs no
    special-casing."""
    evidence = EvidenceStore()
    ref = evidence.mint("member", {"company_id": 1, "customer_id": 18})
    evidence.resolve(ref)["minted_turn"] = 2

    same_turn = policy.authorize_confirm(
        ref, evidence=evidence, state=_FakeState(turn_counter=2), user_message="yes",
    )
    assert same_turn is not None and same_turn["code"] == "CONFIRMATION_MUST_BE_A_LATER_TURN"

    later_turn = policy.authorize_confirm(
        ref, evidence=evidence, state=_FakeState(turn_counter=3), user_message="yes",
    )
    assert later_turn is None


def test_authorize_confirm_rejects_a_ref_that_is_no_longer_the_current_pending_one():
    """Real gap confirmed 2026-08-10: nothing previously stopped the model
    from confirming an OLD preview_ref it held onto from earlier in its own
    context, even after a newer preview replaced it (or after confirm
    already consumed the pending one). current_ref is
    state.pending_preview_ref/pending_member_ref — the caller's own record
    of what's actually still outstanding."""
    evidence = EvidenceStore()
    stale_ref = evidence.mint("preview", {"booking_args": {"price": 62}})
    evidence.resolve(stale_ref)["minted_turn"] = 3
    current_ref = evidence.mint("preview", {"booking_args": {"price": 80}})
    evidence.resolve(current_ref)["minted_turn"] = 4

    rejection = policy.authorize_confirm(
        stale_ref, evidence=evidence, state=_FakeState(turn_counter=5),
        user_message="yes", current_ref=current_ref,
    )
    assert rejection is not None
    assert rejection["code"] == "PREVIEW_NOT_CURRENT"

    # Confirming the actually-current one still works normally.
    allowed = policy.authorize_confirm(
        current_ref, evidence=evidence, state=_FakeState(turn_counter=5),
        user_message="yes", current_ref=current_ref,
    )
    assert allowed is None


def test_authorize_confirm_skips_the_current_ref_check_when_caller_omits_it():
    """Callers that don't track a "current" ref (or tests exercising the
    gate in isolation) get the pre-existing behavior unchanged — current_ref
    is opt-in, not a new hard requirement."""
    evidence = EvidenceStore()
    ref = evidence.mint("preview", {"booking_args": {}})
    evidence.resolve(ref)["minted_turn"] = 3

    rejection = policy.authorize_confirm(
        ref, evidence=evidence, state=_FakeState(turn_counter=4), user_message="yes",
    )
    assert rejection is None


# ---------------------------------------------------------------------------
# authorize_mutation_target_confirmation — the same "not the model's own
# same-turn say-so" rule, for cancel_booking/reschedule_booking's
# confirm_pet_name step (a gap the underlying confirm_pet_name!=customer's-
# own-name check never closed on its own: the model already knows the
# pet's real name from context and could pass it on the very same turn as
# the preview).
# ---------------------------------------------------------------------------


def test_mutation_target_confirmation_allows_the_first_preview_only_call():
    """confirm_pet_name empty is always the preview step — never blocked."""
    rejection = policy.authorize_mutation_target_confirmation(
        {"booking_ref": "booking_abc"}, state=_FakeState(turn_counter=4), user_message="cancel it",
    )
    assert rejection is None


def test_mutation_target_confirmation_allows_when_nothing_is_pending_yet():
    """No pending_mutation_target at all (e.g. the model somehow supplies
    confirm_pet_name unprompted) — not this gate's concern; the underlying
    tool's own real-name check still applies."""
    rejection = policy.authorize_mutation_target_confirmation(
        {"booking_ref": "booking_abc", "confirm_pet_name": "Milo"},
        state=_FakeState(turn_counter=4), user_message="Milo",
    )
    assert rejection is None


def test_mutation_target_confirmation_blocks_a_same_turn_confirm_pet_name():
    state = _FakeState(
        turn_counter=4,
        pending_mutation_target={"tool": "cancel_booking", "booking_ref": "booking_abc", "minted_turn": 4},
    )
    rejection = policy.authorize_mutation_target_confirmation(
        {"booking_ref": "booking_abc", "confirm_pet_name": "Milo"},
        state=state, user_message="Milo",
    )
    assert rejection is not None
    assert rejection["code"] == "CONFIRMATION_MUST_BE_A_LATER_TURN"


def test_mutation_target_confirmation_allows_a_genuinely_later_turn():
    state = _FakeState(
        turn_counter=5,
        pending_mutation_target={"tool": "cancel_booking", "booking_ref": "booking_abc", "minted_turn": 4},
    )
    rejection = policy.authorize_mutation_target_confirmation(
        {"booking_ref": "booking_abc", "confirm_pet_name": "Milo"},
        state=state, user_message="Milo",
    )
    assert rejection is None
