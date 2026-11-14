"""Contract check: invoice lines against the rules taken from the contract."""

from decimal import Decimal

from app.checker import _conflict
from app.checks import contract
from app.context import LineCategory, RateCardLine
from app.schemas import LineIn
from tests.conftest import TRAVEL_DE, codes, example, line, make_ctx, travel_rule, with_line


def travel_invoice(**extra):
    return with_line(example("ORD-DE-0077"), line("Reisekosten Anfahrt", 1, 120.00, **extra))


def test_invoice_without_excluded_costs_passes(config):
    assert contract.run(make_ctx(config, contract_rules=(travel_rule(),))) == []


def test_travel_line_against_a_confirmed_clause_is_an_error_with_quote_and_explanation(config):
    ctx = make_ctx(config, travel_invoice(category="travel"), contract_rules=(travel_rule(),))

    findings = contract.run(ctx)

    assert codes(findings) == ["contract.expense_not_billable"]
    finding = findings[0]
    assert (finding.severity, finding.certainty, finding.line) == ("error", "verified_rule", 3)
    assert finding.source.type == "contract_clause"
    assert finding.source.quote == TRAVEL_DE  # the original German sentence
    assert finding.source.explanation_nl == "Reiskosten worden niet apart in rekening gebracht."
    assert "reiskosten" in finding.message and "Regel 3" in finding.message


def test_a_cost_category_the_contract_does_not_exclude_passes(config):
    invoice = with_line(example("ORD-DE-0077"), line("Uebernachtung Hotel", 1, 110.00, category="accommodation"))
    assert contract.run(make_ctx(config, invoice, contract_rules=(travel_rule(),))) == []


def test_rules_no_person_confirmed_give_certainty_ai_and_a_warning_on_every_invoice(config):
    ctx = make_ctx(config, travel_invoice(category="travel"), contract_status="ai_extracted",
                   contract_rules=(travel_rule("ai"),))
    findings = contract.run(ctx)
    assert codes(findings) == ["contract.rules_unverified", "contract.expense_not_billable"]
    assert [f.certainty for f in findings] == ["ai", "ai"]

    clean = make_ctx(config, contract_status="ai_extracted", contract_rules=(travel_rule("ai"),))
    assert codes(contract.run(clean)) == ["contract.rules_unverified"]


def test_a_contract_that_was_not_analysed_gives_a_not_run_warning(config):
    ctx = make_ctx(config, contract_status="not_analysed", contract_reason="AI-check niet uitgevoerd: geen token")
    findings = contract.run(ctx)
    assert codes(findings) == ["contract.not_analysed"]
    assert (findings[0].severity, findings[0].certainty) == ("warning", "not_run")
    assert "niet uitgevoerd" in findings[0].message and "geen token" in findings[0].message


def test_a_line_the_model_classified_is_never_certain_even_under_a_confirmed_rule(config):
    invoice = travel_invoice()
    base = make_ctx(config, invoice)
    ctx = make_ctx(config, invoice, contract_rules=(travel_rule(),),
                   line_categories=base.line_categories[:2] + (LineCategory("travel", "model"),))

    findings = contract.run(ctx)

    assert codes(findings) == ["lines.ai_classified", "contract.expense_not_billable"]
    assert [f.certainty for f in findings] == ["ai", "ai"]
    assert findings[0].severity == "warning"


def test_a_line_that_points_to_another_category_gives_a_certain_warning(config):
    base = make_ctx(config)
    ctx = make_ctx(config, contract_rules=(travel_rule(),),
                   line_categories=(LineCategory("labour", "rate_card", "travel"),) + base.line_categories[1:])
    findings = contract.run(ctx)
    assert [(f.code, f.severity, f.certainty, f.line) for f in findings] == [
        ("lines.category_conflict", "warning", "code", 1)]
    assert "dienstcode op de tariefkaart" in findings[0].source.ref


def test_words_from_the_rate_cards_own_description_are_not_a_conflict(config):
    card = RateCardLine("LAB-MAT", "Materialpruefung Labor", "test", Decimal("180.00"), "lab_test")

    def conflict(description: str) -> str | None:
        invoice_line = LineIn.model_validate(line(description, 1, 180.00, service_code="LAB-MAT"))
        return _conflict("lab_test", invoice_line, config.keywords, card)

    assert conflict("Materialpruefung Labor, Probe 2") is None
    assert conflict("Materialpruefung Labor und Hotel") == "accommodation"


def test_a_line_nobody_could_classify_gives_a_warning(config):
    ctx = make_ctx(config, travel_invoice(), contract_rules=(travel_rule(),))  # no category on line 3
    findings = contract.run(ctx)
    assert [(f.code, f.severity, f.certainty, f.line) for f in findings] == [("lines.unclassified", "warning", "not_run", 3)]
