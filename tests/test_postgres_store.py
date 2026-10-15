"""Round-trip tests for PostgresLedgerStore against a real local Postgres.

Spec references: docs/sprints/09-neon-store-and-export.md ("Tests required").
Skipped automatically (see conftest.py) when no database is reachable at
TEST_DATABASE_URL -- `docker compose up -d` starts one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import asyncpg
import pytest

import tests.conftest
from app.models.category import Category
from app.models.exchange_rate import ExchangeRate
from app.models.expense import Expense, ExpenseSplit
from app.models.receipt import ReceiptLineItem
from app.models.trip import Trip
from app.models.user import TripUser
from app.services.ledger_store import LedgerUnavailable
from app.services.postgres_store import PostgresLedgerStore

pytestmark = pytest.mark.postgres


def _trip(chat_id: int = -100, **overrides: object) -> Trip:
    defaults = dict(
        chat_id=chat_id,
        name="Japan trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_by=1,
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Trip(**defaults)  # type: ignore[arg-type]


def _expense(trip: Trip, expense_id: str, message_id: int = 1, payer_id: int = 1) -> Expense:
    return Expense(
        expense_id=expense_id,
        created_at=datetime.now(UTC),
        transaction_date=date(2026, 10, 1),
        chat_id=trip.chat_id,
        message_id=message_id,
        created_by_user_id=payer_id,
        payer_user_id=payer_id,
        payer_username="ada",
        payer_display_name="Ada",
        description="Ramen",
        merchant=None,
        category=Category.FOOD,
        original_amount_minor=1200,
        original_currency="JPY",
        trip_currency=trip.trip_currency,
        home_currency=trip.home_currency,
        trip_amount_minor=1200,
        home_amount_minor=1100,
        rate=Decimal("0.0092"),
        rate_date=date(2026, 10, 1),
        source_type="text",
        split_method="equal",
        status="confirmed",
        cancelled_at=None,
        notes=None,
        splits=[
            ExpenseSplit(
                user_id=payer_id,
                username="ada",
                display_name="Ada",
                share_trip_minor=1200,
                share_home_minor=1100,
                split_method="equal",
            )
        ],
    )


async def test_save_and_get_binding_roundtrip(postgres_store: PostgresLedgerStore) -> None:
    trip = _trip()
    saved = await postgres_store.save_binding(trip)
    assert saved.id is not None

    fetched = await postgres_store.get_binding(trip.chat_id)
    assert fetched is not None
    assert fetched.id == saved.id
    assert fetched.trip_currency == "JPY"
    assert fetched.closed_at is None


async def test_save_binding_closes_previous_open_trip(postgres_store: PostgresLedgerStore) -> None:
    trip = _trip()
    first = await postgres_store.save_binding(trip)
    second = await postgres_store.save_binding(_trip(name="Korea trip"))

    trips = await postgres_store.list_trips(trip.chat_id)
    by_id = {t.id: t for t in trips}
    assert by_id[first.id].closed_at is not None
    assert by_id[second.id].closed_at is None

    open_trip = await postgres_store.get_binding(trip.chat_id)
    assert open_trip is not None
    assert open_trip.id == second.id


async def test_close_trip(postgres_store: PostgresLedgerStore) -> None:
    trip = await postgres_store.save_binding(_trip())
    await postgres_store.close_trip(trip)
    assert await postgres_store.get_binding(trip.chat_id) is None
    trips = await postgres_store.list_trips(trip.chat_id)
    assert trips[0].closed_at is not None


async def test_upsert_user_roundtrip_and_idempotent(postgres_store: PostgresLedgerStore) -> None:
    trip = await postgres_store.save_binding(_trip())
    now = datetime.now(UTC)
    user = TripUser(
        user_id=1, username="ada", display_name="Ada", first_seen=now, last_seen=now, joined=True
    )
    await postgres_store.upsert_user(trip, user, joined=True)
    await postgres_store.upsert_user(trip, user, joined=True)

    users = await postgres_store.list_users(trip)
    assert len(users) == 1
    assert users[0].joined is True

    joined_only = await postgres_store.list_users(trip, joined_only=True)
    assert len(joined_only) == 1


async def test_append_expense_roundtrip(postgres_store: PostgresLedgerStore) -> None:
    trip = await postgres_store.save_binding(_trip())
    expense = _expense(trip, "01EXPENSE000000000000001")
    await postgres_store.append_expense(trip, expense)

    assert await postgres_store.expense_exists(trip, expense.expense_id)
    records = await postgres_store.list_expenses(trip)
    assert len(records) == 1
    assert records[0].expense_id == expense.expense_id
    assert records[0].original_amount_minor == 1200

    splits = await postgres_store.list_splits(trip)
    assert len(splits) == 1
    assert splits[0].share_home_minor == 1100


async def test_append_expense_with_line_items_roundtrip(
    postgres_store: PostgresLedgerStore,
) -> None:
    trip = await postgres_store.save_binding(_trip())
    expense = _expense(trip, "01EXPENSE00000000000LINE")
    line_items = [
        ReceiptLineItem(description="Ramen", quantity=1, amount_minor=1000, kind="item"),
        ReceiptLineItem(description="Tax", quantity=1, amount_minor=200, kind="tax"),
    ]
    await postgres_store.append_expense(
        trip, expense, line_items=line_items, assignments={0: ([1], "claimed")}
    )

    records = await postgres_store.list_line_items(trip)

    assert [r.description for r in records] == ["Ramen", "Tax"]
    assert records[0].position == 0
    assert records[0].assigned_user_ids == [1]
    assert records[0].allocation_method == "claimed"
    assert records[1].allocation_method == "unassigned"
    assert records[1].assigned_user_ids == []


async def test_append_expense_same_message_id_leaves_one_row(
    postgres_store: PostgresLedgerStore,
) -> None:
    trip = await postgres_store.save_binding(_trip())
    first = _expense(trip, "01EXPENSE000000000000002", message_id=42)
    second = _expense(trip, "01EXPENSE000000000000003", message_id=42)

    await postgres_store.append_expense(trip, first)
    await postgres_store.append_expense(trip, second)  # same (chat_id, message_id)

    records = await postgres_store.list_expenses(trip)
    assert len(records) == 1
    assert records[0].expense_id == first.expense_id


async def test_append_expense_same_id_leaves_one_row(postgres_store: PostgresLedgerStore) -> None:
    trip = await postgres_store.save_binding(_trip())
    expense = _expense(trip, "01EXPENSE000000000000004", message_id=1)
    other_message = _expense(trip, "01EXPENSE000000000000004", message_id=2)

    await postgres_store.append_expense(trip, expense)
    await postgres_store.append_expense(trip, other_message)  # same expense id

    records = await postgres_store.list_expenses(trip)
    assert len(records) == 1


async def test_append_expense_failing_split_leaves_no_expense_row(
    postgres_store: PostgresLedgerStore,
) -> None:
    """A split with a user id that violates a constraint rolls the whole insert back."""
    trip = await postgres_store.save_binding(_trip())
    expense = _expense(trip, "01EXPENSE000000000000005")
    bad_split = expense.splits[0].model_copy(update={"split_method": "not-a-real-method"})
    broken = expense.model_copy(update={"splits": [bad_split]})

    async def _do() -> None:
        async with postgres_store.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                insert into expenses
                    (id, trip_id, chat_id, message_id, created_by, payer_id, description,
                     merchant, category, original_amount_minor, original_currency,
                     trip_amount_minor, home_amount_minor, rate, rate_date, transaction_date,
                     source_type, split_method, status, created_at, cancelled_at, notes)
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                        $16, $17, $18, $19, $20, $21, $22)
                """,
                broken.expense_id,
                trip.id,
                broken.chat_id,
                broken.message_id,
                broken.created_by_user_id,
                broken.payer_user_id,
                broken.description,
                broken.merchant,
                str(broken.category),
                broken.original_amount_minor,
                broken.original_currency,
                broken.trip_amount_minor,
                broken.home_amount_minor,
                broken.rate,
                broken.rate_date,
                broken.transaction_date,
                broken.source_type,
                broken.split_method,
                broken.status,
                broken.created_at,
                broken.cancelled_at,
                broken.notes,
            )
            for split in broken.splits:
                await conn.execute(
                    "insert into splits "
                    "(expense_id, user_id, share_trip_minor, share_home_minor, split_method) "
                    "values ($1, $2, $3, $4, $5)",
                    broken.expense_id,
                    split.user_id,
                    split.share_trip_minor,
                    split.share_home_minor,
                    split.split_method,
                )

    with pytest.raises(asyncpg.CheckViolationError):
        await _do()

    assert not await postgres_store.expense_exists(trip, broken.expense_id)


