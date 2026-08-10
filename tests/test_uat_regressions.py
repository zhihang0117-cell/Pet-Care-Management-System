from pathlib import Path
from types import SimpleNamespace

from app.context.state import ConversationState
from app.db.customer_context import canonical_phone_number, phones_match, validate_phone_number
from app.db.date_normalization import extract_customer_date, parse_week_range
from app.db.relational_actions import (
    _room_capacity_status,
    _serialize_daycare_booking,
    create_pet,
)
from app.db.customer_context import CustomerContext
from app.db.time_normalization import extract_duration_minutes, extract_time_from_message, extract_time_range
from app.documents import service as document_service
from app.orchestrator import PawfectOrchestrator


def test_phone_identity_never_matches_a_short_suffix():
    assert phones_match("+60 12-345 6705", "012-345 6705")
    assert canonical_phone_number("012-345 6705") == "+60123456705"
    assert not phones_match("+60 12-345 6705", "6705")
    try:
        validate_phone_number("6705")
    except ValueError:
        pass
    else:
        raise AssertionError("short phone fragments must be rejected")


def test_create_pet_database_boundary_refuses_missing_or_species_as_breed():
    context = CustomerContext(company_id=1, resolved_customer_id=9)

    missing = create_pet(context, pet_name="Milo", pet_type="dog", height_cm=30, breed="")
    species = create_pet(context, pet_name="Milo", pet_type="dog", height_cm=30, breed="dog")

    assert missing["status"] == "missing_information"
    assert missing["data"]["missing_fields"] == ["breed"]
    assert species["status"] == "missing_information"
    assert species["data"]["missing_fields"] == ["breed"]


def test_chinese_date_time_and_duration_are_deterministic():
    assert extract_time_from_message("晚上7点半") == "19:30"
    assert extract_time_from_message("早上十点") == "10:00"
    assert extract_duration_minutes("需要三个小时") == 180
    assert extract_duration_minutes("for 2.5 hours") == 150
    assert extract_time_range("下午5点半到晚上8点半") == ("17:30", "20:30", 180)
    assert extract_customer_date("大后天早上") is not None
    assert parse_week_range("下周下午") is not None


def test_existing_booking_survives_beyond_first_turn(monkeypatch):
    latest = {
        "booking_id": 88,
        "service_type": "DAYCARE",
        "booking_date": "2026-08-08",
        "booking_status": "Scheduled",
    }

    class Repo:
        def get_customer_by_phone(self, *_args):
            return {
                "status": "success",
                "data_found": True,
                "data": {"customer_id": 9, "full_name": "Alicia Lee", "address": "KL"},
            }

        def list_customer_pets(self, *_args):
            return {"status": "success", "data": {"pets": []}}

        def get_latest_booking(self, *_args):
            return {"status": "success", "data": latest}

    monkeypatch.setattr("app.orchestrator.get_relational_repository", lambda: Repo())
    orchestrator = object.__new__(PawfectOrchestrator)
    state = ConversationState(phone_number="+60123456705", company_id="1")
    first = orchestrator._resolve_identity("1", state)
    assert first["latest_booking"]["booking_id"] == 88
    state.history.append({"role": "human", "content": "hi"})
    second = orchestrator._resolve_identity("1", state)
    assert second["latest_booking"]["booking_id"] == 88


def test_pdf_paths_do_not_collide_across_service_tables(monkeypatch):
    uploaded = []
    monkeypatch.setattr(document_service, "_fill_pet_name", lambda _company, booking: booking)
    monkeypatch.setattr(document_service, "get_billing_profile", lambda _company: {"company_name": "P", "currency": "RM"})
    monkeypatch.setattr(document_service, "_customer_for_pet", lambda *_args: {"customer_id": 1, "customer_name": "A", "phone_number": "+60123456705"})
    monkeypatch.setattr(document_service, "_staff_name", lambda *_args: "Sam")
    monkeypatch.setattr(document_service, "_loyalty_snapshot", lambda *_args: None)
    monkeypatch.setattr(document_service, "build_booking_confirmation_pdf", lambda *_args: b"pdf")
    monkeypatch.setattr(
        document_service,
        "upload_customer_document",
        lambda _company, _category, filename, _content: uploaded.append(filename) or f"https://example/{filename}",
    )
    monkeypatch.setattr(document_service, "send_whatsapp_document", lambda *_args: {"status": "sent"})

    for service_type, pet_id in (("GROOMING", 1), ("DAYCARE", 2)):
        result = document_service.generate_and_send_booking_confirmation(
            1,
            {"booking_id": 7, "service_type": service_type, "pet_id": pet_id},
        )
        assert result["status"] == "success"
    assert uploaded == ["grooming-7.pdf", "daycare-7.pdf"]


def test_daycare_add_on_is_exposed_to_pdf_and_payment_layers():
    serialized = _serialize_daycare_booking(
        {
            "daycare_booking_id": 4,
            "package_type": "Hourly Care",
            "add_on": "Webcam access",
            "add_on_price": 12,
        }
    )
    assert serialized["add_on"] == "Webcam access"
    assert serialized["add_on_price"] == 12


class _FakeQuery:
    def __init__(self, table, data):
        self.table = table
        self.data = data

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=self.data[self.table])


class _FakeClient:
    def __init__(self, data):
        self.data = data

    def table(self, name):
        return _FakeQuery(name, self.data)


def test_boarding_room_is_occupied_on_every_intermediate_date(monkeypatch):
    client = _FakeClient(
        {
            "room": [{"capacity": 1}],
            "boarding_booking": [
                {
                    "boarding_booking_id": 1,
                    "check_in_date": "2026-08-01",
                    "check_out_date": "2026-08-10",
                    "booking_status": "Scheduled",
                }
            ],
        }
    )
    monkeypatch.setattr("app.db.relational_actions.get_supabase_client", lambda: client)
    context = CustomerContext(company_id=1)
    middle = _room_capacity_status(context, "Mars", "2026-08-05", "2026-08-06")
    after_checkout = _room_capacity_status(context, "Mars", "2026-08-10", "2026-08-11")
    assert middle == {"capacity": 1, "booked_count": 1, "available": False, "held_for_minutes": None}
    assert after_checkout == {"capacity": 1, "booked_count": 0, "available": True, "held_for_minutes": None}


def test_sql_blocks_full_daycare_interval_and_links_redemption_to_payment():
    root = Path(__file__).resolve().parents[1]
    conflict_sql = (root / "backend/sql/booking_conflict_prevention_migration.sql").read_text()
    redemption_sql = (root / "backend/sql/verify_payment_function.sql").read_text()
    assert "b.booking_date + b.check_in_time, b.booking_date + b.check_out_time" in conflict_sql
    assert "v_checkout + interval '30 minutes'" in conflict_sql
    assert "b.check_in_date <= p_date and p_date < b.check_out_date" in conflict_sql
    assert "set redemption_id = v_redemption_id" in redemption_sql
