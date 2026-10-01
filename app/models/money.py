"""The `Money` value type: an integer amount in minor units plus a currency code.

Spec references: pinned-decisions §4, sprint 2 pins. Money is handled with `int` minor
units everywhere in the domain; `Decimal` is used only inside conversion (see
`app.services.conversion`).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.services.currencies import decimals_for, symbol_for


class Money(BaseModel):
    """An amount in the smallest unit of a currency (1 yen, 1 cent)."""

    model_config = ConfigDict(frozen=True)

    amount_minor: int
    currency: str

    def format(self) -> str:
        """Human-readable amount with symbol and thousands separators, e.g. "¥1,200",
        "S$10.42".
        """
        decimals = decimals_for(self.currency)
        symbol = symbol_for(self.currency)
        if decimals == 0:
            return f"{symbol}{self.amount_minor:,}"
        major = self.amount_minor / (10**decimals)
        return f"{symbol}{major:,.{decimals}f}"
