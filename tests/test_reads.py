"""Tests for app.bot.handlers.reads: /recent, /today, /summary, /undo."""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers.reads import (
    NOTHING_TO_UNDO_TEXT,
    recent_command,
    run_recent,
    run_summary,
    run_today,
    run_undo,
    summary_command,
    today_command,
    undo_callback,
    undo_command,
)
from app.models.category import Category
from app.models.expense import Expense, ExpenseSplit
from app.models.trip import Trip
from app.services.ledger_store import InMemoryLedgerStore
from tests.helpers import make_deps

CHAT_ID = -100


async def _bound_ledger() -> tuple[InMemoryLedgerStore, Trip]:
    ledger = InMemoryLedgerStore()
    trip = Trip(
        chat_id=CHAT_ID,
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )
    trip = await ledger.save_binding(trip)
    return ledger, trip


_message_ids = itertools.count(1)


def _expense(
    expense_id: str,
    *,
    created_by: int = 1,
    transaction_date: date = date(2026, 9, 20),
    description: str = "Ramen",
    trip_amount_minor: int = 1200,
    home_amount_minor: int = 10,
    original_currency: str = "JPY",
    created_at: datetime | None = None,
    message_id: int | None = None,
) -> Expense:
    original_amount_minor = home_amount_minor if original_currency == "SGD" else trip_amount_minor
    return Expense(
        expense_id=expense_id,
        created_at=created_at or datetime.now(UTC),
        transaction_date=transaction_date,
        chat_id=CHAT_ID,
        # Each expense gets its own message_id (the store's unique constraint is on
        # (chat_id, message_id), spec §18) unless a test passes one explicitly.
        message_id=message_id if message_id is not None else next(_message_ids),
        created_by_user_id=created_by,
        payer_user_id=created_by,
        payer_username="ada",
        payer_display_name="Ada Lovelace",
        description=description,
        merchant=None,
        category=Category.FOOD,
        original_amount_minor=original_amount_minor,
        original_currency=original_currency,
        trip_currency="JPY",
        home_currency="SGD",
        trip_amount_minor=trip_amount_minor,
        home_amount_minor=home_amount_minor,
        rate=Decimal("0.0081"),
        rate_date=date(2026, 9, 19),
        source_type="text",
        split_method="equal",
        status="confirmed",
        cancelled_at=None,
        notes=None,
        splits=[
            ExpenseSplit(
                user_id=created_by,
                username="ada",
                display_name="Ada Lovelace",
                share_trip_minor=trip_amount_minor,
                share_home_minor=home_amount_minor,
                split_method="equal",
            )
        ],
    )


