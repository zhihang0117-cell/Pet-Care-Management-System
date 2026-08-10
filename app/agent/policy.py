"""The deterministic "Check" layer — Phase 8 of REFACTOR_PLAN.md.

This was never a port of app/agent/guardrails.py's ~1500 lines (deleted in
the 2026-08-10 V1 decommission along with the rest of app/orchestrator.py's
raw-value tool loop). Most of what that file existed to police — a
model-restated pet species/breed/height, a mismatched coupon_id, an
unverified payment_id, a booking payload that doesn't match its own cached
availability check, a stale date carried across turns — is structurally
impossible to violate under the reference-based tool design (Phases
3/4/6/7): the model literally cannot hand a Phase-3+ tool a raw price, pet
identifier, coupon, payment, or date; every one of those is an opaque ref
resolved server-side against real evidence (see app/agent/evidence.py).
This module is the small, genuinely-needed set of checks for the reference-
based tools, organized under the 8 categories used throughout this
session's architecture discussion, so each one has exactly one place to
live instead of being re-derived per tool.

For each category below: either real code (when something was still
missing or duplicated), or a docstring pointing at the existing mechanism
that already satisfies it — written down here so the category has one
place to be audited from, not scattered across reference_tools.py/
evidence.py/runtime.py with no map between them.
"""

from __future__ import annotations

from typing import Any

from app.agent.confirmation import confirmation_intent
from app.agent.evidence import EvidenceStore

# =============================================================================
# 1. IDENTITY SCOPE — a pet/customer/company reference must resolve against
#    THIS session's own real roster, never a bare model-supplied identifier.
#
# Already enforced: app.agent.runtime._resolve_pet_ref validates a pet_ref
# against AgentContext.customer.pets (the exact shape the model was shown,
# keyed "ref"/"name" — not resolve_identity()'s raw "pet_id"/"pet_name"
# dict, a real bug fixed earlier this session where that mismatch rejected
# every genuinely correct pet_ref). company_id/customer_id are never tool
# parameters the model fills in at all — every app.tools.reference_tools
# function takes them as caller-supplied keyword args, injected server-side
# from session state in app.agent.runtime._bind_tools.
# =============================================================================


# =============================================================================
# 2. EVIDENCE SCOPE — every price/date/slot/option/coupon/payment/booking
#    fact the model acts on must trace back to a real tool result via a ref
#    it was actually handed, never something it restates or computes itself.
#
# Already enforced structurally: EvidenceStore.mint()/resolve() (see
# app/agent/evidence.py) is the only way a ref becomes valid, and every
# app.tools.reference_tools function that consumes one calls evidence.resolve()
# and fails closed (UNKNOWN_OR_EXPIRED_*_REF) on a miss — there is no
# fallback path that trusts a model-invented ref or a raw value instead.
# =============================================================================


# =============================================================================
# 3. MUTATION AUTHORITY — a real write (confirm_booking, confirm_membership,
#    a cancel/reschedule's second, executing call) requires the customer's
#    own genuine authorization on THIS turn, not the model's own say-so.
#
# Previously duplicated near-identically as _authorize_confirm_booking and
# _authorize_confirm_membership directly in app/agent/runtime.py. authorize_confirm()
# below is the one implementation both now call. cancel_booking/
# reschedule_booking use a different, already-sufficient mechanism (the
# underlying app.tools.booking_tools business logic itself requires
# confirm_pet_name to equal the pet's real name — stricter than a plain
# "yes", since undoing a booking is harder to recover from than not
# writing one) — but that check never required the pet name to have come
# from a LATER turn than the preview, the same gap confirm_booking had
# before this session's Phase 6 fix. authorize_mutation_target_confirmation()
# closes it the same way.
# =============================================================================


def authorize_confirm(
    ref: str | None, *, evidence: EvidenceStore, state: Any, user_message: str,
    current_ref: str | None = None,
) -> dict | None:
    """Shared gate for any preview_ref-style confirmation (confirm_booking's
    preview_ref, confirm_membership's member_ref, and any future one built
    the same way): the ref must resolve, the customer's CURRENT message
    must be a genuine standalone affirmative (never inferred from an
    ambient "ok" three turns ago), the ref must have been minted on an
    EARLIER turn than this one — a same-turn preview+confirm means the
    model manufactured both halves itself, not the customer — and, when
    the caller passes current_ref (state.pending_preview_ref/
    pending_member_ref), the ref being confirmed must be the CURRENT one:
    if the customer changed their mind and a newer preview replaced it,
    the old ref is stale and must never be confirmable even if it hasn't
    technically expired yet (real gap confirmed 2026-08-10 — nothing
    previously stopped the model from confirming an out-of-date preview
    it held onto from earlier in its own context).

    Returns an envelope-shaped rejection dict, or None to allow the call
    through (the caller — confirm_booking/confirm_membership — still does
    its own UNKNOWN_OR_EXPIRED_*_REF reporting when ref itself is invalid,
    so an unresolvable ref here is deliberately not rejected — that's not
    an authority problem, it's a "nothing to confirm" one)."""
    resolved = evidence.resolve(ref)
    if resolved is None:
        return None
    if confirmation_intent(user_message) != "affirmative":
        return {
            "ok": False,
            "code": "CONFIRMATION_REQUIRED",
            "data": {},
            "needs": ["explicit_affirmative_confirmation"],
            "evidence": None,
            "recoverable": True,
            "handoff": False,
        }
    if resolved.get("minted_turn") == state.turn_counter:
        return {
            "ok": False,
            "code": "CONFIRMATION_MUST_BE_A_LATER_TURN",
            "data": {},
            "needs": [],
            "evidence": None,
            "recoverable": True,
            "handoff": False,
        }
    if current_ref is not None and ref != current_ref:
        return {
            "ok": False,
            "code": "PREVIEW_NOT_CURRENT",
            "data": {},
            "needs": [],
            "evidence": None,
            "recoverable": True,
            "handoff": False,
        }
    return None


