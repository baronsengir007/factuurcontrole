"""Pydantic shapes of the API: the draft invoice that comes in and the check that goes out."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal["labour", "lab_test", "travel", "accommodation", "materials", "subcontracting"]
CATEGORIES: tuple[str, ...] = ("labour", "lab_test", "travel", "accommodation", "materials", "subcontracting")
# The categories a contract can exclude from separate invoicing.
EXPENSE_CATEGORIES: tuple[str, ...] = ("travel", "accommodation", "materials", "subcontracting")

Severity = Literal["error", "warning", "info"]
# code: computed by code from structured data. verified_rule: a rule a person confirmed.
# ai: rests on a model answer no person confirmed. not_run: the check could not run.
Certainty = Literal["code", "verified_rule", "ai", "not_run"]
Verdict = Literal["goedgekeurd", "controle_nodig", "afgewezen"]

Money = Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
Quantity = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3)]
Percentage = Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)]
Text = Annotated[str, Field(min_length=1, max_length=64)]


class _In(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, populate_by_name=True)


class Period(_In):
    from_: date = Field(alias="from")
    to: date

    @model_validator(mode="after")
    def _ordered(self) -> "Period":
        if self.to < self.from_:
            raise ValueError("period.to lies before period.from")
        return self


class LineIn(_In):
    description: str = Field(min_length=1, max_length=300)
    service_code: Text | None = None
    category: Category | None = None
    quantity: Quantity
    unit: Text
    unit_price: Money
    vat_rate: Percentage
    amount: Money


class InvoiceIn(_In):
    """A draft invoice (conceptfactuur) as the billing system sends it."""

    invoice_number: Text
    order_id: Text
    customer_id: Text
    country: str = Field(pattern=r"^[A-Z]{2}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    invoice_date: date
    period: Period
    po_number: Text | None = None
    lines: list[LineIn] = Field(min_length=1, max_length=500)
    subtotal: Money
    vat_total: Money
    total: Money

    def as_stored(self) -> dict:
        """The invoice as JSON-ready data, with the field names of the API."""
        return self.model_dump(mode="json", by_alias=True)


class Source(BaseModel):
    """What a finding rests on (bron)."""

    type: Literal[
        "calculation",
        "vat_rule",
        "rate_card",
        "time_entries",
        "contract_clause",
        "customer_requirement",
        "earlier_check",
        "line_category",
    ]
    ref: str
    quote: str | None = None
    explanation_nl: str | None = None


class Finding(BaseModel):
    """One problem found (bevinding)."""

    code: str
    check: str
    severity: Severity
    certainty: Certainty
    message: str
    source: Source
    line: int | None = Field(default=None, description="Line number on the invoice, starting at 1.")
    ai_mode: str | None = Field(default=None, description="Set when the finding rests on a model answer.")
    # The short instruction used in the summary; not part of the API.
    action: str = Field(default="", exclude=True)


class CheckOut(BaseModel):
    """One stored run of the service on one invoice (controle)."""

    check_id: str
    verdict: Verdict
    checked_at: datetime
    invoice_number: str
    order_id: str
    ai_mode: str
    findings: list[Finding]
    summary: str


class FieldError(BaseModel):
    field: str
    problem: str


class ErrorOut(BaseModel):
    """An error response: no verdict was given."""

    error: str
    detail: str
    check_id: str | None = None
    fields: list[FieldError] | None = None
