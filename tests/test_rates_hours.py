"""Rates and hours: unit prices against the rate card, hours against the time entries."""

from decimal import Decimal

from app.checks import rates_hours
from app.context import LineCategory
from tests.conftest import codes, example, make_ctx


def test_good_invoice_has_no_findings(config):
    assert rates_hours.run(make_ctx(config)) == []


def test_a_rate_that_differs_from_the_rate_card_is_an_error(config):
    invoice = example("ORD-DE-0077")
    invoice["lines"][0].update(unit_price=99.00, amount=1584.00)

    findings = rates_hours.run(make_ctx(config, invoice))

    assert codes(findings) == ["rates.mismatch"]
    finding = findings[0]
    assert (finding.severity, finding.certainty, finding.line) == ("error", "code", 1)
    assert finding.source.type == "rate_card" and "95,00" in finding.source.ref
    assert "99,00" in finding.message and "95,00" in finding.message


def test_a_unit_that_differs_from_the_rate_card_is_a_warning_and_spelling_in_capitals_is_not(config):
    invoice = example("ORD-DE-0077")
    invoice["lines"][0]["unit"] = "day"  # 16 days at the hourly price; the amounts still add up

    findings = rates_hours.run(make_ctx(config, invoice))

    assert [(f.code, f.severity, f.certainty, f.line) for f in findings] == [("rates.unit_mismatch", "warning", "code", 1)]
    assert "day" in findings[0].message and "per hour" in findings[0].message

    invoice["lines"][0]["unit"] = " Hour "
    assert rates_hours.run(make_ctx(config, invoice)) == []


def test_a_service_code_that_is_not_on_the_rate_card_is_an_error(config):
    invoice = example("ORD-DE-0077")
    invoice["lines"][0]["service_code"] = "INSP-L9"
    assert "rates.unknown_service" in codes(rates_hours.run(make_ctx(config, invoice)))


def test_more_hours_than_recorded_is_an_error_with_the_time_entries_as_source(config):
    invoice = example("ORD-DE-0077")
    invoice["lines"][0].update(quantity=18, amount=1710.00)

    findings = rates_hours.run(make_ctx(config, invoice))

    assert codes(findings) == ["hours.exceeds_records"]
    assert (findings[0].severity, findings[0].certainty) == ("error", "code")
    assert findings[0].source.type == "time_entries" and "16 uur geboekt" in findings[0].source.ref


def test_fewer_hours_than_recorded_is_a_warning_not_an_error(config):
    """Agent decision in ARCHITECTURE.md: under-billing is lost money, not a reason to reject."""
    invoice = example("ORD-DE-0077")
    invoice["lines"][0].update(quantity=12, amount=1140.00)

    findings = rates_hours.run(make_ctx(config, invoice))

    assert codes(findings) == ["hours.below_records"]
    assert findings[0].severity == "warning"


def test_hours_are_summed_over_the_lines_of_one_service_code(config):
    invoice = example("ORD-DE-0077")
    first = invoice["lines"][0]
    invoice["lines"] = [{**first, "quantity": 10, "amount": 950.00}, {**first, "quantity": 6, "amount": 570.00}]
    assert rates_hours.run(make_ctx(config, invoice)) == []


def test_lab_tests_are_not_checked_against_time_entries(config):
    ctx = make_ctx(config, recorded_hours={"INSP-L2": Decimal("16")})  # no entry for LAB-MAT
    assert rates_hours.run(ctx) == []


def test_a_labour_line_without_a_service_code_cannot_be_checked_and_gives_a_warning(config):
    invoice = example("ORD-DE-0077")
    invoice["lines"].append({"description": "Inspektion Zusatzstunden", "quantity": 2, "unit": "hour",
                             "unit_price": 150.00, "vat_rate": 19, "amount": 300.00})
    ctx = make_ctx(config, invoice)
    ctx = make_ctx(config, invoice, line_categories=ctx.line_categories[:2] + (LineCategory("labour", "keyword"),))

    findings = rates_hours.run(ctx)

    assert [(f.code, f.severity, f.line) for f in findings] == [("rates.no_service_code", "warning", 3)]


def test_a_missing_service_code_on_a_line_the_model_called_labour_is_not_certain(config):
    """Row 0: a customer table that rejects on this warning must not reject on a model answer."""
    invoice = example("ORD-DE-0077")
    invoice["lines"].append({"description": "Sonderposten", "quantity": 2, "unit": "hour",
                             "unit_price": 150.00, "vat_rate": 19, "amount": 300.00})
    base = make_ctx(config, invoice).line_categories[:2]
    findings = rates_hours.run(make_ctx(config, invoice, line_categories=base + (LineCategory("labour", "model"),)))
    assert [(f.code, f.certainty) for f in findings] == [("rates.no_service_code", "ai")]