def authorize_mutation_target_confirmation(
    args: dict, *, state: Any, user_message: str
) -> dict | None:
    """Same "not the same turn as its own preview" rule as authorize_confirm(),
    for cancel_booking/reschedule_booking's confirm_pet_name step. Only
    fires once a target is already pending (state.pending_mutation_target,
    set by _update_pending_mutation_target) and this call is attempting the
    executing step (confirm_pet_name non-empty) — the first, preview-only
    call (confirm_pet_name empty) is always allowed through, same as
    before."""
    confirm_pet_name = str(args.get("confirm_pet_name") or "").strip()
    if not confirm_pet_name:
        return None
    target = getattr(state, "pending_mutation_target", None)
    if not isinstance(target, dict):
        return None
    if target.get("minted_turn") != state.turn_counter:
        return None
    return {
        "ok": False,
        "code": "CONFIRMATION_MUST_BE_A_LATER_TURN",
        "data": {},
        "needs": [],
        "evidence": None,
        "recoverable": True,
        "handoff": False,
    }


# =============================================================================
# 4. FRESHNESS — a preview, slot, or other cached fact must not outlive the
#    real-world state it described.
#
# Already enforced: EvidenceStore.REF_TTL_SECONDS (15 minutes, matching
# app/db/slot_holds.py's own HOLD_TTL_SECONDS) expires every ref on
# resolve(), not just at read time for a UI — a slot_ref genuinely should
# not outlive the slot hold it corresponds to. confirm_booking additionally
# re-runs the real create_booking business logic at write time regardless
# of how old its preview_ref is, so a real staff/room/pet conflict that
# appeared after the preview was shown is still caught, not just an expired
# ref.
# =============================================================================


# =============================================================================
# 5. TRANSACTION INTEGRITY — a write must be built from evidence that was
#    actually fully verified, never a partially-checked candidate.
#
# Already enforced: preview_booking (app/tools/reference_tools.py) refuses a
# slot_ref whose status is "candidate" (SLOT_NOT_YET_VERIFIED) — the
# candidate/verified distinction added this session for DAYCARE/BOARDING's
# one-side-first availability queries. preview_booking also refuses a
# slot_ref minted for a different option_ref (SLOT_DOES_NOT_MATCH_OPTION).
# =============================================================================


# =============================================================================
# 6. IDEMPOTENCY — a retried or duplicated write must not double-execute.
#
# Already enforced: EvidenceStore.mint() always returns a fresh, unique ref
# regardless of payload content (see its own docstring for the real bug an
# earlier content-addressed version caused), and preview_ref/member_ref are
# used directly as the write's idempotency key — no separate nonce, no
# hash formula.
# =============================================================================


# =============================================================================
# 7. TENANT ISOLATION — one company/customer's session must never see or
#    resolve another's refs, evidence, or facts.
#
# Already enforced: EvidenceStoreRegistry keys one EvidenceStore per
# (company_id, phone_number) — a ref minted in one session cannot resolve
# in another's EvidenceStore instance at all (see
# tests/test_refactor_phase3_reference_tools.py::
# test_evidence_store_refs_never_cross_session_instances). company_id
# itself is never a value the model supplies to a Phase 3+ tool (see
# category 1).
# =============================================================================


# =============================================================================
# 8. HUMAN BOUNDARY — a request that needs a real person must actually
#    reach one, not just get an apologetic reply.
#
# Already enforced: handoff_to_staff (app/tools/reference_tools.py) writes a
# real row via app.db.escalations.save_staff_enquiry — the only thing that
# makes "I've escalated this to our team" true, independent of what the
# model says back to the customer. Choosing WHEN to call it is left to the
# model's own judgment (SYSTEM_PROMPT_V2's STAFF HANDOFF section), same
# "wording/timing free, evidence/action constrained" split as everything
# else — a deterministic low-confidence backstop (mirroring
# app/orchestrator.py's _is_low_confidence_response for the live path) is
# the one piece of this category not yet ported; noted here rather than
# silently skipped.
# =============================================================================
