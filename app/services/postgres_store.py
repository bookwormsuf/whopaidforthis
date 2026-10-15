"""PostgresLedgerStore: the Postgres-on-Neon implementation of LedgerStore.

Spec references: docs/sprints/09-neon-store-and-export.md, pinned-decisions.md §18,
ADR 0003. Every asyncpg error is mapped to `LedgerUnavailable` at this module's
boundary. Retries: 3 with 1/2/4s backoff, on connection errors only
(`asyncpg.PostgresConnectionError`, `OSError`, `asyncio.TimeoutError`); every other
asyncpg error (a check/unique violation, a bad query) is never retried. Writes are
never retried blindly -- `append_expense` treats a unique violation on `expenses.id`
or `(chat_id, message_id)` as "already saved" and returns normally instead of
raising.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import TypeVar

import asyncpg

from app.db import create_pool, migrate
from app.models.category import Category
from app.models.exchange_rate import ExchangeRate
from app.models.expense import Expense, ExpenseRecord, SplitRecord
from app.models.receipt import LineItemRecord, ReceiptLineItem
from app.models.trip import Trip
from app.models.user import TripUser
from app.services.conversion import both_amounts
from app.services.ledger_store import LedgerUnavailable

logger = logging.getLogger(__name__)

_RETRY_DELAYS = (1.0, 2.0, 4.0)
_CONNECTION_ERRORS = (asyncpg.PostgresConnectionError, OSError, asyncio.TimeoutError)

T = TypeVar("T")


class PostgresLedgerStore:
    """`LedgerStore` backed by Postgres on Neon. Construct with `database_url` and
    call `connect()` once at startup (creates the pool and runs migrations), or use
    `PostgresLedgerStore.from_pool` with an already-connected pool (tests).
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    @classmethod
    def from_pool(cls, pool: asyncpg.Pool) -> PostgresLedgerStore:
        store = cls.__new__(cls)
        store._database_url = ""
        store._pool = pool
        return store

    async def connect(self) -> None:
        """Create the pool (min 1, max 5) and apply migrations. Raises on failure --
        callers fail fast rather than starting half-configured.
        """
        self._pool = await create_pool(self._database_url)
        await migrate(self._pool)

    @property
    def pool(self) -> asyncpg.Pool:
        assert self._pool is not None, "PostgresLedgerStore.connect() was never called"
        return self._pool

    async def _call(self, func: Callable[[], Awaitable[T]]) -> T:
        """Run `func`, retrying connection errors 3x with 1/2/4s backoff, and
        mapping every other asyncpg error to `LedgerUnavailable`. Callers that need
        to special-case a particular `asyncpg.PostgresError` (e.g. a unique
        violation meaning "already saved") must catch it themselves around this
        call -- it is re-raised unwrapped.
        """
        last_exc: Exception | None = None
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                return await func()
            except _CONNECTION_ERRORS as exc:
                last_exc = exc
                if attempt < len(_RETRY_DELAYS):
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
                    continue
                raise LedgerUnavailable("Could not reach the database") from exc
        raise LedgerUnavailable("Could not reach the database") from last_exc

    async def _run(self, func: Callable[[], Awaitable[T]]) -> T:
        """`_call`, plus the blanket "every other asyncpg error -> LedgerUnavailable"
        mapping, for callers with no special-case error of their own.
        """
        try:
            return await self._call(func)
        except LedgerUnavailable:
            raise
        except asyncpg.PostgresError as exc:
            raise LedgerUnavailable("Could not reach the database") from exc

    # -- Trips --------------------------------------------------------------

    async def get_binding(self, chat_id: int) -> Trip | None:
        async def _do() -> Trip | None:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "select * from trips where chat_id = $1 and closed_at is null", chat_id
                )
            return _row_to_trip(row) if row is not None else None

        return await self._run(_do)

    async def save_binding(self, trip: Trip) -> Trip:
        async def _do() -> Trip:
            async with self.pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    "update trips set closed_at = now() where chat_id = $1 and closed_at is null",
                    trip.chat_id,
                )
                row = await conn.fetchrow(
                    """
                    insert into trips
                        (chat_id, name, trip_currency, home_currency, timezone,
                         created_by, created_at)
                    values ($1, $2, $3, $4, $5, $6, $7)
                    returning *
                    """,
                    trip.chat_id,
                    trip.name,
                    trip.trip_currency,
                    trip.home_currency,
                    trip.timezone,
                    trip.created_by,
                    trip.created_at,
                )
            return _row_to_trip(row)

        return await self._run(_do)

    async def close_trip(self, trip: Trip) -> None:
        assert trip.id is not None

        async def _do() -> None:
            async with self.pool.acquire() as conn:
                await conn.execute("update trips set closed_at = now() where id = $1", trip.id)

        await self._run(_do)

    async def delete_trips(self, chat_id: int) -> None:
        async def _do() -> None:
            async with self.pool.acquire() as conn:
                await conn.execute("delete from trips where chat_id = $1", chat_id)

        await self._run(_do)

    async def list_trips(self, chat_id: int) -> list[Trip]:
        async def _do() -> list[Trip]:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "select * from trips where chat_id = $1 order by created_at", chat_id
                )
            return [_row_to_trip(row) for row in rows]

        return await self._run(_do)

    # -- Trip users -----------------------------------------------------------

    async def upsert_user(self, trip: Trip, user: TripUser, joined: bool | None) -> TripUser:
        assert trip.id is not None

        async def _do() -> TripUser:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    insert into trip_users
                        (trip_id, user_id, username, display_name, joined,
                         first_seen_at, last_seen_at)
                    values ($1, $2, $3, $4, coalesce($5, false), $6, $6)
                    on conflict (trip_id, user_id) do update set
                        username = excluded.username,
                        display_name = excluded.display_name,
                        joined = coalesce($5, trip_users.joined),
                        last_seen_at = excluded.last_seen_at
                    returning *
                    """,
                    trip.id,
                    user.user_id,
                    user.username,
                    user.display_name,
                    joined,
                    user.last_seen,
                )
            return _row_to_trip_user(row)

        return await self._run(_do)

    async def list_users(self, trip: Trip, joined_only: bool = False) -> list[TripUser]:
        assert trip.id is not None

        async def _do() -> list[TripUser]:
            async with self.pool.acquire() as conn:
                if joined_only:
                    rows = await conn.fetch(
                        "select * from trip_users where trip_id = $1 and joined", trip.id
                    )
                else:
                    rows = await conn.fetch("select * from trip_users where trip_id = $1", trip.id)
            return [_row_to_trip_user(row) for row in rows]

        return await self._run(_do)

    # -- Expenses ---------------------------------------------------------------

    async def append_expense(
        self,
        trip: Trip,
        expense: Expense,
        line_items: Sequence[ReceiptLineItem] = (),
        assignments: Mapping[int, tuple[list[int], str]] | None = None,
    ) -> None:
        """One transaction: insert `expenses`, `splits`, `line_items`. A unique
        violation on `expenses.id` or `(chat_id, message_id)` rolls back (the
        transaction block does this automatically) and is swallowed -- that write
        already happened, so this one is "already saved".
        """
        assert trip.id is not None

        async def _do() -> None:
            async with self.pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    """
                    insert into expenses
                        (id, trip_id, chat_id, message_id, created_by, payer_id,
                         description, merchant, category, original_amount_minor,
                         original_currency, trip_amount_minor, home_amount_minor,
                         rate, rate_date, transaction_date, source_type,
                         split_method, status, created_at, cancelled_at, notes)
                    values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                            $14, $15, $16, $17, $18, $19, $20, $21, $22)
                    """,
                    expense.expense_id,
                    trip.id,
                    expense.chat_id,
                    expense.message_id,
                    expense.created_by_user_id,
                    expense.payer_user_id,
                    expense.description,
                    expense.merchant,
                    str(expense.category),
                    expense.original_amount_minor,
                    expense.original_currency,
                    expense.trip_amount_minor,
                    expense.home_amount_minor,
                    expense.rate,
                    expense.rate_date,
                    expense.transaction_date,
                    expense.source_type,
                    expense.split_method,
                    expense.status,
                    expense.created_at,
                    expense.cancelled_at,
                    expense.notes,
                )
                for split in expense.splits:
                    await conn.execute(
                        """
                        insert into splits
                            (expense_id, user_id, share_trip_minor, share_home_minor,
                             split_method)
                        values ($1, $2, $3, $4, $5)
                        """,
                        expense.expense_id,
                        split.user_id,
                        split.share_trip_minor,
                        split.share_home_minor,
                        split.split_method,
                    )
                for index, item in enumerate(line_items):
                    assigned, method = (assignments or {}).get(index, ([], "unassigned"))
                    # Items are in the receipt's currency (expense.original_currency);
                    # Amount <T> is always the trip currency, so a home-currency
                    # receipt's items are converted at the expense's own rate,
                    # rounded half-even per item (spec §10).
                    amount_trip_minor, _ = both_amounts(
                        item.amount_minor,
                        expense.original_currency,
                        expense.trip_currency,
                        expense.home_currency,
                        expense.rate,
                    )
                    await conn.execute(
                        """
                        insert into line_items
                            (expense_id, position, description, quantity,
                             amount_trip_minor, kind, allocation_method,
                             assigned_user_ids)
                        values ($1, $2, $3, $4, $5, $6, $7, $8)
                        """,
                        expense.expense_id,
                        index,
                        item.description,
                        item.quantity,
                        amount_trip_minor,
                        item.kind,
                        method,
                        assigned,
                    )

        try:
            await self._call(_do)
        except LedgerUnavailable:
            raise
        except asyncpg.UniqueViolationError as exc:
            # Only a repeat of the expense's own identity (its id, or the
            # (chat_id, message_id) pair) means "already saved". Any other unique
            # violation (e.g. a duplicate split row) is a bug, not a duplicate send,
            # and must not be swallowed (sprint 9 review item 3).
            if exc.constraint_name in ("expenses_pkey", "expenses_one_per_message"):
                logger.info("expense %s: already saved", expense.expense_id)
                return
            raise LedgerUnavailable("Could not reach the database") from exc
        except asyncpg.PostgresError as exc:
            raise LedgerUnavailable("Could not reach the database") from exc

    async def list_expenses(self, trip: Trip) -> list[ExpenseRecord]:
        assert trip.id is not None

        async def _do() -> list[ExpenseRecord]:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    select e.*, tu.username as payer_username, tu.display_name as payer_display_name
                    from expenses e
                    left join trip_users tu on tu.trip_id = e.trip_id and tu.user_id = e.payer_id
                    where e.trip_id = $1
                    order by e.transaction_date, e.created_at
                    """,
                    trip.id,
                )
            return [_row_to_expense_record(row) for row in rows]

        return await self._run(_do)

    async def list_splits(self, trip: Trip) -> list[SplitRecord]:
        assert trip.id is not None

        async def _do() -> list[SplitRecord]:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    select s.*, tu.username, tu.display_name
                    from splits s
                    join expenses e on e.id = s.expense_id
                    left join trip_users tu on tu.trip_id = e.trip_id and tu.user_id = s.user_id
                    where e.trip_id = $1
                    """,
                    trip.id,
                )
            return [
                SplitRecord(
                    expense_id=row["expense_id"],
                    user_id=row["user_id"],
                    username=row["username"],
                    display_name=row["display_name"] or "",
                    share_trip_minor=row["share_trip_minor"],
                    share_home_minor=row["share_home_minor"],
                    split_method=row["split_method"],
                )
                for row in rows
            ]

        return await self._run(_do)

    async def list_line_items(self, trip: Trip) -> list[LineItemRecord]:
        assert trip.id is not None

        async def _do() -> list[LineItemRecord]:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    select li.*
                    from line_items li
                    join expenses e on e.id = li.expense_id
                    where e.trip_id = $1
                    order by li.expense_id, li.position
                    """,
                    trip.id,
                )
            return [
                LineItemRecord(
                    expense_id=row["expense_id"],
                    position=row["position"],
                    description=row["description"],
                    quantity=row["quantity"],
                    amount_trip_minor=row["amount_trip_minor"],
                    kind=row["kind"],
                    allocation_method=row["allocation_method"],
                    assigned_user_ids=list(row["assigned_user_ids"]),
                )
                for row in rows
            ]

        return await self._run(_do)

    async def cancel_expense(
        self, trip: Trip, expense_id: str, cancelled_at: datetime, note_suffix: str
    ) -> bool:
        async def _do() -> bool:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    update expenses
                    set status = 'cancelled',
                        cancelled_at = $2,
                        notes = case
                            when notes is not null and notes <> '' then notes || ' ' || $3
                            else $3
                        end
                    where id = $1
                    returning id
                    """,
                    expense_id,
                    cancelled_at,
                    note_suffix,
                )
            return row is not None

        return await self._run(_do)

    async def expense_exists(self, trip: Trip, expense_id: str) -> bool:
        async def _do() -> bool:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("select 1 from expenses where id = $1", expense_id)
            return row is not None

        return await self._run(_do)

    # -- Exchange rates (global: spec §18) ---------------------------------------

    async def find_rate(
        self, trip: Trip, base: str, quote: str, requested_date: date
    ) -> ExchangeRate | None:
        async def _do() -> ExchangeRate | None:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "select * from exchange_rates where base = $1 and quote = $2 "
                    "and requested_date = $3",
                    base,
                    quote,
                    requested_date,
                )
            return _row_to_rate(row) if row is not None else None

        return await self._run(_do)

    async def save_rate(self, trip: Trip, rate: ExchangeRate) -> None:
        async def _do() -> None:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    insert into exchange_rates
                        (base, quote, requested_date, rate_date, rate, provider, retrieved_at)
                    values ($1, $2, $3, $4, $5, $6, $7)
                    on conflict (base, quote, requested_date) do update set
                        rate_date = excluded.rate_date,
                        rate = excluded.rate,
                        provider = excluded.provider,
                        retrieved_at = excluded.retrieved_at
                    """,
                    rate.base,
                    rate.quote,
                    rate.requested_date,
                    rate.rate_date,
                    rate.rate,
                    rate.provider,
                    rate.retrieved_at,
                )

        await self._run(_do)


