"""BookingWorkingState — persistent "what has the customer actually
selected" memory, separate from ConversationState.active_options/
active_slots ("what did the system just show").

Real gap confirmed live 2026-08-10: a customer picked a real service
option by ordinal ("second one?") with zero date/time ever mentioned, and
the model silently invented a date AND time ("this Friday at 10am") and
went straight to a preview requiring only confirmation — never asking or
recommending. The existing guards (selection_required, active_options
staleness clearing) only ever protected AVAILABILITY choices the model
picked from a shown list; nothing stopped the model from fabricating a
date/time out of thin air when the customer never stated one at all,
because there was no single place tracking "what has actually been
selected for this specific booking" for a hydration/guard layer to check
against.

This does not replace active_options/active_slots (still real: "what was
just shown, so an ordinal/'the cheapest one' pick resolves"). This is the
complementary layer: "of what's been shown or offered, what has the
customer actually committed to". `app/agent/booking_state.py`'s change_*
functions are the only intended way to mutate this — each one applies the
dependency-invalidation rule for its own field (e.g. changing the
selected option invalidates any already-selected slot/preview, since a
different package changes what's available).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BookingWorkingState:
    service_type: str | None = None

    pet_ref: str | None = None

    selected_option_ref: str | None = None
    selected_add_on_refs: list[str] = field(default_factory=list)

    # datetime_ref/check_out_datetime_ref are the freshest ref to use when
    # hydrating a later tool call. They are NOT used for change detection —
    # app.agent.evidence.EvidenceStore.mint() always returns a fresh,
    # unique ref even for identical content (by design — see
    # reference_tools.preview_booking's own docstring on why), so comparing
    # by ref string would treat every single resolve_datetime call as a
    # "new" datetime and spuriously invalidate a still-valid selected slot/
    # preview on every turn. datetime_signature/check_out_datetime_signature
    # (the actual resolved (date, time, period) tuple) is the real content
    # identity used for that comparison instead.
    datetime_ref: str | None = None
    datetime_signature: tuple | None = None
    check_out_datetime_ref: str | None = None
    check_out_datetime_signature: tuple | None = None

    selected_slot_ref: str | None = None

    preferred_staff: str | None = None

    preview_ref: str | None = None

    # Incremented whenever a booking condition affecting availability
    # changes (pet/service/option/datetime/staff — see booking_state.py).
    # Stamped onto every slot check_availability mints; preview_booking
    # refuses a slot_ref whose stamped revision no longer matches the
    # current one (see runtime.py's STALE_SLOT_REF check) — the customer
    # changed something after that slot was checked, so it may no longer
    # be real.
    revision: int = 0
