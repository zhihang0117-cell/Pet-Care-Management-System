"""Pure inspection helpers for results produced by one tool-loop batch."""

from __future__ import annotations


def reused_failed_mutation(
    tool_name: str, failed_mutation_results: dict[str, dict]
) -> dict | None:
    cached_failure = failed_mutation_results.get(tool_name)
    if cached_failure is None:
        return None
    return {
        **cached_failure,
        "_internal_duplicate_mutation_suppressed": True,
        "message": (
            "This mutation already returned a non-recoverable operational "
            "failure during this customer turn. Do not call it again; explain "
            "the failure and continue without claiming success."
        ),
    }


def booking_availability_repair(
    records_by_id: dict[str, dict],
) -> tuple[dict, dict] | None:
    """Return exact availability and retry args from a rejected booking draft."""
    stale_booking_record = next(
        (
            record
            for record in records_by_id.values()
            if record["tool_call"]["name"] == "create_booking"
            and isinstance(record["result"], dict)
            and record["result"].get("error") == "UNVERIFIED_AVAILABILITY_SLOT"
            and isinstance(record["result"].get("_internal_required_args"), dict)
            and isinstance(record["result"].get("_internal_retry_args"), dict)
        ),
        None,
    )
    if stale_booking_record is None:
        return None
    result = stale_booking_record["result"]
    return result["_internal_required_args"], result["_internal_retry_args"]


def availability_result(records_by_id: dict[str, dict]):
    record = next(
        (
            item
            for item in records_by_id.values()
            if item["tool_call"]["name"] == "check_availability"
        ),
        None,
    )
    return record.get("result") if record is not None else None


def batch_has_duplicate_suppression(records_by_id: dict[str, dict]) -> bool:
    return any(
        isinstance(record.get("result"), dict)
        and (
            record["result"].get("_internal_duplicate_read_suppressed")
            or record["result"].get("_internal_duplicate_mutation_suppressed")
        )
        for record in records_by_id.values()
    )
