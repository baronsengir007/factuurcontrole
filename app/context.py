"""CheckContext: everything a check may read. The checker builds it from the store.

Checks are pure functions over this object: no I/O, so each one is tested with a hand-built
context.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.config import CountryConfig, DuplicatesConfig
from app.schemas import InvoiceIn


@dataclass(frozen=True)
class RateCardLine:
    service_code: str
    description: str
    unit: str
    unit_price: Decimal
    category: str


@dataclass(frozen=True)
class Rule:
    """A contract rule or a customer requirement, as a check uses it."""

    id: str
    type: str  # expense_not_billable | po_required | po_format
    certainty: str  # verified_rule when a person confirmed or configured it, else ai
    ref: str
    category: str | None = None
    regex: str | None = None
    quote: str | None = None
    explanation_nl: str | None = None
    mode: str | None = None  # live | recorded; None for a rule a person configured


@dataclass(frozen=True)
class LineCategory:
    category: str | None
    source: str  # rate_card | field | keyword | model | none
    conflict: str | None = None  # another category that the line's own field or description points to


@dataclass(frozen=True)
class EarlierCheck:
    check_id: str
    invoice: dict  # the stored input of that check


@dataclass(frozen=True)
class CheckContext:
    invoice: InvoiceIn
    country: CountryConfig
    rate_card: dict[str, RateCardLine]
    recorded_hours: dict[str, Decimal]
    contract_status: str
    contract_reason: str | None
    contract_rules: tuple[Rule, ...]
    requirements_status: str | None  # None: the customer has no free-text requirements
    requirements_reason: str | None
    po_rules: tuple[Rule, ...]
    line_categories: tuple[LineCategory, ...]
    earlier_checks: tuple[EarlierCheck, ...]
    duplicates: DuplicatesConfig
    ai_mode: str
