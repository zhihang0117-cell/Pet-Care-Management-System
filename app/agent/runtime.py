"""New agent core loop — Phase 6 of REFACTOR_PLAN.md.

Reuses, unchanged: app.context.memory.ConversationMemory (session/history
store), app.context.runtime_context.resolve_identity (real customer/pet/
booking prefetch), app.tools.calendar_tools.resolve_datetime (deterministic
per-turn date/time preprocessing) — none of that infrastructure was broken,
so none of it is reinvented. What's new is everything Phases 1-5 built:
AgentContext (small, fixed-shape model context instead of a ~20-field
ConversationState dump), the tool-result envelope, reference-based tools
(option_ref/slot_ref/preview_ref instead of the model stating raw prices/
dates), and the 108-line prompt.

Phase 9's controlled cutover wired handle_turn() into main.py's /chat behind
a v1/v2 flag with app/orchestrator.py kept as a one-click fallback; the
2026-08-10 V1 decommission removed that flag (and app/orchestrator.py
itself) once app/agent/policy.py's and this module's own checks had proven
out live — handle_turn() is now the only thing main.py's /chat ever calls.

Deliberately smaller in scope than the ~3300-line app/orchestrator.py it
replaced: MAKE_BOOKING end to end (catalogue -> availability -> preview ->
confirm), plus Phase 7's policy/booking-history/cancel/reschedule/loyalty/
documents/staff-handoff tools, all reusing the same unmodified underlying
business logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.agent import booking_state, policy
from app.agent.booking_hydration import hydrate_tool_args
from app.agent.envelope import normalize_tool_result
from app.agent.evidence import EvidenceStore
from app.agent.turn_state import capture_explicit_daycare_duration
from app.context.builder import build_agent_context
from app.context.memory import ConversationMemory
from app.context.runtime_context import resolve_identity
from app.prompts.system_v2 import SYSTEM_PROMPT_V2

MAX_AGENT_STEPS = 6

# Which goal a successful call to each tool implies — replaces the model
# having to call a separate update_conversation_state tool to declare its
# own active_scenario (see REFACTOR_PLAN.md's user-review point 18: "the
# orchestrator maintains active_goal from which business tool got called,
# not from the model announcing a state machine transition itself").
_TOOL_GOAL = {
    "get_service_options": "MAKE_BOOKING",
    "check_availability": "MAKE_BOOKING",
    "preview_booking": "MAKE_BOOKING",
    "confirm_booking": "MAKE_BOOKING",
    "cancel_booking": "CANCEL_BOOKING",
    "reschedule_booking": "RESCHEDULE_BOOKING",
    "get_loyalty": "LOYALTY",
    "redeem_reward": "LOYALTY",
    "preview_membership": "LOYALTY",
    "confirm_membership": "LOYALTY",
    "handoff_to_staff": "STAFF_HANDOFF",
}


@dataclass
class RuntimeResult:
    reply: str
    trace: list[dict]
    agent_context: dict


class UnknownPetRefError(Exception):
    pass


def _resolve_pet_ref(pet_ref: str, customer: dict) -> int:
    """The model may only ever name a pet via the ref AgentContext already
    showed it (customer.pets[].ref) — never a raw pet_id it could guess or
    carry over from a different customer's earlier conversation. Mirrors
    Phase 3/4's option_ref/slot_ref validation, applied to identity too."""
    for pet in customer.get("pets") or []:
        if str(pet.get("ref")) == str(pet_ref):
            return int(pet["ref"])
    raise UnknownPetRefError(pet_ref)


def _resolve_datetime_ref(evidence: EvidenceStore, ref: str) -> dict | None:
    """A resolve_datetime call's own output, minted as a ref — see
    resolve_datetime's docstring below for why this exists instead of
    letting the model pass a raw date string to check_availability."""
    return evidence.resolve(ref)


