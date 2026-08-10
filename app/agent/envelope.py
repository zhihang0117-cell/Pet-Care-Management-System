"""Unified tool-result envelope — Phase 2 of REFACTOR_PLAN.md.

Today's tools return at least four different result shapes:

1. app/db/relational_actions.py's _result() — the majority shape:
   {"action", "status", "success", "data_found", "data", "error",
   "handoff_required", "handoff_reason"}, status one of "success",
   "not_found", "missing_information", "ambiguous", "confirmation_required",
   "error".
2. A tool-layer inline error with no "status" at all — e.g.
   create_booking's own {"error": "MISSING_PACKAGE_SELECTION", "message": ...}
   or a guardrail rejection {"error": "UNVERIFIED_AVAILABILITY_SLOT",
   "message": ...}.
3. A bare data dict with no "status"/"error" key at all — resolve_datetime
   just returns {"date", "time", ..., "ambiguous"} directly.
4. check_availability_range's own top-level shape (no "action"/"success"/
   "data_found" at all): {"status", "service_type", "start_date",
   "end_date", "days"} or {"status": "error", ..., "failed_days"}.

normalize_tool_result() maps any of these onto ONE fixed shape:

    {"ok": bool, "code": str, "data": dict, "needs": list[str],
     "evidence": dict | None, "recoverable": bool, "handoff": bool}

Pure function, called from nowhere in the live request path yet — this
phase's only job is proving the mapping is lossless/correct against real,
live-observed result shapes (see tests/test_refactor_phase2_envelope.py)
before app/agent/runtime.py (Phase 6) is ever wired to consume it instead
of a tool's raw result.
"""

from __future__ import annotations

from typing import Any

# Statuses relational_actions._result() actually produces (see its own
# docstring/callers) that still count as the CALL having succeeded, even
# when status != "success" — e.g. "not_found" is a real, complete answer
# ("this customer has no upcoming booking"), not a failure.
_OK_STATUSES = {"success", "not_found", "missing_information", "ambiguous", "confirmation_required"}


def normalize_tool_result(raw: Any) -> dict:
    if not isinstance(raw, dict):
        # A tool returning something un-dict-shaped is itself the failure —
        # never silently coerce it into looking like a normal result.
        return {
            "ok": False,
            "code": "MALFORMED_RESULT",
            "data": {},
            "needs": [],
            "evidence": None,
            "recoverable": False,
            "handoff": False,
        }

    if "status" in raw:
        return _normalize_status_shape(raw)
    if "error" in raw:
        return _normalize_inline_error_shape(raw)
    # A bare data dict (resolve_datetime, and similar deterministic-
    # preprocessing tools with no notion of "failure", only "found nothing
    # yet") — the call itself always succeeded; whether it actually
    # resolved anything is a data question the caller reads from `data`.
    return {
        "ok": True,
        "code": "OK",
        "data": raw,
        "needs": ["date_or_time"] if raw.get("ambiguous") else [],
        "evidence": None,
        "recoverable": True,
        "handoff": False,
    }


def _normalize_status_shape(raw: dict) -> dict:
    status = str(raw.get("status") or "").strip()
    data = raw.get("data")
    if data is None:
        # check_availability_range's own shape keeps "days"/"start_date"/
        # etc. at the top level instead of nested under "data" — carry the
        # whole payload through rather than losing it.
        data = {k: v for k, v in raw.items() if k not in {"status", "error"}}

    handoff = bool(raw.get("handoff_required"))
    ok = status in _OK_STATUSES

    needs: list[str] = []
    if status == "missing_information":
        needs = list(
            data.get("missing_fields")
            or data.get("missing_information")
            or []
        )
    elif status == "ambiguous":
        needs = ["disambiguation"]

    if status == "success":
        code = "OK"
    elif status == "not_found":
        code = "NOT_FOUND"
    elif status == "missing_information":
        code = "EVIDENCE_MISSING"
    elif status == "ambiguous":
        code = "AMBIGUOUS"
    elif status == "confirmation_required":
        code = str(raw.get("error_code") or "CONFIRMATION_REQUIRED")
    elif status == "error":
        # The real, specific reason belongs in `code` — "error" itself is
        # not informative enough for a caller to branch on.
        code = str(raw.get("error") or "ERROR")
    else:
        code = status.upper() or "UNKNOWN"

    evidence = _extract_evidence(data)

    return {
        "ok": ok,
        "code": code,
        "data": data,
        "needs": needs,
        "evidence": evidence,
        "recoverable": not (status == "error" and handoff),
        "handoff": handoff,
    }


def _normalize_inline_error_shape(raw: dict) -> dict:
    code = str(raw.get("error") or "ERROR")
    data = {k: v for k, v in raw.items() if k not in {"error", "message"}}
    return {
        "ok": False,
        "code": code,
        "data": data,
        "needs": [],
        "evidence": None,
        "recoverable": True,
        "handoff": bool(raw.get("handoff_required")),
    }


def _extract_evidence(data: dict) -> dict | None:
    """Best-effort real identifier a caller can point back to — full
    reference-registry minting is Phase 3; this just carries through
    whatever real ID the underlying result already has instead of
    inventing one."""
    for key in ("booking_id", "payment_id", "coupon_id", "redemption_id"):
        if data.get(key) is not None:
            return {"ref_kind": key, "ref": data[key]}
    return None
