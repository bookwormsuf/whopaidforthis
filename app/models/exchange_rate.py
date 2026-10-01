"""The `ExchangeRate` domain model: one trip->home rate used for one or more expenses.

Spec references: pinned-decisions §4.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ExchangeRate(BaseModel):
    """A currency exchange rate, `base` -> `quote`, published for `rate_date`."""

    model_config = ConfigDict(frozen=True)

    base: str
    quote: str
    requested_date: date
    rate_date: date
    rate: Decimal
    provider: str
    retrieved_at: datetime
