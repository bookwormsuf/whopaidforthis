"""Tests for the `Close this trip` button on /settle's reply (sprint 9 item 2)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers.balances import (
    CLOSE_TRIP_NOT_ALLOWED_TOAST,
    TRIP_CLOSED_SUFFIX,
    close_trip_callback,
    run_balances,
    run_mine,
    run_settle,
    settle_command,
)
from app.models.category import Category
from app.models.expense import Expense, ExpenseSplit
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


def _fake_send():
    sent: list[tuple[str, dict]] = []

    async def send(text: str, *, reply_markup=None):
        sent.append((text, {"reply_markup": reply_markup}))
        return SimpleNamespace(message_id=1)

    return send, sent


def _expense(payer_id: int) -> Expense:
    return Expense(
        expense_id="e1",
        created_at=datetime.now(UTC),
        transaction_date=date(2026, 10, 1),
        chat_id=CHAT_ID,
        message_id=1,
        created_by_user_id=payer_id,
        payer_user_id=payer_id,
        payer_username="ada",
        payer_display_name="Ada",
        description="Ramen",
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
        status="confirmed",
        cancelled_at=None,
        notes=None,
        splits=[
            ExpenseSplit(
                user_id=payer_id,
                username="ada",
                display_name="Ada",
                share_trip_minor=1200,
                share_home_minor=1000,
                split_method="equal",
            )
        ],
    )


async def test_run_balances_sends_who_owes_what_with_a_fake_send() -> None:
    ledger, trip = await _bound_ledger()
    await ledger.append_expense(trip, _expense(STARTER_ID))
    deps = make_deps(ledger)
    send, sent = _fake_send()

    await run_balances(deps, SimpleNamespace(id=CHAT_ID), SimpleNamespace(id=STARTER_ID), send)

    assert sent[0][0].startswith("<b>Who owes what</b>")


async def test_run_mine_sends_the_callers_standing_with_a_fake_send() -> None:
    ledger, trip = await _bound_ledger()
    await ledger.append_expense(trip, _expense(STARTER_ID))
    deps = make_deps(ledger)
    send, sent = _fake_send()

    await run_mine(deps, SimpleNamespace(id=CHAT_ID), SimpleNamespace(id=STARTER_ID), send)

    assert "You paid" in sent[0][0]


async def test_run_settle_sends_all_square_with_a_close_button() -> None:
    ledger, trip = await _bound_ledger()
    await ledger.append_expense(trip, _expense(STARTER_ID))
    deps = make_deps(ledger)
    send, sent = _fake_send()

    await run_settle(deps, SimpleNamespace(id=CHAT_ID), SimpleNamespace(id=STARTER_ID), send)

    assert sent[0][0] == "All square 🎉"
    assert sent[0][1]["reply_markup"] is not None
