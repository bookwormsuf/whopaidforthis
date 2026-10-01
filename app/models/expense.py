"""The `Expense` domain model and its splits. Spec references: pinned-decisions §5, §10.

`Expense.trip_currency`/`home_currency` are not Expenses-tab columns (the trip's
currencies live on the `Trip` tab, not repeated per row); they are carried on the
model purely so the invariant validator below can check `original_amount_minor`
against the right one of `trip_amount_minor`/`home_amount_minor`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.category import Category

SourceType = Literal["text", "receipt"]
SplitMethod = Literal["equal", "custom", "items"]
Status = Literal["confirmed", "cancelled"]


class ExpenseSplit(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int
    username: str | None
    display_name: str
    share_trip_minor: int
    share_home_minor: int
    split_method: SplitMethod


class Expense(BaseModel):
    model_config = ConfigDict(frozen=True)

    expense_id: str
    created_at: datetime
    transaction_date: date
    chat_id: int
    message_id: int
    created_by_user_id: int
    payer_user_id: int
    payer_username: str | None
    payer_display_name: str
    description: str
    merchant: str | None
    category: Category
    original_amount_minor: int
    original_currency: str
    trip_currency: str
    home_currency: str
    trip_amount_minor: int
    home_amount_minor: int
    rate: Decimal
    rate_date: date
    source_type: SourceType
    split_method: SplitMethod
    status: Status
    cancelled_at: datetime | None
    notes: str | None
    splits: list[ExpenseSplit]

    @model_validator(mode="after")
    def _check_invariants(self) -> Expense:
        share_trip_sum = sum(s.share_trip_minor for s in self.splits)
        if share_trip_sum != self.trip_amount_minor:
            raise ValueError(
                f"sum(share_trip)={share_trip_sum} != trip_amount_minor={self.trip_amount_minor}"
            )
        share_home_sum = sum(s.share_home_minor for s in self.splits)
        if share_home_sum != self.home_amount_minor:
            raise ValueError(
                f"sum(share_home)={share_home_sum} != home_amount_minor={self.home_amount_minor}"
            )
        if self.original_currency == self.trip_currency:
            if self.original_amount_minor != self.trip_amount_minor:
                raise ValueError("original_amount_minor must equal trip_amount_minor")
        elif self.original_currency == self.home_currency:
            if self.original_amount_minor != self.home_amount_minor:
                raise ValueError("original_amount_minor must equal home_amount_minor")
        else:
            raise ValueError("original_currency must be the trip or home currency")
        return self


class ExpenseRecord(BaseModel):
    """Expenses-tab columns only, no splits. Used by read commands."""

    model_config = ConfigDict(frozen=True)

    expense_id: str
    created_at: datetime
    transaction_date: date
    chat_id: int
    message_id: int
    created_by_user_id: int
    payer_user_id: int
    payer_username: str | None
    payer_display_name: str
    description: str
    merchant: str | None
    category: Category
    original_amount_minor: int
    original_currency: str
    trip_amount_minor: int
    home_amount_minor: int
    rate: Decimal
    rate_date: date
    source_type: SourceType
    split_method: SplitMethod
    status: Status
    cancelled_at: datetime | None
    notes: str | None
