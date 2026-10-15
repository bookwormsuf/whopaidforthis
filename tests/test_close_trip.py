"""Tests for the `Close this trip` button on /settle's reply (sprint 9 item 2)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers.balances import (
    CLOSE_TRIP_NOT_ALLOWED_TOAST,
    TRIP_CLOSED_SUFFIX,
    close_trip_callback,
    settle_command,
)
from app.models.trip import Trip
from app.services.ledger_store import InMemoryLedgerStore
from tests.helpers import make_deps

CHAT_ID = -100
STARTER_ID = 5
SETTLE_PRESSER_ID = 6
OTHER_USER_ID = 7


async def _bound_ledger() -> tuple[InMemoryLedgerStore, Trip]:
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
    return ledger, trip


def _update(user_id: int) -> MagicMock:
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    update.effective_user = SimpleNamespace(
        id=user_id, first_name="Settler", last_name=None, username="settler"
    )
    update.effective_message.reply_text = AsyncMock(return_value=SimpleNamespace(message_id=42))
    return update


def _context(deps) -> MagicMock:
    context = MagicMock()
    context.application.bot_data = {"deps": deps}
    return context


def _callback_update(data: str, presser_id: int, message_text: str = "To settle up") -> MagicMock:
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    query = update.callback_query
    query.data = data
    query.from_user = SimpleNamespace(id=presser_id)
    query.answer = AsyncMock()
    query.message.text_html = message_text
    query.edit_message_text = AsyncMock()
    return update


async def test_settle_reply_has_close_this_trip_button() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update(SETTLE_PRESSER_ID)

    await settle_command(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    (_text,), kwargs = update.effective_message.reply_text.call_args
    markup = kwargs["reply_markup"]
    assert markup is not None
    buttons = [b.text for row in markup.inline_keyboard for b in row]
    assert buttons == ["Close this trip"]
    callback_data = markup.inline_keyboard[0][0].callback_data
    assert callback_data == f"closetrip:{trip.id}:{SETTLE_PRESSER_ID}"


async def test_close_trip_button_allowed_for_trip_starter() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    data = f"closetrip:{trip.id}:{SETTLE_PRESSER_ID}"
    update = _callback_update(data, STARTER_ID)

    await close_trip_callback(update, context)

    update.callback_query.answer.assert_awaited_once_with()
    update.callback_query.edit_message_text.assert_awaited_once_with(
        "To settle up" + TRIP_CLOSED_SUFFIX, parse_mode="HTML", reply_markup=None
    )
    reloaded = (await ledger.list_trips(CHAT_ID))[0]
    assert reloaded.closed_at is not None


async def test_close_trip_button_allowed_for_settle_presser() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    data = f"closetrip:{trip.id}:{SETTLE_PRESSER_ID}"
    update = _callback_update(data, SETTLE_PRESSER_ID)

    await close_trip_callback(update, context)

    reloaded = (await ledger.list_trips(CHAT_ID))[0]
    assert reloaded.closed_at is not None


async def test_close_trip_button_denied_for_other_user() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    data = f"closetrip:{trip.id}:{SETTLE_PRESSER_ID}"
    update = _callback_update(data, OTHER_USER_ID)

    await close_trip_callback(update, context)

    update.callback_query.answer.assert_awaited_once_with(CLOSE_TRIP_NOT_ALLOWED_TOAST)
    update.callback_query.edit_message_text.assert_not_awaited()
    reloaded = (await ledger.list_trips(CHAT_ID))[0]
    assert reloaded.closed_at is None
