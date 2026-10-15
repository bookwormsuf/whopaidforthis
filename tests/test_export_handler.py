"""Tests for app.bot.handlers.export: `/export`."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from openpyxl import load_workbook

from app.bot.handlers.export import export_command
from app.bot.handlers.join import UNBOUND_TEXT
from app.models.category import Category
from app.models.expense import Expense, ExpenseSplit
from app.models.trip import Trip
from app.services.ledger_store import InMemoryLedgerStore
from tests.helpers import make_deps

CHAT_ID = -100


def _trip_kwargs(**overrides: object) -> dict:
    defaults = dict(
        chat_id=CHAT_ID,
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return defaults


def _expense(expense_id: str, message_id: int, payer_id: int = 1) -> Expense:
    return Expense(
        expense_id=expense_id,
        created_at=datetime.now(UTC),
        transaction_date=date(2026, 10, 1),
        chat_id=CHAT_ID,
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


def _update() -> MagicMock:
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    update.effective_message.reply_document = AsyncMock()
    update.effective_message.reply_text = AsyncMock()
    return update


def _context(deps) -> MagicMock:
    context = MagicMock()
    context.application.bot_data = {"deps": deps}
    return context


async def test_export_sends_workbook_for_open_trip() -> None:
    ledger = InMemoryLedgerStore()
    trip = await ledger.save_binding(Trip(**_trip_kwargs()))
    await ledger.append_expense(trip, _expense("e1", message_id=1))
    deps = make_deps(ledger)
    update = _update()

    await export_command(update, _context(deps))

    update.effective_message.reply_document.assert_awaited_once()
    _, kwargs = update.effective_message.reply_document.call_args
    document = kwargs["document"]
    assert document.filename.startswith("whopaid-japan-trip-")
    assert document.filename.endswith(".xlsx")
    assert kwargs["caption"] == "Japan Trip · 1 expense"

    wb = load_workbook(BytesIO(document.input_file_content))
    assert wb.sheetnames == ["Expenses", "Shares", "Line items", "People", "Balances"]
    update.effective_message.reply_text.assert_not_called()


async def test_export_falls_back_to_most_recently_closed_trip() -> None:
    ledger = InMemoryLedgerStore()
    first = await ledger.save_binding(Trip(**_trip_kwargs(name="First")))
    await ledger.append_expense(first, _expense("e1", message_id=1))
    # Starting a second trip closes the first (InMemoryLedgerStore.save_binding).
    await ledger.save_binding(Trip(**_trip_kwargs(name="Second")))
    await ledger.close_trip(await ledger.get_binding(CHAT_ID))  # close "Second" too
    deps = make_deps(ledger)
    update = _update()

    await export_command(update, _context(deps))

    update.effective_message.reply_document.assert_awaited_once()
    _, kwargs = update.effective_message.reply_document.call_args
    # "Second" closed most recently, so it's picked over "First".
    assert kwargs["caption"] == "Second · 0 expenses"


async def test_export_with_no_trip_at_all_uses_unbound_gate_reply() -> None:
    ledger = InMemoryLedgerStore()
    deps = make_deps(ledger)
    update = _update()

    await export_command(update, _context(deps))

    update.effective_message.reply_document.assert_not_called()
    update.effective_message.reply_text.assert_awaited_once_with(UNBOUND_TEXT, parse_mode="HTML")


async def test_export_retries_once_on_upload_timeout() -> None:
    from telegram.error import TimedOut

    from app.bot.handlers import export as export_mod

    ledger = InMemoryLedgerStore()
    await ledger.save_binding(Trip(**_trip_kwargs()))
    update, context = _update(), _context(make_deps(ledger))
    calls: list[dict] = []

    async def flaky(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimedOut()

    update.effective_message.reply_document = flaky
    await export_mod.export_command(update, context)
    assert len(calls) == 2
    assert calls[0]["write_timeout"] == export_mod.UPLOAD_TIMEOUT_S


async def test_export_gives_up_after_second_timeout() -> None:
    from telegram.error import TimedOut

    from app.bot.handlers import export as export_mod

    ledger = InMemoryLedgerStore()
    await ledger.save_binding(Trip(**_trip_kwargs()))
    update, context = _update(), _context(make_deps(ledger))

    async def always(**kwargs):
        raise TimedOut()

    update.effective_message.reply_document = always
    await export_mod.export_command(update, context)
    update.effective_message.reply_text.assert_awaited_with(
        export_mod.UPLOAD_FAILED_TEXT, parse_mode="HTML"
    )