def _bind_tools(
    evidence: EvidenceStore, *, company_id: int, customer_id: int | None, customer: dict,
    pet_name_by_ref: dict[str, str], state: Any, phone_number: str,
):
    from langchain_core.tools import tool

    from app.tools import reference_tools as rt

    @tool
    def create_customer(full_name: str) -> dict:
        """Register a brand-new customer (AgentContext.customer.found is false). Call this once you have their name — phone_number is already known from the session, never ask the customer for it or state it yourself. Item #7 of the 2026-08-10 review: without this, a new customer could never get past greeting. After this succeeds, tell the customer they're registered and ask for their pet's details (name, species, breed, height) — the pet itself needs its own create_pet call, and get_service_options needs a real pet_ref that only becomes available on a LATER turn once identity resolution picks up the new customer."""
        return rt.create_customer(company_id=company_id, full_name=full_name, phone_number=phone_number)

    @tool
    def create_pet(pet_name: str, pet_type: str, height_text: str, breed: str) -> dict:
        """Register a new pet for an ALREADY-registered customer (call create_customer first if AgentContext.customer.found is false, and wait for the customer to exist before calling this — never in the same turn as create_customer). breed is REQUIRED (preserve the customer's own wording — "mixed" or "unknown" only when that's literally what they said, never guessed from species/size/name). height_text is the customer's own wording in whatever unit they used (e.g. "24 inches", "60cm") — never estimate or convert it yourself, ask if you don't have it. pet_type must be "cat" or "dog" for a verified size to be computed (needed for grooming pricing later)."""
        if customer_id is None:
            return {"ok": False, "code": "CUSTOMER_NOT_YET_REGISTERED"}
        return rt.create_pet(
            company_id=company_id, customer_id=customer_id, pet_name=pet_name,
            pet_type=pet_type, height_text=height_text, breed=breed,
        )

    @tool
    def get_service_options(pet_ref: str = "", service_type: str = "") -> dict:
        """Get real bookable service options and prices (GROOMING, DAYCARE, or BOARDING) for one of this customer's own pets (pet_ref from AgentContext.customer.pets). Returns each option with an option_ref — use that ref in later calls, never restate the name/price yourself."""
        try:
            pet_id = _resolve_pet_ref(pet_ref, customer)
        except UnknownPetRefError:
            return {"ok": False, "code": "UNKNOWN_PET_REF"}
        # Defensive, not just cosmetic: the underlying tool this delegates
        # to declares service_type as a Literal["GROOMING","DAYCARE",
        # "BOARDING"] and validates it at its own .invoke() boundary — an
        # empty/invalid value reaching that boundary raises an unhandled
        # pydantic ValidationError instead of a normal tool-level failure
        # (confirmed live 2026-08-10: check_availability crashed the same
        # way on a null datetime_ref, taking the whole turn down with it).
        # Reject here instead, gracefully, before it can reach that layer.
        normalized_service_type = str(service_type or "").strip().upper()
        if normalized_service_type not in {"GROOMING", "DAYCARE", "BOARDING"}:
            return {"ok": False, "code": "INVALID_SERVICE_TYPE"}
        return rt.get_service_options(
            evidence, company_id=company_id, customer_id=customer_id, pet_id=pet_id,
            service_type=normalized_service_type,
        )

    @tool
    def resolve_datetime(text: str) -> dict:
        """Resolve natural-language date/time text (e.g. "this Thursday", "10am") using the business calendar. Returns a datetime_ref — pass THAT to check_availability, never a date string you compute or remember yourself. Call this once per distinct date/time expression the customer states (BOARDING check-in and check-out are two separate expressions needing two separate calls, even when said in the same sentence)."""
        from app.tools.calendar_tools import resolve_datetime as _existing
        raw = _existing.invoke({"text": text})
        if not isinstance(raw, dict) or raw.get("ambiguous"):
            return {"ok": False, "code": "AMBIGUOUS_DATETIME", "data": raw}
        ref = evidence.mint("datetime", raw)
        return {
            "ok": True,
            "datetime_ref": ref,
            "date": raw.get("date"),
            "time": raw.get("time"),
            "period": raw.get("period"),
            "needs_time_selection": raw.get("needs_time_selection"),
        }

    @tool
    def check_availability(
        # Real crash confirmed live 2026-08-10: these three used to have no
        # default, and the model — correctly, per the CUSTOMER CHOICE
        # prompt fix, NOT inventing a date the customer never gave — still
        # called this tool with datetime_ref explicitly null rather than
        # not calling it at all. LangChain/pydantic reject a null against
        # a required `str` parameter before this function's own body ever
        # runs, taking the whole turn down as an unhandled exception
        # instead of a normal, recoverable tool-level rejection. Empty-
        # string defaults let a still-missing ref reach the graceful
        # UNKNOWN_*_REF handling already below instead.
        pet_ref: str = "",
        option_ref: str = "",
        datetime_ref: str = "",
        check_out_datetime_ref: str = "",
        time_preference: str = "",
        duration_minutes: int | None = None,
        preferred_staff: str = "",
        selection_target: str = "CHECK_IN",
        exclude_booking_id: str = "",
    ) -> dict:
        """Check real availability for a previously returned option_ref, using a datetime_ref from resolve_datetime — never a date you state yourself. Returns real slot_ref values; never invent a time. For BOARDING pass check_out_datetime_ref (resolve the customer's check-out expression with its own resolve_datetime call first). For a requested staff member pass preferred_staff. For rescheduling pass exclude_booking_id (the booking being moved), or it will conflict with itself.

        DAYCARE/BOARDING: you may call this with only ONE side known (e.g. just a check-in time, no duration/checkout yet) — present those real times to the customer right away instead of asking for both first. Each slot then carries status "candidate" (not yet fully verified — still_needs names what's missing) or "verified" (fully checked). preview_booking refuses a "candidate" slot_ref; once the customer gives the missing piece, call check_availability again with it to get a "verified" slot_ref before previewing.

        Each slot also carries selection_required: true unless it matches an exact time the customer actually gave you this call — a slot that merely happens to be available is a candidate to present, never one to build a preview from on your own. When the customer hasn't named or delegated an exact time, present the real options (or your recommendation) and get their pick before calling this again with their chosen time; preview_booking refuses a selection_required slot_ref."""
        try:
            pet_id = _resolve_pet_ref(pet_ref, customer)
        except UnknownPetRefError:
            return {"ok": False, "code": "UNKNOWN_PET_REF"}
        dt = _resolve_datetime_ref(evidence, datetime_ref)
        if dt is None:
            return {"ok": False, "code": "UNKNOWN_OR_EXPIRED_DATETIME_REF"}
        date = dt.get("date")
        if not date:
            return {"ok": False, "code": "DATETIME_REF_HAS_NO_DATE"}

        check_out_date = ""
        check_out_time = ""
        if check_out_datetime_ref:
            checkout_dt = _resolve_datetime_ref(evidence, check_out_datetime_ref)
            if checkout_dt is None:
                return {"ok": False, "code": "UNKNOWN_OR_EXPIRED_CHECKOUT_DATETIME_REF"}
            check_out_date = checkout_dt.get("date") or ""
            check_out_time = checkout_dt.get("time") or ""

        # DAYCARE duration authority stays with the deterministic capture
        # (app.agent.tool_loop.capture_explicit_daycare_duration, run once
        # per turn on the customer's real message), never the model's own
        # duration_minutes argument — the exact gap a 2026-08-10 review
        # found: this tool used to trust whatever the model passed, even
        # though the same-file datetime_ref already carried the real,
        # deterministically-resolved value in most cases and the dedicated
        # capture (extracted from the live orchestrator this session) is
        # more robust still — it also handles the "customer only revised
        # the check-in time" recompute case explicit_range/explicit_duration
        # distinguish between.
        resolved_option = evidence.resolve(option_ref)
        if (
            resolved_option
            and str(resolved_option.get("service_type") or "").upper() == "DAYCARE"
            and getattr(state, "daycare_duration_minutes", None)
            and not check_out_time
        ):
            duration_minutes = state.daycare_duration_minutes

        return rt.check_availability(
            evidence, company_id=company_id, customer_id=customer_id, pet_id=pet_id,
            option_ref=option_ref, date=date, check_out_date=check_out_date,
            time_preference=time_preference or dt.get("time") or dt.get("period") or "",
            duration_minutes=duration_minutes, check_out_time=check_out_time,
            preferred_staff=preferred_staff, selection_target=selection_target,
            check_in_time=dt.get("time") or "", exclude_booking_id=exclude_booking_id,
        )

    @tool
    def preview_booking(pet_ref: str = "", option_ref: str = "", slot_ref: str = "", add_on_ref: str = "") -> dict:
        """Preview a booking from a verified option_ref and slot_ref. Never writes anything — returns a preview_ref and a summary to show the customer, who must then explicitly confirm before confirm_booking is called. Pass add_on_ref for a GROOMING/DAYCARE add-on the customer chose (its own option_ref from get_service_options' add_ons list). A requested staff member is NOT a separate parameter here — pass preferred_staff to check_availability instead; the slot_ref it verifies already carries that constraint, so it always matches automatically."""
        try:
            pet_id = _resolve_pet_ref(pet_ref, customer)
        except UnknownPetRefError:
            return {"ok": False, "code": "UNKNOWN_PET_REF"}
        # Real gap this closes: a slot minted under one booking condition
        # (a specific pet/service/option/date/staff) could still be passed
        # to preview_booking after the customer changed one of those —
        # e.g. picked a different package — even though nothing about the
        # OLD slot itself expired. booking_revision, stamped on every slot
        # check_availability mints (see runtime.py's dispatch loop) and
        # bumped whenever a booking-defining condition actually changes
        # (see app/agent/booking_state.py), catches that: a mismatch means
        # something changed after this slot was checked, so it may no
        # longer be real.
        slot_evidence = evidence.resolve(slot_ref)
        if (
            slot_evidence is not None
            and slot_evidence.get("booking_revision") is not None
            and slot_evidence.get("booking_revision") != state.booking.revision
        ):
            return {
                "ok": False,
                "code": "STALE_SLOT_REF",
                "message": (
                    "A booking detail changed since this slot was checked. Call "
                    "check_availability again to get a current slot_ref."
                ),
            }
        pet_name = pet_name_by_ref.get(str(pet_ref), "")
        return rt.preview_booking(
            evidence, company_id=company_id, customer_id=customer_id, pet_id=pet_id, pet_name=pet_name,
            option_ref=option_ref, slot_ref=slot_ref, add_on_ref=add_on_ref or None,
        )

    @tool
    def confirm_booking(preview_ref: str) -> dict:
        """Create the booking a preview_booking call already previewed. Only call this after the customer has explicitly confirmed that exact preview on their own later message — never in the same turn preview_booking was called."""
        return rt.confirm_booking(evidence, preview_ref=preview_ref)

    @tool
    def retrieve_policy(query: str, service_type: str = "", pet_type: str = "", pet_size: str = "") -> dict:
        """Retrieve company policy/SOP knowledge (cancellation rules, vaccination requirements, fees, etc.) through RAG. Pass service_type when the question clearly belongs to one service; leave it blank for a general enquiry."""
        return rt.retrieve_policy(
            company_id=company_id, query=query,
            service_type=service_type or None, pet_type=pet_type or None, pet_size=pet_size or None,
        )

    @tool
    def get_active_bookings() -> dict:
        """List the customer's own currently active (Scheduled/Pending) bookings, each with a booking_ref. Call this before cancel_booking/reschedule_booking whenever the customer has not clearly named which booking they mean, or you're not sure they have exactly one. If exactly one of the results matches what the customer described (by date/service), immediately call cancel_booking/reschedule_booking with its booking_ref in this SAME turn — do not ask the customer to confirm the pet's name in your own words first; cancel_booking's own preview call generates that requirement for you and tells you exactly what to relay."""
        return rt.get_active_bookings(evidence, company_id=company_id, customer_id=customer_id)

    @tool
    def cancel_booking(booking_ref: str = "", confirm_pet_name: str = "") -> dict:
        """Cancel a booking. confirm_pet_name is NOT "the pet's name" for you to fill in from what you already know — leave it empty on your FIRST call regardless of whether you already know the pet's name from context. That first call previews and returns the real booking's details (or, if ambiguous, real candidates with their own booking_refs) and IS the confirmation request; relay its details and ask the customer to type the pet's name, do not compose that request yourself before calling this or pre-fill confirm_pet_name from context. Only call again, with confirm_pet_name set to exactly what the CUSTOMER typed on their next message, after they've actually replied — a plain "yes" is deliberately not enough for something this hard to undo, and neither is you already knowing the pet's name."""
        return rt.cancel_booking(
            evidence, company_id=company_id, customer_id=customer_id,
            booking_ref=booking_ref, confirm_pet_name=confirm_pet_name,
        )

    @tool
    def reschedule_booking(
        booking_ref: str = "", datetime_ref: str = "", check_out_datetime_ref: str = "",
        duration_minutes: int | None = None, confirm_pet_name: str = "",
    ) -> dict:
        """Reschedule a booking to a new datetime_ref (never a date you state yourself — resolve it first). Same two-step confirm_pet_name protection as cancel_booking: leave confirm_pet_name empty on your FIRST call even if you already know the pet's name from context — it is the CUSTOMER's own typed reply on a LATER turn, never something you pre-fill. For BOARDING pass check_out_datetime_ref too — a stay always has both a check-in and check-out date."""
        return rt.reschedule_booking(
            evidence, company_id=company_id, customer_id=customer_id,
            booking_ref=booking_ref, datetime_ref=datetime_ref, check_out_datetime_ref=check_out_datetime_ref,
            duration_minutes=duration_minutes, confirm_pet_name=confirm_pet_name,
        )

    @tool
    def get_loyalty() -> dict:
        """Get the customer's loyalty points balance, tier, and eligible coupons (each with a coupon_ref) in one call."""
        return rt.get_loyalty(evidence, company_id=company_id, customer_id=customer_id)

    @tool
    def redeem_reward(coupon_ref: str, payment_ref: str = "") -> dict:
        """Submit a coupon redemption request against a specific payment. payment_ref comes from AgentContext.last_created_payment_ref (the most recent booking's real payment) unless the customer means an older one — never a payment_ref you invent. Submits a REQUEST only; a staff member must approve it before points are deducted or a discount applied."""
        return rt.redeem_reward(evidence, company_id=company_id, customer_id=customer_id, coupon_ref=coupon_ref, payment_ref=payment_ref)

    @tool
    def preview_membership() -> dict:
        """Preview enrolling the customer in the loyalty program. Never writes anything. An already-enrolled customer's real account comes back immediately (ok=True, already_member=True) — nothing further to do. Otherwise returns a member_ref; call confirm_membership with EXACTLY that ref only after the customer has actually said yes to joining."""
        return rt.preview_membership(evidence, company_id=company_id, customer_id=customer_id)

    @tool
    def confirm_membership(member_ref: str) -> dict:
        """Actually enrol the customer — the customer's own explicit yes to a membership offer, and only that, authorizes this call. Use EXACTLY the member_ref preview_membership returned; never reconstruct or guess one."""
        return rt.confirm_membership(evidence, member_ref=member_ref)

    @tool
    def send_booking_confirmation(booking_ref: str = "") -> dict:
        """Resend a booking confirmation document. Leave booking_ref empty to resend for the customer's latest booking."""
        return rt.send_booking_confirmation(evidence, company_id=company_id, customer_id=customer_id, booking_ref=booking_ref)

    @tool
    def handoff_to_staff(user_message: str, reason: str) -> dict:
        """Escalate to a real staff member — required whenever the customer explicitly asks for a person, a required fact can't be safely resolved, or the request needs human authority. Only this tool makes "I've escalated this" true."""
        return rt.handoff_to_staff(company_id=company_id, customer_id=customer_id, user_message=user_message, reason=reason)

    return [
        create_customer, create_pet,
        get_service_options, resolve_datetime, check_availability, preview_booking, confirm_booking,
        retrieve_policy, get_active_bookings, cancel_booking, reschedule_booking,
        get_loyalty, redeem_reward, preview_membership, confirm_membership,
        send_booking_confirmation, handoff_to_staff,
    ]


