"""Tests for /join, the group /start alias, and require_trip."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.deps import Deps
from app.bot.handlers.basic import start_command
from app.bot.handlers.join import UNAVAILABLE_TEXT, UNBOUND_TEXT, join_command
from app.models.trip import Trip
from app.models.user import TripUser
from app.services.ledger_store import InMemoryLedgerStore, LedgerUnavailable
from tests.helpers import make_deps


def _deps(ledger: InMemoryLedgerStore) -> Deps:
    return make_deps(ledger)


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


async def test_join_button_joins_presser_not_initiator() -> None:
    from app.bot.handlers.callbacks import join_button_callback

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

    update = MagicMock()
    update.callback_query.data = "join:-100"
    update.callback_query.from_user = SimpleNamespace(
        id=2, first_name="Bob", last_name=None, username="bob"
    )
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message = SimpleNamespace(
        text="Connected ✅ Trip · JPY → SGD · Asia/Tokyo",
        text_html="Connected ✅ Trip · JPY → SGD · Asia/Tokyo",
        reply_markup=None,
    )

    await join_button_callback(update, context)

    users = await ledger.list_users(trip, joined_only=True)
    assert len(users) == 1
    assert users[0].user_id == 2
    update.callback_query.answer.assert_awaited_once_with("Joined ✅ Bob")


async def test_join_reply_to_message_joins_the_replied_to_user() -> None:
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

    update = _update(user_id=1)
    update.effective_message.reply_to_message = SimpleNamespace(
        from_user=SimpleNamespace(
            id=99, first_name="Carol", last_name=None, username="carol", is_bot=False
        )
    )

    await join_command(update, context)

    users = await ledger.list_users(trip, joined_only=True)
    assert len(users) == 1
    assert users[0].user_id == 99
    update.effective_message.reply_text.assert_awaited_once()
    (text,), kwargs = update.effective_message.reply_text.call_args
    assert text == "Joined ✅ Carol"
    assert kwargs["reply_markup"] is not None


def test_add_joined_so_far_grows_without_duplicates_and_returns_count() -> None:
    from app.bot.handlers.callbacks import add_joined_so_far

    text = "Connected ✅ Trip · JPY → SGD · Asia/Tokyo"
    text, count = add_joined_so_far(text, "Ada")
    assert text.endswith("Joined so far: Ada")
    assert count == 1
    text, count = add_joined_so_far(text, "Bob")
    assert text.endswith("Joined so far: Ada, Bob")
    assert count == 2
    text, count = add_joined_so_far(text, "Ada")  # no duplicate
    assert text.endswith("Joined so far: Ada, Bob")
    assert count == 2


def test_join_reply_counts_the_person_who_sent_join() -> None:
    from app.bot.handlers.callbacks import add_joined_so_far

    text, count = add_joined_so_far("Joined ✅ Sufyan", "Amir")
    assert text == "Joined ✅ Sufyan\nJoined so far: Sufyan, Amir"
    assert count == 2


async def test_join_button_tells_someone_already_named_they_are_in() -> None:
    from app.bot.handlers.callbacks import ALREADY_IN_TEXT, join_button_callback

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
    context = MagicMock()
    context.application.bot_data = {"deps": make_deps(ledger)}
    update = MagicMock()
    update.callback_query.data = "join:-100:tap"
    update.callback_query.from_user = SimpleNamespace(
        id=1, first_name="Sufyan", last_name=None, username="s"
    )
    update.callback_query.message.text_html = "Joined ✅ Sufyan"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()

    await join_button_callback(update, context)

    update.callback_query.answer.assert_awaited_once_with(ALREADY_IN_TEXT)
    update.callback_query.edit_message_text.assert_not_awaited()


async def test_join_button_uses_text_html_and_escapes_ampersand_in_new_name() -> None:
    """message.text_html (not .text) is edited, so existing HTML markup survives;
    a joiner's own name is escaped going in, even when it contains an HTML-special
    character like "&".
    """
    from app.bot.handlers.callbacks import join_button_callback

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

    update = MagicMock()
    update.callback_query.data = "join:-100"
    update.callback_query.from_user = SimpleNamespace(
        id=3, first_name="Bed & Breakfast Bob", last_name=None, username="bnb_bob"
    )
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message = SimpleNamespace(
        text="Connected ✅ Trip · JPY → SGD · Asia/Tokyo",
        text_html="Connected ✅ <b>Trip</b> · JPY → SGD · Asia/Tokyo",
        reply_markup=None,
    )

    await join_button_callback(update, context)

    update.callback_query.edit_message_text.assert_awaited_once()
    (new_text,), kwargs = update.callback_query.edit_message_text.call_args
    assert "<b>Trip</b>" in new_text  # existing HTML markup preserved
    assert "Bed &amp; Breakfast Bob" in new_text  # the new name is HTML-escaped
    assert "Bed & Breakfast Bob" not in new_text  # never unescaped


async def test_join_replies_lost_access_message_when_trip_sheet_inaccessible() -> None:
    from app.bot.handlers.join import lost_access_text

    ledger = InMemoryLedgerStore(inaccessible_sheet_ids=frozenset({"sheet-id-1234567890"}))
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
    deps = _deps(ledger)
    context = _context(deps)
    update = _update()

    await join_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        lost_access_text(deps.service_account_email), parse_mode="HTML"
    )


def _joined_row_update(data: str, *, user_id: int, first_name: str, message_text: str) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user = SimpleNamespace(
        id=user_id, first_name=first_name, last_name=None, username=first_name.lower()
    )
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message = SimpleNamespace(
        text=message_text, text_html=message_text, reply_markup="unused"
    )
    return update


async def _bound_trip(ledger: InMemoryLedgerStore) -> Trip:
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
    return trip


async def test_join_button_label_shows_count_after_one_and_two_joins() -> None:
    from app.bot.handlers.callbacks import join_button_callback

    ledger = InMemoryLedgerStore()
    await _bound_trip(ledger)
    deps = _deps(ledger)
    context = _context(deps)

    first = _joined_row_update(
        "join:-100:tap", user_id=1, first_name="Ada", message_text="Connected ✅ Trip"
    )
    await join_button_callback(first, context)
    (text_after_one,), kwargs_one = first.callback_query.edit_message_text.call_args
    markup_one = kwargs_one["reply_markup"]
    assert markup_one.inline_keyboard[0][0].text == "I'm in too (1 joined)"
    assert "Joined so far: Ada" in text_after_one

    second = _joined_row_update(
        "join:-100:tap", user_id=2, first_name="Bob", message_text=text_after_one
    )
    await join_button_callback(second, context)
    (text_after_two,), kwargs_two = second.callback_query.edit_message_text.call_args
    markup_two = kwargs_two["reply_markup"]
    assert markup_two.inline_keyboard[0][0].text == "I'm in too (2 joined)"
    assert "Joined so far: Ada, Bob" in text_after_two

    # The counter and the "Joined so far" line always agree.
    joined_names = text_after_two.split("Joined so far: ")[1].split("\n")[0].split(", ")
    assert len(joined_names) == 2


async def test_everyones_in_removes_keyboard_and_appends_closing_line() -> None:
    from app.bot.handlers.callbacks import join_button_callback

    ledger = InMemoryLedgerStore()
    trip = await _bound_trip(ledger)
    await ledger.upsert_user(
        trip,
        TripUser(
            user_id=1,
            username="ada",
            display_name="Ada",
            first_seen=datetime.now(UTC),
            last_seen=datetime.now(UTC),
            joined=True,
        ),
        joined=True,
    )
    deps = _deps(ledger)
    context = _context(deps)

    update = _joined_row_update(
        "join:-100:done",
        user_id=1,
        first_name="Ada",
        message_text="Connected ✅ Trip\nJoined so far: Ada",
    )

    await join_button_callback(update, context)

    update.callback_query.answer.assert_awaited_once_with()
    update.callback_query.edit_message_text.assert_awaited_once()
    (text,), kwargs = update.callback_query.edit_message_text.call_args
    assert text.endswith("Everyone's in. Latecomers can type /join.")
    assert kwargs["reply_markup"] is None


async def test_everyones_in_rejects_non_joined_presser() -> None:
    from app.bot.handlers.callbacks import _NOT_JOINED_TEXT, join_button_callback

    ledger = InMemoryLedgerStore()
    await _bound_trip(ledger)  # nobody joined yet
    deps = _deps(ledger)
    context = _context(deps)

    update = _joined_row_update(
        "join:-100:done",
        user_id=999,
        first_name="Stranger",
        message_text="Connected ✅ Trip\nJoined so far: Ada",
    )

    await join_button_callback(update, context)

    update.callback_query.answer.assert_awaited_once_with(_NOT_JOINED_TEXT)
    update.callback_query.edit_message_text.assert_not_awaited()


async def test_tap_after_closed_answers_closed_toast_and_does_not_join() -> None:
    from app.bot.handlers.callbacks import _CLOSED_TEXT, join_button_callback

    ledger = InMemoryLedgerStore()
    trip = await _bound_trip(ledger)
    deps = _deps(ledger)
    context = _context(deps)

    closed_text = "Connected ✅ Trip\nJoined so far: Ada\nEveryone's in. Latecomers can type /join."
    update = _joined_row_update(
        "join:-100:tap", user_id=42, first_name="Late", message_text=closed_text
    )

    await join_button_callback(update, context)

    update.callback_query.answer.assert_awaited_once_with(_CLOSED_TEXT)
    update.callback_query.edit_message_text.assert_not_awaited()
    users = await ledger.list_users(trip, joined_only=True)
    assert len(users) == 0
