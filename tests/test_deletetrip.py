"""Tests for /deletetrip (sprint 9 item 4)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers.deletetrip import (
    CONFIRM_TEXT,
    DELETED_TEXT,
    KEPT_TEXT,
    NOT_ALLOWED_TEXT,
    deletetrip_callback,
    deletetrip_command,
)
from app.bot.handlers.join import UNBOUND_TEXT
from app.models.trip import Trip
from app.services.ledger_store import InMemoryLedgerStore
from tests.helpers import make_deps

CHAT_ID = -100
STARTER_ID = 5
OTHER_USER_ID = 7


async def _bound_ledger(*, closed: bool = False) -> tuple[InMemoryLedgerStore, Trip]:
    ledger = InMemoryLedgerStore()
    trip = Trip(
        chat_id=CHAT_ID,
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
        created_by=STARTER_ID,
    )
    trip = await ledger.save_binding(trip)
    if closed:
        await ledger.close_trip(trip)
    return ledger, trip


def _update(user_id: int) -> MagicMock:
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    update.effective_user = SimpleNamespace(
        id=user_id, first_name="U", last_name=None, username="u"
    )
    update.effective_message.reply_text = AsyncMock(return_value=SimpleNamespace(message_id=42))
    return update


def _context(deps) -> MagicMock:
    context = MagicMock()
    context.application.bot_data = {"deps": deps}
    return context


def _callback_update(data: str, presser_id: int) -> MagicMock:
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    query = update.callback_query
    query.data = data
    query.from_user = SimpleNamespace(id=presser_id)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return update


async def test_deletetrip_on_unbound_chat_replies_run_settrip_first() -> None:
    ledger = InMemoryLedgerStore()
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update(STARTER_ID)

    await deletetrip_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(UNBOUND_TEXT, parse_mode="HTML")


async def test_deletetrip_by_non_starter_is_refused() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update(OTHER_USER_ID)

    await deletetrip_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        NOT_ALLOWED_TEXT, parse_mode="HTML"
    )


async def test_deletetrip_by_starter_asks_to_confirm() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update(STARTER_ID)

    await deletetrip_command(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    (text,), kwargs = update.effective_message.reply_text.call_args
    assert text == CONFIRM_TEXT
    buttons = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert buttons == ["Delete everything", "Keep"]


async def test_deletetrip_works_on_the_latest_closed_trip_when_none_is_open() -> None:
    ledger, trip = await _bound_ledger(closed=True)
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update(STARTER_ID)

    await deletetrip_command(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    (text,), _kwargs = update.effective_message.reply_text.call_args
    assert text == CONFIRM_TEXT


async def test_delete_everything_removes_every_row() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    data = f"deletetrip:{trip.id}:yes"
    update = _callback_update(data, STARTER_ID)

    await deletetrip_callback(update, context)

    update.callback_query.answer.assert_awaited_once_with()
    update.callback_query.edit_message_text.assert_awaited_once_with(
        DELETED_TEXT, parse_mode="HTML"
    )
    assert await ledger.list_trips(CHAT_ID) == []


async def test_delete_everything_denied_for_non_starter() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    data = f"deletetrip:{trip.id}:yes"
    update = _callback_update(data, OTHER_USER_ID)

    await deletetrip_callback(update, context)

    update.callback_query.answer.assert_awaited_once_with(NOT_ALLOWED_TEXT)
    update.callback_query.edit_message_text.assert_not_awaited()
    assert len(await ledger.list_trips(CHAT_ID)) == 1


async def test_keep_leaves_the_trip_in_place() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    data = f"deletetrip:{trip.id}:keep"
    update = _callback_update(data, STARTER_ID)

    await deletetrip_callback(update, context)

    update.callback_query.edit_message_text.assert_awaited_once_with(KEPT_TEXT, parse_mode="HTML")
    assert len(await ledger.list_trips(CHAT_ID)) == 1
