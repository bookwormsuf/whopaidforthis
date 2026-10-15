"""Tests for app.bot.handlers.text_expense: the handler and draft_callback wiring."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers.join import CLOSED_TRIP_TEXT
from app.bot.handlers.text_expense import draft_callback, text_expense_handler
from app.models.trip import Trip
from app.services.ledger_store import InMemoryLedgerStore, LedgerUnavailable
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


def _update(text: str, *, user_id: int = 1, message_id: int = 1, update_id: int = 900) -> MagicMock:
    update = MagicMock()
    update.update_id = update_id
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    update.effective_user = SimpleNamespace(
        id=user_id, first_name="Ada", last_name=None, username="ada"
    )
    message = MagicMock()
    message.text = text
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
    return context


async def test_silent_on_unbound_chat() -> None:
    ledger = InMemoryLedgerStore()
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update("1200 yen ramen")

    await text_expense_handler(update, context)

    update.effective_message.reply_text.assert_not_awaited()


async def test_closed_trip_replies_with_closed_text() -> None:
    """sprint 9 item 3: a closed trip (no open trip for the chat) replies the
    one-line closed text, instead of staying silent like an unbound chat."""
    ledger, trip = await _bound_ledger()
    await ledger.close_trip(trip)
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update("1200 yen ramen")

    await text_expense_handler(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        CLOSED_TRIP_TEXT, parse_mode="HTML"
    )


async def test_silent_on_non_expense_text() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update("lol that ramen was so good")

    await text_expense_handler(update, context)

    update.effective_message.reply_text.assert_not_awaited()


async def test_unsupported_currency_gets_one_line_reply() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update("500 baht massage")

    await text_expense_handler(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        "I can only record JPY or SGD for this trip.", parse_mode="HTML"
    )


async def test_valid_expense_sends_draft_card_and_joins_sender() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update("1200 yen ramen")

    await text_expense_handler(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    (text,), kwargs = update.effective_message.reply_text.call_args
    assert "Ramen" in text
    assert kwargs["reply_markup"] is not None

    users = await ledger.list_users(trip, joined_only=True)
    assert len(users) == 1
    assert users[0].user_id == 1


async def test_duplicate_message_id_still_leaves_one_saved_expense() -> None:
    """There is no in-memory dedup any more (spec §18): a re-send of the same
    Telegram message (same message_id) starts a second draft and gets its own
    reply, but the store's own unique constraint on (chat_id, message_id) means
    confirming it leaves exactly one expense row -- the store's insert is the
    idempotency check.
    """
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)

    first = _update("1200 yen ramen", message_id=42, update_id=1)
    await text_expense_handler(first, context)
    first.effective_message.reply_text.assert_awaited_once()
    first_draft_id = (
        first.effective_message.reply_text.call_args.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data.split(":")[0]
    )
    await deps.draft_service.choose_just_me(first_draft_id, presser_id=1)
    await deps.draft_service.confirm(first_draft_id, presser_id=1)

    second = _update("1200 yen ramen", message_id=42, update_id=2)
    await text_expense_handler(second, context)
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


async def test_replayed_update_id_is_silently_skipped() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)

    update = _update("1200 yen ramen", message_id=1, update_id=777)
    await text_expense_handler(update, context)
    update.effective_message.reply_text.assert_awaited_once()

    replay = _update("1200 yen ramen", message_id=2, update_id=777)
    await text_expense_handler(replay, context)
    replay.effective_message.reply_text.assert_not_awaited()


async def test_thirteen_word_message_stays_silent() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update("I paid 1200 yen for the amazing ramen dinner we all had together")

    await text_expense_handler(update, context)

    update.effective_message.reply_text.assert_not_awaited()


def _callback_update(data: str, *, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    update.callback_query.data = data
    update.callback_query.from_user = SimpleNamespace(id=user_id)
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def test_draft_callback_stale_draft_reports_expired() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    update = _callback_update("01UNKNOWNDRAFTID0000000000:justme:-")

    await draft_callback(update, context)

    update.callback_query.edit_message_text.assert_awaited_once_with(
        "This expense expired, please send it again", parse_mode="HTML"
    )


async def test_draft_callback_foreign_presser_gets_toast_no_edit() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update("1200 yen ramen")
    await text_expense_handler(update, context)

    draft_id = (
        update.effective_message.reply_text.call_args.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data.split(":")[0]
    )

    cb_update = _callback_update(f"{draft_id}:justme:-", user_id=999)
    await draft_callback(cb_update, context)

    cb_update.callback_query.answer.assert_awaited_once_with("Only Ada can edit this")
    cb_update.callback_query.edit_message_text.assert_not_awaited()


async def test_draft_callback_full_flow_confirm_edits_saved_message() -> None:
    ledger, trip = await _bound_ledger()
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update("1200 yen ramen")
    await text_expense_handler(update, context)

    draft_id = (
        update.effective_message.reply_text.call_args.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data.split(":")[0]
    )

    justme_update = _callback_update(f"{draft_id}:justme:-")
    await draft_callback(justme_update, context)
    justme_update.callback_query.edit_message_text.assert_awaited_once()

    confirm_update = _callback_update(f"{draft_id}:confirm:-")
    await draft_callback(confirm_update, context)
    confirm_update.callback_query.edit_message_text.assert_awaited_once()
    (text,), kwargs = confirm_update.callback_query.edit_message_text.call_args
    assert text.startswith("Saved ✅ Ramen")
    assert "reply_markup" not in kwargs or kwargs.get("reply_markup") is None

    saved = await ledger.list_expenses(trip)
    assert len(saved) == 1
    assert saved[0].status == "confirmed"


class _AlwaysFailAppendLedger(InMemoryLedgerStore):
    async def append_expense(self, trip, expense, line_items=(), assignments=None) -> None:
        raise LedgerUnavailable("boom")


async def test_draft_callback_confirm_failure_shows_retry_save_with_error_line() -> None:
    """Spec §10/§14: a failed save keeps the draft in preview with a Retry save
    button and an error line, the message edited with its keyboard intact.
    """
    ledger = _AlwaysFailAppendLedger()
    trip = Trip(
        chat_id=CHAT_ID,
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )
    trip = await ledger.save_binding(trip)
    deps = make_deps(ledger)
    context = _context(deps)
    update = _update("1200 yen ramen")
    await text_expense_handler(update, context)

    draft_id = (
        update.effective_message.reply_text.call_args.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data.split(":")[0]
    )

    justme_update = _callback_update(f"{draft_id}:justme:-")
    await draft_callback(justme_update, context)

    confirm_update = _callback_update(f"{draft_id}:confirm:-")
    await draft_callback(confirm_update, context)

    confirm_update.callback_query.answer.assert_awaited_once()
    confirm_update.callback_query.edit_message_text.assert_awaited_once()
    (text,), kwargs = confirm_update.callback_query.edit_message_text.call_args
    assert "Couldn't reach Google Sheets" in text
    assert "Retry save" in text or any(
        button.text == "Retry save"
        for row in kwargs["reply_markup"].inline_keyboard
        for button in row
    )
    buttons = [button for row in kwargs["reply_markup"].inline_keyboard for button in row]
    retry_button = next(b for b in buttons if b.callback_data == f"{draft_id}:confirm:-")
    assert retry_button.text == "Retry save"
    assert any(b.callback_data == f"{draft_id}:edit:-" for b in buttons)
    assert any(b.callback_data == f"{draft_id}:cancel:-" for b in buttons)

    stuck = await deps.draft_service.get(draft_id)
    assert stuck.state == "preview"
