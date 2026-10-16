"""Tests for app.bot.handlers.photo_expense: the handler and its callback wiring."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from PIL import Image

from app.bot.handlers.join import CLOSED_TRIP_TEXT
from app.bot.handlers.photo_expense import photo_expense_handler
from app.bot.handlers.text_expense import draft_callback, text_expense_handler
from app.bot.receipt_gate import CHAT_CAPPED_TEXT, FIVE_LEFT_WARNING_TEXT, GLOBAL_CAPPED_TEXT
from app.models.trip import Trip
from app.services.ledger_store import InMemoryLedgerStore
from app.services.llm_schemas import ReceiptExtraction
from app.services.receipt_budget import ReceiptReadBudget
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
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )
    trip = await ledger.save_binding(trip)
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


async def test_closed_trip_replies_with_closed_text() -> None:
    """sprint 9 item 3: same closed-trip reply as text expenses, not silence."""
    ledger, trip = await _bound_ledger()
    await ledger.close_trip(trip)
    reader = FakeReceiptReader(extraction=_confident_extraction())
    deps = make_deps(ledger, receipt_reader=reader)
    context = _context(deps)
    update = _update()

    await photo_expense_handler(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        CLOSED_TRIP_TEXT, parse_mode="HTML"
    )
    assert len(reader.calls) == 0


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


async def test_same_photo_sent_twice_still_leaves_one_saved_expense() -> None:
    """There is no in-memory dedup any more (spec §18): a second send of the same
    photo message starts a second draft and gets its own card, but the store's own
    unique constraint on (chat_id, message_id) means confirming it leaves exactly
    one expense row.
    """
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(extraction=_confident_extraction())
    deps = make_deps(ledger, receipt_reader=reader)
    context = _context(deps)

    first = _update(message_id=7, update_id=1)
    await photo_expense_handler(first, context)
    first.effective_message.reply_text.assert_awaited_once()
    first_draft_id = (
        first.effective_message.reply_text.call_args.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data.split(":")[0]
    )
    await deps.draft_service.choose_just_me(first_draft_id, presser_id=1)
    await deps.draft_service.confirm(first_draft_id, presser_id=1)

    second = _update(message_id=7, update_id=2)
    await photo_expense_handler(second, context)
    second.effective_message.reply_text.assert_awaited_once()
    second_draft_id = (
        second.effective_message.reply_text.call_args.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data.split(":")[0]
    )
    await deps.draft_service.choose_just_me(second_draft_id, presser_id=1)
    await deps.draft_service.confirm(second_draft_id, presser_id=1)

    saved = await ledger.list_expenses(trip)
    assert len(saved) == 1
    assert saved[0].expense_id == first_draft_id


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


def _draft_id_from(update: MagicMock) -> str:
    return (
        update.effective_message.reply_text.call_args.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data.split(":")[0]
    )


def _unreadable_extraction() -> ReceiptExtraction:
    """What the prompt asks for on a blurry photo or a non-receipt: no candidates."""
    return ReceiptExtraction(
        candidate_totals=[], description="Expense", category="other", confidence=0.1
    )


async def test_no_candidate_totals_shows_two_buttons_not_sheets_error() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger, receipt_reader=FakeReceiptReader(extraction=_unreadable_extraction()))
    context = _context(deps)
    update = _update()

    await photo_expense_handler(update, context)

    (text,), kwargs = update.effective_message.reply_text.call_args
    assert "Couldn't read that receipt" in text
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert labels == ["Try again", "Enter amount manually"]


async def test_download_failure_shows_two_buttons() -> None:
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(extraction=_confident_extraction())
    deps = make_deps(ledger, receipt_reader=reader)
    context = _context(deps)
    context.bot.get_file = AsyncMock(side_effect=RuntimeError("network blip"))
    update = _update()

    await photo_expense_handler(update, context)

    (text,), _ = update.effective_message.reply_text.call_args
    assert "Couldn't read that receipt" in text
    assert reader.calls == []


async def test_caption_with_amount_drops_the_amount_from_description() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger, receipt_reader=FakeReceiptReader(extraction=_confident_extraction()))
    context = _context(deps)
    update = _update(caption="¥3000 dinner")

    await photo_expense_handler(update, context)

    (text,), _ = update.effective_message.reply_text.call_args
    assert "<b>Dinner</b>" in text
    assert "3000" not in text


async def test_manual_amount_reply_sets_amount_and_keeps_caption() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger, receipt_reader=FakeReceiptReader(error=ReceiptUnreadable()))
    context = _context(deps)
    context.bot.edit_message_text = AsyncMock()
    photo = _update(caption="dinner", message_id=11, update_id=11)
    await photo_expense_handler(photo, context)
    draft_id = _draft_id_from(photo)

    await draft_callback(_callback_update(f"{draft_id}:manual:-"), context)

    reply = _text_update("2400", reply_to_message_id=5000, update_id=12)
    await text_expense_handler(reply, context)

    reply.effective_message.reply_text.assert_not_awaited()  # no second draft
    kwargs = context.bot.edit_message_text.call_args.kwargs
    assert kwargs["message_id"] == 5000
    assert "Dinner" in kwargs["text"]
    assert "¥2,400" in kwargs["text"]


async def test_reply_to_other_bot_message_still_parses_as_expense() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    reply = _text_update("1200 yen ramen", reply_to_message_id=4321, update_id=13)

    await text_expense_handler(reply, context)

    (text,), _ = reply.effective_message.reply_text.call_args
    assert "Ramen" in text


async def test_retry_answers_once_and_keeps_buttons_when_download_fails() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger, receipt_reader=FakeReceiptReader(error=ReceiptUnreadable()))
    context = _context(deps)
    photo = _update(message_id=14, update_id=14)
    await photo_expense_handler(photo, context)
    draft_id = _draft_id_from(photo)

    context.bot.get_file = AsyncMock(side_effect=RuntimeError("file gone"))
    callback = _callback_update(f"{draft_id}:retry:-")
    await draft_callback(callback, context)

    callback.callback_query.answer.assert_awaited_once_with("Reading the receipt again…")
    (text,), kwargs = callback.callback_query.edit_message_text.call_args
    assert "Couldn't read that receipt" in text
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert labels == ["Try again", "Enter amount manually"]


async def test_retry_success_moves_to_detected_card() -> None:
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(error=ReceiptUnreadable())
    deps = make_deps(ledger, receipt_reader=reader)
    context = _context(deps)
    photo = _update(message_id=15, update_id=15)
    await photo_expense_handler(photo, context)
    draft_id = _draft_id_from(photo)

    reader._error = None
    reader._extraction = _confident_extraction()
    callback = _callback_update(f"{draft_id}:retry:-")
    await draft_callback(callback, context)

    callback.callback_query.answer.assert_awaited_once()
    (text,), _ = callback.callback_query.edit_message_text.call_args
    assert "Lawson" in text
    assert "Who should share this?" in text


def _text_update(text: str, *, reply_to_message_id: int, update_id: int) -> MagicMock:
    update = MagicMock()
    update.update_id = update_id
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    update.effective_user = SimpleNamespace(id=1, first_name="Ada", last_name=None, username="ada")
    message = MagicMock()
    message.text = text
    message.message_id = update_id + 1000
    message.date = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    message.reply_to_message = SimpleNamespace(message_id=reply_to_message_id)
    message.reply_text = AsyncMock(return_value=SimpleNamespace(message_id=6000))
    update.effective_message = message
    return update


async def test_photo_refused_before_download_when_chat_capped() -> None:
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(extraction=_confident_extraction())
    budget = ReceiptReadBudget(chat_cap=0, global_cap=400)
    deps = make_deps(ledger, receipt_reader=reader, receipt_budget=budget)
    context = _context(deps)
    update = _update()

    await photo_expense_handler(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(CHAT_CAPPED_TEXT)
    context.bot.get_file.assert_not_awaited()
    assert len(reader.calls) == 0


async def test_photo_refused_before_download_when_global_capped() -> None:
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(extraction=_confident_extraction())
    budget = ReceiptReadBudget(chat_cap=40, global_cap=0)
    deps = make_deps(ledger, receipt_reader=reader, receipt_budget=budget)
    context = _context(deps)
    update = _update()

    await photo_expense_handler(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(GLOBAL_CAPPED_TEXT)
    context.bot.get_file.assert_not_awaited()


async def test_retry_refused_leaves_card_unchanged() -> None:
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(error=ReceiptUnreadable())
    budget = ReceiptReadBudget(chat_cap=40, global_cap=400)
    deps = make_deps(ledger, receipt_reader=reader, receipt_budget=budget)
    context = _context(deps)
    photo = _update(message_id=20, update_id=20)
    await photo_expense_handler(photo, context)
    draft_id = _draft_id_from(photo)
    assert context.bot.get_file.await_count == 1

    budget.chat_cap = 0  # capped by the time the retry is pressed
    callback = _callback_update(f"{draft_id}:retry:-")
    callback.callback_query.message = SimpleNamespace(reply_text=AsyncMock())
    await draft_callback(callback, context)

    callback.callback_query.message.reply_text.assert_awaited_once_with(CHAT_CAPPED_TEXT)
    callback.callback_query.edit_message_text.assert_not_awaited()
    assert context.bot.get_file.await_count == 1  # retry never re-downloaded


async def test_retry_non_initiator_leaves_budget_unchanged() -> None:
    """A press `check_retry` would reject (wrong presser) must not burn a read."""
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(error=ReceiptUnreadable())
    budget = ReceiptReadBudget(chat_cap=40, global_cap=400)
    deps = make_deps(ledger, receipt_reader=reader, receipt_budget=budget)
    context = _context(deps)
    photo = _update(message_id=23, update_id=23)
    await photo_expense_handler(photo, context)
    draft_id = _draft_id_from(photo)
    assert context.bot.get_file.await_count == 1
    remaining_before = budget.remaining(CHAT_ID, datetime.now(UTC))

    callback = _callback_update(f"{draft_id}:retry:-", user_id=999)
    await draft_callback(callback, context)

    callback.callback_query.answer.assert_awaited_once_with("Only Ada can edit this")
    callback.callback_query.edit_message_text.assert_not_awaited()
    assert context.bot.get_file.await_count == 1  # retry never re-downloaded
    assert budget.remaining(CHAT_ID, datetime.now(UTC)) == remaining_before  # no read taken


async def test_retry_five_left_warning_sent_after_card_edit() -> None:
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(error=ReceiptUnreadable())
    budget = ReceiptReadBudget(chat_cap=7, global_cap=400)
    deps = make_deps(ledger, receipt_reader=reader, receipt_budget=budget)
    context = _context(deps)
    photo = _update(message_id=24, update_id=24)
    await photo_expense_handler(photo, context)
    draft_id = _draft_id_from(photo)

    reader._error = None
    reader._extraction = _confident_extraction()
    callback = _callback_update(f"{draft_id}:retry:-")
    callback.callback_query.message = SimpleNamespace(reply_text=AsyncMock())
    await draft_callback(callback, context)

    callback.callback_query.edit_message_text.assert_awaited_once()
    callback.callback_query.message.reply_text.assert_awaited_once_with(FIVE_LEFT_WARNING_TEXT)
    (text,), _ = callback.callback_query.edit_message_text.call_args
    assert "Lawson" in text  # the card was already showing the re-read receipt


async def test_warning_sent_once_at_five_remaining_not_again() -> None:
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(extraction=_confident_extraction())
    budget = ReceiptReadBudget(chat_cap=6, global_cap=400)
    deps = make_deps(ledger, receipt_reader=reader, receipt_budget=budget)
    context = _context(deps)

    first = _update(message_id=21, update_id=21)
    await photo_expense_handler(first, context)
    calls = first.effective_message.reply_text.call_args_list
    assert len(calls) == 2
    assert calls[1].args[0] == FIVE_LEFT_WARNING_TEXT

    second = _update(message_id=22, update_id=22)
    await photo_expense_handler(second, context)
    assert len(second.effective_message.reply_text.call_args_list) == 1


async def test_no_warning_when_chat_cap_is_five_or_less() -> None:
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(extraction=_confident_extraction())
    budget = ReceiptReadBudget(chat_cap=5, global_cap=400)
    deps = make_deps(ledger, receipt_reader=reader, receipt_budget=budget)
    context = _context(deps)
    update = _update(message_id=23, update_id=23)

    await photo_expense_handler(update, context)

    assert len(update.effective_message.reply_text.call_args_list) == 1


async def test_failed_read_still_counts_against_budget() -> None:
    ledger, trip = await _bound_ledger()
    reader = FakeReceiptReader(error=ReceiptUnreadable())
    budget = ReceiptReadBudget(chat_cap=1, global_cap=400)
    deps = make_deps(ledger, receipt_reader=reader, receipt_budget=budget)
    context = _context(deps)

    first = _update(message_id=24, update_id=24)
    await photo_expense_handler(first, context)
    (text,), _ = first.effective_message.reply_text.call_args
    assert "Couldn't read that receipt" in text  # allowed, just unreadable

    second = _update(message_id=25, update_id=25)
    await photo_expense_handler(second, context)
    second.effective_message.reply_text.assert_awaited_once_with(CHAT_CAPPED_TEXT)
