"""Totals check: a good invoice passes, each kind of arithmetic error is caught."""

from app.checks import totals
from tests.conftest import codes, example, make_ctx


def test_good_invoice_has_no_findings(config):
    assert totals.run(make_ctx(config)) == []


def test_wrong_line_amount_is_an_error_with_the_calculation_as_source(config):
    invoice = example("ORD-DE-0077")
    invoice["lines"][0]["amount"] = 1530.00  # 16 x 95.00 = 1520.00
    invoice.update(subtotal=1890.00, vat_total=359.10, total=2249.10)  # consistent with the wrong line

    findings = totals.run(make_ctx(config, invoice))

    assert codes(findings) == ["totals.line_amount"]
    finding = findings[0]
    assert (finding.severity, finding.certainty, finding.line) == ("error", "code", 1)
    assert finding.source.type == "calculation"
    assert "16 x 95,00 = 1.520,00, factuur zegt 1.530,00" in finding.source.ref
    assert "Regel 1" in finding.message


def test_wrong_subtotal(config):
    invoice = example("ORD-DE-0077")
    invoice.update(subtotal=1808.00, total=2165.20)  # 1880.00 expected; total follows the stated numbers
    assert codes(totals.run(make_ctx(config, invoice))) == ["totals.subtotal"]


def test_wrong_vat_total(config):
    invoice = example("ORD-DE-0077")
    invoice.update(vat_total=375.20, total=2255.20)  # 19% of 1880.00 = 357.20
    assert codes(totals.run(make_ctx(config, invoice))) == ["totals.vat_total"]


def test_wrong_total(config):
    invoice = example("ORD-DE-0077")
    invoice["total"] = 2273.20  # 1880.00 + 357.20 = 2237.20
    assert codes(totals.run(make_ctx(config, invoice))) == ["totals.total"]


def test_one_cent_on_an_aggregate_is_tolerated_two_cents_is_not(config):
    invoice = example("ORD-DE-0077")
    invoice.update(vat_total=357.21, total=2237.21)
    assert totals.run(make_ctx(config, invoice)) == []
    invoice.update(vat_total=357.22, total=2237.22)
    assert codes(totals.run(make_ctx(config, invoice))) == ["totals.vat_total"]


def test_a_line_is_rounded_half_up_and_must_be_exact(config):
    invoice = example("ORD-DE-0077")
    # 1.5 x 33.33 = 49.995 -> 50.00 with half-up rounding
    invoice["lines"] = [{"description": "Werkstoffpruefung Labor", "service_code": "LAB-MAT", "quantity": 1.5,
                         "unit": "test", "unit_price": 33.33, "vat_rate": 19, "amount": 50.00}]
    invoice.update(subtotal=50.00, vat_total=9.50, total=59.50)
    assert totals.run(make_ctx(config, invoice)) == []
    invoice["lines"][0]["amount"] = 49.99
    invoice.update(subtotal=49.99, vat_total=9.50, total=59.49)
    assert codes(totals.run(make_ctx(config, invoice))) == ["totals.line_amount"]


def test_vat_is_computed_per_rate_group(config):
    invoice = example("ORD-DE-0077")
    invoice["lines"][1]["vat_rate"] = 7  # 1520.00 at 19% = 288.80; 360.00 at 7% = 25.20
    invoice.update(vat_total=314.00, total=2194.00)
    assert totals.run(make_ctx(config, invoice)) == []
