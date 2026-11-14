"""VAT: does each line carry the rate the country config gives for its category?

When a country has one rate for every category, the category does not matter and the finding
is certain. When the rate depends on the category, a finding on a line whose category came
from the model (or from nobody) is not certain, so it cannot reject the invoice on its own.
"""

from __future__ import annotations

from app.context import CheckContext
from app.messages import finding, number
from app.schemas import Finding, Source

CODE_SOURCES = ("rate_card", "field", "keyword")


def run(ctx: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    country = ctx.country
    for index, (line, cat) in enumerate(zip(ctx.invoice.lines, ctx.line_categories), start=1):
        expected = country.vat_rate(cat.category)
        if line.vat_rate != expected:
            kind = "standaardtarief" if expected == country.vat.standard else f"tarief voor {cat.category}"
            if cat.source in CODE_SOURCES or not country.vat.by_category:
                certainty = "code"
            else:
                certainty = "ai" if cat.source == "model" else "not_run"
            findings.append(finding(
                "vat.wrong_rate", certainty,
                Source(type="vat_rule", ref=f"config/countries/{country.country}.yaml: {kind} {number(expected)}%"),
                line=index, stated=number(line.vat_rate), expected=number(expected),
                country_nl=country.name_nl,
            ))
    return findings
