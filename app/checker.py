"""check_invoice(invoice, deps) -> CheckResult. The deep module.

Loads the context of the order from the store, gives every line a category, runs every check,
lets the decision table give the verdict, and stores the check. Every check always runs;
config only gives the checks their parameters.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from app.ai.classify import classify_line
from app.ai.client import ModelClient
from app.checks import contract, duplicates, po, rates_hours, totals, vat
from app.config import AppConfig, log
from app.context import CheckContext, EarlierCheck, LineCategory, RateCardLine, Rule
from app.messages import summary
from app.schemas import CheckOut, Finding, InvoiceIn, LineIn
from app.store import NO_VERDICT, VERIFIED, Store
from app.verdict import decide

CheckResult = CheckOut

# The fixed registry. Every check runs on every invoice, in this order.
CHECKS = (totals, vat, po, rates_hours, contract, duplicates)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Deps:
    store: Store
    config: AppConfig
    client: ModelClient
    now: Callable[[], datetime] = field(default=_utc_now)


class DomainError(Exception):
    """The invoice cannot be checked: a clear error, no verdict."""

    status = 422
    error = "invalid_invoice"

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail
        self.check_id: str | None = None


class UnknownOrder(DomainError):
    status = 404
    error = "unknown_order"


class CustomerMismatch(DomainError):
    error = "customer_mismatch"


class UnknownCountry(DomainError):
    error = "unknown_country"


class CountryMismatch(DomainError):
    error = "country_mismatch"


class CurrencyMismatch(DomainError):
    error = "currency_mismatch"


def keyword_categories(description: str, keywords: dict[str, list[str]]) -> set[str]:
    """Every category of which a keyword occurs in the description."""
    text = description.casefold()
    return {cat for cat, words in keywords.items() if any(word.casefold() in text for word in words)}


def keyword_category(description: str, keywords: dict[str, list[str]]) -> str | None:
    """The category whose keywords occur in the description; None when none or more than one do."""
    hits = keyword_categories(description, keywords)
    return hits.pop() if len(hits) == 1 else None


def _conflict(category: str, line: LineIn, keywords: dict[str, list[str]], card: RateCardLine | None = None) -> str | None:
    """Another category that the line itself points to, or None.

    A service code or a category field decides the category of a line. When the line's own
    field or its description names a different category, the line may bill something else
    under that code (travel under an inspection code), so a person must look. Words that are
    in the rate card's own description of the service do not count.
    """
    if line.category and line.category != category:
        return line.category
    others = keyword_categories(line.description, keywords) - {category}
    if card is not None:
        others -= keyword_categories(card.description, keywords)
    return min(others) if others else None


def _line_categories(invoice: InvoiceIn, rate_card: dict[str, RateCardLine], deps: Deps) -> tuple[LineCategory, ...]:
    """Rate card, else the line's own field, else the keyword table, else the model, else none."""
    keywords = deps.config.keywords
    known_names: list[str] | None = None
    result = []
    for line in invoice.lines:
        if line.service_code and line.service_code in rate_card:
            card = rate_card[line.service_code]
            result.append(LineCategory(card.category, "rate_card", _conflict(card.category, line, keywords, card)))
        elif line.category:
            result.append(LineCategory(line.category, "field", _conflict(line.category, line, keywords)))
        elif category := keyword_category(line.description, keywords):
            result.append(LineCategory(category, "keyword"))
        else:
            if known_names is None:
                known_names = deps.store.known_names()
            category = classify_line(line.description, deps.client, known_names)
            result.append(LineCategory(category, "model" if category else "none"))
    return tuple(result)


def _rule(row: dict, certainty: str, ref: str) -> Rule:
    return Rule(
        id=row["id"], type=row["rule_type"], certainty=certainty, ref=f"{ref} / regel {row['id']}",
        category=row["category"], regex=row["regex"], quote=row["quote"],
        explanation_nl=row["explanation_nl"], mode=row["mode"],
    )


def _po_rules(customer: dict, store: Store) -> tuple[Rule, ...]:
    """Structured config set by a person counts as verified; extracted rules follow their status."""
    rules: list[Rule] = []
    ref = f"{customer['id']} / klantconfiguratie"
    if customer["po_required"]:
        rules.append(Rule(id="config_po_required", type="po_required", certainty="verified_rule", ref=ref))
    if customer["po_format"]:
        rules.append(Rule(
            id="config_po_format", type="po_format", certainty="verified_rule",
            ref=f"{ref}: formaat {customer['po_format']}", regex=customer["po_format"],
        ))
    certainty = "verified_rule" if customer["requirements_status"] == VERIFIED else "ai"
    for row in store.requirement_rules(customer["id"]):
        rules.append(_rule(row, certainty, f"{customer['id']} / factuureisen"))
    return tuple(rules)


