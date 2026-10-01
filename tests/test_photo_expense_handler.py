"""Tests for app.bot.handlers.photo_expense: the handler and its callback wiring."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from PIL import Image

from app.bot.handlers.photo_expense import photo_expense_handler
from app.bot.handlers.text_expense import draft_callback
from app.models.trip import Trip
from app.services.ledger_store import InMemoryLedgerStore
from app.services.llm_schemas import ReceiptExtraction
from app.services.receipt_parser import ReceiptUnreadable
from tests.helpers import FakeReceiptReader, make_deps

CHAT_ID = -100


def _jpeg_bytes() -> bytes:
    image = Image.new("RGB", (100, 100), color=(10, 20, 30))
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


async def _bound_ledger() -> tuple[InMemoryLedgerStore, Trip]:
    ledger = InMemoryLedgerStore()
    trip = Trip(
        chat_id=CHAT_ID,
        sheet_id="sheet-id-1234567890",
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )
    await ledger.save_binding(trip)
    await ledger.initialise_trip_sheet(trip)
    return ledger, trip


def _confident_extraction() -> ReceiptExtraction:
    return ReceiptExtraction(
        merchant="Lawson",
        currency="JPY",
        transaction_datetime="2026-09-20T14:00:00",
        line_items=[{"description": "Onigiri", "quantity": 2, "amount_minor": 300, "kind": "item"}],
        candidate_totals=[
            {"label_as_printed": "合計", "amount_minor": 300, "is_grand_total_guess": True}
        ],
        description="Konbini Snacks",
        category="groceries",
        confidence=0.95,
    )


def _update(
    *, caption: str | None = None, user_id: int = 1, message_id: int = 1, update_id: int = 900
) -> MagicMock:
    update = MagicMock()
    update.update_id = update_id
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    update.effective_user = SimpleNamespace(
        id=user_id, first_name="Ada", last_name=None, username="ada"
    )
    message = MagicMock()
    message.photo = [SimpleNamespace(file_id="photo-file-id", file_size=1000)]
    message.caption = caption
    message.message_id = message_id
    message.date = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    message.reply_text = AsyncMock(return_value=SimpleNamespace(message_id=5000))
    update.effective_message = message
    update.message = message
    return update


def _context(deps) -> MagicMock:
    context = MagicMock()
    context.application.bot_data = {"deps": deps}
    context.bot.username = "whopaidforthisbot"
    context.bot.get_file = AsyncMock(
        return_value=SimpleNamespace(
            file_size=1000, download_as_bytearray=AsyncMock(return_value=bytearray(_jpeg_bytes()))
        )
    )
    return context


async def test_confident_receipt_sends_detected_card() -> None:
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(extraction=_confident_extraction())
    deps = make_deps(ledger, receipt_reader=reader)
    context = _context(deps)
    update = _update()

    await photo_expense_handler(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    (text,), kwargs = update.effective_message.reply_text.call_args
    assert "Lawson" in text
    assert "Konbini Snacks" in text
    assert kwargs["reply_markup"] is not None
    assert len(reader.calls) == 1


async def test_caption_overrides_description() -> None:
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(extraction=_confident_extraction())
    deps = make_deps(ledger, receipt_reader=reader)
    context = _context(deps)
    update = _update(caption="dinner")

    await photo_expense_handler(update, context)

    (text,), _ = update.effective_message.reply_text.call_args
    assert "Dinner" in text
    assert "Konbini Snacks" not in text


async def test_unreadable_receipt_shows_two_buttons() -> None:
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(error=ReceiptUnreadable())
    deps = make_deps(ledger, receipt_reader=reader)
    context = _context(deps)
    update = _update()

    await photo_expense_handler(update, context)

    (text,), kwargs = update.effective_message.reply_text.call_args
    assert "Couldn't read that receipt" in text
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert labels == ["Try again", "Enter amount manually"]


async def test_same_photo_sent_twice_only_one_draft() -> None:
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(extraction=_confident_extraction())
    deps = make_deps(ledger, receipt_reader=reader)
    context = _context(deps)

    first = _update(message_id=7, update_id=1)
    await photo_expense_handler(first, context)
    first.effective_message.reply_text.assert_awaited_once()
    draft_id = (
        first.effective_message.reply_text.call_args.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data.split(":")[0]
    )
    await deps.draft_service.choose_just_me(draft_id, presser_id=1)
    await deps.draft_service.confirm(draft_id, presser_id=1)

    second = _update(message_id=7, update_id=2)
    await photo_expense_handler(second, context)
    second.effective_message.reply_text.assert_not_awaited()


def _callback_update(data: str, *, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    update.callback_query.data = data
    update.callback_query.from_user = SimpleNamespace(id=user_id)
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def test_total_choice_callback_proceeds_to_reconcile() -> None:
    ledger, trip = await _bound_ledger()
    extraction = ReceiptExtraction(
        merchant="Ichiran",
        currency="JPY",
        line_items=[{"description": "Ramen", "quantity": 1, "amount_minor": 2380, "kind": "item"}],
        candidate_totals=[
            {"label_as_printed": "小計", "amount_minor": 2380, "is_grand_total_guess": False},
            {"label_as_printed": "合計", "amount_minor": 2618, "is_grand_total_guess": True},
        ],
        description="Ramen",
        category="food",
        confidence=0.8,
    )
    reader = FakeReceiptReader(extraction=extraction)
    deps = make_deps(ledger, receipt_reader=reader)
    context = _context(deps)
    update = _update(message_id=9, update_id=3)

    await photo_expense_handler(update, context)
    draft_id = (
        update.effective_message.reply_text.call_args.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data.split(":")[0]
    )

    callback_update = _callback_update(f"{draft_id}:total:0")
    await draft_callback(callback_update, context)
    callback_update.callback_query.edit_message_text.assert_awaited_once()
    (text,), kwargs = callback_update.callback_query.edit_message_text.call_args
    assert "Use the receipt total anyway?" in text
