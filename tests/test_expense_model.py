"""Tests for app.models.expense.Expense's invariant validator (spec §5)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.category import Category
from app.models.expense import Expense, ExpenseSplit

_NOW = datetime.now(UTC)


def _base_kwargs(**overrides: object) -> dict:
    kwargs = dict(
        expense_id="01EXPENSE00000000000000000",
        created_at=_NOW,
        transaction_date=date(2026, 9, 20),
        chat_id=-100,
        message_id=1,
        created_by_user_id=1,
        payer_user_id=1,
        payer_username="ada",
        payer_display_name="Ada",
        description="Ramen",
        merchant=None,
        category=Category.FOOD,
        original_amount_minor=1200,
        original_currency="JPY",
        trip_currency="JPY",
        home_currency="SGD",
        trip_amount_minor=1200,
        home_amount_minor=10,
        rate=Decimal("0.0081"),
        rate_date=date(2026, 9, 19),
        source_type="text",
        split_method="equal",
        status="confirmed",
        cancelled_at=None,
        notes=None,
        splits=[
            ExpenseSplit(
                user_id=1,
                username="ada",
                display_name="Ada",
                share_trip_minor=1200,
                share_home_minor=10,
                split_method="equal",
            )
        ],
    )
    kwargs.update(overrides)
    return kwargs


def test_valid_expense_constructs() -> None:
    Expense(**_base_kwargs())


def test_rejects_mismatched_trip_shares() -> None:
    bad_splits = [
        ExpenseSplit(
            user_id=1,
            username="ada",
            display_name="Ada",
            share_trip_minor=1199,  # off by one
            share_home_minor=10,
            split_method="equal",
        )
    ]
    with pytest.raises(ValidationError):
        Expense(**_base_kwargs(splits=bad_splits))


def test_rejects_mismatched_home_shares() -> None:
    bad_splits = [
        ExpenseSplit(
            user_id=1,
            username="ada",
            display_name="Ada",
            share_trip_minor=1200,
            share_home_minor=11,  # off by one
            split_method="equal",
        )
    ]
    with pytest.raises(ValidationError):
        Expense(**_base_kwargs(splits=bad_splits))


def test_rejects_mismatched_original_amount() -> None:
    with pytest.raises(ValidationError):
        Expense(**_base_kwargs(original_amount_minor=1199))


def test_rejects_original_currency_not_trip_or_home() -> None:
    with pytest.raises(ValidationError):
        Expense(**_base_kwargs(original_currency="THB"))
