"""Regression coverage for app/agent/turn_state.py's cross-turn helpers —
capture_explicit_daycare_duration and apply_datetime_resolution.

These tests used to live in tests/test_agentic_orchestration.py, exercised
through app/orchestrator.py's PawfectOrchestrator (V1). The 2026-08-10 V1
decommission deleted that file along with the rest of V1's raw-value tool
loop, but these two functions are real, architecture-independent logic
app/agent/runtime.py's handle_turn() (V2, the only architecture left)
still depends on — so their coverage moved here rather than disappearing
with V1.
"""

from app.agent.turn_state import apply_datetime_resolution, capture_explicit_daycare_duration, compact_evidence_result
from app.context.state import ConversationState


def test_daycare_duration_survives_side_flow_and_is_captured_from_the_message():
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        service_type="DAYCARE",
    )
    state.turn_counter = 2
    capture_explicit_daycare_duration(state, "daycare 三个小时")

    assert state.daycare_duration_minutes == 180
    assert state.daycare_duration_source == "explicit_duration"


def test_daycare_time_range_is_cached_as_exact_duration():
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        service_type="DAYCARE",
    )

    capture_explicit_daycare_duration(state, "早上9点到下午5点")

    assert state.daycare_duration_minutes == 480


def test_daycare_range_edit_of_check_in_time_alone_keeps_the_stated_pickup_time():
    """"12pm to 5pm" then just "actually make it 1pm" should hold 5pm fixed
    and shrink the duration to 4h, not silently keep the stale 5h length."""
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        service_type="DAYCARE",
    )
    capture_explicit_daycare_duration(state, "daycare 12pm to 5pm")
    assert state.daycare_duration_minutes == 300
    assert state.daycare_duration_source == "explicit_range"
    assert state.daycare_range_end_time == "17:00"

    capture_explicit_daycare_duration(state, "actually make it 1pm")

    assert state.daycare_duration_minutes == 240
    assert state.daycare_range_end_time == "17:00"


def test_daycare_explicit_duration_edit_of_check_in_time_keeps_the_stated_length():
    """"5 hours" then just "make it 1pm" should keep the 5h length fixed
    (the pickup time shifts) rather than being reinterpreted as a range edit."""
    state = ConversationState(
        phone_number="+60123456705",
        company_id="1",
        active_scenario="MAKE_BOOKING",
        service_type="DAYCARE",
    )
    capture_explicit_daycare_duration(state, "daycare for 5 hours")
    assert state.daycare_duration_minutes == 300
    assert state.daycare_duration_source == "explicit_duration"

    capture_explicit_daycare_duration(state, "make it 1pm")

    assert state.daycare_duration_minutes == 300