async def test_duplicate_split_user_id_raises_ledger_unavailable_not_already_saved(
    postgres_store: PostgresLedgerStore,
) -> None:
    """A unique violation on anything other than the expense's own identity (here,
    two splits for the same user) is a bug, not a duplicate send, and must not be
    swallowed as "already saved" (sprint 9 review item 3).
    """
    trip = await postgres_store.save_binding(_trip())
    expense = _expense(trip, "01EXPENSE0000000000DUPE")
    split = expense.splits[0]
    duplicated = split.model_copy(
        update={"share_trip_minor": split.share_trip_minor // 2, "share_home_minor": 0}
    )
    remainder = split.model_copy(
        update={
            "share_trip_minor": split.share_trip_minor - duplicated.share_trip_minor,
            "share_home_minor": split.share_home_minor,
        }
    )
    broken = expense.model_copy(update={"splits": [duplicated, remainder]})

    with pytest.raises(LedgerUnavailable):
        await postgres_store.append_expense(trip, broken)

    assert not await postgres_store.expense_exists(trip, broken.expense_id)


async def test_delete_trips_removes_everything(postgres_store: PostgresLedgerStore) -> None:
    trip = await postgres_store.save_binding(_trip())
    expense = _expense(trip, "01EXPENSE000000000000006")
    await postgres_store.append_expense(trip, expense)
    now = datetime.now(UTC)
    await postgres_store.upsert_user(
        trip,
        TripUser(
            user_id=1,
            username="ada",
            display_name="Ada",
            first_seen=now,
            last_seen=now,
            joined=True,
        ),
        joined=True,
    )

    await postgres_store.delete_trips(trip.chat_id)

    assert await postgres_store.get_binding(trip.chat_id) is None
    assert await postgres_store.list_trips(trip.chat_id) == []
    assert not await postgres_store.expense_exists(trip, expense.expense_id)


async def test_latest_trip_prefers_open_over_newer_closed(
    postgres_store: PostgresLedgerStore,
) -> None:
    chat_id = -200
    first = await postgres_store.save_binding(_trip(chat_id=chat_id, name="First"))
    await postgres_store.close_trip(first)
    second = await postgres_store.save_binding(_trip(chat_id=chat_id, name="Second"))
    await postgres_store.close_trip(second)
    third = await postgres_store.save_binding(_trip(chat_id=chat_id, name="Third"))  # open

    latest = await postgres_store.latest_trip(chat_id)
    assert latest is not None
    assert latest.id == third.id


async def test_latest_trip_with_no_open_trip_picks_most_recently_created(
    postgres_store: PostgresLedgerStore,
) -> None:
    chat_id = -201
    first = await postgres_store.save_binding(_trip(chat_id=chat_id, name="First"))
    await postgres_store.close_trip(first)
    second = await postgres_store.save_binding(_trip(chat_id=chat_id, name="Second"))
    await postgres_store.close_trip(second)

    latest = await postgres_store.latest_trip(chat_id)
    assert latest is not None
    assert latest.id == second.id


async def test_latest_trip_for_unknown_chat_is_none(postgres_store: PostgresLedgerStore) -> None:
    assert await postgres_store.latest_trip(-99999) is None


async def test_get_trip_scoped_to_chat(postgres_store: PostgresLedgerStore) -> None:
    chat_id = -202
    trip = await postgres_store.save_binding(_trip(chat_id=chat_id))

    found = await postgres_store.get_trip(chat_id, trip.id)
    assert found is not None
    assert found.id == trip.id

    assert await postgres_store.get_trip(chat_id, trip.id + 999) is None
    assert await postgres_store.get_trip(chat_id + 1, trip.id) is None


async def test_find_and_save_rate_roundtrip(postgres_store: PostgresLedgerStore) -> None:
    trip = await postgres_store.save_binding(_trip())
    rate = ExchangeRate(
        base="JPY",
        quote="SGD",
        requested_date=date(2026, 10, 1),
        rate_date=date(2026, 10, 1),
        rate=Decimal("0.0092"),
        provider="frankfurter",
        retrieved_at=datetime.now(UTC),
    )
    await postgres_store.save_rate(trip, rate)
    found = await postgres_store.find_rate(trip, "JPY", "SGD", date(2026, 10, 1))
    assert found is not None
    assert found.rate == Decimal("0.0092")
    assert await postgres_store.find_rate(trip, "JPY", "SGD", date(2026, 9, 1)) is None


async def test_connect_then_close_releases_the_pool() -> None:
    """Lifecycle round-trip against a real Postgres: connect() creates its own
    pool (and migrates it), close() releases it (sprint 9 review item 7). Uses
    its own pool, not the session-scoped `postgres_store` fixture's, so closing
    it never affects other tests.
    """
    store = PostgresLedgerStore(tests.conftest.TEST_DATABASE_URL)
    await store.connect()
    assert store.pool is not None

    await store.close()
    with pytest.raises(asyncpg.InterfaceError):
        async with store.pool.acquire():
            pass


async def test_connection_error_becomes_ledger_unavailable(
    postgres_store: PostgresLedgerStore,
) -> None:
    broken_pool = await asyncpg.create_pool(
        "postgresql://postgres:postgres@localhost:1/does-not-exist", min_size=0, max_size=1
    )
    store = PostgresLedgerStore.from_pool(broken_pool)
    try:
        with pytest.raises(LedgerUnavailable):
            await store.get_binding(-1)
    finally:
        await broken_pool.close()
