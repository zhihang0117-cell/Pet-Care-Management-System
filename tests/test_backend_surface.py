"""Regression coverage for architecture-independent backend surfaces —
domain tools (app/tools/customer_tools.py), business-logic helpers
(app/db/relational_actions.py), main.py's document/debug endpoints,
ConversationMemory, and RAG retrieval — none of which belonged to
app/orchestrator.py's V1 tool loop, deleted in the 2026-08-10 V1
decommission. Moved out of tests/test_constrained_ai_backend.py (which was
otherwise almost entirely V1-specific and was deleted with it) so this
coverage doesn't disappear along with V1.
"""

from datetime import date

from app.db import relational_actions, supabase_client
from app.tools.customer_tools import _pet_details_for, get_booking_service_options


def test_pet_specific_catalogue_requires_resolved_customer_ownership():
    result = get_booking_service_options.invoke({
        "company_id": 1,
        "service_type": "GROOMING",
        "pet_id": 99,
    })
    assert result["error_code"] == "PET_OWNERSHIP_UNVERIFIED"


def test_pet_detail_lookup_includes_customer_id_filter(monkeypatch):
    class Query:
        def __init__(self):
            self.filters = []

        def select(self, _fields):
            return self

        def eq(self, field, value):
            self.filters.append((field, value))
            return self

        def limit(self, _count):
            return self

        def execute(self):
            return type("Result", (), {"data": [{"pet_type": "Dog", "size": "S"}]})()

    query = Query()
    client = type("Client", (), {"table": lambda self, _name: query})()
    monkeypatch.setattr(supabase_client, "get_supabase_client", lambda: client)

    assert _pet_details_for(1, 42, 99) == ("Dog", "S")
    assert ("company_id", 1) in query.filters
    assert ("customer_id", 42) in query.filters
    assert ("pet_id", 99) in query.filters


def test_last_completed_lookup_is_scoped_to_owned_pet_service_and_done_status(monkeypatch):
    calls = []
    monkeypatch.setattr(
        relational_actions,
        "get_customer_pets",
        lambda context: {
            "status": "success",
            "data": {"pets": [{"pet_id": 1}, {"pet_id": 2}]},
        },
    )
    monkeypatch.setattr(
        relational_actions,
        "_pet_name_map",
        lambda client, company_id, pet_ids: {1: "Milo"},
    )
    monkeypatch.setattr(relational_actions, "today_business", lambda: date(2026, 8, 7))

    def fake_fetch(client, company_id, pet_ids, service_type):
        calls.append((company_id, list(pet_ids), service_type))
        return [
            {
                "grooming_booking_id": 999,
                "pet_id": 1,
                "service_name": "Wrong pending service",
                "booking_date": "2026-08-06",
                "booking_time": "16:00:00",
                "booking_status": "Pending",
                "price": 999,
            },
            {
                "grooming_booking_id": 339,
                "pet_id": 1,
                "service_name": "Standard Bath - Groomers Choice",
                "booking_date": "2026-08-03",
                "booking_time": "14:30:00",
                "booking_status": "Done",
                "price": 80,
                "add_on": "-",
                "add_on_price": 0,
            },
        ]

    monkeypatch.setattr(relational_actions, "_fetch_bookings_for_customer", fake_fetch)
    context = relational_actions.CustomerContext(company_id=7)
    context.resolved_customer_id = 42

    booking = relational_actions._collect_last_completed_customer_booking(
        context,
        object(),
        pet_id=1,
        service_type="GROOMING",
    )

    assert calls == [(7, [1], "GROOMING")]
    assert booking["booking_id"] == 339
    assert booking["package_name"] == "Standard Bath - Groomers Choice"
    assert booking["price"] == 80


def test_last_completed_lookup_rejects_pet_outside_customer_roster(monkeypatch):
    monkeypatch.setattr(
        relational_actions,
        "get_customer_pets",
        lambda context: {"status": "success", "data": {"pets": [{"pet_id": 1}]}},
    )
    context = relational_actions.CustomerContext(company_id=7)
    context.resolved_customer_id = 42

    assert relational_actions._collect_last_completed_customer_booking(
        context, object(), pet_id=999, service_type="GROOMING"
    ) is None


