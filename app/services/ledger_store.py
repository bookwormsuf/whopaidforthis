"""The `LedgerStore` interface and an in-memory implementation for tests.

Spec references: pinned-decisions §10.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol

from app.models.exchange_rate import ExchangeRate
from app.models.expense import Expense, ExpenseRecord
from app.models.receipt import ReceiptLineItem
from app.models.trip import Trip
from app.models.user import TripUser


class LedgerUnavailable(RuntimeError):
    """The ledger backend (Google Sheets) could not be reached or refused a call.

    Message must never include a Sheet URL, Sheet ID, or Sheet contents. `status`
    carries the HTTP status code when one is known (e.g. 429, 500), else None.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TripSheetInaccessible(RuntimeError):
    """A chat has a binding row, but the trip Sheet it points to can no longer be
    opened (403/404/not shared any more). Distinct from `LedgerUnavailable`
    (a transient/backend problem): this is "the binding itself is stale", which the
    handlers can offer a specific fix for (`/settrip` again, or re-share).

    Message is generic; never includes a Sheet URL or ID. `sheet_id` carries it for
    callers that need it internally (never for display).
    """

    def __init__(self, sheet_id: str) -> None:
        super().__init__("This group's trip Sheet can no longer be opened")
        self.sheet_id = sheet_id


# The one user-facing line for a LedgerUnavailable, shared by every caller (handlers
# that catch it locally, and the PTB-wide error handler as the last resort).
LEDGER_UNAVAILABLE_TEXT = "Couldn't reach Google Sheets, please try again."


class LedgerStore(Protocol):
    """Persistence for trip bindings, trip facts, and trip users."""

    async def get_binding(self, chat_id: int) -> Trip | None: ...

    async def save_binding(self, trip: Trip) -> None: ...

    async def can_open(self, sheet_id: str) -> bool: ...

    async def initialise_trip_sheet(self, trip: Trip) -> None: ...

    async def upsert_user(self, trip: Trip, user: TripUser, joined: bool | None) -> TripUser: ...

    async def list_users(self, trip: Trip, joined_only: bool = False) -> list[TripUser]: ...

    # -- Sprint 2: expenses, rates, dedup -----------------------------------

    async def append_expense(
        self, trip: Trip, expense: Expense, line_items: Sequence[ReceiptLineItem] = ()
    ) -> None: ...

    async def list_expenses(self, trip: Trip) -> list[ExpenseRecord]: ...

    async def cancel_expense(
        self, trip: Trip, expense_id: str, cancelled_at: datetime, note_suffix: str
    ) -> bool: ...

    async def expense_exists(self, trip: Trip, expense_id: str) -> bool: ...

    async def find_rate(
        self, trip: Trip, base: str, quote: str, requested_date: date
    ) -> ExchangeRate | None: ...

    async def save_rate(self, trip: Trip, rate: ExchangeRate) -> None: ...


def merge_user(existing: TripUser | None, incoming: TripUser, joined: bool | None) -> TripUser:
    """Pure merge logic shared by every LedgerStore implementation's `upsert_user`.

    `incoming` carries the freshly observed username/display name and a `last_seen`
    timestamp (built via `TripUser.from_telegram_user`). `joined=None` means "keep
    whatever joined flag the user already has"; a brand-new user with joined=None
    starts out not joined.
    """
    if existing is None:
        return TripUser(
            user_id=incoming.user_id,
            username=incoming.username,
            display_name=incoming.display_name,
            first_seen=incoming.last_seen,
            last_seen=incoming.last_seen,
            joined=bool(joined) if joined is not None else False,
        )
    return TripUser(
        user_id=existing.user_id,
        username=incoming.username,
        display_name=incoming.display_name,
        first_seen=existing.first_seen,
        last_seen=incoming.last_seen,
        joined=existing.joined if joined is None else joined,
    )


