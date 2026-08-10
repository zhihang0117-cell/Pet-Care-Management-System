from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from app.context.booking_working_state import BookingWorkingState

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
    # Turn on which the customer explicitly identified this pet by name.
    # A cached pet from an earlier completed booking is not permission to use
    # that pet for a new multi-pet booking flow.
    pet_selected_turn: int | None = None

    # First customer turn belonging to the current MAKE_BOOKING flow.  Optional
    # choices such as staff and add-ons are authorized only from statements at
    # or after this boundary; an old booking's preference must never leak into
    # a later booking merely because it is still present in chat history.
    booking_flow_started_turn: int | None = None

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
    #
    # "Latest" here means whichever booking has the furthest-forward date
    # overall — a future booking always outranks any past one, so whenever
    # an upcoming booking exists, latest_booking silently hides any recent
    # PAST visit entirely (never surfaced to a greeting). recent_booking and
    # upcoming_booking below exist specifically so a greeting can reference
    # both concepts instead of this one ambiguous blend.
    latest_booking: dict | None = None

    # The customer's most recent COMPLETED visit (booking_date in the past,
    # a real qualifying status) — populated by the same identity prefetch as
    # latest_booking, kept separate so it survives even when the customer
    # also has an upcoming booking (which would otherwise always win
    # latest_booking's single slot).
    recent_booking: dict | None = None

    # The customer's SOONEST still-active future booking (booking_date on or
    # after today, Scheduled/Pending) — the "next appointment" concept a
    # greeting actually wants, as opposed to latest_booking's "furthest away"
    # one when a customer has several upcoming bookings.
    upcoming_booking: dict | None = None

    # Real gap confirmed 2026-08-10: an existing customer's loyalty/member
    # status was only ever looked up (a) when the customer's own message
    # used a loyalty keyword, or (b) forced once after a booking just
    # succeeded — so a recommendation/enquiry turn earlier in the same
    # conversation had zero member context to reason with, even for a
    # real Gold-tier member. Hydrated once per session (like
    # recent_booking/upcoming_booking above), the same real read
    # app.tools.loyalty_tools.get_loyalty_balance/reference_tools.get_loyalty
    # already use — not a new lookup, just surfaced earlier and reused
    # instead of re-queried every turn. None distinguishes "not yet
    # hydrated" from a genuine non-member (loyalty_context_status
    # "available" with loyalty_account None).
    loyalty_account: dict | None = None
    loyalty_context_status: str | None = None

    # Phase 6 of REFACTOR_PLAN.md (app/agent/runtime.py) only — the most
    # recent still-unconfirmed preview_booking() ref for this session.
    # Unused by the live app/orchestrator.py path (which has its own
    # pending_actions mechanism below). Confirmed live: without surfacing
    # this back into AgentContext on the NEXT turn, the model has no way to
    # recall which specific preview_ref a customer's later "yes" refers to
    # (state.history only keeps the final text reply, never tool
    # results/refs) and tried to reconstruct the whole booking from scratch
    # instead of confirming the one it already made. Cleared once
    # confirm_booking actually succeeds against it.
    pending_preview_ref: str | None = None

    # Same reasoning as pending_preview_ref, for preview_membership's
    # member_ref (item #8 of the 2026-08-10 architecture review — membership
    # registration follows the same preview_ref->confirm_ref pattern as
    # booking). Cleared once confirm_membership actually succeeds.
    pending_member_ref: str | None = None

    # Phase 6 of REFACTOR_PLAN.md only — the most recent get_service_options
    # result's options, each still holding its real option_ref, so
    # AgentContext can re-surface them on a LATER turn. Without this, "the
    # second one"/"the cheapest one" stated on a turn after the one that
    # listed them would have no ref to resolve against (state.history keeps
    # only the final text reply) and the model would have to call
    # get_service_options again just to recover a ref it already had.
    # Replaced wholesale on every successful get_service_options call, not
    # merged — an old list is not a partial answer to a new one.
    active_options: list[dict] | None = None

    # Item #9 of the 2026-08-10 architecture review: the same reasoning as
    # active_options above, for check_availability's most recent shown
    # slot list. Without this, a customer picking "12 please" from a
    # just-shown time list on a LATER turn had no slot_ref to resolve
    # against — only the bare re-resolved time, with check_availability
    # needing a full date/option context to re-derive anything at all.
    # Replaced wholesale on every successful check_availability call.
    active_slots: list[dict] | None = None

    # Real gap confirmed live 2026-08-10: a customer picked a real service
    # option by ordinal with zero date/time ever mentioned, and the model
    # silently invented a date AND time and went straight to a preview —
    # active_options/active_slots above only ever remembered what was
    # JUST SHOWN, never what the customer actually SELECTED for the
    # booking currently being built. See app/context/booking_working_state.py
    # for the full rationale; app/agent/booking_state.py's change_* functions
    # are the only intended way to mutate this.
    booking: BookingWorkingState = field(default_factory=BookingWorkingState)

    # Phase 7 of REFACTOR_PLAN.md only — the payment_ref confirm_booking
    # minted for its own real payment_id, kept so redeem_reward on a LATER
    # turn ("actually, can I use my voucher on that") has something real to
    # reference without the model restating a raw payment_id itself.
    last_created_payment_ref: str | None = None

    # Phase 7 of REFACTOR_PLAN.md only — the booking_ref of the most recent
    # unresolved cancel_booking/reschedule_booking preview (each:
    # {"tool", "booking_ref"}). Confirmed live: without this, once
    # cancel_booking previews a specific booking and the customer replies
    # with just the pet's name to confirm, the model has no memory of WHICH
    # booking_ref that preview targeted (state.history keeps only the final
    # text reply) and calls cancel_booking again with confirm_pet_name set
    # but booking_ref empty — which makes it re-resolve from scratch and
    # hit "ambiguous, multiple active bookings" even though the specific
    # target was already identified one turn ago. Cleared once the mutation
    # actually succeeds/fails for real.
    pending_mutation_target: dict | None = None

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
    # execute only after an affirmative reply on the immediately next turn.
    # Each entry is
    # {"signature", "args", "preview_turn", "scenario"}; it is created and checked by the
    # orchestrator, never by the model.
    pending_actions: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Exact DAYCARE duration explicitly supplied by the customer (or carried
    # by a structured catalogue option they selected). Keep it separate from
    # free-form history so a loyalty/policy side trip cannot make the model
    # drop it before check_availability/create_booking. This is never inferred
    # from an open-ended tier such as "Above 3 Hours".
    daycare_duration_minutes: int | None = None

    # Which dimension the customer actually committed to, so a later message
    # that only edits the start/check-in time knows what to hold fixed:
    #   "explicit_duration" — they stated an hour count ("5 hours") — keep
    #       duration_minutes fixed, recompute the checkout/pickup time.
    #   "explicit_range" — they stated both clock times ("12pm to 5pm") —
    #       keep the stated end/pickup time fixed, recompute duration when
    #       the start/check-in time is later revised on its own.
    # None means no DAYCARE duration has been pinned down yet this booking.
    daycare_duration_source: str | None = None

    # The end/pickup clock time (HH:MM) the customer explicitly stated,
    # captured only when daycare_duration_source == "explicit_range". Used
    # to recompute daycare_duration_minutes if the check-in time changes
    # without a new end time or duration also being given.
    daycare_range_end_time: str | None = None

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
    # actually happened on the immediately following turn. A same-turn retry
    # doesn't count, which is what closes the
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
