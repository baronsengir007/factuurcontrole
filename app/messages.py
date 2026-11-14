"""Dutch message templates per finding code, and the summary line.

A billing employee reads these. Every message is one or two plain sentences. The templates
live in code, so a message cannot drift from the finding it belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.schemas import Finding, Source


@dataclass(frozen=True)
class CodeSpec:
    check: str
    severity: str
    message: str
    action: str


CATEGORY_NL = {
    "labour": "arbeid",
    "lab_test": "laboratoriumwerk",
    "travel": "reiskosten",
    "accommodation": "verblijfkosten",
    "materials": "materiaalkosten",
    "subcontracting": "kosten van onderaannemers",
}

CODES: dict[str, CodeSpec] = {
    "totals.line_amount": CodeSpec(
        "totals", "error",
        "Regel {line}: {quantity} x {unit_price} = {expected}, maar op de factuur staat {stated}.",
        "reken regel {line} na",
    ),
    "totals.subtotal": CodeSpec(
        "totals", "error",
        "De regels tellen op tot {expected}, maar als subtotaal staat er {stated}.",
        "reken het subtotaal na",
    ),
    "totals.vat_total": CodeSpec(
        "totals", "error",
        "De btw over de regels is {expected}, maar op de factuur staat {stated}.",
        "reken de btw na",
    ),
    "totals.total": CodeSpec(
        "totals", "error",
        "Subtotaal {subtotal} plus btw {vat_total} is {expected}, maar als totaal staat er {stated}.",
        "reken het totaal na",
    ),
    "vat.wrong_rate": CodeSpec(
        "vat", "error",
        "Regel {line} heeft {stated}% btw. Voor {country_nl} hoort dat {expected}% te zijn.",
        "pas het btw-tarief van regel {line} aan",
    ),
    "po.missing": CodeSpec(
        "po", "error",
        "Deze klant eist een inkoopordernummer (PO) op de factuur, maar het ontbreekt.",
        "vul het inkoopordernummer in",
    ),
    "po.bad_format": CodeSpec(
        "po", "error",
        "Het inkoopordernummer {po_number} past niet bij het formaat dat de klant eist.",
        "controleer het inkoopordernummer",
    ),
    "po.requirements_not_analysed": CodeSpec(
        "po", "warning",
        "De factuureisen van deze klant zijn niet door AI gelezen ({reason}). "
        "Controle nodig: niet uitgevoerd. Controleer het inkoopordernummer zelf.",
        "controleer het inkoopordernummer tegen de eisen van de klant",
    ),
    "po.requirements_unverified": CodeSpec(
        "po", "warning",
        "De factuureisen van deze klant zijn door AI gelezen ({count} gevonden) maar nog niet door "
        "een collega bevestigd.",
        "laat de factuureisen van de klant bevestigen",
    ),
    "rates.mismatch": CodeSpec(
        "rates_hours", "error",
        "Regel {line}: het tarief voor {service_code} is {stated}, maar afgesproken is {expected}.",
        "pas het tarief van regel {line} aan",
    ),
    "rates.unknown_service": CodeSpec(
        "rates_hours", "error",
        "Regel {line}: dienstcode {service_code} staat niet op de tariefkaart van deze opdracht.",
        "controleer de dienstcode van regel {line}",
    ),
    "rates.no_service_code": CodeSpec(
        "rates_hours", "warning",
        "Regel {line} is {category_nl} maar heeft geen dienstcode. Tarief en uren zijn daardoor "
        "niet te controleren.",
        "vul de dienstcode van regel {line} in",
    ),
    "rates.unit_mismatch": CodeSpec(
        "rates_hours", "warning",
        "Regel {line}: op de factuur staat de eenheid {stated}, maar {service_code} is afgesproken "
        "per {expected}. Controleer de hoeveelheid en het tarief.",
        "controleer de eenheid van regel {line}",
    ),
    "hours.exceeds_records": CodeSpec(
        "rates_hours", "error",
        "Voor {service_code} staan {invoiced} uur op de factuur, maar in de urenregistratie staan "
        "{recorded} uur in deze periode.",
        "controleer de uren voor {service_code}",
    ),
    "hours.below_records": CodeSpec(
        "rates_hours", "warning",
        "Voor {service_code} staan {invoiced} uur op de factuur, terwijl er {recorded} uur geboekt "
        "zijn. Mogelijk wordt er te weinig gefactureerd.",
        "controleer de uren voor {service_code}",
    ),
    "contract.expense_not_billable": CodeSpec(
        "contract", "error",
        "Regel {line} ({description}, {amount}) is {category_nl}. Volgens het contract mogen "
        "{category_nl} niet apart gefactureerd worden.",
        "haal regel {line} van de factuur of controleer het contract",
    ),
    "contract.rules_unverified": CodeSpec(
        "contract", "warning",
        "De contractregels van deze opdracht zijn door AI uitgelezen ({count} gevonden) maar nog "
        "niet door een collega bevestigd.",
        "laat de contractregels bevestigen",
    ),
    "contract.not_analysed": CodeSpec(
        "contract", "warning",
        "Het contract van deze opdracht is niet door AI gecontroleerd ({reason}). "
        "Controle nodig: niet uitgevoerd.",
        "controleer de factuur zelf tegen het contract",
    ),
    "lines.ai_classified": CodeSpec(
        "contract", "warning",
        "Regel {line} ({description}) is automatisch ingedeeld als {category_nl}. Controleer of dat klopt.",
        "controleer de indeling van regel {line}",
    ),
    "lines.unclassified": CodeSpec(
        "contract", "warning",
        "Regel {line} ({description}) kon niet worden ingedeeld. Controleer zelf of deze kosten "
        "gefactureerd mogen worden.",
        "controleer regel {line}",
    ),
    "lines.category_conflict": CodeSpec(
        "contract", "warning",
        "Regel {line} ({description}) is ingedeeld als {category_nl}, maar de omschrijving of de "
        "categorie op de regel wijst op {other_nl}. Controleer of deze kosten gefactureerd mogen worden.",
        "controleer de indeling van regel {line}",
    ),
    "duplicate.suspected": CodeSpec(
        "duplicates", "warning",
        "Deze factuur lijkt sterk op factuur {other_number}, die eerder is gecontroleerd "
        "(overeenkomst {score}%). Mogelijk een dubbele factuur.",
        "vergelijk met factuur {other_number}",
    ),
    "duplicate.resubmitted": CodeSpec(
        "duplicates", "info",
        "Factuur {invoice_number} is eerder met dezelfde inhoud gecontroleerd.",
        "",
    ),
}


def amount(value: Decimal) -> str:
    """1520 -> '1.520,00' (Dutch notation)."""
    text = f"{value.quantize(Decimal('0.01')):,.2f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def number(value: Decimal) -> str:
    """16.0 -> '16', 16.5 -> '16,5'."""
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


def shorten(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def finding(
    code: str, certainty: str, source: Source, line: int | None = None,
    ai_mode: str | None = None, **params,
) -> Finding:
    """Build a finding from its code: severity, check and both texts come from the table."""
    spec = CODES[code]
    params = {"line": line, **params}
    return Finding(
        code=code,
        check=spec.check,
        severity=spec.severity,
        certainty=certainty,
        message=spec.message.format(**params),
        source=source,
        line=line,
        ai_mode=ai_mode,
        action=spec.action.format(**params),
    )


def summary(verdict: str, findings: list[Finding]) -> str:
    """Counts plus the first thing to do. Built from templates; no model writes this."""
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    if not errors and not warnings:
        return "Geen fouten of twijfelpunten gevonden."
    counts = (
        f"{len(errors)} {'fout' if len(errors) == 1 else 'fouten'}, "
        f"{len(warnings)} {'twijfelpunt' if len(warnings) == 1 else 'twijfelpunten'}."
    )
    if verdict == "afgewezen":
        # Only a certain finding can reject. With a stricter customer table that can be a warning.
        certain = [f for f in errors + warnings if f.certainty in ("code", "verified_rule")]
        return f"{counts} Niet versturen. Eerst: {(certain or errors or warnings)[0].action}."
    first = (errors or warnings)[0]
    return f"{counts} Laat iemand meekijken voor het versturen. Eerst: {first.action}."
