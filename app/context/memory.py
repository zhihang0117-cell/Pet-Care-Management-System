import time
import threading
from contextlib import contextmanager

from app.context.state import ConversationState

SESSION_TTL_SECONDS = 15 * 60  # no activity for this long -> session resets


class ConversationMemory:
    """
    In-memory per-phone-number conversation state. Swap for Redis/DB in production.

    Sessions expire after SESSION_TTL_SECONDS of inactivity: if a customer's
    next message arrives after that gap, they get a fresh ConversationState
    instead of the stale one (avoids resuming a scenario/state the customer
    has long since walked away from).
    """

    def __init__(self, ttl_seconds: float = SESSION_TTL_SECONDS):
        self._store: dict[str, ConversationState] = {}
        self._last_seen: dict[str, float] = {}
        self._ttl_seconds = ttl_seconds
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._active_lock_counts: dict[str, int] = {}

    @staticmethod
    def _key(phone_number: str, company_id: str) -> str:
        # Scoped by company_id, not just phone_number: this deployment only
        # ever runs with one company_id (see get_relational_company_id), so
        # this couldn't be observed yet, but the system prompt/company
        # config is explicitly designed for "multi-company pet-care
        # businesses" — if this backend is ever shared across more than one
        # company (e.g. per-request company_id from a webhook), the same
        # physical phone number messaging two different companies would
        # otherwise get back the SAME cached state (customer_id, pets,
        # active booking flow) from whichever company it talked to first.
        return f"{company_id}::{phone_number}"

    def get(self, phone_number: str, company_id: str) -> ConversationState:
        key = self._key(phone_number, company_id)
        now = time.monotonic()
        with self._locks_guard:
            self._sweep_expired_locked(now)
            last_seen = self._last_seen.get(key)
            expired = last_seen is not None and (now - last_seen) > self._ttl_seconds
            if key not in self._store or expired:
                self._store[key] = ConversationState(
                    phone_number=phone_number, company_id=company_id
                )
            self._last_seen[key] = now
            return self._store[key]

    def _sweep_expired_locked(self, now: float) -> None:
        """Remove inactive session data and its per-session lock.

        Caller must hold ``_locks_guard``. Active lock users are excluded so
        a concurrent request can never receive a second lock for the same
        session while the first request is still running.
        """
        expired = [
            key
            for key, last_seen in self._last_seen.items()
            if now - last_seen > self._ttl_seconds
            and self._active_lock_counts.get(key, 0) == 0
        ]
        for key in expired:
            self._store.pop(key, None)
            self._last_seen.pop(key, None)
            self._locks.pop(key, None)
            self._active_lock_counts.pop(key, None)

    @contextmanager
    def session_lock(self, phone_number: str, company_id: str):
        """Serialize simultaneous messages for one customer in this process."""
        key = self._key(phone_number, company_id)
        with self._locks_guard:
            self._sweep_expired_locked(time.monotonic())
            lock = self._locks.setdefault(key, threading.RLock())
            self._last_seen.setdefault(key, time.monotonic())
            self._active_lock_counts[key] = self._active_lock_counts.get(key, 0) + 1
        try:
            with lock:
                yield
        finally:
            with self._locks_guard:
                remaining = self._active_lock_counts.get(key, 1) - 1
                if remaining > 0:
                    self._active_lock_counts[key] = remaining
                else:
                    self._active_lock_counts.pop(key, None)

    def save(self, state: ConversationState) -> None:
        key = self._key(state.phone_number, state.company_id)
        now = time.monotonic()
        with self._locks_guard:
            self._sweep_expired_locked(now)
            self._store[key] = state
            self._last_seen[key] = now

    def clear(self, phone_number: str, company_id: str | None = None) -> bool:
        """Clears the session for phone_number. If company_id is omitted
        (the existing /debug/clear-session call site never had it to give),
        clears that phone_number across every company rather than silently
        no-op'ing or guessing — this is a manual test/debug action, not a
        security-sensitive path."""
        with self._locks_guard:
            if company_id is not None:
                keys = [self._key(phone_number, company_id)]
            else:
                suffix = f"::{phone_number}"
                keys = [key for key in self._store if key.endswith(suffix)]
            removed = False
            for key in keys:
                removed = self._store.pop(key, None) is not None or removed
                self._last_seen.pop(key, None)
                if self._active_lock_counts.get(key, 0) == 0:
                    self._locks.pop(key, None)
                    self._active_lock_counts.pop(key, None)
            return removed
