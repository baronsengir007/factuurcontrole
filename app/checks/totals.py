"""Totals: do the numbers on the invoice add up?

Decimal arithmetic, half-up rounding to cents. A line must be exact; the aggregates may differ
by one cent, because the billing system may round VAT per line.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from app.context import CheckContext
from app.messages import amount, finding, number
from app.schemas import Finding, Source

CENT = Decimal("0.01")
AGGREGATE_TOLERANCE = Decimal("0.01")


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def expected_vat(lines) -> Decimal:
    """VAT per rate group: round(sum of amounts at that rate x rate / 100), then summed."""
    per_rate: dict[Decimal, Decimal] = defaultdict(Decimal)
    for line in lines:
        per_rate[line.vat_rate] += line.amount
    return sum((cents(base * rate / 100) for rate, base in per_rate.items()), Decimal("0"))


def run(ctx: CheckContext) -> list[Finding]:
    inv = ctx.invoice
    findings: list[Finding] = []

    for index, line in enumerate(inv.lines, start=1):
        expected = cents(line.quantity * line.unit_price)
        if expected != line.amount:
            calc = f"{number(line.quantity)} x {amount(line.unit_price)} = {amount(expected)}, factuur zegt {amount(line.amount)}"
            findings.append(finding(
                "totals.line_amount", "code", Source(type="calculation", ref=calc), line=index,
                quantity=number(line.quantity), unit_price=amount(line.unit_price),
                expected=amount(expected), stated=amount(line.amount),
            ))

    line_sum = sum((line.amount for line in inv.lines), Decimal("0"))
    if abs(line_sum - inv.subtotal) > AGGREGATE_TOLERANCE:
        calc = f"som van {len(inv.lines)} regels = {amount(line_sum)}, factuur zegt {amount(inv.subtotal)}"
        findings.append(finding(
            "totals.subtotal", "code", Source(type="calculation", ref=calc),
            expected=amount(line_sum), stated=amount(inv.subtotal),
        ))

    vat = expected_vat(inv.lines)
    if abs(vat - inv.vat_total) > AGGREGATE_TOLERANCE:
        calc = f"btw per tarief over de regelbedragen = {amount(vat)}, factuur zegt {amount(inv.vat_total)}"
        findings.append(finding(
            "totals.vat_total", "code", Source(type="calculation", ref=calc),
            expected=amount(vat), stated=amount(inv.vat_total),
        ))

    total = inv.subtotal + inv.vat_total
    if abs(total - inv.total) > AGGREGATE_TOLERANCE:
        calc = f"{amount(inv.subtotal)} + {amount(inv.vat_total)} = {amount(total)}, factuur zegt {amount(inv.total)}"
        findings.append(finding(
            "totals.total", "code", Source(type="calculation", ref=calc),
            subtotal=amount(inv.subtotal), vat_total=amount(inv.vat_total),
            expected=amount(total), stated=amount(inv.total),
        ))
    return findings
