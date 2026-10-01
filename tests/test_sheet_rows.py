"""Tests for app.services.sheet_rows."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.category import Category
from app.models.expense import Expense, ExpenseSplit
from app.services.sheet_rows import (
    expense_to_row,
    row_to_expense_record,
    row_to_split_record,
    split_to_row,
)

_NOW = datetime.now(UTC).replace(microsecond=0)


def _expense(**overrides: object) -> Expense:
    kwargs = dict(
        expense_id="01EXPENSE00000000000000000",
        created_at=_NOW,
        transaction_date=date(2026, 9, 20),
        chat_id=-100,
        message_id=7,
        created_by_user_id=1,
        payer_user_id=1,
        payer_username="ada",
        payer_display_name="Ada Lovelace",
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
                display_name="Ada Lovelace",
                share_trip_minor=1200,
                share_home_minor=10,
                split_method="equal",
            )
        ],
    )
    kwargs.update(overrides)
    return Expense(**kwargs)


def test_expense_to_row_column_count_matches_header() -> None:
    from app.services.sheet_layout import HEADERS

    row = expense_to_row(_expense())
    assert len(row) == len(HEADERS["Expenses"])


def test_row_to_expense_record_round_trips() -> None:
    expense = _expense()
    row = expense_to_row(expense)
    record = row_to_expense_record(row, "JPY", "SGD")

    assert record.expense_id == expense.expense_id
    assert record.transaction_date == expense.transaction_date
    assert record.chat_id == expense.chat_id
    assert record.message_id == expense.message_id
    assert record.payer_user_id == expense.payer_user_id
    assert record.payer_display_name == expense.payer_display_name
    assert record.description == expense.description
    assert record.category == expense.category
    assert record.original_amount_minor == expense.original_amount_minor
    assert record.original_currency == expense.original_currency
    assert record.trip_amount_minor == expense.trip_amount_minor
    assert record.home_amount_minor == expense.home_amount_minor
    assert record.rate_date == expense.rate_date
    assert record.source_type == expense.source_type
    assert record.split_method == expense.split_method
    assert record.status == expense.status


def test_round_trip_with_sgd_original_currency() -> None:
    expense = _expense(
        original_amount_minor=1200,
        original_currency="SGD",
        trip_amount_minor=148148,
        home_amount_minor=1200,
        payer_display_name="Bo",
        splits=[
            ExpenseSplit(
                user_id=1,
                username="ada",
                display_name="Bo",
                share_trip_minor=148148,
                share_home_minor=1200,
                split_method="equal",
            )
        ],
    )
    row = expense_to_row(expense)
    record = row_to_expense_record(row, "JPY", "SGD")
    assert record.original_currency == "SGD"
    assert record.original_amount_minor == 1200
    assert record.home_amount_minor == 1200


def test_split_to_row_column_count_matches_header() -> None:
    from app.services.sheet_layout import HEADERS

    split = ExpenseSplit(
        user_id=1,
        username="ada",
        display_name="Ada",
        share_trip_minor=1200,
        share_home_minor=10,
        split_method="equal",
    )
    row = split_to_row(split, "01EXPENSE00000000000000000", "JPY", "SGD")
    assert len(row) == len(HEADERS["Splits"])


def test_split_row_round_trips() -> None:
    split = ExpenseSplit(
        user_id=7,
        username=None,
        display_name="Bo",
        share_trip_minor=1617,
        share_home_minor=1310,
        split_method="custom",
    )
    row = split_to_row(split, "01EXPENSE00000000000000000", "JPY", "SGD")
    record = row_to_split_record(row, "JPY", "SGD")
    assert record.expense_id == "01EXPENSE00000000000000000"
    assert (record.user_id, record.username, record.display_name) == (7, None, "Bo")
    assert (record.share_trip_minor, record.share_home_minor) == (1617, 1310)
    assert record.split_method == "custom"
