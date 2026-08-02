"""
Short-lived, in-process "this slot is being decided on" holds.

Without this, two customers who both get shown the same open slot can both
be told it's available, and whoever's create_booking happens to reach the
database second either double-books the slot or gets a confusing rejection
after already being told "yes, that's free". A hold makes the slot
provisionally unavailable to everyone else the moment it's actually shown to
one customer as a specific option to confirm, for HOLD_TTL_SECONDS — long
enough to decide, short enough that an abandoned flow doesn't lock the slot
forever. Swap for Redis/DB in a multi-process deployment, same as
ConversationMemory.
"""

from __future__ import annotations

import time

HOLD_TTL_SECONDS = 15 * 60


class SlotHoldRegistry:
    def __init__(self, ttl_seconds: float = HOLD_TTL_SECONDS):
        self._holds: dict[tuple, tuple[str, float]] = {}  # key -> (phone_number, expires_at)
        self._ttl_seconds = ttl_seconds

    def _is_live(self, key: tuple) -> bool:
        entry = self._holds.get(key)
        if entry is None:
            return False
        _phone, expires_at = entry
        if expires_at <= time.monotonic():
            del self._holds[key]
            return False
        return True

    def held_by_other(self, key: tuple, phone_number: str) -> bool:
        """True if some OTHER phone number currently holds this slot."""
        if not self._is_live(key):
            return False
        holder, _expires_at = self._holds[key]
        return holder != phone_number

    def acquire(self, key: tuple, phone_number: str) -> bool:
        """
        Hold this slot for phone_number. Returns False (does not acquire) if
        someone else already holds it live; True otherwise (including
        refreshing this same phone's own existing hold).
        """
        if self.held_by_other(key, phone_number):
            return False
        self._holds[key] = (phone_number, time.monotonic() + self._ttl_seconds)
        return True

    def release(self, key: tuple) -> None:
        self._holds.pop(key, None)

    def release_all_for(self, phone_number: str, prefix: tuple | None = None) -> None:
        """Release every hold currently owned by phone_number (optionally
        only ones whose key starts with prefix) — called when that customer
        picks a different slot or completes/abandons the booking, so they
        can't accumulate holds on multiple slots indefinitely."""
        stale = [
            key
            for key, (holder, _exp) in self._holds.items()
            if holder == phone_number and (prefix is None or key[: len(prefix)] == prefix)
        ]
        for key in stale:
            del self._holds[key]


SLOT_HOLDS = SlotHoldRegistry()
