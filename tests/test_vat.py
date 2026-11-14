"""VAT check: the rate must be the one the country config gives."""

from decimal import Decimal

from app.checks import vat
from app.context import LineCategory
from tests.conftest import codes, example, make_ctx


def test_german_invoice_with_19_percent_passes(config):
    assert vat.run(make_ctx(config)) == []


def test_german_invoice_with_21_percent_is_an_error_that_names_the_config_file(config):
    invoice = example("ORD-DE-0077")
    for line in invoice["lines"]:
        line["vat_rate"] = 21

    findings = vat.run(make_ctx(config, invoice))

    assert codes(findings) == ["vat.wrong_rate", "vat.wrong_rate"]
    assert (findings[0].severity, findings[0].certainty) == ("error", "code")
    assert findings[0].source.type == "vat_rule"
    assert "config/countries/DE.yaml" in findings[0].source.ref and "19%" in findings[0].source.ref
    assert "Duitsland" in findings[0].message and "19%" in findings[0].message


def test_dutch_invoice_needs_21_percent(config):
    invoice = example("ORD-DE-0077")
    ctx = make_ctx(config, invoice, country=config.countries["NL"])
    assert codes(vat.run(ctx)) == ["vat.wrong_rate", "vat.wrong_rate"]
    for line in invoice["lines"]:
        line["vat_rate"] = 21
    assert vat.run(make_ctx(config, invoice, country=config.countries["NL"])) == []


def test_a_rate_per_category_is_config_not_code(config):
    """A country with another rate for one category needs a line in its YAML file, nothing else."""
    country = config.countries["DE"].model_copy(deep=True)
    country.vat.by_category["lab_test"] = Decimal("7")
    invoice = example("ORD-DE-0077")  # line 2 is a lab test at 19%
    ctx = make_ctx(config, invoice, country=country)

    findings = vat.run(ctx)

    assert [(f.code, f.line) for f in findings] == [("vat.wrong_rate", 2)]
    assert ctx.line_categories[1] == LineCategory("lab_test", "rate_card")


def test_a_vat_finding_that_rests_on_a_model_category_is_not_certain(config):
    """Row 0: a model answer can never reject. Here the rate depends on the category the model gave."""
    country = config.countries["DE"].model_copy(deep=True)
    country.vat.by_category["travel"] = Decimal("0")
    invoice = example("ORD-DE-0077")
    invoice["lines"].append({"description": "Pauschale Anfahrt Werk 2", "quantity": 1, "unit": "piece",
                             "unit_price": 120.00, "vat_rate": 19, "amount": 120.00})
    base = make_ctx(config, invoice, country=country).line_categories[:2]

    by_model = vat.run(make_ctx(config, invoice, country=country, line_categories=base + (LineCategory("travel", "model"),)))
    by_field = vat.run(make_ctx(config, invoice, country=country, line_categories=base + (LineCategory("travel", "field"),)))
    by_nobody = vat.run(make_ctx(config, {**invoice, "lines": invoice["lines"][:2] + [{**invoice["lines"][2], "vat_rate": 7}]},
                                 country=country, line_categories=base + (LineCategory(None, "none"),)))

    assert [(f.code, f.certainty, f.line) for f in by_model] == [("vat.wrong_rate", "ai", 3)]
    assert [(f.code, f.certainty, f.line) for f in by_field] == [("vat.wrong_rate", "code", 3)]
    assert [(f.code, f.certainty, f.line) for f in by_nobody] == [("vat.wrong_rate", "not_run", 3)]


def test_with_one_rate_for_every_category_the_category_does_not_matter(config):
    invoice = example("ORD-DE-0077")
    invoice["lines"].append({"description": "Pauschale Anfahrt Werk 2", "quantity": 1, "unit": "piece",
                             "unit_price": 120.00, "vat_rate": 21, "amount": 120.00})
    base = make_ctx(config, invoice).line_categories[:2]
    findings = vat.run(make_ctx(config, invoice, line_categories=base + (LineCategory("travel", "model"),)))
    assert [(f.code, f.certainty, f.line) for f in findings] == [("vat.wrong_rate", "code", 3)]
