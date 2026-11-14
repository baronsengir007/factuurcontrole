"""Contract: invoice lines against the rules taken from the contract of the order.

The model wrote the rules at intake; this check only applies them. A finding is certain
(`verified_rule`) only when a person confirmed the rule set AND the line category did not come
from the model. Until a person confirmed the set, every invoice on the order gets a warning,
so a rule the model missed can never lead to goedgekeurd. A line whose own field or description
points to another category than its service code or category field gets a warning too.
"""

from __future__ import annotations

from app.context import CheckContext
from app.messages import CATEGORY_NL, amount, finding, shorten
from app.schemas import Finding, Source

CODE_SOURCES = ("rate_card", "field", "keyword")
SOURCE_NL = {"rate_card": "dienstcode op de tariefkaart", "field": "categorie op de regel"}


def run(ctx: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    order = ctx.invoice.order_id

    if ctx.contract_status == "not_analysed":
        findings.append(finding(
            "contract.not_analysed", "not_run",
            Source(type="contract_clause", ref=f"{order} / contract, status not_analysed"),
            reason=ctx.contract_reason or "onbekende reden",
        ))
    elif ctx.contract_status == "ai_extracted":
        findings.append(finding(
            "contract.rules_unverified", "ai",
            Source(type="contract_clause", ref=f"{order} / contract, status ai_extracted"),
            ai_mode=ctx.ai_mode, count=len(ctx.contract_rules),
        ))

    for index, (line, cat) in enumerate(zip(ctx.invoice.lines, ctx.line_categories), start=1):
        description = shorten(line.description)
        if cat.source == "model":
            findings.append(finding(
                "lines.ai_classified", "ai",
                Source(type="line_category", ref=f"regel {index}: door het model ingedeeld als {cat.category}"),
                line=index, ai_mode=ctx.ai_mode, description=description,
                category_nl=CATEGORY_NL[cat.category],
            ))
        elif cat.category is None:
            findings.append(finding(
                "lines.unclassified", "not_run",
                Source(type="line_category", ref=f"regel {index}: geen categorie, geen trefwoord, geen model"),
                line=index, description=description,
            ))
        if cat.conflict:
            findings.append(finding(
                "lines.category_conflict", "code",
                Source(type="line_category", ref=(
                    f"regel {index}: ingedeeld als {cat.category} ({SOURCE_NL[cat.source]}), "
                    f"maar de regel zelf wijst op {cat.conflict}"
                )),
                line=index, description=description, category_nl=CATEGORY_NL[cat.category],
                other_nl=CATEGORY_NL[cat.conflict],
            ))

        for rule in ctx.contract_rules:
            if rule.type != "expense_not_billable" or rule.category != cat.category:
                continue
            certain = rule.certainty == "verified_rule" and cat.source in CODE_SOURCES
            findings.append(finding(
                "contract.expense_not_billable", "verified_rule" if certain else "ai",
                Source(type="contract_clause", ref=rule.ref, quote=rule.quote, explanation_nl=rule.explanation_nl),
                line=index, ai_mode=rule.mode, description=description, amount=amount(line.amount),
                category_nl=CATEGORY_NL[cat.category],
            ))
    return findings