def test_invoice_boundary_rejects_unpaid_and_accepts_paid(monkeypatch):
    import main

    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def execute(self):
            return type("Result", (), {"data": self.rows})()

    class _Client:
        def __init__(self, rows_by_table):
            self.rows_by_table = rows_by_table

        def table(self, name):
            return _Query(self.rows_by_table.get(name, []))

    generated = []
    monkeypatch.setattr(main, "generate_and_send_invoice", lambda *args, **kwargs: generated.append((args, kwargs)) or {"status": "success"})

    monkeypatch.setattr(
        "app.db.supabase_client.get_supabase_client",
        lambda: _Client({"payment": [{"payment_id": 1, "status": "Pending"}]}),
    )
    rejected = main.documents_invoice(main.InvoiceRequest(company_id=7, payment_id=1))
    assert rejected["error"] == "INVOICE_REQUIRES_PAID_PAYMENT"
    assert generated == []

    monkeypatch.setattr(
        "app.db.supabase_client.get_supabase_client",
        lambda: _Client({
            "payment": [{"payment_id": 1, "status": "Paid"}],
            "grooming_booking": [{"grooming_booking_id": 8, "payment_id": 1}],
        }),
    )
    assert main.documents_invoice(main.InvoiceRequest(company_id=7, payment_id=1))["status"] == "success"
    assert len(generated) == 1


def test_booking_status_notice_reports_delivery_failed_when_whatsapp_not_configured(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "build_booking_status_notice",
        lambda *_args: {"phone_number": "+60123456705", "message": "Your booking is now Pending."},
    )
    monkeypatch.setattr(main, "_seed_notice_into_history", lambda *_args: None)
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *_args: {"status": "not_configured"})

    result = main.documents_booking_status_notice(
        main.BookingStatusNoticeRequest(
            company_id=7, service_type="GROOMING", booking_id=1, old_status="Scheduled", new_status="Pending"
        )
    )

    assert result["status"] == "delivery_failed"
    # The notice text is still surfaced to staff even though delivery failed.
    assert result["message"] == "Your booking is now Pending."


def test_outbound_notice_history_uses_request_tenant(monkeypatch):
    import main

    calls = []
    state = type("State", (), {"history": []})()

    class _Memory:
        def get(self, phone_number, company_id):
            calls.append((phone_number, company_id))
            return state

        def save(self, value):
            assert value is state

    monkeypatch.setattr(main, "_memory", _Memory())
    main._seed_notice_into_history(77, "+60123456705", "Your booking is ready")

    assert calls == [("+60123456705", "77")]
    assert state.history[-1]["content"] == "Your booking is ready"


def test_conversation_memory_sweeps_expired_store_and_lock_entries():
    from app.context.memory import ConversationMemory

    memory = ConversationMemory(ttl_seconds=0)
    for index in range(8):
        phone = f"+601200000{index}"
        with memory.session_lock(phone, "7"):
            memory.save(memory.get(phone, "7"))

    # The current key may remain until the next sweep; one-off expired
    # sessions and their locks must not accumulate without bound.
    assert len(memory._store) <= 1
    assert len(memory._last_seen) <= 1
    assert len(memory._locks) <= 1


def test_rag_search_drops_chunks_below_minimum_similarity(monkeypatch):
    from app.rag import retriever as retriever_module

    monkeypatch.setattr(retriever_module, "embed_query", lambda _query: [0.0])

    class _RPC:
        def __init__(self, rows):
            self._rows = rows

        def execute(self):
            return type("Result", (), {"data": self._rows})()

    class _Client:
        def __init__(self, rows):
            self._rows = rows

        def rpc(self, _name, _params):
            return _RPC(self._rows)

    # An off-topic query still gets rows back from the RPC (it has no
    # relevance cutoff) — the closest row is only weakly related, the
    # second is genuinely on-topic.
    rows = [
        {"chunk_id": "1", "content": "Unrelated boilerplate paragraph about something else entirely, padded out.", "metadata": {}, "similarity": 0.22},
        {"chunk_id": "2", "content": "Grooming price list: Standard Bath - Groomers Choice RM45.", "metadata": {}, "similarity": 0.61},
    ]
    monkeypatch.setattr(retriever_module, "get_supabase_client", lambda: _Client(rows))

    results = retriever_module.CompanyRAGRetriever().search("7", "irrelevant question")

    assert len(results) == 1
    assert "RM45" in results[0]["content"]


def test_debug_session_endpoints_key_off_the_same_canonical_phone_chat_uses():
    """/chat stores state under canonical_phone_number(phone) (e.g. "+60
    12-345 6701" -> "+60123456701"). /debug/clear-session and /debug/outbox
    previously keyed off the raw, un-normalized string instead, so clearing
    a session typed with spaces/dashes (exactly what the eval console's
    default test-phone field looks like) silently cleared nothing at all —
    confirmed live: cleared:false while the real session stayed intact."""
    import main

    raw = "+60 12-345 6701"
    canonical = "+60123456701"
    assert main._canonical_or_raw_phone(raw) == canonical
    # Already-canonical input is a no-op, not just "happens to still work".
    assert main._canonical_or_raw_phone(canonical) == canonical
    # An unparseable value falls back to the raw string rather than raising.
    assert main._canonical_or_raw_phone("not-a-phone") == "not-a-phone"
