"""Rates and hours: unit prices and units against the rate card, invoiced hours against the time entries.

More hours than recorded is an error. Fewer hours than recorded is a warning: under-billing
is lost money, but not a reason to reject the invoice.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from app.context import CheckContext
from app.messages import CATEGORY_NL, amount, finding, number, shorten
from app.schemas import Finding, Source

HOUR = "hour"
NEEDS_SERVICE_CODE = ("labour", "lab_test")


def run(ctx: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    order = ctx.invoice.order_id
    invoiced: dict[str, Decimal] = defaultdict(Decimal)

    for index, (line, cat) in enumerate(zip(ctx.invoice.lines, ctx.line_categories), start=1):
        code = line.service_code
        if not code:
            if cat.category in NEEDS_SERVICE_CODE:
                # A category the model gave is not certain, so this finding is not certain either.
                findings.append(finding(
                    "rates.no_service_code", "ai" if cat.source == "model" else "code",
                    Source(type="rate_card", ref=f"{order} / tariefkaart: regel zonder dienstcode"),
                    line=index, category_nl=CATEGORY_NL[cat.category],
                ))
            continue
        card = ctx.rate_card.get(code)
        if card is None:
            findings.append(finding(
                "rates.unknown_service", "code",
                Source(type="rate_card", ref=f"{order} / tariefkaart: {', '.join(sorted(ctx.rate_card)) or 'leeg'}"),
                line=index, service_code=code,
            ))
            continue
        if line.unit_price != card.unit_price:
            findings.append(finding(
                "rates.mismatch", "code",
                Source(type="rate_card", ref=f"{order} / {code}: {amount(card.unit_price)} per {card.unit}"),
                line=index, service_code=code, stated=amount(line.unit_price),
                expected=amount(card.unit_price),
            ))
        if line.unit.strip().casefold() != card.unit.casefold():
            # 16 days at the hourly price is not the agreed rate. The unit is free text, so a
            # different spelling is possible: a warning, a person looks.
            findings.append(finding(
                "rates.unit_mismatch", "code",
                Source(type="rate_card", ref=f"{order} / {code}: {amount(card.unit_price)} per {card.unit}"),
                line=index, service_code=code, stated=shorten(line.unit, 20), expected=card.unit,
            ))
        if card.unit == HOUR:
            invoiced[code] += line.quantity

    period = ctx.invoice.period
    for code, hours in invoiced.items():
        recorded = ctx.recorded_hours.get(code, Decimal("0"))
        if hours == recorded:
            continue
        source = Source(
            type="time_entries",
            ref=f"{order} / {code}: {number(recorded)} uur geboekt van {period.from_} t/m {period.to}",
        )
        findings.append(finding(
            "hours.exceeds_records" if hours > recorded else "hours.below_records", "code", source,
            service_code=code, invoiced=number(hours), recorded=number(recorded),
        ))
    return findings
