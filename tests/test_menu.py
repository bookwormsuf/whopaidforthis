"""Tests for the /start welcome menu (sprint 10, docs/sprints/10-start-menu.md)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from telegram.error import BadRequest, Forbidden

from app.bot.handlers.deletetrip import NOT_ALLOWED_TEXT
from app.bot.handlers.join import CLOSED_TRIP_TEXT, UNBOUND_TEXT
from app.bot.handlers.menu import (
    HOWTO_TEXT,
    PIN_HINT,
    bot_added_to_group,
    menu_callback,
    menu_keyboard,
    post_welcome,
    private_start_keyboard,
    welcome_text,
)
from app.models.trip import Trip
from app.services.ledger_store import InMemoryLedgerStore
from tests.helpers import make_deps

CHAT_ID = -100
STARTER_ID = 5
OTHER_USER_ID = 7


def _labels(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


def _callbacks(markup) -> list[list[str | None]]:
    return [[b.callback_data for b in row] for row in markup.inline_keyboard]


class _Bot:
    """A fake `telegram.Bot`, just enough surface for `post_welcome`/`menu_callback`."""

    def __init__(self, *, pin_error: Exception | None = None, bot_id: int = 999) -> None:
        self.id = bot_id
        self.username = "whopaidbot"
        self._pin_error = pin_error
        self._next_message_id = 0
        self.sent_messages: list[dict] = []
        self.sent_documents: list[dict] = []
        self.pinned: list[tuple[int, int]] = []
        self.edited: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        self._next_message_id += 1
        self.sent_messages.append({"chat_id": chat_id, "text": text, **kwargs})
        return SimpleNamespace(message_id=self._next_message_id)

    async def send_document(self, chat_id, document, **kwargs):
        self._next_message_id += 1
        self.sent_documents.append({"chat_id": chat_id, "document": document, **kwargs})
        return SimpleNamespace(message_id=self._next_message_id)

    async def pin_chat_message(self, chat_id, message_id, **kwargs):
        if self._pin_error is not None:
            raise self._pin_error
        self.pinned.append((chat_id, message_id))

    async def edit_message_text(self, text, *, chat_id, message_id, **kwargs):
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "text": text, **kwargs})


async def _bound_ledger(*, closed: bool = False):
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


def _chat(chat_id: int = CHAT_ID) -> SimpleNamespace:
    return SimpleNamespace(id=chat_id, type="supergroup", title="Trip")


def _user(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, first_name="U", last_name=None, username="u")


def _context(deps, bot) -> MagicMock:
    context = MagicMock()
    context.application.bot_data = {"deps": deps}
    context.bot = bot
    return context


def _callback_update(data: str, presser_id: int, chat_id: int = CHAT_ID) -> MagicMock:
    update = MagicMock()
    update.effective_chat = _chat(chat_id)
    query = update.callback_query
    query.data = data
    query.from_user = _user(presser_id)
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    return update


# --- welcome_text ----------------------------------------------------------


def test_welcome_text_no_trip() -> None:
    text = welcome_text(None, False)
    assert text.endswith("No trip yet.")


def test_welcome_text_closed() -> None:
    text = welcome_text(None, True)
    assert text.endswith("The last trip is closed. Start a new one or export it.")


async def test_welcome_text_open_names_the_trip() -> None:
    _, trip = await _bound_ledger()
    text = welcome_text(trip, False)
    assert text.endswith("Trip: Japan Trip (JPY → SGD)")


# --- menu_keyboard: shape per state and page --------------------------------


def test_keyboard_no_trip_is_one_page() -> None:
    markup = menu_keyboard("no_trip")
    assert _labels(markup) == [["Start a trip"]]
    assert _callbacks(markup) == [["menu:settrip"]]


def test_keyboard_closed_trip() -> None:
    markup = menu_keyboard("closed")
    assert _labels(markup) == [["Start a new trip"], ["Export"], ["Delete trip"]]
    assert _callbacks(markup) == [["menu:settrip"], ["menu:export"], ["menu:deletetrip"]]


def test_keyboard_open_page_1() -> None:
    markup = menu_keyboard("open", page=1)
    assert _labels(markup) == [
        ["Join"],
        ["Balances", "Settle"],
        ["Recent", "Today", "Summary"],
        ["More ▸"],
    ]
    assert _callbacks(markup)[-1] == ["menu:page:2"]


def test_keyboard_open_page_2() -> None:
    markup = menu_keyboard("open", page=2)
    assert _labels(markup) == [
        ["Mine", "Undo last"],
        ["Export"],
        ["How to add an expense"],
        ["Delete trip"],
        ["◂ Back"],
    ]
    assert _callbacks(markup)[-1] == ["menu:page:1"]


# --- menu_callback: paging edits the keyboard, not the text ----------------


async def test_paging_edits_markup_not_text() -> None:
    bot = _Bot()
    deps = make_deps((await _bound_ledger())[0])
    context = _context(deps, bot)
    update = _callback_update("menu:page:2", STARTER_ID)

    await menu_callback(update, context)

    update.callback_query.answer.assert_awaited_once_with()
    update.callback_query.edit_message_reply_markup.assert_awaited_once()
    _, kwargs = update.callback_query.edit_message_reply_markup.call_args
    assert _labels(kwargs["reply_markup"])[0] == ["Mine", "Undo last"]
    assert not bot.sent_messages


async def test_back_returns_to_page_1() -> None:
    bot = _Bot()
    deps = make_deps((await _bound_ledger())[0])
    context = _context(deps, bot)
    update = _callback_update("menu:page:1", STARTER_ID)

    await menu_callback(update, context)

    _, kwargs = update.callback_query.edit_message_reply_markup.call_args
    assert _labels(kwargs["reply_markup"])[0] == ["Join"]


# --- menu_callback: one dispatch test per action, same text as the command -


async def test_press_balances_matches_typed_command_text() -> None:
    from app.bot.handlers.balances import NO_BALANCES_TEXT

    ledger, _ = await _bound_ledger()
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:balances", STARTER_ID)

    await menu_callback(update, context)

    assert bot.sent_messages[-1]["text"] == NO_BALANCES_TEXT


async def test_press_recent_matches_typed_command_text() -> None:
    from app.bot.handlers.reads import NO_RECENT_EXPENSES_TEXT

    ledger, _ = await _bound_ledger()
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:recent", STARTER_ID)

    await menu_callback(update, context)

    assert bot.sent_messages[-1]["text"] == NO_RECENT_EXPENSES_TEXT


async def test_press_today_matches_typed_command_text() -> None:
    from app.bot.handlers.reads import NO_EXPENSES_TODAY_TEXT

    ledger, _ = await _bound_ledger()
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:today", STARTER_ID)

    await menu_callback(update, context)

    assert bot.sent_messages[-1]["text"] == NO_EXPENSES_TODAY_TEXT


async def test_press_summary_matches_typed_command_text() -> None:
    ledger, _ = await _bound_ledger()
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:summary", STARTER_ID)

    await menu_callback(update, context)

    assert bot.sent_messages[-1]["text"].startswith("0 expenses")


async def test_press_settle_matches_typed_command_text() -> None:
    from app.bot.handlers.balances import ALL_SQUARE_TEXT

    ledger, _ = await _bound_ledger()
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:settle", STARTER_ID)

    await menu_callback(update, context)

    assert bot.sent_messages[-1]["text"] == ALL_SQUARE_TEXT


async def test_press_mine_matches_typed_command_text() -> None:
    ledger, _ = await _bound_ledger()
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:mine", STARTER_ID)

    await menu_callback(update, context)

    assert bot.sent_messages[-1]["text"].startswith(f"<b>User {STARTER_ID}</b>")


async def test_press_undo_matches_typed_command_text() -> None:
    from app.bot.handlers.reads import NOTHING_TO_UNDO_TEXT

    ledger, _ = await _bound_ledger()
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:undo", STARTER_ID)

    await menu_callback(update, context)

    assert bot.sent_messages[-1]["text"] == NOTHING_TO_UNDO_TEXT


async def test_press_join_matches_typed_command_text() -> None:
    ledger, _ = await _bound_ledger()
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:join", OTHER_USER_ID)

    await menu_callback(update, context)

    assert bot.sent_messages[-1]["text"].startswith("Joined ✅")


async def test_press_settrip_starts_the_flow() -> None:
    ledger = InMemoryLedgerStore()
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:settrip", STARTER_ID)

    await menu_callback(update, context)

    assert bot.sent_messages[-1]["text"] == "Trip currency?"


async def test_press_howto_sends_the_pinned_examples() -> None:
    ledger, _ = await _bound_ledger()
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:howto", STARTER_ID)

    await menu_callback(update, context)

    assert bot.sent_messages[-1]["text"] == HOWTO_TEXT


async def test_press_export_sends_a_document() -> None:
    ledger, _ = await _bound_ledger()
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:export", STARTER_ID)

    await menu_callback(update, context)

    assert len(bot.sent_documents) == 1


# --- gates: non-starter deletetrip, closed trip, no trip -------------------


async def test_deletetrip_by_non_starter_is_refused() -> None:
    ledger, _ = await _bound_ledger()
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:deletetrip", OTHER_USER_ID)

    await menu_callback(update, context)

    assert bot.sent_messages[-1]["text"] == NOT_ALLOWED_TEXT


async def test_press_on_closed_trip_gets_the_closed_line() -> None:
    ledger, _ = await _bound_ledger(closed=True)
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:balances", STARTER_ID)

    await menu_callback(update, context)

    assert bot.sent_messages[-1]["text"] == CLOSED_TRIP_TEXT


async def test_press_on_no_trip_gets_the_unbound_line() -> None:
    ledger = InMemoryLedgerStore()
    bot = _Bot()
    context = _context(make_deps(ledger), bot)
    update = _callback_update("menu:balances", STARTER_ID)

    await menu_callback(update, context)

    assert bot.sent_messages[-1]["text"] == UNBOUND_TEXT


# --- bot_added_to_group ------------------------------------------------------


def _new_members_update(members: list[SimpleNamespace], chat_id: int = CHAT_ID) -> MagicMock:
    update = MagicMock()
    update.effective_chat = _chat(chat_id)
    update.effective_message.new_chat_members = members
    return update


async def test_added_to_group_with_bot_in_members_posts_welcome() -> None:
    ledger = InMemoryLedgerStore()
    bot = _Bot(bot_id=999)
    context = _context(make_deps(ledger), bot)
    update = _new_members_update([SimpleNamespace(id=999)])

    await bot_added_to_group(update, context)

    assert len(bot.sent_messages) == 1
    assert bot.sent_messages[0]["text"].endswith("No trip yet.")


async def test_added_to_group_with_another_user_does_nothing() -> None:
    ledger = InMemoryLedgerStore()
    bot = _Bot(bot_id=999)
    context = _context(make_deps(ledger), bot)
    update = _new_members_update([SimpleNamespace(id=1234)])

    await bot_added_to_group(update, context)

    assert not bot.sent_messages


# --- pinning ----------------------------------------------------------------


async def test_post_welcome_pin_success_appends_nothing() -> None:
    ledger = InMemoryLedgerStore()
    bot = _Bot()
    deps = make_deps(ledger)

    await post_welcome(deps, bot, _chat())

    assert bot.pinned == [(CHAT_ID, 1)]
    assert not bot.edited


async def test_post_welcome_pin_failure_appends_the_pin_line() -> None:
    ledger = InMemoryLedgerStore()
    bot = _Bot(pin_error=BadRequest("not enough rights"))
    deps = make_deps(ledger)

    await post_welcome(deps, bot, _chat())

    assert not bot.pinned
    assert len(bot.edited) == 1
    assert bot.edited[0]["text"].endswith(PIN_HINT)


async def test_post_welcome_pin_forbidden_also_appends_the_pin_line() -> None:
    ledger = InMemoryLedgerStore()
    bot = _Bot(pin_error=Forbidden("forbidden"))
    deps = make_deps(ledger)

    await post_welcome(deps, bot, _chat())

    assert len(bot.edited) == 1
    assert bot.edited[0]["text"].endswith(PIN_HINT)


# --- private_start_keyboard ---------------------------------------------


def test_private_start_keyboard_builds_startgroup_url() -> None:
    markup = private_start_keyboard("whopaidbot")
    button = markup.inline_keyboard[0][0]
    assert button.text == "Add me to a group"
    assert button.url == "https://t.me/whopaidbot?startgroup=true"
