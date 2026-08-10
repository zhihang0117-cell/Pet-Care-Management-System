"""Evidence store — Phase 3 of REFACTOR_PLAN.md.

Mints and resolves short-lived reference tokens (option_ref, slot_ref,
preview_ref, ...) so a tool can hand the model an opaque handle instead of
the model having to restate (or worse, recompute) the real facts behind it
— company_id, customer_id, exact price, exact verified time — on every
later call. This is what Phase 3's reference-based tools
(app/tools/reference_tools.py) and Phase 4's preview_booking/confirm_booking
are built on.

Session-scoped by construction: one EvidenceStore instance per
ConversationState (not a module-level singleton), so a ref minted in one
customer's conversation can never resolve in another's. Refs expire —
matching app/db/slot_holds.py's own HOLD_TTL_SECONDS, since a slot_ref
genuinely should not outlive the slot hold it corresponds to.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

REF_TTL_SECONDS = 15 * 60


@dataclass
class EvidenceStore:
    _entries: dict[str, tuple[dict, float]] = field(default_factory=dict)

    def mint(self, kind: str, payload: dict[str, Any]) -> str:
        """Every call mints a fresh, unique ref — simpler than content-
        addressing (no hash of the payload needed to decide the ref), and
        it sidesteps a real bug content-addressing caused earlier: two
        unrelated preview_booking calls that happened to describe identical
        booking facts minted the SAME ref, so their write's idempotency key
        collided and the second one silently replayed the first's cached
        result instead of writing anything. A plain unique ref per mint
        can't collide that way."""
        ref = f"{kind}_{uuid.uuid4().hex[:10]}"
        self._entries[ref] = (dict(payload), time.monotonic() + REF_TTL_SECONDS)
        return ref

    def resolve(self, ref: str | None) -> dict | None:
        if not ref:
            return None
        entry = self._entries.get(ref)
        if entry is None:
            return None
        payload, expires_at = entry
        if time.monotonic() > expires_at:
            del self._entries[ref]
            return None
        return payload

    def is_valid(self, ref: str | None) -> bool:
        return self.resolve(ref) is not None


@dataclass
class EvidenceStoreRegistry:
    """One EvidenceStore per (company_id, phone_number) session, same
    scoping key as app.context.memory.ConversationMemory — Phase 6's
    app/agent/runtime.py reuses that existing session store for
    conversation state/history and pairs it with one of these for refs,
    rather than inventing a second parallel session mechanism."""

    _stores: dict[str, EvidenceStore] = field(default_factory=dict)

    @staticmethod
    def _key(phone_number: str, company_id: str) -> str:
        return f"{company_id}::{phone_number}"

    def get(self, phone_number: str, company_id: str) -> EvidenceStore:
        key = self._key(phone_number, company_id)
        store = self._stores.get(key)
        if store is None:
            store = EvidenceStore()
            self._stores[key] = store
        return store