def _row_to_trip(row: asyncpg.Record) -> Trip:
    return Trip(
        id=row["id"],
        chat_id=row["chat_id"],
        name=row["name"] or "",
        trip_currency=row["trip_currency"],
        home_currency=row["home_currency"],
        timezone=row["timezone"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        closed_at=row["closed_at"],
    )


def _row_to_trip_user(row: asyncpg.Record) -> TripUser:
    return TripUser(
        user_id=row["user_id"],
        username=row["username"],
        display_name=row["display_name"] or "",
        first_seen=row["first_seen_at"],
        last_seen=row["last_seen_at"],
        joined=row["joined"],
    )


def _row_to_expense_record(row: asyncpg.Record) -> ExpenseRecord:
    return ExpenseRecord(
        expense_id=row["id"],
        created_at=row["created_at"],
        transaction_date=row["transaction_date"],
        chat_id=row["chat_id"],
        message_id=row["message_id"],
        created_by_user_id=row["created_by"],
        payer_user_id=row["payer_id"],
        payer_username=row["payer_username"],
        payer_display_name=row["payer_display_name"] or "",
        description=row["description"],
        merchant=row["merchant"],
        category=Category(row["category"]),
        original_amount_minor=row["original_amount_minor"],
        original_currency=row["original_currency"],
        trip_amount_minor=row["trip_amount_minor"],
        home_amount_minor=row["home_amount_minor"],
        rate=Decimal(row["rate"]),
        rate_date=row["rate_date"],
        source_type=row["source_type"],
        split_method=row["split_method"],
        status=row["status"],
        cancelled_at=row["cancelled_at"],
        notes=row["notes"],
    )


def _row_to_rate(row: asyncpg.Record) -> ExchangeRate:
    return ExchangeRate(
        base=row["base"],
        quote=row["quote"],
        requested_date=row["requested_date"],
        rate_date=row["rate_date"],
        rate=Decimal(row["rate"]),
        provider=row["provider"],
        retrieved_at=row["retrieved_at"],
    )
