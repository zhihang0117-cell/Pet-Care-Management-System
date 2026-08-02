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
    offered_options: list[dict[str, Any]] = field(default_factory=list)

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

    # Turn number on which a loyalty tool (get_loyalty_balance/
    # check_coupon_eligibility/register_loyalty_member/redeem_reward) was
    # last called. create_booking is only allowed once this is set AND is
    # strictly earlier than the current turn — so the model must let a full
    # turn boundary pass (i.e. actually say something to the customer and
    # get a reply) before the booking can go through, not just call the
    # tool and immediately confirm in the same breath.
    loyalty_offer_shown_turn: int | None = None

    # Turn number on which register_loyalty_member last returned
    # confirmation_required. confirmed=true is only honored by
    # _run_tool if this is set AND strictly earlier than the current turn —
    # same reasoning as above, prevents the model previewing then
    # immediately "confirming" a signup the customer was never actually
    # asked about in a real, separate reply.
    register_preview_turn: int | None = None

    # Customer's stated staff preference for the CURRENT booking flow, once
    # they've named one — reused if a later create_booking call in the same
    # flow omits it (the model has been observed carrying it in some turns
    # but dropping it in others for the same booking). Cleared whenever a
    # booking actually completes/fails so it never leaks into an unrelated
    # later booking.
    preferred_staff: str | None = None