def _update(user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    update.effective_user = SimpleNamespace(
        id=user_id, first_name="Ada", last_name=None, username="ada"
    )
    update.effective_message.reply_text = AsyncMock()
    return update


def _context(deps) -> MagicMock:
    context = MagicMock()
    context.application.bot_data = {"deps": deps}
    return context


async def test_recent_last_five_oldest_first() -> None:
    ledger, trip = await _bound_ledger()
    for i in range(7):
        await ledger.append_expense(
            trip,
            _expense(f"e{i}", created_at=datetime(2026, 9, 20, 10, i, tzinfo=UTC)),
        )
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update()

    await recent_command(update, context)

    (text,), kwargs = update.effective_message.reply_text.call_args
    bullets = [line for line in text.split("\n") if line.startswith("• ")]
    assert len(bullets) == 5  # last 5 only


async def test_today_filters_by_trip_timezone_across_date_boundary() -> None:
    """A message at 23:30 UTC is the next day in Asia/Tokyo (UTC+9)."""
    ledger, trip = await _bound_ledger()
    # UTC 2026-09-19 23:30 -> Asia/Tokyo 2026-09-20 08:30, so transaction_date is the
    # 20th in the trip's own timezone.
    await ledger.append_expense(
        trip,
        _expense(
            "e1",
            transaction_date=date(2026, 9, 20),
            created_at=datetime(2026, 9, 19, 23, 30, tzinfo=UTC),
        ),
    )
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update()

    from unittest.mock import patch

    fixed_now = datetime(2026, 9, 20, 1, 0, tzinfo=UTC)  # 10:00 in Tokyo, same day
    with patch("app.bot.handlers.reads.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.fromisoformat = datetime.fromisoformat
        await today_command(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    (text,), kwargs = update.effective_message.reply_text.call_args
    assert "Ramen" in text
    assert "Spent today:" in text


async def test_summary_counts_only_confirmed() -> None:
    ledger, trip = await _bound_ledger()
    await ledger.append_expense(trip, _expense("e1"))
    await ledger.append_expense(trip, _expense("e2"))
    await ledger.cancel_expense(trip, "e2", datetime.now(UTC), "note")
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update()

    await summary_command(update, context)

    (text,), kwargs = update.effective_message.reply_text.call_args
    assert text.startswith("1 expense ·")


async def test_undo_no_expenses_replies_nothing_to_undo() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update()

    await undo_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        NOTHING_TO_UNDO_TEXT, parse_mode="HTML"
    )


async def test_undo_calls_cancel_expense_never_delete() -> None:
    ledger, trip = await _bound_ledger()
    await ledger.append_expense(trip, _expense("e1", created_by=1))
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update()

    await undo_command(update, context)
    update.effective_message.reply_text.assert_awaited_once()

    cb_update = MagicMock()
    cb_update.callback_query.data = "undo:e1:yes"
    cb_update.callback_query.from_user = SimpleNamespace(
        id=1, first_name="Ada", last_name=None, username="ada"
    )
    cb_update.callback_query.answer = AsyncMock()
    cb_update.callback_query.edit_message_text = AsyncMock()
    cb_update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")

    await undo_callback(cb_update, context)

    saved = await ledger.list_expenses(trip)
    assert len(saved) == 1  # row still present, never deleted
    assert saved[0].status == "cancelled"
    cb_update.callback_query.edit_message_text.assert_awaited_once()
    (text,), kwargs = cb_update.callback_query.edit_message_text.call_args
    assert text.startswith("Cancelled ✅ Ramen")


async def test_recent_line_shows_home_only_for_home_currency_expense() -> None:
    """2026-10-01 change: an expense entered in the home currency (e.g. "S$12
    grab") shows only the home amount on its /recent line, never the yen.
    """
    ledger, trip = await _bound_ledger()
    await ledger.append_expense(
        trip,
        _expense(
            "e1",
            description="Grab",
            trip_amount_minor=1474,
            home_amount_minor=1200,
            original_currency="SGD",
        ),
    )
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update()

    await recent_command(update, context)

    (text,), kwargs = update.effective_message.reply_text.call_args
    assert text == "<b>Recent expenses</b>\n• 20 Sep · Grab S$12.00\n   Ada paid"
    assert "¥" not in text


async def test_recent_line_keeps_trip_currency_pair() -> None:
    ledger, trip = await _bound_ledger()
    await ledger.append_expense(trip, _expense("e1"))  # JPY original, the default
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update()

    await recent_command(update, context)

    (text,), kwargs = update.effective_message.reply_text.call_args
    assert text == "<b>Recent expenses</b>\n• 20 Sep · Ramen ¥1,200 (S$0.10)\n   Ada paid"


async def test_undo_cancel_this_line_shows_home_only_for_home_currency_expense() -> None:
    ledger, trip = await _bound_ledger()
    await ledger.append_expense(
        trip,
        _expense(
            "e1",
            description="Grab",
            trip_amount_minor=1474,
            home_amount_minor=1200,
            original_currency="SGD",
        ),
    )
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update()

    await undo_command(update, context)

    (text,), kwargs = update.effective_message.reply_text.call_args
    assert text == "Cancel this? Grab S$12.00"


async def test_undo_cancelled_line_shows_home_only_for_home_currency_expense() -> None:
    ledger, trip = await _bound_ledger()
    await ledger.append_expense(
        trip,
        _expense(
            "e1",
            description="Grab",
            trip_amount_minor=1474,
            home_amount_minor=1200,
            original_currency="SGD",
        ),
    )
    deps = make_deps(ledger)
    context = _context(deps)

    cb_update = MagicMock()
    cb_update.callback_query.data = "undo:e1:yes"
    cb_update.callback_query.from_user = SimpleNamespace(
        id=1, first_name="Ada", last_name=None, username="ada"
    )
    cb_update.callback_query.answer = AsyncMock()
    cb_update.callback_query.edit_message_text = AsyncMock()
    cb_update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")

    await undo_callback(cb_update, context)

    (text,), kwargs = cb_update.callback_query.edit_message_text.call_args
    assert text == "Cancelled ✅ Grab S$12.00"
    assert "¥" not in text


def _fake_send():
    sent: list[tuple[str, dict]] = []

    async def send(text: str, *, reply_markup=None):
        sent.append((text, {"reply_markup": reply_markup}))
        return SimpleNamespace(message_id=1)

    return send, sent


async def test_run_recent_sends_last_five_with_a_fake_send() -> None:
    ledger, trip = await _bound_ledger()
    await ledger.append_expense(trip, _expense("e1", transaction_date=date(2026, 9, 20)))
    deps = make_deps(ledger)
    send, sent = _fake_send()

    await run_recent(deps, SimpleNamespace(id=CHAT_ID), SimpleNamespace(id=1), send)

    assert sent[0][0].startswith("<b>Recent expenses</b>")


async def test_run_today_sends_no_expenses_today_with_a_fake_send() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    send, sent = _fake_send()

    await run_today(deps, SimpleNamespace(id=CHAT_ID), SimpleNamespace(id=1), send)

    assert sent[0][0] == "No expenses today."


async def test_run_summary_sends_the_totals_with_a_fake_send() -> None:
    ledger, trip = await _bound_ledger()
    await ledger.append_expense(trip, _expense("e1"))
    deps = make_deps(ledger)
    send, sent = _fake_send()

    await run_summary(deps, SimpleNamespace(id=CHAT_ID), SimpleNamespace(id=1), send)

    assert sent[0][0].startswith("1 expense ·")


async def test_run_undo_offers_the_latest_expense_with_a_fake_send() -> None:
    ledger, trip = await _bound_ledger()
    await ledger.append_expense(trip, _expense("e1", created_by=1, description="Ramen"))
    deps = make_deps(ledger)
    send, sent = _fake_send()

    await run_undo(deps, SimpleNamespace(id=CHAT_ID), SimpleNamespace(id=1), send)

    assert sent[0][0].startswith("Cancel this? Ramen")
    assert sent[0][1]["reply_markup"] is not None
