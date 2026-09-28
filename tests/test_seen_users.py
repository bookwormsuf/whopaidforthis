"""Tests for the seen-user upsert TypeHandler."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.bot.deps import Deps
from app.bot.handlers import seen_users
from app.models.trip import Trip
from app.services.currencies import StaticCurrencyCatalog
from app.services.ledger_store import InMemoryLedgerStore


def _deps(ledger: InMemoryLedgerStore) -> Deps:
    return Deps(
        ledger=ledger,
        currencies=StaticCurrencyCatalog(frozenset({"JPY", "SGD"})),
        service_account_email="whopaid-bot@example.iam.gserviceaccount.com",
        bot_version="test",
    )


def _update(chat_id: int, user_id: int) -> MagicMock:
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=chat_id, type="supergroup")
    update.effective_user = SimpleNamespace(
        id=user_id, first_name="Bo", last_name=None, username="bo"
    )
    return update


async def test_seen_user_upserted_with_joined_none_in_bound_chat() -> None:
    ledger = InMemoryLedgerStore()
    trip = Trip(
        chat_id=-1,
        sheet_id="sheet-id-1234567890",
        name="Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )
    await ledger.save_binding(trip)
    await ledger.initialise_trip_sheet(trip)
    deps = _deps(ledger)
    context = MagicMock()
    context.application.bot_data = {"deps": deps}

    await seen_users.upsert_seen_user(_update(-1, 5), context)

    users = await ledger.list_users(trip)
    assert len(users) == 1
    assert users[0].joined is False


async def test_seen_user_throttled_to_once_per_hour() -> None:
    ledger = InMemoryLedgerStore()
    trip = Trip(
        chat_id=-1,
        sheet_id="sheet-id-1234567890",
        name="Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )
    await ledger.save_binding(trip)
    await ledger.initialise_trip_sheet(trip)
    deps = _deps(ledger)
    context = MagicMock()
    context.application.bot_data = {"deps": deps}

    await seen_users.upsert_seen_user(_update(-1, 5), context)
    first_seen_at = (await ledger.list_users(trip))[0].last_seen

    await seen_users.upsert_seen_user(_update(-1, 5), context)  # same process, right away
    second_seen_at = (await ledger.list_users(trip))[0].last_seen

    assert first_seen_at == second_seen_at  # throttled: no second upsert happened


async def test_seen_user_skipped_in_unbound_chat() -> None:
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    context = MagicMock()
    context.application.bot_data = {"deps": deps}

    await seen_users.upsert_seen_user(_update(-1, 5), context)

    # No trip exists at all, so there is nothing to assert on the ledger beyond
    # "it did not raise" -- upsert_user was never called because get_binding is None.


async def test_seen_user_never_raises_on_missing_deps() -> None:
    context = MagicMock()
    context.application.bot_data = {}

    await seen_users.upsert_seen_user(_update(-1, 5), context)  # must not raise