class InMemoryLedgerStore:
    """In-memory LedgerStore for tests and for use before Sheets wiring exists.

    `unopenable_sheet_ids` simulates Sheets that exist but are not shared with the
    service account, for testing the `/settrip` access-check path (`can_open`).
    `inaccessible_sheet_ids` simulates a *currently bound* trip Sheet that has since
    been unshared/deleted, for testing the `get_binding` -> `TripSheetInaccessible`
    path.
    """

    def __init__(
        self,
        unopenable_sheet_ids: frozenset[str] = frozenset(),
        inaccessible_sheet_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._unopenable_sheet_ids = unopenable_sheet_ids
        self._inaccessible_sheet_ids = inaccessible_sheet_ids
        self._bindings: dict[int, Trip] = {}
        self._users: dict[str, dict[int, TripUser]] = {}
        self.initialised_sheet_ids: set[str] = set()
        self._expenses: dict[str, dict[str, Expense]] = {}
        self._expense_order: dict[str, list[str]] = {}
        self._rates: dict[str, dict[tuple[str, str, date], ExchangeRate]] = {}
        self._line_items: dict[str, list[ReceiptLineItem]] = {}
        self.get_binding_calls = 0

    async def get_binding(self, chat_id: int) -> Trip | None:
        self.get_binding_calls += 1
        trip = self._bindings.get(chat_id)
        if trip is not None and trip.sheet_id in self._inaccessible_sheet_ids:
            raise TripSheetInaccessible(trip.sheet_id)
        return trip

    async def save_binding(self, trip: Trip) -> None:
        self._bindings[trip.chat_id] = trip

    async def can_open(self, sheet_id: str) -> bool:
        return sheet_id not in self._unopenable_sheet_ids

    async def initialise_trip_sheet(self, trip: Trip) -> None:
        self.initialised_sheet_ids.add(trip.sheet_id)
        self._users.setdefault(trip.sheet_id, {})

    async def upsert_user(self, trip: Trip, user: TripUser, joined: bool | None) -> TripUser:
        users = self._users.setdefault(trip.sheet_id, {})
        existing = users.get(user.user_id)
        merged = merge_user(existing, user, joined)
        users[user.user_id] = merged
        return merged

    async def list_users(self, trip: Trip, joined_only: bool = False) -> list[TripUser]:
        users = self._users.get(trip.sheet_id, {}).values()
        if joined_only:
            return [user for user in users if user.joined]
        return list(users)

    # -- Sprint 2: expenses, rates, dedup -----------------------------------

    async def append_expense(
        self, trip: Trip, expense: Expense, line_items: Sequence[ReceiptLineItem] = ()
    ) -> None:
        expenses = self._expenses.setdefault(trip.sheet_id, {})
        order = self._expense_order.setdefault(trip.sheet_id, [])
        expenses[expense.expense_id] = expense
        order.append(expense.expense_id)
        if line_items:
            self._line_items[expense.expense_id] = list(line_items)

    def line_items_for(self, expense_id: str) -> list[ReceiptLineItem]:
        """Test helper: the line items passed to `append_expense` for an expense."""
        return self._line_items.get(expense_id, [])

    async def list_expenses(self, trip: Trip) -> list[ExpenseRecord]:
        expenses = self._expenses.get(trip.sheet_id, {})
        order = self._expense_order.get(trip.sheet_id, [])
        return [_expense_to_record(expenses[expense_id]) for expense_id in order]

    async def cancel_expense(
        self, trip: Trip, expense_id: str, cancelled_at: datetime, note_suffix: str
    ) -> bool:
        expenses = self._expenses.get(trip.sheet_id, {})
        expense = expenses.get(expense_id)
        if expense is None:
            return False
        notes = f"{expense.notes} {note_suffix}".strip() if expense.notes else note_suffix
        expenses[expense_id] = expense.model_copy(
            update={"status": "cancelled", "cancelled_at": cancelled_at, "notes": notes}
        )
        return True

    async def expense_exists(self, trip: Trip, expense_id: str) -> bool:
        return expense_id in self._expenses.get(trip.sheet_id, {})

    async def find_rate(
        self, trip: Trip, base: str, quote: str, requested_date: date
    ) -> ExchangeRate | None:
        # Plain lookup by requested date; the rate-date-as-cache-key rule (spec §4)
        # lives only in RateService's in-memory cache, not here.
        rates = self._rates.get(trip.sheet_id, {})
        return rates.get((base, quote, requested_date))

    async def save_rate(self, trip: Trip, rate: ExchangeRate) -> None:
        rates = self._rates.setdefault(trip.sheet_id, {})
        rates[(rate.base, rate.quote, rate.requested_date)] = rate


def _expense_to_record(expense: Expense) -> ExpenseRecord:
    return ExpenseRecord(
        expense_id=expense.expense_id,
        created_at=expense.created_at,
        transaction_date=expense.transaction_date,
        chat_id=expense.chat_id,
        message_id=expense.message_id,
        created_by_user_id=expense.created_by_user_id,
        payer_user_id=expense.payer_user_id,
        payer_username=expense.payer_username,
        payer_display_name=expense.payer_display_name,
        description=expense.description,
        merchant=expense.merchant,
        category=expense.category,
        original_amount_minor=expense.original_amount_minor,
        original_currency=expense.original_currency,
        trip_amount_minor=expense.trip_amount_minor,
        home_amount_minor=expense.home_amount_minor,
        rate=expense.rate,
        rate_date=expense.rate_date,
        source_type=expense.source_type,
        split_method=expense.split_method,
        status=expense.status,
        cancelled_at=expense.cancelled_at,
        notes=expense.notes,
    )
