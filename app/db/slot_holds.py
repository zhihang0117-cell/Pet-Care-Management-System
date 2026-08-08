"""
Short-lived local cache for "this slot is being decided on" holds.

Without this, two customers who both get shown the same open slot can both
be told it's available, and whoever's create_booking happens to reach the
database second either double-books the slot or gets a confusing rejection
after already being told "yes, that's free". A hold makes the slot
provisionally unavailable to everyone else the moment it's actually shown to
one customer as a specific option to confirm, for HOLD_TTL_SECONDS — long
enough to decide, short enough that an abandoned flow doesn't lock the slot
forever. The booking_slot_hold database table is authoritative across workers;
this registry remains the fast local layer and compatibility fallback.
"""

from __future__ import annotations

import time
from threading import RLock

HOLD_TTL_SECONDS = 15 * 60


class SlotHoldRegistry:
    def __init__(self, ttl_seconds: float = HOLD_TTL_SECONDS):
        self._holds: dict[tuple, tuple[str, float]] = {}  # key -> (phone_number, expires_at)
        self._ttl_seconds = ttl_seconds
        self._lock = RLock()

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
        with self._lock:
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
        with self._lock:
            if self.held_by_other(key, phone_number):
                return False
            self._holds[key] = (phone_number, time.monotonic() + self._ttl_seconds)
            return True

    def staff_held_by_other(
        self, company_id: int, staff_id: int, slot_date: str,
        start_minutes: int, end_minutes: int, holder: str,
    ) -> bool:
        """Whether another local hold overlaps this staff interval."""
        with self._lock:
            for key, (held_by, _expires_at) in list(self._holds.items()):
                if not self._is_live(key) or held_by == holder:
                    continue
                if len(key) != 7 or key[0] != company_id or key[1] != "STAFF":
                    continue
                _company, _kind, held_staff, held_date, held_start, held_end, _service = key
                if (
                    int(held_staff) == int(staff_id)
                    and str(held_date) == str(slot_date)
                    and start_minutes < int(held_end)
                    and int(held_start) < end_minutes
                ):
                    return True
            return False

    def release(self, key: tuple) -> None:
        with self._lock:
            self._holds.pop(key, None)

    def release_all_for(self, phone_number: str, prefix: tuple | None = None) -> None:
        """Release every hold currently owned by phone_number (optionally
        only ones whose key starts with prefix) — called when that customer
        picks a different slot or completes/abandons the booking, so they
        can't accumulate holds on multiple slots indefinitely."""
        with self._lock:
            stale = [
                key
                for key, (holder, _exp) in self._holds.items()
                if holder == phone_number and (prefix is None or key[: len(prefix)] == prefix)
            ]
            for key in stale:
                del self._holds[key]

    def count_overlapping_room_holds(
        self,
        company_id: int,
        room_type: str,
        check_in_date: str,
        check_out_date: str,
        holder: str | None = None,
    ) -> int:
        """Count other live room holds whose half-open stays overlap.

        Room holds used to conflict only when their complete date-range tuple
        was identical.  Aug 1-3 and Aug 2-4 therefore both appeared free even
        though they consume the same room on Aug 2.
        """
        with self._lock:
            count = 0
            for key, (held_by, _expires_at) in list(self._holds.items()):
                if not self._is_live(key):
                    continue
                if len(key) != 5 or key[0] != company_id or key[1] != "BOARDING_ROOM":
                    continue
                _company, _kind, held_room, held_in, held_out = key
                if str(held_room).casefold() != str(room_type).casefold():
                    continue
                if holder is not None and held_by == holder:
                    continue
                if str(held_in) < str(check_out_date) and str(check_in_date) < str(held_out):
                    count += 1
            return count


SLOT_HOLDS = SlotHoldRegistry()
