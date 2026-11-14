"""The store: schema, seed, the reads a check needs, and the stored checks."""

import sqlite3
from contextlib import closing
from decimal import Decimal

from app.cli import analyse_pending, seed
from app.store import Store
from tests.conftest import SEED, FakeClient
from app.ai.client import Unavailable

AT = "2026-11-14T10:00:00+00:00"


def test_the_schema_has_seven_tables_and_plain_sqlite_is_enough(store):
    with closing(sqlite3.connect(store.path)) as db:
        tables = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    assert tables == ["checks", "contract_rules", "contracts", "customers", "orders", "rate_cards", "time_entries"]


def test_the_seed_holds_three_customers_and_four_orders_with_their_contracts(store):
    assert [store.get_order(o)["customer_id"] for o in ("ORD-DE-0077", "ORD-DE-0082", "ORD-NL-0031", "ORD-NL-0045")] == [
        "CUST-ROTH", "CUST-ROTH", "CUST-VLIET", "CUST-HAVEN"]
    assert store.get_order("ORD-XX-9999") is None
    assert store.get_contract("ORD-DE-0077")["status"] == "not_analysed"
    vliet = store.get_customer("CUST-VLIET")
    assert (vliet["po_required"], vliet["po_format"], vliet["requirements_status"]) == (1, r"^PO-\d{6}$", None)
    assert store.get_customer("CUST-ROTH")["requirements_status"] == "not_analysed"


def test_init_is_idempotent_a_second_run_changes_nothing(tmp_path):
    store = Store(tmp_path / "app.db")
    seed(store, SEED, FakeClient())
    assert store.verify_contract("ORD-DE-0077", "test", AT)
    before = (store.get_contract("ORD-DE-0077"), store.contract_rules("ORD-DE-0077"), store.rate_card("ORD-DE-0077"))

    client = FakeClient()
    seed(store, SEED, client)

    assert client.calls == []  # nothing was pending, so the model was not asked again
    assert (store.get_contract("ORD-DE-0077"), store.contract_rules("ORD-DE-0077"), store.rate_card("ORD-DE-0077")) == before


def test_seeding_without_a_token_and_again_with_one_analyses_what_was_pending(tmp_path):
    store = Store(tmp_path / "app.db")
    seed(store, SEED, Unavailable())
    contract = store.get_contract("ORD-DE-0077")
    assert (contract["status"], contract["reason"]) == ("not_analysed", "AI-check niet uitgevoerd: geen token")

    seed(store, SEED, FakeClient())

    contract = store.get_contract("ORD-DE-0077")
    assert (contract["status"], contract["reason"], contract["mode"], contract["model"]) == ("ai_extracted", None, "live", "sonnet")
    assert store.get_customer("CUST-ROTH")["requirements_status"] == "ai_extracted"
    assert [r["rule_type"] for r in store.requirement_rules("CUST-ROTH")] == ["po_required", "po_format"]


def test_recorded_hours_are_summed_per_service_code_inside_the_period(store):
    assert store.recorded_hours("ORD-NL-0031", "2026-10-01", "2026-10-31") == {"INSP-L1": Decimal("24"), "NDT-UT": Decimal("6")}
    assert store.recorded_hours("ORD-NL-0031", "2026-10-13", "2026-10-14") == {"INSP-L1": Decimal("16")}
    assert store.recorded_hours("ORD-NL-0031", "2026-11-01", "2026-11-30") == {}


def test_rates_come_back_as_decimals(store):
    card = {r["service_code"]: r["unit_price"] for r in store.rate_card("ORD-NL-0031")}
    assert card == {"INSP-L1": Decimal("82.50"), "NDT-UT": Decimal("110.00")}


def test_only_a_rule_set_the_model_produced_can_be_confirmed(store):
    assert store.verify_contract("ORD-DE-0077", "test", AT) is False  # still not_analysed
    assert store.verify_contract("ORD-XX-9999", "test", AT) is False
    analyse_pending(store, FakeClient())
    assert store.verify_contract("ORD-DE-0077", "J. Jansen", AT) is True
    contract = store.get_contract("ORD-DE-0077")
    assert (contract["status"], contract["verified_by"], contract["verified_at"]) == ("verified", "J. Jansen", AT)
    assert store.verify_requirements("CUST-ROTH", "J. Jansen", AT) is True
    assert store.verify_requirements("CUST-VLIET", "J. Jansen", AT) is False  # no free-text requirements


def test_a_new_contract_text_drops_the_old_rules_and_the_old_confirmation(store):
    analyse_pending(store, FakeClient())
    store.verify_contract("ORD-DE-0077", "test", AT)
    store.set_contract_text("ORD-DE-0077", "Neuer Vertragstext.")
    contract = store.get_contract("ORD-DE-0077")
    assert (contract["status"], contract["verified_by"]) == ("not_analysed", None)
    assert store.contract_rules("ORD-DE-0077") == []


def test_known_names_are_customers_contacts_and_inspectors(store):
    names = store.known_names()
    for name in ("Rothmann Anlagenbau GmbH", "Katrin Vogel", "Jonas Brandt"):
        assert name in names


def test_a_check_is_stored_and_read_back_whole(store):
    record = {
        "id": "chk_1", "received_at": AT, "invoice_number": "INV-1", "order_id": "ORD-DE-0077",
        "customer_id": "CUST-ROTH", "outcome": "afgewezen", "input": {"invoice_number": "INV-1", "lines": []},
        "findings": [{"code": "totals.total"}], "summary": "1 fout", "config_hash": "abc",
        "rules": {"contract_status": "verified", "rule_ids": ["cr_1"]}, "ai_mode": "live", "duration_ms": 3,
    }
    store.save_check(record)
    assert store.get_check("chk_1") == {**record, "error": None}
    assert store.get_check("chk_2") is None
    assert [c["id"] for c in store.find_checks("INV-1")] == ["chk_1"]


def test_only_checks_with_a_verdict_are_candidates_for_the_duplicate_check(store):
    base = {"received_at": AT, "invoice_number": "INV-1", "order_id": "ORD-DE-0077", "customer_id": "CUST-ROTH",
            "input": {"invoice_number": "INV-1"}}
    store.save_check({**base, "id": "chk_1", "outcome": "goedgekeurd"})
    store.save_check({**base, "id": "chk_2", "outcome": "geen_oordeel", "error": {"error": "unknown_order"}})
    store.save_check({**base, "id": "chk_3", "outcome": "afgewezen", "customer_id": "CUST-VLIET"})
    assert [c["check_id"] for c in store.earlier_checks("CUST-ROTH")] == ["chk_1"]