def _context(invoice: InvoiceIn, deps: Deps) -> tuple[CheckContext, dict]:
    store, config = deps.store, deps.config
    order = store.get_order(invoice.order_id)
    if order is None:
        raise UnknownOrder(f"Opdracht {invoice.order_id} bestaat niet. Geen oordeel gegeven.")
    if order["customer_id"] != invoice.customer_id:
        raise CustomerMismatch(
            f"Opdracht {invoice.order_id} hoort niet bij klant {invoice.customer_id}. Geen oordeel gegeven."
        )
    country = config.countries.get(invoice.country)
    if country is None:
        known = ", ".join(sorted(config.countries))
        raise UnknownCountry(f"Land {invoice.country} is niet ingericht (wel: {known}). Geen oordeel gegeven.")
    if order["country"] != invoice.country:
        raise CountryMismatch(
            f"Opdracht {invoice.order_id} hoort bij land {order['country']}, niet bij {invoice.country}. "
            "Geen oordeel gegeven."
        )
    if country.currency != invoice.currency:
        raise CurrencyMismatch(
            f"Valuta {invoice.currency} past niet bij land {invoice.country} ({country.currency}). "
            "Geen oordeel gegeven."
        )

    customer = store.get_customer(order["customer_id"])
    stored_contract = store.get_contract(order["id"]) or {"status": "not_analysed", "reason": "geen contract vastgelegd"}
    rate_card = {r["service_code"]: RateCardLine(
        r["service_code"], r["description"], r["unit"], r["unit_price"], r["category"]
    ) for r in store.rate_card(order["id"])}
    contract_certainty = "verified_rule" if stored_contract["status"] == VERIFIED else "ai"
    contract_rules = tuple(
        _rule(row, contract_certainty, f"{order['id']} / contract") for row in store.contract_rules(order["id"])
    )
    po_rules = _po_rules(customer, store)

    ctx = CheckContext(
        invoice=invoice,
        country=country,
        rate_card=rate_card,
        recorded_hours=store.recorded_hours(order["id"], invoice.period.from_.isoformat(), invoice.period.to.isoformat()),
        contract_status=stored_contract["status"],
        contract_reason=stored_contract.get("reason"),
        contract_rules=contract_rules,
        requirements_status=customer["requirements_status"],
        requirements_reason=customer["requirements_reason"],
        po_rules=po_rules,
        line_categories=_line_categories(invoice, rate_card, deps),
        earlier_checks=tuple(EarlierCheck(c["check_id"], c["invoice"]) for c in store.earlier_checks(customer["id"])),
        duplicates=config.duplicates,
        ai_mode=deps.client.mode,
    )
    used = {
        "contract_status": stored_contract["status"],
        "requirements_status": customer["requirements_status"],
        "rule_ids": [r.id for r in contract_rules + po_rules],
        "decision_table": customer["decision_table"] or "decision_table.yaml",
    }
    return ctx, used


def _new_id() -> str:
    return f"chk_{uuid.uuid4().hex}"


def check_invoice(invoice: InvoiceIn, deps: Deps) -> CheckResult:
    started = time.perf_counter()
    check_id, now = _new_id(), deps.now()
    stored_input = invoice.as_stored()
    try:
        ctx, used = _context(invoice, deps)
    except DomainError as exc:
        exc.check_id = check_id
        _save_no_verdict(deps, check_id, now, stored_input, {"error": exc.error, "detail": exc.detail}, started)
        raise

    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check.run(ctx))
    verdict = decide(findings, deps.config.table(used["decision_table"]))
    result = CheckOut(
        check_id=check_id, verdict=verdict, checked_at=now, invoice_number=invoice.invoice_number,
        order_id=invoice.order_id, ai_mode=deps.client.mode, findings=findings,
        summary=summary(verdict, findings),
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    deps.store.save_check({
        "id": check_id, "received_at": now.isoformat(), "invoice_number": invoice.invoice_number,
        "order_id": invoice.order_id, "customer_id": invoice.customer_id, "outcome": verdict,
        "input": stored_input,
        "findings": [f.model_dump(mode="json", exclude_none=True) for f in findings],
        "summary": result.summary, "config_hash": deps.config.config_hash, "rules": used,
        "ai_mode": deps.client.mode, "duration_ms": duration_ms,
    })
    log.info("check", extra={"fields": {
        "check_id": check_id, "invoice_number": invoice.invoice_number, "order_id": invoice.order_id,
        "verdict": verdict, "finding_codes": [f.code for f in findings],
        "duration_ms": duration_ms, "ai_mode": deps.client.mode,
    }})
    return result


def record_invalid_input(raw: object, error: dict, deps: Deps) -> str:
    """Store a request that failed validation, so the audit trail has every call. No verdict."""
    check_id = _new_id()
    _save_no_verdict(deps, check_id, deps.now(), raw, error, time.perf_counter())
    return check_id


def _save_no_verdict(deps: Deps, check_id: str, now: datetime, raw: object, error: dict, started: float) -> None:
    def text_field(name: str) -> str | None:
        value = raw.get(name) if isinstance(raw, dict) else None
        return value[:64] if isinstance(value, str) else None  # the whole input is in `input`

    duration_ms = round((time.perf_counter() - started) * 1000)
    deps.store.save_check({
        "id": check_id, "received_at": now.isoformat(), "invoice_number": text_field("invoice_number"),
        "order_id": text_field("order_id"), "customer_id": text_field("customer_id"),
        "outcome": NO_VERDICT, "error": error, "input": raw, "config_hash": deps.config.config_hash,
        "ai_mode": deps.client.mode, "duration_ms": duration_ms,
    })
    log.info("check", extra={"fields": {
        "check_id": check_id, "invoice_number": text_field("invoice_number"),
        "order_id": text_field("order_id"), "verdict": NO_VERDICT, "error": error.get("error"),
        "duration_ms": duration_ms, "ai_mode": deps.client.mode,
    }})
