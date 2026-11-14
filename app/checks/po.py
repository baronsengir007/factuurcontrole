"""PO number: present when the customer requires one, and in the customer's format.

The rules come from the customer's structured config (set by a person) or from the customer's
free-text requirements (read by the model at intake). The certainty of a finding follows the
rule: a rule no person confirmed gives certainty `ai`.
"""

from __future__ import annotations

import re

from app.context import CheckContext, Rule
from app.messages import finding
from app.schemas import Finding, Source


def _source(rule: Rule) -> Source:
    return Source(
        type="customer_requirement", ref=rule.ref, quote=rule.quote, explanation_nl=rule.explanation_nl
    )


def run(ctx: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    customer = ctx.invoice.customer_id

    if ctx.requirements_status == "not_analysed":
        findings.append(finding(
            "po.requirements_not_analysed", "not_run",
            Source(type="customer_requirement", ref=f"{customer} / factuureisen, status not_analysed"),
            reason=ctx.requirements_reason or "onbekende reden",
        ))
    elif ctx.requirements_status == "ai_extracted":
        count = sum(1 for r in ctx.po_rules if r.certainty == "ai")
        findings.append(finding(
            "po.requirements_unverified", "ai",
            Source(type="customer_requirement", ref=f"{customer} / factuureisen, status ai_extracted"),
            ai_mode=ctx.ai_mode, count=count,
        ))

    required = next((r for r in ctx.po_rules if r.type == "po_required"), None)
    formats = [r for r in ctx.po_rules if r.type == "po_format" and r.regex]
    po_number = ctx.invoice.po_number

    if not po_number:
        rule = required or (formats[0] if formats else None)  # a format rule implies a PO is needed
        if rule:
            findings.append(finding("po.missing", rule.certainty, _source(rule), ai_mode=rule.mode))
        return findings

    for rule in formats:
        if not re.fullmatch(rule.regex, po_number):
            findings.append(finding(
                "po.bad_format", rule.certainty, _source(rule), ai_mode=rule.mode, po_number=po_number,
            ))
    return findings