_OPTION_DELEGATION_PHRASES = (
    "cheapest", "cheaper", "lowest price", "least expensive", "most affordable",
    "same as last time", "same as before", "same as previous", "usual", "as usual",
    "you choose", "your choice", "your recommendation", "you decide", "up to you",
    "whatever", "any option", "any package", "any one", "anything", "recommend",
)
# Generic words that appear across MOST/ALL grooming option names ("Standard
# Bath", "Premium Bath", "Luxury Bath", "... Fur") — overlap on these alone
# would make ANY option look "named" by a customer who just said "a bath"
# for their dog, defeating the whole point of the check below.
_OPTION_NAME_STOPWORDS = {
    "bath", "fur", "grooming", "groom", "care", "service", "package",
    "the", "and", "for", "with", "add", "on", "hair", "cut",
}


def _option_named_or_delegated(user_message: str, option_name: str) -> bool:
    """A word-overlap heuristic — deliberately not full NLU: does the
    CURRENT customer message share a real, distinguishing word with this
    SPECIFIC option's name (e.g. "standard", "luxury", "short"), or use a
    delegation phrase ("the cheapest one", "same as last time", "you
    choose")? Used to tell a genuine customer choice apart from the Agent
    silently picking one — real gap confirmed 2026-08-10, reproduced live
    twice: "book grooming this Friday" (zero relation to any specific
    option) reached check_availability having silently used "Luxury Bath -
    DAVIS". Errs toward ALLOWING a plausible match through rather than
    blocking a customer's genuinely one-shot, fully-specified request
    ("Milo standard grooming this Friday at 1pm") — a false negative here
    just means one extra clarifying turn, never a wrong booking; this is
    the same asymmetry _needs_tool_repair-style checks elsewhere in this
    codebase already accept."""
    text = str(user_message or "").lower()
    if any(phrase in text for phrase in _OPTION_DELEGATION_PHRASES):
        return True
    import re as _re

    name_words = {
        word for word in _re.findall(r"[a-z]+", option_name.lower())
        if len(word) > 2 and word not in _OPTION_NAME_STOPWORDS
    }
    return any(word in text for word in name_words)


