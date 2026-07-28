from intent_debug_catalog import build_intent_debug_catalog
from intent_schema import ALLOWED_SCENARIO_INTENTS


def _scenario(catalog: dict, name: str) -> dict:
    return next(item for item in catalog["scenarios"] if item["scenario_intent"] == name)


def test_catalog_exposes_every_schema_scenario():
    catalog = build_intent_debug_catalog()
    exposed = {item["scenario_intent"] for item in catalog["scenarios"]}

    assert set(ALLOWED_SCENARIO_INTENTS).issubset(exposed)
    assert {"GET_BOOKING_SERVICE_OPTIONS", "BOOKING_CONFIRMATION_ORPHAN"}.issubset(exposed)


def test_booking_catalog_makes_profile_rag_database_and_write_boundaries_visible():
    catalog = build_intent_debug_catalog()

    make_booking = _scenario(catalog, "MAKE_BOOKING")
    availability = _scenario(catalog, "CHECK_AVAILABILITY")
    service_info = _scenario(catalog, "SERVICE_INFORMATION")
    confirmation = _scenario(catalog, "CONFIRM_BOOKING")

    assert make_booking["rag"] is True
    assert make_booking["relational"] is True
    assert any("pet profile" in step.lower() for step in make_booking["process_flow"])
    assert any("category" in step.lower() for step in make_booking["process_flow"])
    assert "check_available_slots" in availability["tools"]
    assert availability["relational"] is True
    assert service_info["rag"] is True
    assert service_info["rag_sources"]
    assert confirmation["write_action"] is True
    assert "create_booking" in confirmation["tools"]