def test_datetime_resolution_survives_a_later_time_only_or_unrelated_turn():
    """Confirmed live (external audit + verified against actual code): the
    resolve_datetime preprocessing that runs automatically every turn used
    to unconditionally REPLACE state.current_datetime_resolution — or wipe
    it to None entirely on an "ambiguous" result — every single turn. A
    customer resolving "next Wednesday", then later just saying "10 AM"
    (resolves time only), or an unrelated aside like "how much does
    boarding cost"/"yes confirm" (resolves NOTHING — ambiguous=True on its
    own), silently lost the already-established date, forcing the model to
    reconstruct it from raw conversation history instead of
    RUNTIME_CONTEXT — this is exactly the "sometimes it works, sometimes
    it doesn't" pattern reported live."""
    state = ConversationState(phone_number="+60123456705", company_id="1")
    calls = []

    def fake_cache(state, tool_name, result):
        calls.append(result)

    # Turn 1: "next Wednesday" resolves a real date.
    apply_datetime_resolution(
        state,
        {"date": "2026-08-12", "date_range": None, "time": None, "period": None,
         "duration_minutes": None, "ambiguous": False, "needs_time_selection": False,
         "raw_text": "next Wednesday"},
        cache_resolved_date=fake_cache,
        compact_evidence_result=compact_evidence_result,
    )
    assert state.current_datetime_resolution["date"] == "2026-08-12"

    # Turn 2: "10 AM" resolves only a time — the date must survive.
    apply_datetime_resolution(
        state,
        {"date": None, "date_range": None, "time": "10:00", "period": None,
         "duration_minutes": None, "ambiguous": False, "needs_time_selection": False,
         "raw_text": "Nice! I would like to book at 10 AM"},
        cache_resolved_date=fake_cache,
        compact_evidence_result=compact_evidence_result,
    )
    assert state.current_datetime_resolution["date"] == "2026-08-12"
    assert state.current_datetime_resolution["time"] == "10:00"

    # Turn 3: a fully unrelated aside resolves nothing at all
    # (ambiguous=True) — must be a no-op, not wipe the state.
    apply_datetime_resolution(
        state,
        {"date": None, "date_range": None, "time": None, "period": None,
         "duration_minutes": None, "ambiguous": True, "needs_time_selection": False,
         "raw_text": "how much does boarding cost"},
        cache_resolved_date=fake_cache,
        compact_evidence_result=compact_evidence_result,
    )
    assert state.current_datetime_resolution["date"] == "2026-08-12"
    assert state.current_datetime_resolution["time"] == "10:00"

    # Turn 4: the customer explicitly names a NEW date — must still fully
    # override, same as before this fix.
    apply_datetime_resolution(
        state,
        {"date": "2026-08-20", "date_range": None, "time": None, "period": None,
         "duration_minutes": None, "ambiguous": False, "needs_time_selection": False,
         "raw_text": "actually let's do next Thursday instead"},
        cache_resolved_date=fake_cache,
        compact_evidence_result=compact_evidence_result,
    )
    assert state.current_datetime_resolution["date"] == "2026-08-20"
    # The stale time from the abandoned date must not be silently carried
    # into the new date without the customer re-stating it.
    assert state.current_datetime_resolution["time"] is None


def test_period_preference_merges_forward_the_same_way_date_does():
    """Audited live per explicit request ("先audit看对不对，才决定要不要改")
    before changing anything: period parsing, backend-hardcoded period
    windows, and genuine availability filtering by period were all already
    correct. "next Friday" -> "afternoon" and "next Friday afternoon" ->
    "2pm" both already merged correctly. Only the reverse order was
    broken: "afternoon" stated before any date, then a later turn naming
    the date without repeating "afternoon", silently dropped the
    customer's already-stated time-of-day preference — the same class of
    bug the date/date_range merge fix above already covers for date, just
    never extended to period. Fixed by carrying period forward the same
    way, unless the new turn states its own period or an exact time (an
    exact time is more specific and must replace, not coexist with, a
    stale period)."""
    state = ConversationState(phone_number="+60123456706", company_id="1")
    calls = []

    def fake_cache(state, tool_name, result):
        calls.append(result)

    # Turn 1: "afternoon" resolves only a period — no date yet.
    apply_datetime_resolution(
        state,
        {"date": None, "date_range": None, "time": None, "period": "afternoon",
         "duration_minutes": None, "ambiguous": False, "needs_time_selection": False,
         "raw_text": "I'd like to come in the afternoon"},
        cache_resolved_date=fake_cache,
        compact_evidence_result=compact_evidence_result,
    )
    assert state.current_datetime_resolution["period"] == "afternoon"

    # Turn 2: "next Friday" resolves a new date without restating the
    # period — the period must survive, not silently vanish.
    apply_datetime_resolution(
        state,
        {"date": "2026-08-21", "date_range": None, "time": None, "period": None,
         "duration_minutes": None, "ambiguous": False, "needs_time_selection": False,
         "raw_text": "let's do next Friday"},
        cache_resolved_date=fake_cache,
        compact_evidence_result=compact_evidence_result,
    )
    assert state.current_datetime_resolution["date"] == "2026-08-21"
    assert state.current_datetime_resolution["period"] == "afternoon"

    # Turn 3: an exact time is more specific than a period and must
    # replace the stale period rather than coexist with it.
    apply_datetime_resolution(
        state,
        {"date": None, "date_range": None, "time": "14:00", "period": None,
         "duration_minutes": None, "ambiguous": False, "needs_time_selection": False,
         "raw_text": "actually 2pm works"},
        cache_resolved_date=fake_cache,
        compact_evidence_result=compact_evidence_result,
    )
    assert state.current_datetime_resolution["date"] == "2026-08-21"
    assert state.current_datetime_resolution["time"] == "14:00"
    assert state.current_datetime_resolution["period"] is None
