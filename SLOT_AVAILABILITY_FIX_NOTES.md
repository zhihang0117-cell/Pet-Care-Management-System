# Slot Availability Fix

This release keeps the existing conversational/orchestrator architecture and only tightens slot availability consistency.

## Changes

1. Exact-time checks are now explicit.
   - `check_availability(..., time="2:30pm")` returns only the exact slot when it is available.
   - If unavailable, `available_slots=[]`, `requested_time_available=false`, and `all_available_slots` contains the real alternatives.

2. Service choices and slot choices no longer share one lifecycle only.
   - Added `active_service_options` and `active_slots` to conversation state.
   - Added `selected_slot` and `availability_context`.
   - `offered_options` remains for backwards compatibility/cancel-reschedule candidates.

3. Short time replies reuse the exact verified context.
   - If the assistant just offered 12:00, 12:30, 1:00, 2:30 and the customer says `2:30pm`, the system binds that clock to the previously verified service/date/duration/room/checkout context before any exact re-check.
   - Explicitly changed service/date/room conditions are never overwritten by stale slot context.

4. Stale slot lists are replaced after an exact re-check.
   - If 2:30 is no longer free, the old list is replaced with the fresh alternatives and 2:30 is removed.

5. Existing Grooming conflicts use persisted duration.
   - `_booking_interval()` now uses each existing Grooming row's `duration_minutes` instead of always assuming 90 minutes.
   - This aligns read-side availability more closely with final database conflict validation.

6. Final writes remain safe.
   - `create_booking` still performs its own fresh DB availability validation before inserting.

## Deterministic checks run

- Python compileall: PASS
- Exact availability contract check: PASS
- Active slot selection/context binding check: PASS
- Exact unavailable stale-list replacement check: PASS
- Existing Grooming persisted-duration check: PASS

Full LangChain integration tests were not executed in this container because `langchain_core` is not installed here; Cloud Run installs it from `requirements.txt`.