def _confirm_booking_hit_a_genuine_write_rejection(result: dict, rejection: dict | None) -> bool:
    """True only when confirm_booking actually reached the real write and
    it failed there — a genuine staff/room/pet conflict the database
    caught, or an unresolvable preview_ref — never when
    policy.authorize_confirm blocked the call before it ever touched the
    write (rejection is not None): that's not stale evidence, it's the
    customer not having confirmed yet (a side question, a "not yet"), and
    the pending preview is still perfectly valid. Extracted standalone
    (mirrors _update_pending_mutation_target below) so this distinction is
    directly unit-testable instead of only reachable through a live
    write-conflict scenario."""
    return not result.get("ok") and rejection is None


def _update_pending_mutation_target(state: Any, evidence: EvidenceStore, name: str, args: dict, result: dict) -> None:
    """Same fix as preview_booking's pending_preview_ref, for the SAME real
    bug confirmed live here too: without remembering which booking_ref a
    cancel_booking/reschedule_booking preview identified, the model calling
    it again with confirm_pet_name set but booking_ref empty re-triggers
    "ambiguous, multiple active bookings" even though the target was
    already identified one turn ago. minted_turn additionally backs
    app.agent.policy.authorize_mutation_target_confirmation's "not the
    same turn as its own preview" rule (Phase 8)."""
    if result.get("code") == "CONFIRMATION_REQUIRED":
        target_ref = args.get("booking_ref") or evidence.mint("booking", {
            "booking_id": (result.get("data") or {}).get("booking_id"),
            "service_type": (result.get("data") or {}).get("service_type"),
        })
        state.pending_mutation_target = {
            "tool": name, "booking_ref": target_ref, "minted_turn": state.turn_counter,
        }
    elif result.get("ok") or result.get("code") == "ACTION_DECLINED":
        state.pending_mutation_target = None


