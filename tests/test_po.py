"""PO check: required and format, with the certainty of the rule it rests on."""

from app.checks import po
from app.context import Rule
from tests.conftest import PO_FORMAT_DE, PO_REQUIRED_DE, codes, example, make_ctx

CONFIGURED = (
    Rule(id="config_po_required", type="po_required", certainty="verified_rule", ref="CUST-VLIET / klantconfiguratie"),
    Rule(id="config_po_format", type="po_format", certainty="verified_rule", ref="CUST-VLIET / klantconfiguratie",
         regex=r"^PO-\d{6}$"),
)


def extracted(certainty: str) -> tuple[Rule, ...]:
    return (
        Rule(id="cr_1", type="po_required", certainty=certainty, ref="CUST-ROTH / factuureisen / regel cr_1",
             quote=PO_REQUIRED_DE, explanation_nl="Elke factuur moet het bestelnummer bevatten.", mode="live"),
        Rule(id="cr_2", type="po_format", certainty=certainty, ref="CUST-ROTH / factuureisen / regel cr_2",
             regex=r"^45\d{8}$", quote=PO_FORMAT_DE, explanation_nl="Begint met 45, precies 10 cijfers.", mode="live"),
    )


def invoice_with_po(po_number):
    invoice = example("ORD-DE-0077")
    invoice.pop("po_number", None)
    if po_number is not None:
        invoice["po_number"] = po_number
    return invoice


def test_customer_without_po_requirement_gives_no_finding(config):
    assert po.run(make_ctx(config, invoice_with_po(None))) == []


def test_good_po_passes(config):
    assert po.run(make_ctx(config, invoice_with_po("PO-204518"), po_rules=CONFIGURED)) == []
    ctx = make_ctx(config, invoice_with_po("4500012345"), po_rules=extracted("verified_rule"), requirements_status="verified")
    assert po.run(ctx) == []


def test_missing_po_is_an_error_when_a_person_set_the_rule(config):
    findings = po.run(make_ctx(config, invoice_with_po(None), po_rules=CONFIGURED))
    assert codes(findings) == ["po.missing"]
    assert (findings[0].severity, findings[0].certainty) == ("error", "verified_rule")
    assert findings[0].source.type == "customer_requirement"


def test_wrong_format_is_an_error(config):
    findings = po.run(make_ctx(config, invoice_with_po("PO-2045"), po_rules=CONFIGURED))
    assert codes(findings) == ["po.bad_format"]
    assert "PO-2045" in findings[0].message


def test_a_verified_extracted_rule_carries_the_quote_and_the_dutch_explanation(config):
    ctx = make_ctx(config, invoice_with_po("4400012345"), po_rules=extracted("verified_rule"), requirements_status="verified")
    findings = po.run(ctx)
    assert codes(findings) == ["po.bad_format"]
    assert findings[0].certainty == "verified_rule"
    assert findings[0].source.quote == PO_FORMAT_DE
    assert findings[0].source.explanation_nl == "Begint met 45, precies 10 cijfers."


def test_an_unconfirmed_rule_gives_certainty_ai_and_a_warning_on_every_invoice(config):
    ctx = make_ctx(config, invoice_with_po(None), po_rules=extracted("ai"), requirements_status="ai_extracted")
    findings = po.run(ctx)
    assert codes(findings) == ["po.requirements_unverified", "po.missing"]
    assert [f.certainty for f in findings] == ["ai", "ai"]

    good = make_ctx(config, invoice_with_po("4500012345"), po_rules=extracted("ai"), requirements_status="ai_extracted")
    assert codes(po.run(good)) == ["po.requirements_unverified"]


def test_requirements_that_were_not_analysed_give_a_not_run_warning(config):
    ctx = make_ctx(config, invoice_with_po(None), requirements_status="not_analysed",
                   requirements_reason="AI-check niet uitgevoerd: geen token")
    findings = po.run(ctx)
    assert codes(findings) == ["po.requirements_not_analysed"]
    assert (findings[0].severity, findings[0].certainty) == ("warning", "not_run")
    assert "niet uitgevoerd" in findings[0].message
