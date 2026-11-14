"""Near-duplicate invoices: code finds the candidates, a weighted score decides.

Candidates are earlier stored checks of the same customer with another invoice number, inside
the window. The score is explainable: the source of the finding lists which features matched.
Weights, threshold and window come from config/duplicates.yaml.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.context import CheckContext
from app.messages import finding
from app.schemas import Finding, Source


def line_features(invoice: dict) -> frozenset[tuple[str, Decimal, Decimal]]:
    """What a line is, without its text: (service code or category or description, quantity, price)."""
    return frozenset(
        (
            line.get("service_code") or line.get("category") or " ".join(line["description"].lower().split()),
            Decimal(str(line["quantity"])).normalize(),
            Decimal(str(line["unit_price"])).normalize(),
        )
        for line in invoice["lines"]
    )


def _same_content(a: dict, b: dict) -> bool:
    keys = ("order_id", "invoice_date", "period", "po_number")
    return (
        all(a.get(k) == b.get(k) for k in keys)
        and line_features(a) == line_features(b)
        and Decimal(str(a["total"])) == Decimal(str(b["total"]))
    )


def run(ctx: CheckContext) -> list[Finding]:
    cfg = ctx.duplicates
    current = ctx.invoice.as_stored()
    features = line_features(current)
    total = ctx.invoice.total
    findings: list[Finding] = []
    best: tuple[Decimal, str, str, list[str]] | None = None
    resubmitted = False

    for earlier in ctx.earlier_checks:
        other = earlier.invoice
        if other["invoice_number"] == current["invoice_number"]:
            if not resubmitted and _same_content(other, current):
                resubmitted = True
                findings.append(finding(
                    "duplicate.resubmitted", "code",
                    Source(type="earlier_check", ref=f"{earlier.check_id} / {other['invoice_number']}"),
                    invoice_number=current["invoice_number"],
                ))
            continue
        days = abs((ctx.invoice.invoice_date - date.fromisoformat(other["invoice_date"])).days)
        if days > cfg.window_days:
            continue

        other_features = line_features(other)
        union = features | other_features
        overlap = Decimal(len(features & other_features)) / Decimal(len(union)) if union else Decimal("0")
        other_total = Decimal(str(other["total"]))
        total_close = abs(total - other_total) <= abs(other_total) * cfg.total_tolerance_pct / 100
        same_order = other["order_id"] == current["order_id"]
        periods_overlap = (
            date.fromisoformat(other["period"]["from"]) <= ctx.invoice.period.to
            and ctx.invoice.period.from_ <= date.fromisoformat(other["period"]["to"])
        )
        score = (
            cfg.weights.lines * overlap
            + (cfg.weights.total if total_close else 0)
            + (cfg.weights.order if same_order else 0)
            + (cfg.weights.period if periods_overlap else 0)
        )
        matched = [f"regels {round(overlap * 100)}% gelijk"]
        if total_close:
            matched.append(f"totaal binnen {cfg.total_tolerance_pct}%")
        if same_order:
            matched.append("zelfde opdracht")
        if periods_overlap:
            matched.append("periode overlapt")
        if score >= cfg.threshold and (best is None or score > best[0]):
            best = (score, earlier.check_id, other["invoice_number"], matched)

    if best:
        score, check_id, other_number, matched = best
        findings.append(finding(
            "duplicate.suspected", "code",
            Source(
                type="earlier_check",
                ref=f"{check_id} / {other_number}: score {score:.2f} (drempel {cfg.threshold}); {', '.join(matched)}",
            ),
            other_number=other_number, score=round(score * 100),
        ))
    return findings
