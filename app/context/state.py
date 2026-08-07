from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ConversationState:
    phone_number: str
    company_id: str
    active_scenario: str | None = None
    service_type: str | None = None
    current_step: str | None = None

    # Agent-facing progress is evidence-driven rather than a rigid state
    # machine. `objective` is a short business goal inferred from the active
    # scenario; verified_facts/recent_tool_evidence are populated only from
    # runtime context or actual tool results. They let the model choose its own
    # next useful action without re-asking for facts or inventing completion.
    objective: str | None = None
    verified_facts: dict[str, Any] = field(default_factory=dict)
    missing_information: list[str] = field(default_factory=list)
    missing_information_by_tool: dict[str, list[str]] = field(default_factory=dict)
    recent_tool_evidence: list[dict[str, Any]] = field(default_factory=list)
    completion_status: str | None = None

    customer_id: int | None = None
    customer_name: str | None = None
    customer_address: str | None = None

    # Cached once a pet is resolved (via find_pet_by_name, or get_pets when the
    # customer has exactly one pet) so later turns never need the model to
    # re-derive/guess it — and never fall back to passing pet_id=None into
    # tools like get_booking_service_options, which broke species filtering.
    pet_id: int | None = None
    pet_type: str | None = None
    pet_name: str | None = None
    pet_size: str | None = None
    pet_breed: str | None = None

    # Full pet roster, cached once per session (first message prefetch, or
    # any get_pets call) as
    # [{"pet_id", "pet_type", "pet_name", "pet_size", "pet_breed"}, ...]. Lets
    # the orchestrator match a pet name mentioned in the customer's own
    # message against real records deterministically — the model has shown
    # it will guess pet_type/pet_id (including outright wrong species)
    # rather than reliably resolving the named pet first.
    known_pets: list[dict] = field(default_factory=list)

    # Profile hydration is request infrastructure, not a greeting step. These
    # statuses distinguish a successfully loaded empty result from data that
    # was never loaded (or whose Supabase read failed), so any later business
    # turn can retry instead of telling the customer "you have no pets" from
    # missing context.
    pets_context_status: str | None = None
    booking_context_status: str | None = None

    # Set whenever cancel_booking succeeds this session — {"booking_id",
    # "service_type", "package_name", "pet_id", "date", "time", "price"}.
    # "Book it again"/"actually I can make it" right after a cancellation
    # means THIS booking, not a historical completed one — get_last_completed_booking
    # only matches booking_date < today, which a just-cancelled FUTURE
    # booking never satisfies, so it always wrongly reports "not found" for
    # this case. Cleared once a new booking is created.
    last_cancelled_booking: dict | None = None

    # Set whenever create_booking succeeds this session — same shape as
    # last_cancelled_booking. Without this, _resolve_identity only attaches
    # "latest_booking" to RUNTIME_CONTEXT on the session's very FIRST
    # message (see its docstring) — every later turn gets no booking
    # context at all unless the model decides, unprompted, to re-call
    # get_latest_booking/get_booking_by_id itself. Confirmed live: the model
    # would ask the customer for booking details again, or claim it had no
    # record, moments after successfully creating that exact booking earlier
    # in the same session. Injected into RUNTIME_CONTEXT every turn instead
    # of only on turn 1. Cleared once a cancellation targets it.
    last_created_booking: dict | None = None

    # Most recent booking fetched from Supabase for this customer. Unlike
    # last_created_booking this also covers a booking that existed before the
    # current chat session. Keep it in state and inject it every turn: the
    # first-turn identity prefetch used to disappear on turn two, which made
    # the model truthfully see no booking context and incorrectly tell the
    # customer that no booking existed even though Supabase had one.
    latest_booking: dict | None = None

    # Exact historical booking selected for a "same as last time" request.
    # This is deliberately separate from latest_booking: the latter may be a
    # newer upcoming booking in another service, while a repeat request must
    # be scoped to the named pet + requested service and then revalidated
    # against today's catalogue before any availability is offered.
    repeat_booking_template: dict | None = None

    # Set whenever cancel_booking/reschedule_booking returns
    # "confirmation_required" — {"tool", "booking_id", "service_type"}.
    # Confirmed live: once the customer replies with just the pet's name to
    # confirm, the model calls the tool again with confirm_pet_name set but
    # WITHOUT re-passing booking_id/service_type — which makes it re-resolve
    # from scratch and hit "ambiguous, multiple active bookings" again even
    # though the specific target was already identified and confirmed one
    # turn ago. Re-inject these two fields from here when that happens.
    # Cleared once the write actually succeeds/fails for real.
    pending_booking_confirmation: dict | None = None

    collected_slots: dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    # Main bookable choices and optional add-ons must never share one ordinal
    # namespace.  "The second one" means the second main service unless the
    # customer explicitly says they are choosing from the add-on list.
    offered_options: list[dict[str, Any]] = field(default_factory=list)
    offered_add_on_options: list[dict[str, Any]] = field(default_factory=list)

    # Server-observed catalogue and availability evidence used to validate a
    # booking write.  These are deliberately separate from offered_options:
    # the latter is UI/conversation memory and may be replaced whenever a new
    # list is shown, while these two collections form the authorization input
    # for create_booking.
    verified_service_options: list[dict[str, Any]] = field(default_factory=list)
    verified_availability_slots: list[dict[str, Any]] = field(default_factory=list)

    # Consequential actions are always previewed on one customer turn and may
    # execute only after an affirmative reply on a later turn. Each entry is
    # {"signature", "args", "preview_turn"}; it is created and checked by the
    # orchestrator, never by the model.
    pending_actions: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Exact DAYCARE duration explicitly supplied by the customer (or carried
    # by a structured catalogue option they selected). Keep it separate from
    # free-form history so a loyalty/policy side trip cannot make the model
    # drop it before check_availability/create_booking. This is never inferred
    # from an open-ended tier such as "Above 3 Hours".
    daycare_duration_minutes: int | None = None

    # Explicit preference is different from "a loyalty tool happened". This
    # allows a customer who already said "no voucher / just book it" to move on
    # without a forced extra turn, while still preventing the model from
    # silently inventing consent to redeem or enrol.
    loyalty_decision: str | None = None

    # check_coupon_eligibility's real eligible_coupons this session, as
    # [{"coupon_id", "reward_name", "discount_value"}, ...]. redeem_reward's
    # coupon_id has been observed not matching the specific voucher the
    # customer actually asked for by name/amount (confirmed live: customer
    # asked for the "RM20 voucher", model told them it submitted that, but
    # the real tool call used the RM10 Voucher's coupon_id instead) — this
    # is what that mismatch gets checked against.
    known_coupons: list[dict] = field(default_factory=list)

    # The payment_id of the most recent successful create_booking this
    # session. redeem_reward's own docstring says its payment_id must be
    # "the real payment_id from create_booking's result... never guessed" —
    # confirmed live that the model called redeem_reward with payment_id=1
    # (a fabricated placeholder) in the very same turn create_booking had
    # already returned the real one (636) right there in its own result.
    # Cleared once redeem_reward actually succeeds against it.
    last_created_payment_id: int | None = None

    # Every date resolve_datetime has actually returned this session (as
    # ISO strings) — check_availability/create_booking/reschedule_booking
    # reject any date argument not in this list, so a self-computed/guessed
    # date (confirmed to happen live, repeatedly) can never reach a write.
    resolved_dates: list[str] = field(default_factory=list)

    # Deterministic resolution of a relative date/time expression in the
    # current message. This is refreshed every turn and exposed in
    # RUNTIME_CONTEXT so the model never has to calculate "next Tuesday".
    current_datetime_resolution: dict[str, Any] | None = None

    # Real prior turns as [{"role": "human"|"ai", "content": ...}, ...], fed
    # back as proper conversation messages (not crammed into a JSON blob) so
    # the model sees them as history, not as data to reinterpret/re-answer.
    history: list[dict[str, str]] = field(default_factory=list)

    # Set after the last successful mutating tool call this session (only
    # create_booking/redeem_reward — the two that INSERT a new row rather
    # than idempotently update one). {"tool", "signature", "result"}.
    # Confirmed live: the customer's second "yes"/"confirm" after a mutation
    # already succeeded sometimes makes the model call the SAME tool again
    # with identical args — a real duplicate booking, or (worse) a real
    # double loyalty-point deduction. If the next call to one of these tools
    # has the exact same args as this, _run_tool returns the cached result
    # instead of re-executing. Cleared (set to None) on failure, and
    # naturally stops matching once any argument actually changes.
    last_mutation: dict | None = None

    # Incremented once at the start of every /chat call (one customer
    # message = one turn). Used to enforce that a "confirmation" tool call
    # actually happened on a LATER turn than the one that first asked for
    # it — a same-turn retry doesn't count, which is what closes the
    # "model fills in the confirmation itself without truly waiting for the
    # customer's next message" loophole (confirmed live: this happened for
    # both the loyalty upsell and register_loyalty_member's consent check).
    turn_counter: int = 0

    # Turn number on which a successful loyalty lookup/preview was actually
    # surfaced in the customer-facing reply. Merely calling a loyalty tool is
    # not enough: create_booking is allowed only after the customer really saw
    # the offer and a full turn boundary passed.
    loyalty_offer_shown_turn: int | None = None

    # Customer's stated staff preference for the CURRENT booking flow, once
    # they've named one — reused if a later create_booking call in the same
    # flow omits it (the model has been observed carrying it in some turns
    # but dropping it in others for the same booking). Cleared whenever a
    # booking completes or the flow is abandoned so it never leaks into an
    # unrelated later booking. Recoverable create failures retain it;
    # completing or abandoning that flow clears it deterministically.
    preferred_staff: str | None = None
