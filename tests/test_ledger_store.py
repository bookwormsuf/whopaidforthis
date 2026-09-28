"""Tests for app.services.ledger_store: the pure merge_user helper and InMemoryLedgerStore."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

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
        sheet_id="sheet-id-1234567890",
        name="Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )


async def test_in_memory_ledger_store_join_upsert_is_idempotent() -> None:
    store = InMemoryLedgerStore()
    trip = _trip()
    await store.initialise_trip_sheet(trip)
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
    trip = _trip()
    await store.initialise_trip_sheet(trip)

    joined_user = _user(user_id=1, joined=False)
    seen_only_user = _user(user_id=2, joined=False)

    await store.upsert_user(trip, joined_user, joined=True)
    await store.upsert_user(trip, seen_only_user, joined=None)

    all_users = await store.list_users(trip)
    joined_users = await store.list_users(trip, joined_only=True)

    assert len(all_users) == 2
    assert len(joined_users) == 1
    assert joined_users[0].user_id == 1


async def test_in_memory_ledger_store_can_open_respects_unopenable_set() -> None:
    store = InMemoryLedgerStore(unopenable_sheet_ids=frozenset({"blocked-sheet"}))
    assert await store.can_open("blocked-sheet") is False
    assert await store.can_open("open-sheet") is True


async def test_in_memory_ledger_store_save_and_get_binding_roundtrip() -> None:
    store = InMemoryLedgerStore()
    trip = _trip()
    await store.save_binding(trip)
    fetched = await store.get_binding(trip.chat_id)
    assert fetched == trip
    assert await store.get_binding(999) is None
