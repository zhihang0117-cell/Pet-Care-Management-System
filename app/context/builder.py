"""AgentContext — the small, model-facing view (Phase 1 of REFACTOR_PLAN.md).

Today RUNTIME_CONTEXT hands the model most of ConversationState's raw
__dict__ every turn (last_cancelled_booking, last_created_booking,
latest_booking, repeat_booking_template, pending_booking_confirmation,
collected_slots, missing_slots, offered_options, verified_service_options,
verified_availability_slots, pending_actions, known_coupons, resolved_dates,
...) — the model has to figure out which of ~20 fields is actually relevant
this turn. build_agent_context() instead produces one compact object with a
fixed, small shape (company / customer / goal / booking_draft / evidence /
pending_action), matching the design in the user's architecture review.

Not wired into the live request path yet — this is a pure function over an
existing ConversationState + customer dict, callable and testable in
isolation before app/orchestrator.py's RUNTIME_CONTEXT assembly is ever
touched (see REFACTOR_PLAN.md Phase 6 for the actual cutover).
"""

from __future__ import annotations

from typing import Any

from .booking_draft import derive_booking_draft


def build_agent_context(state: Any, customer: dict, company_context: dict) -> dict:
    draft = derive_booking_draft(state)

    pets = customer.get("pets") or []
    goal_type = getattr(state, "active_scenario", None)

    evidence: dict[str, str] = {}
    if getattr(state, "verified_service_options", None):
        evidence["service_option"] = "verified"
    if getattr(state, "verified_availability_slots", None):
        evidence["availability"] = "verified"
    pending_actions = getattr(state, "pending_actions", None) or {}
    evidence["booking_preview"] = "verified" if pending_actions.get("create_booking") else None  # type: ignore[assignment]

    return {
        "company": {
            "name": company_context.get("company_name"),
            "business_date": company_context.get("business_date"),
            "hours": company_context.get("hours"),
        },
        "customer": {
            "found": customer.get("found"),
            "first_name": customer.get("first_name"),
            "pets": [
                {
                    "ref": str(p.get("pet_id")) if p.get("pet_id") is not None else None,
                    "name": p.get("pet_name"),
                    "species": p.get("pet_type"),
                    "size": p.get("pet_size"),
                }
                for p in pets
            ],
        },
        "goal": {
            "type": goal_type,
            "status": getattr(state, "completion_status", None),
        },
        # booking_draft (draft.as_public_dict()) used to be injected here
        # too — removed (item #8 of the 2026-08-10 review): it's a
        # projection off the OLD orchestrator's raw state fields
        # (verified_service_options/verified_availability_slots), its
        # option/slot refs are display-only synthetic ones that can never
        # resolve against EvidenceStore, and SYSTEM_PROMPT_V2 never
        # referenced it at all — dead weight in every turn's context, at
        # best confusing next to the real, resolvable option_ref/slot_ref/
        # preview_ref the model actually gets from Phase 3/4's tools.
        # derive_booking_draft() itself is left in place (still exercised
        # by tests/test_refactor_phase1_context.py, and draft.preview_ref
        # still backs this function's own pending_action fallback below)
        # rather than deleted outright.
        "is_first_message": not getattr(state, "history", None),
        "recent_booking": customer.get("recent_booking"),
        "upcoming_booking": customer.get("upcoming_booking"),
        # Real gap confirmed 2026-08-10: loyalty/member status was only
        # ever visible after an explicit loyalty question or a forced
        # post-booking check — a pre-booking recommendation turn had zero
        # member context even for a real existing member. Hydrated once
        # per session by resolve_identity (app/context/runtime_context.py),
        # same mechanism as recent_booking/upcoming_booking above — None
        # means genuinely not a member (or not yet hydrated); check
        # loyalty_context_status to tell those apart if it matters.
        "loyalty_account": customer.get("loyalty_account"),
        "loyalty_context_status": customer.get("loyalty_context_status"),
        "evidence": {k: v for k, v in evidence.items() if v is not None},
        "pending_action": (
            {"tool": "create_booking", "ref": draft.preview_ref} if draft.preview_ref else None
        ),
        "current_message_datetime": getattr(state, "current_datetime_resolution", None),
    }