def handle_turn(
    *,
    memory: ConversationMemory,
    evidence_registry: Any,
    model: Any,
    company_context: dict,
    phone_number: str,
    user_message: str,
) -> RuntimeResult:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    company_id = str(company_context["company_id"])
    state = memory.get(phone_number, company_id)
    state.turn_counter += 1
    evidence = evidence_registry.get(phone_number, company_id)

    from concurrent.futures import ThreadPoolExecutor
    customer = resolve_identity(
        company_id, state,
        tool_executor=ThreadPoolExecutor(max_workers=2),
        timeout_seconds=10,
        cache_single_pet=lambda s, pets: None,
    )

    from app.tools.calendar_tools import resolve_datetime as _resolve_dt
    dt_result = _resolve_dt.invoke({"text": user_message})
    # Real bug confirmed this session (2026-08-10 review): this used to
    # unconditionally overwrite state.current_datetime_resolution with
    # THIS turn's raw dt_result — so "tomorrow" (date=Aug11) then, on a
    # LATER turn, a bare "1pm" (date=None, time=13:00) silently lost Aug11
    # entirely. app.agent.turn_state.apply_datetime_resolution already
    # fixed exactly this for the live orchestrator earlier this session
    # (merge onto the existing date/date_range when a turn resolves
    # something but no date of its own, and a period the same way) — it
    # just was never migrated here until this same fix landed for both
    # architectures together. cache_resolved_date's real implementation
    # only fed state.resolved_dates, a raw-value guardrail whitelist V1
    # needed and V2 has no equivalent of (datetime_ref evidence makes that
    # class of check structurally unnecessary) — a no-op here is correct,
    # not a shortcut.
    from app.agent.turn_state import apply_datetime_resolution, compact_evidence_result

    apply_datetime_resolution(
        state, dt_result,
        cache_resolved_date=lambda *_args, **_kwargs: None,
        compact_evidence_result=compact_evidence_result,
    )
    current_message_datetime_ref = None
    merged_resolution = state.current_datetime_resolution
    if isinstance(merged_resolution, dict) and (
        merged_resolution.get("date")
        or merged_resolution.get("date_range")
        or merged_resolution.get("time")
        or merged_resolution.get("period")
    ):
        # Minted so the common single-date case (GROOMING/DAYCARE, most
        # turns) can go straight to check_availability(datetime_ref=...)
        # without the model needing a redundant explicit resolve_datetime
        # call just to obtain a ref for what was already resolved
        # automatically this turn. A SECOND distinct expression (e.g.
        # BOARDING's separate check-out date) still needs its own explicit
        # resolve_datetime call — this per-turn preprocessing can only ever
        # resolve one expression per message.
        current_message_datetime_ref = evidence.mint("datetime", merged_resolution)
    capture_explicit_daycare_duration(state, user_message)

    context = build_agent_context(state, customer, company_context)
    context["current_message_datetime_ref"] = current_message_datetime_ref
    context["active_options"] = state.active_options
    context["active_slots"] = state.active_slots
    # Real gap confirmed live 2026-08-10: active_options/active_slots above
    # are "what was just shown" — nothing surfaced "what the customer has
    # actually already selected for THIS booking", so a model that
    # correctly remembered the customer's choice in its own reasoning had
    # no structured evidence of it either. This is that: a compact view of
    # ConversationState.booking (BookingWorkingState) so the model can see
    # at a glance what's already settled and what's still genuinely open,
    # instead of re-deciding (or worse, re-guessing) it every turn.
    context["booking"] = {
        "service_type": state.booking.service_type,
        "pet_ref": state.booking.pet_ref,
        "selected_option_ref": state.booking.selected_option_ref,
        "selected_add_on_refs": state.booking.selected_add_on_refs,
        "datetime_ref": state.booking.datetime_ref,
        "check_out_datetime_ref": state.booking.check_out_datetime_ref,
        "selected_slot_ref": state.booking.selected_slot_ref,
        "preferred_staff": state.booking.preferred_staff,
        "preview_ref": state.booking.preview_ref,
    }
    if state.last_created_payment_ref and not evidence.is_valid(state.last_created_payment_ref):
        state.last_created_payment_ref = None
    context["last_created_payment_ref"] = state.last_created_payment_ref
    if state.pending_preview_ref and evidence.is_valid(state.pending_preview_ref):
        context["pending_action"] = {"tool": "confirm_booking", "preview_ref": state.pending_preview_ref}
    else:
        context["pending_action"] = None
        state.pending_preview_ref = None
    if state.pending_mutation_target and evidence.is_valid(state.pending_mutation_target.get("booking_ref")):
        context["pending_mutation"] = state.pending_mutation_target
    else:
        context["pending_mutation"] = None
        state.pending_mutation_target = None
    if state.pending_member_ref and evidence.is_valid(state.pending_member_ref):
        context["pending_membership"] = {"tool": "confirm_membership", "member_ref": state.pending_member_ref}
    else:
        context["pending_membership"] = None
        state.pending_member_ref = None
    # _resolve_pet_ref/pet_name_by_ref must check against context["customer"]
    # ("ref"/"name" keys — the shape the model actually saw and echoed
    # back), never the raw resolve_identity() dict (keyed "pet_id"/
    # "pet_name" instead) — confirmed live: passing the raw dict here made
    # the model's perfectly correct pet_ref="1" get rejected as
    # UNKNOWN_PET_REF every time, since none of its entries have a "ref" key.
    agent_pets = context["customer"]["pets"]
    pet_name_by_ref = {str(p.get("ref")): p.get("name") or "" for p in agent_pets}
    tools = _bind_tools(
        evidence, company_id=int(company_id), customer_id=customer.get("customer_id"),
        customer={"pets": agent_pets}, pet_name_by_ref=pet_name_by_ref, state=state,
        phone_number=phone_number,
    )
    tools_by_name = {t.name: t for t in tools}
    bound_model = model.bind_tools(tools)

    messages: list[Any] = [SystemMessage(SYSTEM_PROMPT_V2)]
    for turn in state.history:
        cls = HumanMessage if turn.get("role") == "human" else AIMessage
        messages.append(cls(turn.get("content") or ""))
    import json as _json
    messages.append(SystemMessage("AgentContext (check pending_action/pending_mutation/pending_membership FIRST):\n" + _json.dumps(context, default=str, indent=2)))
    messages.append(HumanMessage(user_message))

    trace: list[dict] = []
    final_response = None
    # Item #6 of the 2026-08-10 review: SYSTEM_PROMPT_V2 asks the model to
    # call get_loyalty right after a booking succeeds, but the live
    # orchestrator already proved (twice, live) that instruction alone
    # isn't reliable — the model treats a successful confirm_booking as
    # "done" and skips the bonus call even when told to. force_loyalty_check
    # mirrors that fix (app.orchestrator.PawfectOrchestrator's
    # _create_booking_just_succeeded + force_exact_tool_once): the NEXT
    # iteration after a real confirm_booking success binds ONLY get_loyalty
    # with tool_choice="required", so it happens deterministically exactly
    # once per turn, then normal tool_choice resumes.
    force_loyalty_check = False
    # Item confirmed 2026-08-10, reproduced live twice: get_service_options
    # returning several real options, immediately followed (same turn) by
    # check_availability silently using one of them — "book grooming this
    # Friday" reached availability/preview having picked "Luxury Bath -
    # DAVIS" out of ten real options with zero relation to anything the
    # customer said. Tracks option_refs shown for the FIRST time this
    # turn (not ones state.active_options already had from an earlier
    # turn — those the customer had a real chance to react to) so
    # check_availability can be blocked from silently using one before the
    # customer has actually seen/chosen from them.
    just_shown_options: dict[str, str] = {}
    for _ in range(MAX_AGENT_STEPS):
        if force_loyalty_check:
            response = model.bind_tools(
                [tools_by_name["get_loyalty"]], tool_choice="required",
            ).invoke(messages)
            force_loyalty_check = False
        else:
            response = bound_model.invoke(messages)
        messages.append(response)
        if not response.tool_calls:
            final_response = response
            break
        for call in response.tool_calls:
            name, args = call["name"], call["args"]
            # Verified-argument hydration (2026-08-10): fills a DROPPED
            # argument from state.booking's already-selected evidence —
            # never something the model never established itself. Applied
            # before the rejection checks below so a hydrated option_ref
            # (a genuinely earlier selection, never one of THIS turn's
            # freshly-shown options) is correctly exempt from the
            # "just shown, not yet picked" guard rather than tripping it.
            args = hydrate_tool_args(state, context, name, args)
            rejection = None
            if name == "confirm_booking":
                rejection = policy.authorize_confirm(
                    args.get("preview_ref"), evidence=evidence, state=state, user_message=user_message,
                    current_ref=state.pending_preview_ref,
                )
            if name == "confirm_membership":
                rejection = policy.authorize_confirm(
                    args.get("member_ref"), evidence=evidence, state=state, user_message=user_message,
                    current_ref=state.pending_member_ref,
                )
            if name in ("cancel_booking", "reschedule_booking"):
                rejection = policy.authorize_mutation_target_confirmation(
                    args, state=state, user_message=user_message,
                )
            if name == "check_availability" and args.get("option_ref") in just_shown_options:
                option_name = just_shown_options[args["option_ref"]]
                if not _option_named_or_delegated(user_message, option_name):
                    rejection = {
                        "ok": False,
                        "code": "CUSTOMER_OPTION_SELECTION_REQUIRED",
                        "message": (
                            "Multiple real options were just shown and the customer hasn't "
                            "picked or delegated one yet. Present the options (or your top "
                            "recommendation) and ask which one they want before checking "
                            "availability."
                        ),
                    }
            if rejection is None and name == "check_availability" and state.booking.service_type:
                # Real gap confirmed live 2026-08-10: a genuine write
                # rejection (or, before that fix, a finished booking)
                # could leave a stale option_ref from a DIFFERENT service
                # reachable via state.active_options; the model reused it
                # for a service the customer had already switched to,
                # silently previewing "Daycare Above 3 Hours" for what the
                # customer called boarding. That leak is already fixed at
                # the source (active_options is cleared on both confirm_
                # booking outcomes) — this is a second, independent layer:
                # even if some other path ever reaches this tool with an
                # option belonging to a service_type that doesn't match
                # the booking currently being built, reject outright
                # rather than silently building on the mismatch. A
                # genuine service switch is never blocked by this: get_
                # service_options already updates state.booking.
                # service_type to the NEW service via booking_state.
                # change_service before check_availability could ever be
                # called with the new service's real option_ref.
                mismatch_option = evidence.resolve(args.get("option_ref"))
                if (
                    mismatch_option
                    and str(mismatch_option.get("service_type") or "").upper()
                    != state.booking.service_type
                ):
                    rejection = {
                        "ok": False,
                        "code": "SERVICE_CONTEXT_MISMATCH",
                        "message": (
                            f"The booking currently being built is {state.booking.service_type}. "
                            "This option_ref belongs to a different service — call "
                            "get_service_options for the service actually being discussed and use "
                            "one of its option_refs instead."
                        ),
                    }
            if rejection is not None:
                result = rejection
            else:
                raw = tools_by_name[name].invoke(args)
                result = raw if "ok" in raw else normalize_tool_result(raw)
                if name in _TOOL_GOAL and result.get("ok"):
                    state.active_scenario = _TOOL_GOAL[name]
                if name == "get_service_options" and result.get("ok"):
                    # Replaced wholesale, not merged — see
                    # ConversationState.active_options.
                    state.active_options = result.get("options", []) + result.get("add_ons", [])
                    real_options = result.get("options") or []
                    if len(real_options) > 1:
                        # Only the SERVICE options count as an ambiguous
                        # choice needing the guard above — a single option
                        # (nothing to silently pick between) is fine, and
                        # add_ons are optional extras, not the primary
                        # choice this concerns.
                        just_shown_options.update(
                            {opt["option_ref"]: opt.get("name", "") for opt in real_options}
                        )
                    # capture_explicit_daycare_duration's "is this a DAYCARE
                    # message" check falls back to a raw-text regex when
                    # state.service_type is unset (it always is here — the
                    # new architecture tracks goal/service via _TOOL_GOAL/
                    # AgentContext, not this live-orchestrator-era field) —
                    # that regex only catches a message that says "daycare"
                    # again, so a later turn like "actually make it 1pm"
                    # would never be recognized as DAYCARE context without
                    # this. Keeping it current here mirrors how it's kept
                    # current in the live orchestrator (every turn that asks
                    # about a service's catalogue re-affirms which one).
                    if args.get("service_type"):
                        state.service_type = args["service_type"]
                    # BookingWorkingState: a real service (re)selection —
                    # change_service invalidates any option/slot/preview
                    # left over from a DIFFERENT prior service, so a
                    # genuine switch is never blocked by the
                    # SERVICE_CONTEXT_MISMATCH guard above (by the time a
                    # later check_availability call names this service's
                    # own option_ref, state.booking.service_type already
                    # matches it).
                    booking_state.change_pet(state, args.get("pet_ref"))
                    booking_state.change_service(state, args.get("service_type"))
                if name == "check_availability" and result.get("ok"):
                    # Same reasoning as active_options above, for a shown
                    # slot list — see ConversationState.active_slots.
                    # Replaced wholesale: an old query's slots are not a
                    # partial answer to a new one.
                    state.active_slots = result.get("slots", [])
                    booking_state.change_pet(state, args.get("pet_ref"))
                    booking_state.change_option(state, args.get("option_ref"))
                    resolved_dt = evidence.resolve(args.get("datetime_ref"))
                    if resolved_dt is not None:
                        booking_state.change_datetime(
                            state, args.get("datetime_ref"),
                            (resolved_dt.get("date"), resolved_dt.get("time"), resolved_dt.get("period")),
                        )
                    if args.get("check_out_datetime_ref"):
                        resolved_checkout_dt = evidence.resolve(args["check_out_datetime_ref"])
                        if resolved_checkout_dt is not None:
                            booking_state.change_check_out_datetime(
                                state, args["check_out_datetime_ref"],
                                (
                                    resolved_checkout_dt.get("date"),
                                    resolved_checkout_dt.get("time"),
                                    resolved_checkout_dt.get("period"),
                                ),
                            )
                    booking_state.change_staff(state, args.get("preferred_staff"))
                    # Stamp the CURRENT (possibly just-bumped-by-the-above)
                    # revision onto every slot this call minted —
                    # preview_booking refuses a slot_ref whose stamped
                    # revision no longer matches state.booking.revision
                    # (see _bind_tools' preview_booking closure), catching
                    # a slot built under a booking condition that has since
                    # changed, even one nothing else here caught.
                    for minted_slot in result.get("slots") or []:
                        slot_payload = evidence.resolve(minted_slot.get("slot_ref"))
                        if slot_payload is not None:
                            slot_payload["booking_revision"] = state.booking.revision
                if name == "preview_booking" and result.get("ok"):
                    # Stamp the turn it was minted on so
                    # app.agent.policy.authorize_confirm can enforce "not
                    # this same turn" without re-deriving it from anything
                    # the model states.
                    preview_payload = evidence.resolve(result["preview_ref"])
                    if preview_payload is not None:
                        preview_payload["minted_turn"] = state.turn_counter
                    # Remembered so build_agent_context can surface it as
                    # pending_action on the NEXT turn — the actual fix for
                    # "customer confirms, model has no idea which preview
                    # that refers to" (see ConversationState.pending_preview_ref).
                    state.pending_preview_ref = result["preview_ref"]
                    booking_state.change_slot(state, args.get("slot_ref"))
                    state.booking.preview_ref = result["preview_ref"]
                if name == "confirm_booking" and result.get("ok"):
                    state.pending_preview_ref = None
                    booking_state.reset(state)
                    if result.get("payment_ref"):
                        state.last_created_payment_ref = result["payment_ref"]
                    # A finished booking's DAYCARE duration must not leak
                    # into the next, unrelated one — same reset the live
                    # orchestrator does on its own create_booking success.
                    state.daycare_duration_minutes = None
                    state.daycare_duration_source = None
                    state.daycare_range_end_time = None
                    state.active_slots = None
                    # Real gap confirmed live 2026-08-10 (Grooming -> Daycare
                    # -> Boarding smoke test after the V1 decommission): the
                    # just-shown catalogue (active_options) survived a
                    # finished booking untouched, same class of staleness as
                    # daycare_duration_minutes above — a customer who then
                    # names a DIFFERENT service ("book boarding instead")
                    # can have the model reuse the previous, no-longer-
                    # relevant option_ref instead of calling
                    # get_service_options again, silently building a preview
                    # for the wrong service. Cleared here for the same
                    # reason active_slots is.
                    state.active_options = None
                    if customer.get("customer_id") is not None and not any(
                        item.get("tool") == "get_loyalty" for item in trace
                    ):
                        force_loyalty_check = True
                elif name == "confirm_booking" and _confirm_booking_hit_a_genuine_write_rejection(result, rejection):
                    # Real bug confirmed this session (2026-08-10 review):
                    # a GENUINE write-time rejection (a real staff/room/pet
                    # conflict the database caught, or an unresolvable ref)
                    # left pending_preview_ref/active_slots standing, so
                    # AgentContext.pending_action on the NEXT turn still
                    # pointed at the exact evidence that just failed —
                    # inviting the model to either re-confirm the same
                    # stale preview or reuse a slot that's no longer real.
                    # A same-turn/non-affirmative REJECTION from
                    # policy.authorize_confirm never reached the real
                    # write at all, so the pending preview is still
                    # perfectly valid and must NOT be cleared just because
                    # the customer asked a side question instead of
                    # confirming.
                    state.pending_preview_ref = None
                    state.active_slots = None
                    # Same staleness as the success branch above — a REAL
                    # write-time rejection (confirmed live: a vaccination-
                    # eligibility rejection) still means this attempt is
                    # over; the option catalogue that led to it must not be
                    # silently reused if the customer's next message names a
                    # different service instead of retrying this one.
                    state.active_options = None
                    # The pet/service/option/date the customer chose to GET
                    # here are still perfectly valid to retry with a
                    # different time — only the failed slot/preview
                    # themselves are cleared, not the whole booking
                    # selection (see booking_state.clear_slot_and_preview).
                    booking_state.clear_slot_and_preview(state)
                if name in ("cancel_booking", "reschedule_booking"):
                    _update_pending_mutation_target(state, evidence, name, args, result)
                if name == "preview_membership" and result.get("ok") and result.get("member_ref"):
                    # Same "stamp the minting turn, remember for next turn"
                    # pattern as preview_booking above.
                    member_payload = evidence.resolve(result["member_ref"])
                    if member_payload is not None:
                        member_payload["minted_turn"] = state.turn_counter
                    state.pending_member_ref = result["member_ref"]
                if name == "confirm_membership" and result.get("ok"):
                    state.pending_member_ref = None
                    # A brand-new member's real account exists now — force
                    # a fresh resolve_identity hydration next turn instead
                    # of keeping a stale "not a member" cached (see
                    # ConversationState.loyalty_account).
                    state.loyalty_context_status = "unavailable"
            trace.append({"tool": name, "args": args, "result": result})
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
    else:
        # MAX_AGENT_STEPS exhausted without the model ever choosing to stop
        # on its own (the loop's only `break` is on a tool-call-free
        # response). Real gap confirmed live 2026-08-10 (post-V1-
        # decommission Boarding smoke test): the LAST tool call the final
        # iteration ran had already succeeded — a real preview_booking,
        # a real preview_ref, a real room/date/price — but the customer
        # was shown a hardcoded "staff will help" escalation instead,
        # discarding that real result entirely. `messages` already holds
        # the full SystemMessage/AIMessage(tool_calls)/ToolMessage(results)
        # sequence from every call this turn, so one more model call with
        # no tools bound (forcing a text-only reply) lets it summarize
        # what was actually already accomplished from that real evidence,
        # instead of the loop's progress being silently thrown away. Only
        # falls back to the generic escalation if even that recovery call
        # produces nothing usable.
        try:
            final_response = model.invoke(messages)
        except Exception:
            final_response = None
        if final_response is None or not str(getattr(final_response, "content", "") or "").strip():
            final_response = AIMessage("I'm having trouble completing this — let me get a staff member to help.")

    reply = final_response.content if final_response is not None else ""
    state.history.append({"role": "human", "content": user_message})
    state.history.append({"role": "ai", "content": reply})
    memory.save(state)

    return RuntimeResult(reply=reply, trace=trace, agent_context=context)
