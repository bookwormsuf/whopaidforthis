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
    """A validated receipt reading (spec §7: built from a `ReceiptExtraction` by
    `app.services.receipt_parser.to_receipt`). `currency` is already resolved to the
    trip or home currency; `transaction_datetime` is `None` when no valid date was
    printed. At least one candidate total is required: a reading with none is
    unreadable, not a receipt.
    """

    model_config = ConfigDict(frozen=True)

    merchant: str | None
    currency: str
    transaction_datetime: datetime | None
    line_items: list[ReceiptLineItem] = Field(default_factory=list)
    candidate_totals: list[CandidateTotal] = Field(min_length=1)
    description: str
    category: Category
    confidence: float = Field(ge=0, le=1)
    notes: str | None = None
