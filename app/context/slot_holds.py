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

Capacity-aware: a "slot" backed by several interchangeable resources (a
grooming time slot with several staff free at once, a boarding room_type
with capacity > 1) must allow that many concurrent holders, not just one —
otherwise this hold, meant only to stop a genuine race, silently re-imposes
a "one booking per slot" ceiling on a resource that legitimately supports
several bookings at once. Callers that only ever have a single interchangeable
unit (e.g. a specific room_type+date-range hold, which already folds "an
other hold exists" into a capacity check of its own) can omit `capacity` and
get the original one-holder-at-a-time behavior.
"""

from __future__ import annotations

import time

HOLD_TTL_SECONDS = 15 * 60


class SlotHoldRegistry:
    def __init__(self, ttl_seconds: float = HOLD_TTL_SECONDS):
        # key -> {holder_phone_number: expires_at}. Multiple live holders per
        # key are valid whenever the caller says the key's real capacity is
        # more than one (see capacity_available/acquire below).
        self._holds: dict[tuple, dict[str, float]] = {}
        self._ttl_seconds = ttl_seconds

    def _live_holders(self, key: tuple) -> dict[str, float]:
        entry = self._holds.get(key)
        if not entry:
            return {}
        now = time.monotonic()
        live = {holder: expires_at for holder, expires_at in entry.items() if expires_at > now}
        if len(live) != len(entry):
            if live:
                self._holds[key] = live
            else:
                self._holds.pop(key, None)
        return live

    def held_by_other(self, key: tuple, phone_number: str) -> bool:
        """True if at least one OTHER phone number currently holds this key."""
        live = self._live_holders(key)
        return any(holder != phone_number for holder in live)

    def capacity_available(self, key: tuple, phone_number: str, capacity: int = 1) -> bool:
        """
        True when phone_number already holds this key, or fewer than
        `capacity` OTHER phone numbers currently hold it live. `capacity` is
        the real number of interchangeable units this key currently backs
        (e.g. how many staff are free for this exact slot) — supplied by the
        caller, which alone knows that; this registry only tracks who is
        holding, not why.
        """
        live = self._live_holders(key)
        if phone_number in live:
            return True
        others = sum(1 for holder in live if holder != phone_number)
        return others < max(capacity, 0)

    def acquire(self, key: tuple, phone_number: str, capacity: int = 1) -> bool:
        """
        Hold one of `capacity` concurrent units of this key for
        phone_number. Returns False (does not acquire) if capacity is
        already full with other holders; True otherwise (including
        refreshing this same phone's own existing hold).
        """
        if not self.capacity_available(key, phone_number, capacity):
            return False
        live = self._holds.setdefault(key, {})
        live[phone_number] = time.monotonic() + self._ttl_seconds
        return True

    def release(self, key: tuple, phone_number: str | None = None) -> None:
        """Release phone_number's hold on key, or every hold on key when
        phone_number is omitted."""
        if phone_number is None:
            self._holds.pop(key, None)
            return
        live = self._holds.get(key)
        if live and live.pop(phone_number, None) is not None and not live:
            self._holds.pop(key, None)

    def release_all_for(self, phone_number: str, prefix: tuple | None = None) -> None:
        """Release every hold currently owned by phone_number (optionally
        only ones whose key starts with prefix) — called when that customer
        picks a different slot or completes/abandons the booking, so they
        can't accumulate holds on multiple slots indefinitely."""
        for key in list(self._holds.keys()):
            if prefix is not None and key[: len(prefix)] != prefix:
                continue
            self.release(key, phone_number)


SLOT_HOLDS = SlotHoldRegistry()
