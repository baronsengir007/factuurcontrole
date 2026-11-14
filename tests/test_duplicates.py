"""Near-duplicate check: code finds candidates, a weighted score decides."""

import copy
from decimal import Decimal

from app.checks import duplicates
from app.context import EarlierCheck
from app.schemas import InvoiceIn
from tests.conftest import codes, example, make_ctx


def stored(invoice: dict) -> dict:
    return InvoiceIn.model_validate(invoice).as_stored()


def earlier(invoice: dict, check_id: str = "chk_earlier") -> tuple[EarlierCheck, ...]:
    return (EarlierCheck(check_id, stored(invoice)),)


def test_no_earlier_checks_no_finding(config):
    assert duplicates.run(make_ctx(config)) == []


def test_same_lines_under_another_number_two_days_later_is_suspected(config):
    first = example("ORD-DE-0077")
    second = copy.deepcopy(first)
    second.update(invoice_number="INV-2026-1099", invoice_date="2026-11-05")

    findings = duplicates.run(make_ctx(config, second, earlier_checks=earlier(first)))

    assert codes(findings) == ["duplicate.suspected"]
    finding = findings[0]
    assert (finding.severity, finding.certainty) == ("warning", "code")
    assert finding.source.type == "earlier_check"
    assert "chk_earlier / INV-2026-1042" in finding.source.ref and "score 1.00" in finding.source.ref
    assert "INV-2026-1042" in finding.message and "100%" in finding.message


def test_another_invoice_on_the_same_order_with_other_lines_is_not_suspected(config):
    first = example("ORD-DE-0077")
    second = copy.deepcopy(first)
    second.update(invoice_number="INV-2026-1099")
    second["lines"] = [{**second["lines"][1], "quantity": 5, "amount": 900.00}]
    second.update(subtotal=900.00, vat_total=171.00, total=1071.00)
    # score = 0.4 x 0 + 0 + 0.2 (same order) + 0.1 (period) = 0.3, below the 0.8 threshold
    assert duplicates.run(make_ctx(config, second, earlier_checks=earlier(first))) == []


def test_the_score_is_the_sum_of_the_weights_in_config(config):
    first = example("ORD-DE-0077")
    second = copy.deepcopy(first)
    # Same lines and total, same order, but another period: 0.4 + 0.3 + 0.2 = 0.9
    second.update(invoice_number="INV-2026-1099", period={"from": "2026-11-01", "to": "2026-11-30"})
    findings = duplicates.run(make_ctx(config, second, earlier_checks=earlier(first)))
    assert "score 0.90" in findings[0].source.ref

    stricter = config.duplicates.model_copy(update={"threshold": Decimal("0.95")})
    assert duplicates.run(make_ctx(config, second, earlier_checks=earlier(first), duplicates=stricter)) == []


def test_an_invoice_outside_the_window_is_not_compared(config):
    first = example("ORD-DE-0077")
    first["invoice_date"] = "2026-06-01"  # more than 120 days before
    second = example("ORD-DE-0077")
    second["invoice_number"] = "INV-2026-1099"
    assert duplicates.run(make_ctx(config, second, earlier_checks=earlier(first))) == []


def test_the_same_invoice_again_is_information_not_a_suspected_duplicate(config):
    invoice = example("ORD-DE-0077")
    findings = duplicates.run(make_ctx(config, invoice, earlier_checks=earlier(invoice)))
    assert [(f.code, f.severity) for f in findings] == [("duplicate.resubmitted", "info")]


def test_a_corrected_invoice_with_the_same_number_gives_no_finding(config):
    first = example("ORD-DE-0077")
    corrected = copy.deepcopy(first)
    corrected["lines"][0].update(quantity=15, amount=1425.00)
    corrected.update(subtotal=1785.00, vat_total=339.15, total=2124.15)
    assert duplicates.run(make_ctx(config, corrected, earlier_checks=earlier(first))) == []
