"""Tests for app.services.ledger_store: the pure merge_user helper and InMemoryLedgerStore."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.models.category import Category
from app.models.expense import Expense, ExpenseSplit
from app.models.receipt import ReceiptLineItem
from app.models.trip import Trip
from app.models.user import TripUser
from app.services.ledger_store import InMemoryLedgerStore, merge_user


def _user(user_id: int = 1, joined: bool = False, when: datetime | None = None) -> TripUser:
    now = when or datetime.now(UTC)
    return TripUser(
        user_id=user_id,
        username="ada",
        display_name="Ada Lovelace",
        first_seen=now,
        last_seen=now,
        joined=joined,
    )


def test_merge_user_new_user_joined_none_starts_not_joined() -> None:
    incoming = _user()
    merged = merge_user(None, incoming, joined=None)
    assert merged.joined is False
    assert merged.first_seen == incoming.last_seen


def test_merge_user_new_user_joined_true() -> None:
    incoming = _user()
    merged = merge_user(None, incoming, joined=True)
    assert merged.joined is True


def test_merge_user_existing_user_joined_none_keeps_flag() -> None:
    t0 = datetime.now(UTC) - timedelta(days=1)
    existing = _user(joined=True, when=t0)
    incoming = _user(when=datetime.now(UTC))
    merged = merge_user(existing, incoming, joined=None)
    assert merged.joined is True
    assert merged.first_seen == t0  # first_seen never moves
    assert merged.last_seen == incoming.last_seen


def test_merge_user_existing_user_explicit_joined_overrides() -> None:
    existing = _user(joined=False)
    incoming = _user()
    merged = merge_user(existing, incoming, joined=True)
    assert merged.joined is True


def _trip() -> Trip:
    return Trip(
        chat_id=-1,
        name="Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )


async def test_in_memory_ledger_store_join_upsert_is_idempotent() -> None:
    store = InMemoryLedgerStore()
    trip = await store.save_binding(_trip())
    user = TripUser.from_telegram_user(
        SimpleNamespace(id=1, first_name="Ada", last_name=None, username="ada"),
        datetime.now(UTC),
    )
    await store.upsert_user(trip, user, joined=True)
    await store.upsert_user(trip, user, joined=True)

    users = await store.list_users(trip)
    assert len(users) == 1
    assert users[0].joined is True


async def test_in_memory_ledger_store_list_users_joined_only_excludes_seen_only() -> None:
    store = InMemoryLedgerStore()
    trip = await store.save_binding(_trip())

    joined_user = _user(user_id=1, joined=False)
    seen_only_user = _user(user_id=2, joined=False)

    await store.upsert_user(trip, joined_user, joined=True)
    await store.upsert_user(trip, seen_only_user, joined=None)

    all_users = await store.list_users(trip)
    joined_users = await store.list_users(trip, joined_only=True)

    assert len(all_users) == 2
    assert len(joined_users) == 1
    assert joined_users[0].user_id == 1


async def test_in_memory_ledger_store_save_binding_closes_previous_open_trip() -> None:
    store = InMemoryLedgerStore()
    first = await store.save_binding(_trip())
    second = await store.save_binding(_trip())

    trips = await store.list_trips(first.chat_id)
    by_id = {t.id: t for t in trips}
    assert by_id[first.id].closed_at is not None
    assert by_id[second.id].closed_at is None


async def test_in_memory_ledger_store_save_and_get_binding_roundtrip() -> None:
    store = InMemoryLedgerStore()
    trip = await store.save_binding(_trip())
    fetched = await store.get_binding(trip.chat_id)
    assert fetched == trip
    assert await store.get_binding(999) is None


async def test_in_memory_ledger_store_delete_trips_removes_everything() -> None:
    store = InMemoryLedgerStore()
    trip = await store.save_binding(_trip())
    user = _user(user_id=1, joined=True)
    await store.upsert_user(trip, user, joined=True)

    await store.delete_trips(trip.chat_id)

    assert await store.get_binding(trip.chat_id) is None
    assert await store.list_trips(trip.chat_id) == []


async def test_in_memory_ledger_store_list_line_items() -> None:
    store = InMemoryLedgerStore()
    trip = await store.save_binding(_trip())
    expense = Expense(
        expense_id="e1",
        created_at=datetime.now(UTC),
        transaction_date=date(2026, 10, 1),
        chat_id=trip.chat_id,
        message_id=1,
        created_by_user_id=1,
        payer_user_id=1,
        payer_username="ada",
        payer_display_name="Ada",
        description="Dinner",
        merchant=None,
        category=Category.FOOD,
        original_amount_minor=1200,
        original_currency="JPY",
        trip_currency="JPY",
        home_currency="SGD",
        trip_amount_minor=1200,
        home_amount_minor=1000,
        rate=Decimal("0.0081"),
        rate_date=date(2026, 9, 30),
        source_type="receipt",
        split_method="items",
        status="confirmed",
        cancelled_at=None,
        notes=None,
        splits=[
            ExpenseSplit(
                user_id=1,
                username="ada",
                display_name="Ada",
                share_trip_minor=1200,
                share_home_minor=1000,
                split_method="items",
            )
        ],
    )
    line_items = [
        ReceiptLineItem(description="Ramen", quantity=1, amount_minor=1000, kind="item"),
        ReceiptLineItem(description="Tax", quantity=1, amount_minor=200, kind="tax"),
    ]
    await store.append_expense(
        trip, expense, line_items=line_items, assignments={0: ([1], "claimed")}
    )

    records = await store.list_line_items(trip)

    assert [r.description for r in records] == ["Ramen", "Tax"]
    assert records[0].position == 0
    assert records[0].assigned_user_ids == [1]
    assert records[0].allocation_method == "claimed"
    assert records[1].allocation_method == "unassigned"  # no entry for position 1
    assert records[1].assigned_user_ids == []


def _expense(expense_id: str, *, message_id: int, status: str = "confirmed") -> Expense:
    return Expense(
        expense_id=expense_id,
        created_at=datetime.now(UTC),
        transaction_date=date(2026, 10, 1),
        chat_id=-1,
        message_id=message_id,
        created_by_user_id=1,
        payer_user_id=1,
        payer_username="ada",
        payer_display_name="Ada",
        description="Dinner",
        merchant=None,
        category=Category.FOOD,
        original_amount_minor=1200,
        original_currency="JPY",
        trip_currency="JPY",
        home_currency="SGD",
        trip_amount_minor=1200,
        home_amount_minor=1000,
        rate=Decimal("0.0081"),
        rate_date=date(2026, 9, 30),
        source_type="text",
        split_method="equal",
        status=status,
        cancelled_at=None,
        notes=None,
        splits=[
            ExpenseSplit(
                user_id=1,
                username="ada",
                display_name="Ada",
                share_trip_minor=1200,
                share_home_minor=1000,
                split_method="equal",
            )
        ],
    )


async def test_confirmed_only_filters_list_expenses_and_list_splits() -> None:
    """The store owns the confirmed filter (sprint 9 review item 8)."""
    store = InMemoryLedgerStore()
    trip = await store.save_binding(_trip())
    confirmed = _expense("e1", message_id=1, status="confirmed")
    cancelled = _expense("e2", message_id=2, status="cancelled")
    await store.append_expense(trip, confirmed)
    await store.append_expense(trip, cancelled)

    all_expenses = await store.list_expenses(trip)
    confirmed_expenses = await store.list_expenses(trip, confirmed_only=True)
    assert {e.expense_id for e in all_expenses} == {"e1", "e2"}
    assert {e.expense_id for e in confirmed_expenses} == {"e1"}

    all_splits = await store.list_splits(trip)
    confirmed_splits = await store.list_splits(trip, confirmed_only=True)
    assert {s.expense_id for s in all_splits} == {"e1", "e2"}
    assert {s.expense_id for s in confirmed_splits} == {"e1"}


async def test_latest_trip_prefers_the_open_trip_over_a_newer_closed_one() -> None:
    """Ordering matches the spec: the open trip wins regardless of creation
    order (sprint 9 review item 6).
    """
    store = InMemoryLedgerStore()
    first = await store.save_binding(_trip())
    await store.close_trip(first)
    second = await store.save_binding(_trip())  # open
    await store.close_trip(second)
    third = await store.save_binding(_trip())  # open: most recently created too

    latest = await store.latest_trip(first.chat_id)
    assert latest is not None
    assert latest.id == third.id


async def test_latest_trip_with_no_open_trip_picks_the_most_recently_created() -> None:
    store = InMemoryLedgerStore()
    first = await store.save_binding(_trip())
    await store.close_trip(first)
    second = await store.save_binding(_trip())
    await store.close_trip(second)

    latest = await store.latest_trip(first.chat_id)
    assert latest is not None
    assert latest.id == second.id


async def test_latest_trip_for_a_chat_with_no_trips_is_none() -> None:
    store = InMemoryLedgerStore()
    assert await store.latest_trip(-999) is None


async def test_get_trip_returns_the_matching_trip_scoped_to_its_chat() -> None:
    store = InMemoryLedgerStore()
    trip = await store.save_binding(_trip())

    found = await store.get_trip(trip.chat_id, trip.id)
    assert found is not None
    assert found.id == trip.id

    assert await store.get_trip(trip.chat_id, trip.id + 999) is None
    assert await store.get_trip(trip.chat_id + 1, trip.id) is None
