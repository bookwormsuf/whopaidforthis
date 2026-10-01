"""The `Receipt` domain model: a validated, LLM-read receipt.

Spec references: pinned-decisions §7, sprint 4 pins. Deliberately separate from
`app.services.llm_schemas.ReceiptExtraction` (the raw LLM structured-output shape):
this module is what the rest of the app works with once a `ReceiptExtraction` has
been validated and a total chosen. Money is int minor units, like the rest of the
domain (`app.models.money`, `app.models.expense`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.category import Category

LineItemKind = Literal["item", "tax", "service_charge", "discount"]


class ReceiptLineItem(BaseModel):
    """One priced line from a receipt. Spec §9/§10 glossary: adjustment lines (tax,
    service_charge, discount) are never claimable; this sprint does not claim
    anything (Sprint 6).
    """

    model_config = ConfigDict(frozen=True)

    description: str
    quantity: int = Field(ge=1)
    unit_amount_minor: int | None = None
    amount_minor: int
    kind: LineItemKind


class CandidateTotal(BaseModel):
    """One number on the receipt that could plausibly be the grand total."""

    model_config = ConfigDict(frozen=True)

    label_as_printed: str
    amount_minor: int
    is_grand_total_guess: bool


class Receipt(BaseModel):
    """A validated receipt reading, with a chosen total once one has been picked.

    `chosen_total_minor` is `None` until the total-selection flow (`select_total` in
    `app.services.receipt_parser`) has settled on one -- either automatically (a
    single confident candidate) or by the user picking one of `candidate_totals`.
    """

    model_config = ConfigDict(frozen=True)

    merchant: str | None
    currency: str
    transaction_datetime: datetime | None
    line_items: list[ReceiptLineItem] = Field(default_factory=list)
    candidate_totals: list[CandidateTotal] = Field(default_factory=list)
    chosen_total_minor: int | None = None
    description: str
    category: Category
    confidence: float = Field(ge=0, le=1)
    notes: str | None = None
