"""The `ReceiptExtraction` structured-output schema for the receipt-reading LLM call.

Spec references: pinned-decisions §7, sprint 4 pins. Deliberately separate from the
domain `app.models.receipt.Receipt`: this is exactly the shape handed to
`client.messages.parse(..., output_format=ReceiptExtraction)`, so it must stay
restricted to JSON-schema features structured outputs supports (plain types, `enum`
via `Literal`/`StrEnum`, `ge`/`le` numeric bounds, `default_factory` for arrays) and
`extra="forbid"` so Claude can't add undeclared fields.

`candidate_totals` is allowed to be empty -- the prompt instructs the model to list
every number that could be a total, but a sufficiently unreadable photo may have
none Claude is willing to name. An empty list is rejected when validating
into the domain `Receipt` (`app.services.receipt_parser.to_receipt`), which makes
the photo unreadable rather than a 0-candidate ask.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.category import Category
from app.models.receipt import LineItemKind


class LineItemExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    quantity: int = Field(ge=1)
    unit_amount_minor: int | None = Field(default=None, ge=0)
    amount_minor: int = Field(ge=0)
    kind: LineItemKind


class CandidateTotalExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label_as_printed: str
    amount_minor: int = Field(ge=0)
    is_grand_total_guess: bool
    # The amount exactly as printed, e.g. "¥6,850" (added 2026-10-02): lets
    # `to_receipt` catch a reading off by a factor of 100 (yen read as cents).
    amount_as_printed: str | None = None


class ReceiptExtraction(BaseModel):
    """Exactly the sprint 4 pinned schema (spec §7)."""

    model_config = ConfigDict(extra="forbid")

    merchant: str | None = None
    currency: str | None = None
    transaction_datetime: str | None = None  # ISO 8601, only if printed on the receipt
    line_items: list[LineItemExtraction] = Field(default_factory=list)
    candidate_totals: list[CandidateTotalExtraction] = Field(default_factory=list)
    description: str
    category: Category
    confidence: float = Field(ge=0, le=1)
    notes: str | None = None
