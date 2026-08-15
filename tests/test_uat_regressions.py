import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from app.context.state import ConversationState
from app.db.customer_context import canonical_phone_number, phones_match, validate_phone_number
from app.db.date_normalization import extract_customer_date, parse_week_range
from app.db.relational_actions import (
    _cancel_booking_atomic,
    _room_capacity_status,
    _serialize_daycare_booking,
    create_pet,
)
from app.db.customer_context import CustomerContext
from app.db.time_normalization import extract_duration_minutes, extract_time_from_message, extract_time_range
from app.documents import service as document_service
from app.orchestrator import PawfectOrchestrator
from app.tools import booking_tools


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


def test_next_weekday_rolls_a_full_week_forward_like_a_human_would():
    """Real gap confirmed live 2026-08-10 ("次不是...次要像人类的做
    法"): "next Wednesday"/"this Friday" both landed in the CURRENT week
    — the `modifier == "next"` branch in date_normalization.py's English
    weekday parser was dead code (already implied by, and never reachable
    without, `days_ahead == 0` on the same line, thanks to `and` binding
    tighter than `or`), so "next" was silently ignored whenever the named
    weekday hadn't happened yet this week — "next Wednesday", said on a
    Monday, resolved to THIS week's Wednesday (2 days away) instead of
    next week's (9 days away). The Chinese/Malay parsers in the same file
    already had this right; this locks the English one in to match."""
    reference = date(2026, 8, 10)  # a real Monday
    assert extract_customer_date("this Friday", today=reference) == date(2026, 8, 14)
    assert extract_customer_date("Friday", today=reference) == date(2026, 8, 14)
    assert extract_customer_date("next Wednesday", today=reference) == date(2026, 8, 19)
    assert extract_customer_date("this Wednesday", today=reference) == date(2026, 8, 12)
    assert extract_customer_date("next Friday", today=reference) == date(2026, 8, 21)
    # Today's own weekday, bare or "this"-modified, means the NEXT
    # occurrence (repeating today back at the customer isn't useful);
    # "next" always means a full week forward regardless.
    assert extract_customer_date("Monday", today=reference) == date(2026, 8, 17)
    assert extract_customer_date("next Monday", today=reference) == date(2026, 8, 17)


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


def test_cancel_booking_calls_the_dedicated_atomic_rpc_not_update_booking_atomic(monkeypatch):
    """Real gap confirmed live 2026-08-10 ("why can't my customer cancel
    the booking"): cancel_booking used to route through the generic
    update_booking() -> update_booking_atomic RPC to set booking_status=
    "Cancelled" — the live database has since grown a guard inside
    update_booking_atomic that explicitly REJECTS that ("Use
    cancel_booking_atomic to cancel a booking", P0001 — a schema/code
    drift not captured in any committed migration), requiring the
    dedicated cancel_booking_atomic RPC instead. Confirmed live: every
    cancellation attempt failed with a raw database error before this
    fix. Locks in that cancel_booking's write path now calls the right
    RPC with the right arguments."""
    calls = []

    class _RPC:
        def __init__(self, name, params):
            self.name = name
            self.params = params

        def execute(self):
            calls.append((self.name, self.params))
            return SimpleNamespace(data={
                "grooming_booking_id": 723,
                "booking_status": "Cancelled",
                "pet_id": 1,
                "price": 80.0,
            })

    class _Client:
        def rpc(self, name, params):
            return _RPC(name, params)

    monkeypatch.setattr("app.db.relational_actions.get_supabase_client", lambda: _Client())
    context = CustomerContext(company_id=1)

    result = _cancel_booking_atomic(context, 723, "GROOMING")

    assert result["status"] == "success"
    assert calls == [(
        "cancel_booking_atomic",
        {"p_company_id": 1, "p_booking_type": "grooming", "p_booking_id": 723},
    )]


def test_daycare_price_is_computed_deterministically_by_tiered_duration(monkeypatch):
    """Explicit business rule, 2026-08-10 ("daycare只要少于三个小时...就
    是20块一个小时，如果是大于3个小时，都是55块"): DAYCARE is billed
    RM20/hour under 3 hours, flat RM55 at 3 hours or more — computed
    server-side from the real duration, never trusted from the model's own
    `price` argument (which used to be relied on for hourly arithmetic:
    "set price to hourly rate x number of hours"). Covers both ways a
    DAYCARE duration can legitimately arrive: an explicit drop-off +
    pickup clock time pair, or a drop-off time plus duration_minutes with
    pickup derived. GROOMING/BOARDING are unaffected (not touched by this
    change)."""
    captured = []

    class _FakeRepo:
        def create_booking(self, company_id, command):
            captured.append(command)
            return {"status": "success", "data": {"booking_id": 1}}

    monkeypatch.setattr(booking_tools, "get_relational_repository", lambda: _FakeRepo())
    monkeypatch.setattr(booking_tools, "booking_window_error", lambda _date: None)
    monkeypatch.setattr(booking_tools, "resolve_date_string", lambda value: value)

    base_args = dict(
        company_id=1, customer_id=1, pet_id=1, pet_name="Milo",
        service_type="DAYCARE", package_name="Hourly Care", date="2026-08-20",
    )

    # Under 3 hours, via an explicit duration + derived pickup: 90 minutes
    # -> RM20 * 1.5 = RM30, not whatever the model passed as `price`.
    result = booking_tools.create_booking.func(
        **base_args, time="09:00", price=999, duration_minutes=90,
    )
    assert result.get("status") == "success"
    assert captured[-1].price_quote == 30.0
    assert captured[-1].check_out_time == "10:30"

    # Exactly 3 hours (the boundary) counts as "3 hours or more" -> flat
    # RM55, via an explicit drop-off + pickup pair (no duration_minutes).
    result = booking_tools.create_booking.func(
        **base_args, time="09:00", check_out_time="12:00", price=None,
    )
    assert result.get("status") == "success"
    assert captured[-1].price_quote == 55.0

    # Well over 3 hours -> still flat RM55, not scaled further.
    result = booking_tools.create_booking.func(
        **base_args, time="09:00", check_out_time="17:00", price=1,
    )
    assert result.get("status") == "success"
    assert captured[-1].price_quote == 55.0

    # DAYCARE no longer requires the model to pass a price at all.
    result = booking_tools.create_booking.func(
        **base_args, time="09:00", duration_minutes=60, price=None,
    )
    assert result.get("status") == "success"
    assert captured[-1].price_quote == 20.0


