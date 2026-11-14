"""Shared test tools: a fake model, a seeded temporary store, and a hand-built CheckContext."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai.client import ModelError
from app.checker import Deps
from app.cli import analyse_pending
from app.config import ROOT, load_config
from app.context import CheckContext, LineCategory, RateCardLine, Rule
from app.main import create_app
from app.schemas import InvoiceIn
from app.store import Store

SEED = ROOT / "data" / "seed"
KEY = {"X-API-Key": "test-key"}

TRAVEL_DE = "Reisekosten werden nicht gesondert berechnet."
TRAVEL_NL = "Reiskosten zijn inbegrepen in het uurtarief en worden niet apart gefactureerd."
HOTEL_DE = "Übernachtungskosten werden nicht erstattet."
PO_REQUIRED_DE = "Jede Rechnung muss unsere Bestellnummer enthalten."
PO_FORMAT_DE = "Die Bestellnummer beginnt mit 45 und hat genau 10 Ziffern."


def good_reply(task: str, prompt: str) -> dict:
    """What a careful model answers for the seed texts."""
    if task == "requirement_rules":
        return {"rules": [
            {"type": "po_required", "quote": PO_REQUIRED_DE,
             "explanation_nl": "Elke factuur moet het bestelnummer van de klant bevatten."},
            {"type": "po_format", "regex": r"^45\d{8}$", "quote": PO_FORMAT_DE,
             "explanation_nl": "Het bestelnummer begint met 45 en heeft precies 10 cijfers."},
        ]}
    if task == "contract_rules":
        for quote, category, nl in (
            (TRAVEL_DE, "travel", "Reiskosten worden niet apart in rekening gebracht."),
            (TRAVEL_NL, "travel", "Reiskosten zitten in het uurtarief."),
            (HOTEL_DE, "accommodation", "Overnachtingskosten worden niet vergoed."),
        ):
            if quote in prompt:
                return {"rules": [{"type": "expense_not_billable", "category": category,
                                   "quote": quote, "explanation_nl": nl}]}
        return {"rules": []}
    return {"category": "travel"}


class FakeClient:
    """A ModelClient that answers from a function and remembers every call."""

    mode = "live"

    def __init__(self, reply=good_reply):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, task: str, prompt: str) -> dict:
        self.calls.append((task, prompt))
        answer = self.reply(task, prompt)
        if isinstance(answer, Exception):
            raise answer
        return answer


class BrokenClient(FakeClient):
    def __init__(self):
        super().__init__(lambda task, prompt: ModelError("het model antwoordde niet binnen 20 s"))


@pytest.fixture(scope="session")
def config():
    return load_config()


@pytest.fixture
def store(tmp_path) -> Store:
    """A fresh database with the synthetic seed; nothing analysed yet."""
    store = Store(tmp_path / "test.db")
    store.init_schema()
    store.load_seed(SEED)
    return store


@pytest.fixture
def verified_store(store) -> Store:
    """The seed after a good model read every text and a person confirmed every rule set."""
    analyse_pending(store, FakeClient())
    for order in ("ORD-DE-0077", "ORD-DE-0082", "ORD-NL-0031", "ORD-NL-0045"):
        assert store.verify_contract(order, "test", "2026-11-14T10:00:00+00:00")
    assert store.verify_requirements("CUST-ROTH", "test", "2026-11-14T10:00:00+00:00")
    return store


def fixed_now() -> datetime:
    return datetime(2026, 11, 14, 10, 12, 3, tzinfo=timezone.utc)


def make_deps(store: Store, config, client=None) -> Deps:
    return Deps(store=store, config=config, client=client or FakeClient(), now=fixed_now)


def make_client(store: Store, config, client=None) -> TestClient:
    return TestClient(create_app(make_deps(store, config, client), api_key="test-key"))


@pytest.fixture
def api(verified_store, config) -> TestClient:
    with make_client(verified_store, config) as client:
        yield client


def example(order_id: str) -> dict:
    """A valid invoice for an order, as a dict that a test can change."""
    return json.loads((SEED / "examples" / f"{order_id}.json").read_text(encoding="utf-8"))


def line(description: str, quantity, unit_price, **extra) -> dict:
    amount = (Decimal(str(quantity)) * Decimal(str(unit_price))).quantize(Decimal("0.01"))
    return {"description": description, "quantity": quantity, "unit": "piece",
            "unit_price": unit_price, "vat_rate": 19, "amount": float(amount), **extra}


def with_line(invoice: dict, extra_line: dict) -> dict:
    """The invoice plus one line, with the totals made consistent again."""
    inv = copy.deepcopy(invoice)
    extra_line = {**extra_line, "vat_rate": inv["lines"][0]["vat_rate"]}
    inv["lines"].append(extra_line)
    rate = Decimal(str(extra_line["vat_rate"]))
    subtotal = sum(Decimal(str(l["amount"])) for l in inv["lines"])
    vat = (subtotal * rate / 100).quantize(Decimal("0.01"))
    inv.update(subtotal=float(subtotal), vat_total=float(vat), total=float(subtotal + vat))
    return inv


RATE_CARD_DE77 = {
    "INSP-L2": RateCardLine("INSP-L2", "Inspektion Druckbehaelter, Level 2", "hour", Decimal("95.00"), "labour"),
    "LAB-MAT": RateCardLine("LAB-MAT", "Werkstoffpruefung Labor", "test", Decimal("180.00"), "lab_test"),
}


def make_ctx(config, invoice: dict | None = None, **overrides) -> CheckContext:
    """A context for ORD-DE-0077 in which every check passes; a test overrides what it needs."""
    inv = InvoiceIn.model_validate(invoice or example("ORD-DE-0077"))
    categories = []
    for l in inv.lines:
        if l.service_code in RATE_CARD_DE77:
            categories.append(LineCategory(RATE_CARD_DE77[l.service_code].category, "rate_card"))
        elif l.category:
            categories.append(LineCategory(l.category, "field"))
        else:
            categories.append(LineCategory(None, "none"))
    values = dict(
        invoice=inv,
        country=config.countries[inv.country],
        rate_card=RATE_CARD_DE77,
        recorded_hours={"INSP-L2": Decimal("16")},
        contract_status="verified",
        contract_reason=None,
        contract_rules=(),
        requirements_status=None,
        requirements_reason=None,
        po_rules=(),
        line_categories=tuple(categories),
        earlier_checks=(),
        duplicates=config.duplicates,
        ai_mode="live",
    )
    values.update(overrides)
    return CheckContext(**values)


def travel_rule(certainty: str = "verified_rule") -> Rule:
    return Rule(id="cr_1", type="expense_not_billable", certainty=certainty, ref="ORD-DE-0077 / contract / regel cr_1",
                category="travel", quote=TRAVEL_DE, explanation_nl="Reiskosten worden niet apart in rekening gebracht.",
                mode="live")


def codes(findings) -> list[str]:
    return [f.code for f in findings]
