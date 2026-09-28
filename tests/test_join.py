"""Tests for /join, the group /start alias, and require_trip."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.deps import Deps
from app.bot.handlers.basic import start_command
from app.bot.handlers.join import UNAVAILABLE_TEXT, UNBOUND_TEXT, join_command
from app.models.trip import Trip
from app.services.currencies import StaticCurrencyCatalog
from app.services.ledger_store import InMemoryLedgerStore, LedgerUnavailable


def _deps(ledger: InMemoryLedgerStore) -> Deps:
    return Deps(
        ledger=ledger,
        currencies=StaticCurrencyCatalog(frozenset({"JPY", "SGD"})),
        service_account_email="whopaid-bot@example.iam.gserviceaccount.com",
        bot_version="test",
    )


def _context(deps: Deps) -> MagicMock:
    context = MagicMock()
    context.application.bot_data = {"deps": deps}
    return context


def _update(chat_id: int = -100, user_id: int = 1, chat_type: str = "supergroup") -> MagicMock:
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=chat_id, type=chat_type)
    update.effective_user = SimpleNamespace(
        id=user_id, first_name="Ada", last_name=None, username="ada"
    )
    update.effective_message.reply_text = AsyncMock()
    update.message = update.effective_message
    return update


async def test_join_on_unbound_chat_replies_run_settrip_first() -> None:
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    context = _context(deps)
    update = _update()

    await join_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(UNBOUND_TEXT, parse_mode="HTML")


async def test_join_twice_is_idempotent_one_row_joined_true() -> None:
    ledger = InMemoryLedgerStore()
    trip = Trip(
        chat_id=-100,
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
    context = _context(deps)

    await join_command(_update(), context)
    await join_command(_update(), context)

    users = await ledger.list_users(trip)
    assert len(users) == 1
    assert users[0].joined is True


async def test_start_in_group_behaves_as_join() -> None:
    ledger = InMemoryLedgerStore()
    trip = Trip(
        chat_id=-100,
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
    context = _context(deps)
    update = _update()

    await start_command(update, context)

    users = await ledger.list_users(trip, joined_only=True)
    assert len(users) == 1
    update.effective_message.reply_text.assert_awaited_once()
    (text,) = update.effective_message.reply_text.call_args.args
    assert text.startswith("Joined ✅")


async def test_join_reports_ledger_unavailable() -> None:
    ledger = InMemoryLedgerStore()
    trip = Trip(
        chat_id=-100,
        sheet_id="sheet-id-1234567890",
        name="Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )
    await ledger.save_binding(trip)
    ledger.upsert_user = AsyncMock(side_effect=LedgerUnavailable("boom"))  # type: ignore[method-assign]
    deps = _deps(ledger)
    context = _context(deps)
    update = _update()

    await join_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        UNAVAILABLE_TEXT, parse_mode="HTML"
    )


async def test_start_in_private_shows_help() -> None:
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    context = _context(deps)
    update = _update(chat_type="private")

    await start_command(update, context)

    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.call_args
    assert kwargs.get("parse_mode") == "HTML"
