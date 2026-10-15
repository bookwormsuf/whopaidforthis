"""The `LedgerStore` interface and an in-memory implementation for tests.

Spec references: pinned-decisions §10, §18; ADR 0003.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Protocol

from app.models.exchange_rate import ExchangeRate
from app.models.expense import Expense, ExpenseRecord, SplitRecord
from app.models.receipt import LineItemRecord, ReceiptLineItem
from app.models.trip import Trip
from app.models.user import TripUser


class LedgerUnavailable(RuntimeError):
    """The ledger backend (Postgres) could not be reached or refused a call.

    Message must never include a connection string or row contents. `status` is kept
    for callers that still branch on it; it is always `None` for the Postgres store
    (there is no HTTP status to report).
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# The one user-facing line for a LedgerUnavailable, shared by every caller (handlers
# that catch it locally, and the PTB-wide error handler as the last resort).
LEDGER_UNAVAILABLE_TEXT = "Couldn't reach the database, please try again."


class LedgerStore(Protocol):
    """Persistence for trips, trip users, expenses and exchange rates."""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def get_binding(self, chat_id: int) -> Trip | None: ...

    async def save_binding(self, trip: Trip) -> Trip: ...

    async def close_trip(self, trip: Trip) -> None: ...

    async def delete_trips(self, chat_id: int) -> None: ...

    async def list_trips(self, chat_id: int) -> list[Trip]: ...

    async def latest_trip(self, chat_id: int) -> Trip | None: ...

    async def get_trip(self, chat_id: int, trip_id: int) -> Trip | None: ...

    async def upsert_user(self, trip: Trip, user: TripUser, joined: bool | None) -> TripUser: ...

    async def list_users(self, trip: Trip, joined_only: bool = False) -> list[TripUser]: ...

    async def append_expense(
        self,
        trip: Trip,
        expense: Expense,
        line_items: Sequence[ReceiptLineItem] = (),
        assignments: Mapping[int, tuple[list[int], str]] | None = None,
    ) -> None: ...

    async def list_expenses(
        self, trip: Trip, confirmed_only: bool = False
    ) -> list[ExpenseRecord]: ...

    async def list_splits(self, trip: Trip, confirmed_only: bool = False) -> list[SplitRecord]: ...

    async def list_line_items(self, trip: Trip) -> list[LineItemRecord]: ...

    async def cancel_expense(
        self, trip: Trip, expense_id: str, cancelled_at: datetime, note_suffix: str
    ) -> bool: ...

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
    """In-memory LedgerStore for handler tests. Trips are keyed by their assigned
    `id` (auto-incrementing, like the Postgres `bigserial`); everything else that
    belongs to a trip (users, expenses) is keyed by that same `trip.id`.
    """

    def __init__(self) -> None:
        self._trips_by_id: dict[int, Trip] = {}
        self._trip_ids_by_chat: dict[int, list[int]] = {}
        self._next_trip_id = 1
        self._users: dict[int, dict[int, TripUser]] = {}
        self._expenses: dict[int, dict[str, Expense]] = {}
        self._expense_order: dict[int, list[str]] = {}
        self._message_ids: dict[int, set[tuple[int, int]]] = {}
        self._rates: dict[tuple[str, str, date], ExchangeRate] = {}
        self._line_items: dict[str, list[ReceiptLineItem]] = {}
        self._assignments: dict[str, dict[int, tuple[list[int], str]]] = {}
        self.get_binding_calls = 0

    async def connect(self) -> None:
        """No-op: there is no pool or migration for an in-memory store."""

    async def close(self) -> None:
        """No-op: nothing to release."""

    async def get_binding(self, chat_id: int) -> Trip | None:
        self.get_binding_calls += 1
        for trip_id in reversed(self._trip_ids_by_chat.get(chat_id, [])):
            trip = self._trips_by_id[trip_id]
            if trip.closed_at is None:
                return trip
        return None

    async def save_binding(self, trip: Trip) -> Trip:
        """Insert a new open trip, closing any existing open trip for this chat."""
        existing = await self.get_binding(trip.chat_id)
        if existing is not None:
            self._trips_by_id[existing.id] = existing.model_copy(
                update={"closed_at": datetime.now(existing.created_at.tzinfo)}
            )
        trip_id = self._next_trip_id
        self._next_trip_id += 1
        saved = trip.model_copy(update={"id": trip_id})
        self._trips_by_id[trip_id] = saved
        self._trip_ids_by_chat.setdefault(trip.chat_id, []).append(trip_id)
        self._users.setdefault(trip_id, {})
        self._expenses.setdefault(trip_id, {})
        self._expense_order.setdefault(trip_id, [])
        self._message_ids.setdefault(trip_id, set())
        return saved

    async def close_trip(self, trip: Trip) -> None:
        assert trip.id is not None
        current = self._trips_by_id.get(trip.id)
        if current is not None:
            self._trips_by_id[trip.id] = current.model_copy(
                update={"closed_at": datetime.now(current.created_at.tzinfo)}
            )

    async def delete_trips(self, chat_id: int) -> None:
        for trip_id in self._trip_ids_by_chat.pop(chat_id, []):
            self._trips_by_id.pop(trip_id, None)
            self._users.pop(trip_id, None)
            expense_ids = self._expense_order.pop(trip_id, [])
            expenses = self._expenses.pop(trip_id, {})
            for expense_id in expense_ids:
                self._line_items.pop(expense_id, None)
                self._assignments.pop(expense_id, None)
            del expenses
            self._message_ids.pop(trip_id, None)

    async def list_trips(self, chat_id: int) -> list[Trip]:
        return [self._trips_by_id[tid] for tid in self._trip_ids_by_chat.get(chat_id, [])]

    async def latest_trip(self, chat_id: int) -> Trip | None:
        """The chat's open trip, or (if none is open) its most recently created
        trip, or `None` if the chat has never had one.
        """
        trips = [self._trips_by_id[tid] for tid in self._trip_ids_by_chat.get(chat_id, [])]
        if not trips:
            return None
        return max(trips, key=lambda t: (t.closed_at is None, t.created_at))

    async def get_trip(self, chat_id: int, trip_id: int) -> Trip | None:
        for tid in self._trip_ids_by_chat.get(chat_id, []):
            if tid == trip_id:
                return self._trips_by_id[tid]
        return None

    async def upsert_user(self, trip: Trip, user: TripUser, joined: bool | None) -> TripUser:
        assert trip.id is not None
        users = self._users.setdefault(trip.id, {})
        existing = users.get(user.user_id)
        merged = merge_user(existing, user, joined)
        users[user.user_id] = merged
        return merged

    async def list_users(self, trip: Trip, joined_only: bool = False) -> list[TripUser]:
        assert trip.id is not None
        users = self._users.get(trip.id, {}).values()
        if joined_only:
            return [user for user in users if user.joined]
        return list(users)

    async def append_expense(
        self,
        trip: Trip,
        expense: Expense,
        line_items: Sequence[ReceiptLineItem] = (),
        assignments: Mapping[int, tuple[list[int], str]] | None = None,
    ) -> None:
        """One expense per `(chat_id, message_id)` and one row per expense id -- a
        repeat of either is "already saved" and returns without changing anything,
        matching the Postgres store's unique-violation-means-success behaviour.
        """
        assert trip.id is not None
        expenses = self._expenses.setdefault(trip.id, {})
        order = self._expense_order.setdefault(trip.id, [])
        seen_messages = self._message_ids.setdefault(trip.id, set())
        message_key = (expense.chat_id, expense.message_id)
        if expense.expense_id in expenses or message_key in seen_messages:
            return
        expenses[expense.expense_id] = expense
        order.append(expense.expense_id)
        seen_messages.add(message_key)
        if line_items:
            self._line_items[expense.expense_id] = list(line_items)
            self._assignments[expense.expense_id] = dict(assignments or {})

    def line_items_for(self, expense_id: str) -> list[ReceiptLineItem]:
        """Test helper: the line items passed to `append_expense` for an expense."""
        return self._line_items.get(expense_id, [])

    def assignments_for(self, expense_id: str) -> dict[int, tuple[list[int], str]]:
        """Test helper: line-item index -> (assigned user IDs, allocation method)."""
        return self._assignments.get(expense_id, {})

    def expense_for(self, expense_id: str) -> Expense | None:
        """Test helper: the full `Expense` (with splits) passed to `append_expense`."""
        for expenses in self._expenses.values():
            if expense_id in expenses:
                return expenses[expense_id]
        return None

    async def list_expenses(self, trip: Trip, confirmed_only: bool = False) -> list[ExpenseRecord]:
        assert trip.id is not None
        expenses = self._expenses.get(trip.id, {})
        order = self._expense_order.get(trip.id, [])
        return [
            _expense_to_record(expenses[expense_id])
            for expense_id in order
            if not confirmed_only or expenses[expense_id].status == "confirmed"
        ]

    async def list_splits(self, trip: Trip, confirmed_only: bool = False) -> list[SplitRecord]:
        assert trip.id is not None
        expenses = self._expenses.get(trip.id, {})
        order = self._expense_order.get(trip.id, [])
        return [
            SplitRecord(
                expense_id=expense_id,
                user_id=split.user_id,
                username=split.username,
                display_name=split.display_name,
                share_trip_minor=split.share_trip_minor,
                share_home_minor=split.share_home_minor,
                split_method=split.split_method,
            )
            for expense_id in order
            if not confirmed_only or expenses[expense_id].status == "confirmed"
            for split in expenses[expense_id].splits
        ]

    async def list_line_items(self, trip: Trip) -> list[LineItemRecord]:
        assert trip.id is not None
        order = self._expense_order.get(trip.id, [])
        records: list[LineItemRecord] = []
        for expense_id in order:
            items = self._line_items.get(expense_id, [])
            assignments = self._assignments.get(expense_id, {})
            for position, item in enumerate(items):
                assigned, method = assignments.get(position, ([], "unassigned"))
                records.append(
                    LineItemRecord(
                        expense_id=expense_id,
                        position=position,
                        description=item.description,
                        quantity=item.quantity,
                        amount_trip_minor=item.amount_minor,
                        kind=item.kind,
                        allocation_method=method,
                        assigned_user_ids=list(assigned),
                    )
                )
        return records

    async def cancel_expense(
        self, trip: Trip, expense_id: str, cancelled_at: datetime, note_suffix: str
    ) -> bool:
        assert trip.id is not None
        expenses = self._expenses.get(trip.id, {})
        expense = expenses.get(expense_id)
        if expense is None:
            return False
        notes = f"{expense.notes} {note_suffix}".strip() if expense.notes else note_suffix
        expenses[expense_id] = expense.model_copy(
            update={"status": "cancelled", "cancelled_at": cancelled_at, "notes": notes}
        )
        return True

    async def find_rate(
        self, trip: Trip, base: str, quote: str, requested_date: date
    ) -> ExchangeRate | None:
        # Rates are global (spec §18), not per trip: `trip` is accepted only to match
        # the LedgerStore protocol.
        return self._rates.get((base, quote, requested_date))

    async def save_rate(self, trip: Trip, rate: ExchangeRate) -> None:
        self._rates[(rate.base, rate.quote, rate.requested_date)] = rate


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
