"""Phase 5 of REFACTOR_PLAN.md — a live smoke test of SYSTEM_PROMPT_V2
against a REAL gpt-4o-mini call, bound to the Phase 3/4 reference tools
directly (not through app/orchestrator.py — this is deliberately decoupled
from the live tool-calling loop, which nothing in this refactor has
touched yet).

Specifically exercises the claim from the architecture review this session
is based on: a short, principle-based prompt with reference-based tools
(no raw price/date in the model's hands) can still reason "cheapest one"
correctly on its own, without a hardcoded "if customer says cheapest, call
X" rule — because the prompt only has to say recommending between VERIFIED
options is the model's own job, not spell out how.

Requires OPENAI_API_KEY + SUPABASE_* — skipped otherwise, same guard shape
as Phase 3/4's live tests.
"""

import os

import pytest
from dotenv import load_dotenv

load_dotenv(override=False)
_HAS_LIVE_CREDS = bool(os.getenv("OPENAI_API_KEY")) and bool(os.getenv("SUPABASE_URL"))
requires_live_llm = pytest.mark.skipif(
    not _HAS_LIVE_CREDS, reason="OPENAI_API_KEY/SUPABASE_URL not configured"
)


def test_prompt_v2_states_greeting_required_elements_not_a_fixed_sentence():
    """Item #6 of the 2026-08-10 architecture review: greeting has required
    elements (name/company, and a relevant recent/upcoming booking), but no
    fixed wording — and never on a later turn."""
    from app.prompts.system_v2 import SYSTEM_PROMPT_V2

    assert "is_first_message" in SYSTEM_PROMPT_V2
    assert "never re-greet on a later turn" in SYSTEM_PROMPT_V2
    assert "upcoming_booking" in SYSTEM_PROMPT_V2
    assert "recent_booking" in SYSTEM_PROMPT_V2
    assert "not a fixed" in SYSTEM_PROMPT_V2


def _bind_reference_tools(evidence, *, company_id: int, customer_id: int, pet_id: int, pet_name: str):
    """Thin @tool wrappers closing over a fixed context — stands in for
    Phase 6's real server-side injection (company_id/customer_id/pet_id
    resolved from session state, never model-supplied)."""
    from langchain_core.tools import tool

    from app.tools import reference_tools as rt

    @tool
    def get_service_options(service_type: str) -> dict:
        """Get real bookable service options and prices (GROOMING, DAYCARE, or BOARDING) for the current customer's pet. Returns each option with an option_ref — use that ref in later calls, never restate the name/price yourself."""
        return rt.get_service_options(
            evidence, company_id=company_id, customer_id=customer_id, pet_id=pet_id, service_type=service_type,
        )

    @tool
    def resolve_datetime(text: str) -> dict:
        """Resolve natural-language date/time text (e.g. "this Thursday", "10am") using the business calendar."""
        from app.tools.calendar_tools import resolve_datetime as _existing
        return _existing.invoke({"text": text})

    @tool
    def check_availability(option_ref: str, date: str, time_preference: str = "") -> dict:
        """Check real availability for a previously returned option_ref on a specific date. Returns real slot_ref values — never invent a time."""
        return rt.check_availability(
            evidence, company_id=company_id, customer_id=customer_id, pet_id=pet_id,
            option_ref=option_ref, date=date, time_preference=time_preference,
        )

    @tool
    def preview_booking(option_ref: str, slot_ref: str) -> dict:
        """Preview a booking from a verified option_ref and slot_ref. Never writes anything — returns a preview_ref and a summary to show the customer."""
        return rt.preview_booking(
            evidence, company_id=company_id, customer_id=customer_id, pet_id=pet_id, pet_name=pet_name,
            option_ref=option_ref, slot_ref=slot_ref,
        )

    return [get_service_options, resolve_datetime, check_availability, preview_booking]


@requires_live_llm
def test_prompt_v2_reasons_cheapest_one_without_a_hardcoded_rule():
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_openai import ChatOpenAI

    from app.agent.evidence import EvidenceStore
    from app.prompts.system_v2 import SYSTEM_PROMPT_V2

    evidence = EvidenceStore()
    tools = _bind_reference_tools(evidence, company_id=1, customer_id=1, pet_id=1, pet_name="Milo")
    tools_by_name = {t.name: t for t in tools}
    model = ChatOpenAI(model="gpt-4o-mini", temperature=0).bind_tools(tools)

    messages = [
        SystemMessage(SYSTEM_PROMPT_V2),
        HumanMessage("What grooming options do you have for Milo?"),
    ]

    option_refs_seen: set[str] = set()
    cheapest_ref = None
    cheapest_price = None

    for _ in range(6):
        response = model.invoke(messages)
        messages.append(response)
        if not response.tool_calls:
            break
        for call in response.tool_calls:
            result = tools_by_name[call["name"]].invoke(call["args"])
            if call["name"] == "get_service_options" and isinstance(result, dict) and result.get("ok"):
                for option in result["options"]:
                    option_refs_seen.add(option["option_ref"])
                    if cheapest_price is None or option["price"] < cheapest_price:
                        cheapest_price = option["price"]
                        cheapest_ref = option["option_ref"]
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

    assert cheapest_ref is not None, "model never called get_service_options"

    # Second turn: "cheapest one" — nothing in the prompt tells the model
    # HOW to compute this; it must reason it out from the verified options
    # it already saw.
    messages.append(HumanMessage("The cheapest one please, this Thursday at 10am."))
    picked_option_ref = None
    for _ in range(6):
        response = model.invoke(messages)
        messages.append(response)
        if not response.tool_calls:
            break
        for call in response.tool_calls:
            result = tools_by_name[call["name"]].invoke(call["args"])
            if call["name"] == "check_availability":
                picked_option_ref = call["args"].get("option_ref")
            if call["name"] == "preview_booking" and picked_option_ref is None:
                picked_option_ref = call["args"].get("option_ref")
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

    assert picked_option_ref is not None, "model never acted on a specific option"
    assert picked_option_ref == cheapest_ref, (
        f"model picked {picked_option_ref}, but the actually-cheapest verified option was {cheapest_ref} "
        f"(RM{cheapest_price})"
    )