def test_daycare_price_grounding_corrects_a_stale_self_quoted_price():
    """Real gap confirmed live 2026-08-10: create_booking's own real,
    deterministic DAYCARE price (see the tiered-pricing test above) is
    correct in the actual database write, but the model was observed
    restating whatever price it had guessed BEFORE that call in its final
    confirmation text — a 90-minute booking was billed RM30 for real while
    the customer was told RM55 in the chat reply. This corrects that
    specific, narrow case (exactly one RM mention in the whole reply,
    disagreeing with the tool's own real result)."""
    trace = [{
        "tool": "create_booking",
        "result": json.dumps({
            "success": True,
            "data": {"service_type": "DAYCARE", "price": 30.0},
        }),
    }]

    wrong = AIMessage(content="Booked! **Price**: RM55. Booking confirmed.")
    corrected = PawfectOrchestrator._ground_daycare_price_response(wrong, trace)
    assert "RM30" in corrected.content
    assert "RM55" not in corrected.content

    # Already correct — must be left alone, not just coincidentally re-set.
    right = AIMessage(content="Booked! **Price**: RM30. Booking confirmed.")
    untouched = PawfectOrchestrator._ground_daycare_price_response(right, trace)
    assert untouched is right

    # More than one RM mention (e.g. base price + a separate add-on price)
    # — deliberately left alone rather than guessing which one is wrong.
    ambiguous = AIMessage(content="Price: RM55. Add-on: RM15.")
    assert PawfectOrchestrator._ground_daycare_price_response(ambiguous, trace) is ambiguous

    # Not a DAYCARE booking — must never touch a GROOMING/BOARDING reply.
    grooming_trace = [{
        "tool": "create_booking",
        "result": json.dumps({
            "success": True,
            "data": {"service_type": "GROOMING", "price": 80.0},
        }),
    }]
    grooming_reply = AIMessage(content="Booked! **Price**: RM999.")
    assert PawfectOrchestrator._ground_daycare_price_response(grooming_reply, grooming_trace) is grooming_reply


def test_register_loyalty_member_calls_the_dedicated_atomic_rpc_not_a_manual_id_insert(monkeypatch):
    """Real gap confirmed live 2026-08-11 ("检查我的crud on...loyalty"):
    register_loyalty_member used to compute the next loyalty_id itself
    (_next_table_id: SELECT MAX(loyalty_id)+1) and insert directly. That
    manual id, once written, never advances the table's own underlying
    sequence — every one of these writes left the real sequence a little
    further behind the table's true max. Confirmed live: a plain insert
    relying on the loyaltymember table's own column default (no explicit
    loyalty_id — exactly what register_loyalty_member_atomic does
    instead) collided with an already-existing row THREE TIMES in a row
    before finally succeeding, on a company with a grand total of 25
    members. Locks in that the write now goes through the atomic RPC
    instead."""
    calls = []

    class _LoyaltyMemberQuery:
        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data=[])  # not an existing member yet

    class _RPC:
        def __init__(self, name, params):
            self.name = name
            self.params = params

        def execute(self):
            calls.append((self.name, self.params))
            return SimpleNamespace(data={
                "member": {"loyalty_id": 29, "points_balance": 0, "tier": "Bronze"},
                "already_member": False,
            })

    class _Client:
        def table(self, name):
            assert name == "loyaltymember"
            return _LoyaltyMemberQuery()

        def rpc(self, name, params):
            return _RPC(name, params)

    monkeypatch.setattr("app.db.relational_actions.get_supabase_client", lambda: _Client())
    context = CustomerContext(company_id=1)
    context.resolved_customer_id = 110

    from app.db.relational_actions import register_loyalty_member

    result = register_loyalty_member(context, confirmed=True)

    assert result["status"] == "success"
    assert result["data"]["loyalty_id"] == 29
    assert result["data"]["already_member"] is False
    assert calls == [(
        "register_loyalty_member_atomic",
        {"p_company_id": 1, "p_customer_id": 110},
    )]


def test_sql_blocks_full_daycare_interval_and_links_redemption_to_payment():
    root = Path(__file__).resolve().parents[1]
    conflict_sql = (root / "backend/sql/booking_conflict_prevention_migration.sql").read_text()
    redemption_sql = (root / "backend/sql/verify_payment_function.sql").read_text()
    assert "b.booking_date + b.check_in_time, b.booking_date + b.check_out_time" in conflict_sql
    assert "v_checkout + interval '30 minutes'" in conflict_sql
    assert "b.check_in_date <= p_date and p_date < b.check_out_date" in conflict_sql
    assert "set redemption_id = v_redemption_id" in redemption_sql
