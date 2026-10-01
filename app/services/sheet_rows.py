"""Pure row builders between domain models and Sheet rows (list of Python values).

Spec references: pinned-decisions §10. Amounts are numbers in **major units**; the
caller (google_sheets.py) is responsible for turning each value into the right
`CellData` (`numberValue`/`stringValue`/`boolValue`) for `appendCells`. Column order
here must match `app.services.sheet_layout.HEADERS` exactly.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.models.category import Category
from app.models.expense import Expense, ExpenseRecord, ExpenseSplit
from app.models.receipt import ReceiptLineItem
from app.services.currencies import decimals_for


def _amount_number(amount_minor: int, currency: str) -> float:
    decimals = decimals_for(currency)
    if decimals == 0:
        return float(amount_minor)
    return float(Decimal(amount_minor) / (Decimal(10) ** decimals))


def _minor_from_number(value: object, currency: str) -> int:
    """Parses an amount cell that may come back as `int`, `float`, or `str` -- and,
    defensively, a comma-thousands-formatted `str` (e.g. "1,200"), even though the
    store now always requests `ValueRenderOption.unformatted` so a real read should
    never actually be formatted text.
    """
    decimals = decimals_for(currency)
    text = str(value).replace(",", "") if isinstance(value, str) else str(value)
    as_decimal = Decimal(text)
    return int((as_decimal * (Decimal(10) ** decimals)).to_integral_value())


def expense_to_row(expense: Expense) -> list[object]:
    """One row for the `Expenses` tab, column order per §10."""
    return [
        expense.expense_id,
        expense.created_at.isoformat(),
        expense.transaction_date.isoformat(),
        expense.chat_id,
        expense.message_id,
        expense.created_by_user_id,
        expense.payer_user_id,
        expense.payer_username or "",
        expense.payer_display_name,
        expense.description,
        expense.merchant or "",
        expense.category.value,
        _amount_number(expense.original_amount_minor, expense.original_currency),
        expense.original_currency,
        _amount_number(expense.trip_amount_minor, expense.trip_currency),
        _amount_number(expense.home_amount_minor, expense.home_currency),
        float(round(expense.rate, 8)),
        expense.rate_date.isoformat(),
        expense.source_type,
        expense.split_method,
        expense.status,
        expense.cancelled_at.isoformat() if expense.cancelled_at else "",
        expense.notes or "",
    ]


def split_to_row(
    split: ExpenseSplit, expense_id: str, trip_currency: str, home_currency: str
) -> list[object]:
    """One row for the `Splits` tab, column order per §10."""
    return [
        expense_id,
        split.user_id,
        split.username or "",
        split.display_name,
        _amount_number(split.share_trip_minor, trip_currency),
        _amount_number(split.share_home_minor, home_currency),
        split.split_method,
    ]


def line_item_to_row(
    item: ReceiptLineItem, expense_id: str, item_index: int, trip_currency: str
) -> list[object]:
    """One row for the `Line Items` tab, column order per §10.

    This sprint (sprint 4) always writes `Assigned User IDs` empty and
    `Allocation Method = "unassigned"` -- item claiming is Sprint 6. `Amount <T>` is
    formatted using the trip currency's decimal places; there is no separate "Amount
    <H>" column for line items (known limitation for a receipt paid in the home
    currency, documented in the README).
    """
    return [
        expense_id,
        f"{expense_id}-{item_index:02d}",
        item.description,
        item.quantity,
        _amount_number(item.amount_minor, trip_currency),
        "",
        "unassigned",
        item.kind,
    ]


def row_to_expense_record(
    row: list[object], trip_currency: str, home_currency: str
) -> ExpenseRecord:
    """Inverse of `expense_to_row`, given the trip's two currency codes (not stored
    per-row; they live on the `Trip` tab).
    """
    original_currency = str(row[13])
    original_amount_minor = _minor_from_number(row[12], original_currency)
    merchant = str(row[10]) or None
    cancelled_at_raw = row[21]
    notes = str(row[22]) or None
    return ExpenseRecord(
        expense_id=str(row[0]),
        created_at=datetime.fromisoformat(str(row[1])),
        transaction_date=date.fromisoformat(str(row[2])),
        chat_id=int(row[3]),
        message_id=int(row[4]),
        created_by_user_id=int(row[5]),
        payer_user_id=int(row[6]),
        payer_username=(str(row[7]) or None),
        payer_display_name=str(row[8]),
        description=str(row[9]),
        merchant=merchant,
        category=Category(str(row[11])),
        original_amount_minor=original_amount_minor,
        original_currency=original_currency,
        trip_amount_minor=_minor_from_number(row[14], trip_currency),
        home_amount_minor=_minor_from_number(row[15], home_currency),
        rate=Decimal(str(row[16])),
        rate_date=date.fromisoformat(str(row[17])),
        source_type=str(row[18]),  # type: ignore[arg-type]
        split_method=str(row[19]),  # type: ignore[arg-type]
        status=str(row[20]),  # type: ignore[arg-type]
        cancelled_at=(datetime.fromisoformat(str(cancelled_at_raw)) if cancelled_at_raw else None),
        notes=notes,
    )
