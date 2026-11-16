"""Tests for the /settrip conversation.

Sprint 9 (spec §18): no Sheet link, no access check. `/settrip` in a group with no
open trip goes straight to the currency buttons. A group with an open trip is asked
"Close the current trip and start a new one?" first (sprint 9 item 1); `Start new
trip` moves on to the currency buttons and `save_binding` closes the old trip,
`Keep current` ends the conversation with one line.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.conversations import settrip
from app.bot.deps import Deps
from app.services.currencies import StaticCurrencyCatalog
from app.services.ledger_store import InMemoryLedgerStore, LedgerUnavailable
from tests.helpers import make_deps


def _yielding() -> AsyncMock:
    """An async double that yields to the event loop, as a real Telegram call does."""

    async def _yield(*args: object, **kwargs: object) -> SimpleNamespace:
        await asyncio.sleep(0)
        return SimpleNamespace(message_id=42)

    return AsyncMock(side_effect=_yield)


def _deps(ledger: InMemoryLedgerStore | None = None) -> Deps:
    return make_deps(
        ledger or InMemoryLedgerStore(),
        currencies=StaticCurrencyCatalog(frozenset({"JPY", "SGD", "THB", "USD", "CAD", "IDR"})),
    )


def _context(deps: Deps) -> MagicMock:
    context = MagicMock()
    context.application.bot_data = {"deps": deps}
    context.bot.edit_message_text = _yielding()
    return context


def _command_update(chat_id: int = -100, title: str = "Japan Trip") -> MagicMock:
    update = MagicMock()
    update.effective_message.reply_text = _yielding()
    update.effective_chat = SimpleNamespace(id=chat_id, title=title, type="supergroup")
    update.effective_user = SimpleNamespace(id=1, first_name="Ada", last_name=None, username="ada")
    return update


def _only_session_id(deps: Deps) -> str:
    return next(iter(deps.settrip_sessions._sessions))


def _callback_update(session_id: str, step: str, value: str, user_id: int = 1) -> MagicMock:
    cb_update = MagicMock()
    cb_update.callback_query.data = f"st:{session_id}:{step}:{value}"
    cb_update.callback_query.from_user = SimpleNamespace(id=user_id)
    cb_update.callback_query.answer = _yielding()
    cb_update.callback_query.edit_message_text = _yielding()
    return cb_update


async def test_settrip_goes_straight_to_trip_currency_buttons() -> None:
    deps = _deps()
    context = _context(deps)
    update = _command_update()

    await settrip.settrip_command(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    (text,), kwargs = update.effective_message.reply_text.call_args
    assert text == settrip.TRIP_CCY_TEXT
    buttons = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    # Spec §18 pin, amended 2026-11-01: THB in SGD's place for the trip currency.
    assert buttons == ["THB", "MYR", "JPY", "CNY", "Other…"]
    assert len(deps.settrip_sessions._sessions) == 1


async def test_settrip_private_chat_replies_and_creates_no_session() -> None:
    deps = _deps()
    context = _context(deps)
    update = _command_update(chat_id=555)
    update.effective_chat = SimpleNamespace(id=555, title=None, type="private")

    await settrip.settrip_private_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        settrip.PRIVATE_TEXT, parse_mode="HTML"
    )
    assert len(deps.settrip_sessions._sessions) == 0


async def test_settrip_happy_path_creates_trip() -> None:
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    context = _context(deps)
    update = _command_update()

    await settrip.settrip_command(update, context)
    session_id = _only_session_id(deps)

    await settrip.settrip_callback(_callback_update(session_id, "trip_ccy", "JPY"), context)
    await settrip.settrip_callback(_callback_update(session_id, "home_ccy", "SGD"), context)

    confirm_update = _callback_update(session_id, "confirm", "yes")
    confirm_update.effective_chat = SimpleNamespace(id=-100, title="Japan Trip", type="supergroup")
    context.bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=7))
    context.bot.pin_chat_message = AsyncMock()
    await settrip.settrip_callback(confirm_update, context)

    confirm_update.callback_query.edit_message_text.assert_awaited_once()
    (text,), kwargs = confirm_update.callback_query.edit_message_text.call_args
    assert text.startswith("Connected ✅ Japan Trip")
    # Sprint 10 amendment: the menu follows `Everyone's in`, not Connected.
    context.bot.send_message.assert_not_awaited()
    assert "JPY → SGD" in text
    assert "Asia/Tokyo" in text
    assert kwargs["reply_markup"] is not None

    trip = await ledger.get_binding(-100)
    assert trip is not None
    assert trip.id is not None
    assert trip.trip_currency == "JPY"
    assert trip.home_currency == "SGD"
    assert trip.created_by == 1
    assert trip.closed_at is None


async def _complete_settrip(deps: Deps, context: MagicMock, *, skip_rebind: bool = False) -> None:
    update = _command_update()
    await settrip.settrip_command(update, context)
    session_id = _only_session_id(deps)
    if not skip_rebind:
        await settrip.settrip_callback(_callback_update(session_id, "rebind", "yes"), context)
    await settrip.settrip_callback(_callback_update(session_id, "trip_ccy", "JPY"), context)
    await settrip.settrip_callback(_callback_update(session_id, "home_ccy", "SGD"), context)
    await settrip.settrip_callback(_callback_update(session_id, "confirm", "yes"), context)


async def test_settrip_again_asks_to_close_the_current_trip_first() -> None:
    """A group with an open trip that runs /settrip again is asked to confirm
    before anything happens to the trip (sprint 9 item 1)."""
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    context = _context(deps)

    await _complete_settrip(deps, context, skip_rebind=True)
    first_trip = await ledger.get_binding(-100)
    assert first_trip is not None

    update = _command_update()
    await settrip.settrip_command(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    (text,), kwargs = update.effective_message.reply_text.call_args
    assert text == "Close the current trip and start a new one?"
    buttons = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert buttons == ["Start new trip", "Keep current"]

    # The old trip is untouched until a choice is made.
    still_open = await ledger.get_binding(-100)
    assert still_open is not None and still_open.id == first_trip.id


async def test_settrip_keep_current_ends_without_closing_the_trip() -> None:
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    context = _context(deps)
    await _complete_settrip(deps, context, skip_rebind=True)
    first_trip = await ledger.get_binding(-100)
    assert first_trip is not None

    update = _command_update()
    await settrip.settrip_command(update, context)
    session_id = _only_session_id(deps)
    keep_update = _callback_update(session_id, "rebind", "no")

    await settrip.settrip_callback(keep_update, context)

    keep_update.callback_query.edit_message_text.assert_awaited_once_with(
        "Keeping the current trip.", parse_mode="HTML"
    )
    still_open = await ledger.get_binding(-100)
    assert still_open is not None and still_open.id == first_trip.id
    assert deps.settrip_sessions.get(session_id) is None


async def test_settrip_start_new_trip_moves_on_to_currency_buttons() -> None:
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    context = _context(deps)
    await _complete_settrip(deps, context, skip_rebind=True)

    update = _command_update()
    await settrip.settrip_command(update, context)
    session_id = _only_session_id(deps)
    start_new_update = _callback_update(session_id, "rebind", "yes")

    await settrip.settrip_callback(start_new_update, context)

    start_new_update.callback_query.edit_message_text.assert_awaited_once()
    (text,), kwargs = start_new_update.callback_query.edit_message_text.call_args
    assert text == settrip.TRIP_CCY_TEXT
    buttons = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert buttons == ["THB", "MYR", "JPY", "CNY", "Other…"]


def test_home_currency_buttons_keep_sgd() -> None:
    from app.bot.keyboards.settrip import home_currency_keyboard

    markup = home_currency_keyboard("01SESSION0000000000000000")
    assert [b.text for row in markup.inline_keyboard for b in row] == [
        "SGD",
        "MYR",
        "JPY",
        "CNY",
        "Other…",
    ]


async def test_settrip_again_closes_the_old_open_trip() -> None:
    """Confirming `Start new trip` starts a new trip; the old one is closed, not
    deleted (spec §18)."""
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    context = _context(deps)

    await _complete_settrip(deps, context, skip_rebind=True)
    first_trip = await ledger.get_binding(-100)
    assert first_trip is not None

    await _complete_settrip(deps, context)
    second_trip = await ledger.get_binding(-100)
    assert second_trip is not None
    assert second_trip.id != first_trip.id

    trips = await ledger.list_trips(-100)
    by_id = {t.id: t for t in trips}
    assert by_id[first_trip.id].closed_at is not None
    assert by_id[second_trip.id].closed_at is None


async def test_settrip_confirm_ledger_unavailable_shows_retry_button() -> None:
    ledger = InMemoryLedgerStore()
    ledger.save_binding = AsyncMock(side_effect=LedgerUnavailable("boom"))  # type: ignore[method-assign]
    deps = _deps(ledger)
    context = _context(deps)
    update = _command_update()
    await settrip.settrip_command(update, context)
    session_id = _only_session_id(deps)

    await settrip.settrip_callback(_callback_update(session_id, "trip_ccy", "JPY"), context)
    await settrip.settrip_callback(_callback_update(session_id, "home_ccy", "SGD"), context)
    confirm_update = _callback_update(session_id, "confirm", "yes")

    await settrip.settrip_callback(confirm_update, context)

    confirm_update.callback_query.edit_message_text.assert_awaited_once()
    (text,), kwargs = confirm_update.callback_query.edit_message_text.call_args
    assert text == settrip.UNAVAILABLE_TEXT
    assert kwargs["reply_markup"] is not None
    # the session survives so Retry (same callback data pattern) can be pressed again
    assert deps.settrip_sessions.get(session_id) is not None


async def test_settrip_only_initiator_can_press_buttons() -> None:
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    context = _context(deps)
    update = _command_update()
    await settrip.settrip_command(update, context)
    session_id = _only_session_id(deps)

    cb_update = _callback_update(session_id, "trip_ccy", "JPY", user_id=999)  # not the initiator

    await settrip.settrip_callback(cb_update, context)

    cb_update.callback_query.answer.assert_awaited_once()
    (message,) = cb_update.callback_query.answer.call_args.args
    assert "Only" in message and "can do this" in message


async def test_settrip_unknown_session_reports_expired() -> None:
    deps = _deps()
    context = _context(deps)
    cb_update = MagicMock()
    cb_update.callback_query.data = "st:01UNKNOWNSESSIONID000000:trip_ccy:JPY"
    cb_update.callback_query.answer = AsyncMock()
    cb_update.callback_query.edit_message_text = AsyncMock()

    await settrip.settrip_callback(cb_update, context)

    cb_update.callback_query.edit_message_text.assert_awaited_once_with(
        "This setup expired, run /settrip again"
    )


def test_awaiting_settrip_text_filter_true_only_when_session_awaits_text() -> None:
    from app.bot.session_state import SettripSessionStore

    store = SettripSessionStore()
    session = store.create(
        chat_id=-100,
        initiator_id=1,
        initiator_name="Ada",
        trip_name="Trip",
        step="trip_ccy",
    )
    filt = settrip.AwaitingSettripText(store)

    message = SimpleNamespace(chat=SimpleNamespace(id=-100), from_user=SimpleNamespace(id=1))
    assert filt.filter(message) is False  # not awaiting text yet

    store.update(session.session_id, awaiting_text_for="tz")
    assert filt.filter(message) is True

    other_user_message = SimpleNamespace(
        chat=SimpleNamespace(id=-100), from_user=SimpleNamespace(id=2)
    )
    assert filt.filter(other_user_message) is False


async def test_run_settrip_sends_trip_currency_buttons_with_a_fake_send() -> None:
    deps = _deps()
    chat = SimpleNamespace(id=-100, title="Japan Trip")
    user = SimpleNamespace(id=1, first_name="Ada", last_name=None, username="ada")
    sent: list[tuple[str, dict]] = []

    async def fake_send(text: str, *, reply_markup=None):
        sent.append((text, {"reply_markup": reply_markup}))
        return SimpleNamespace(message_id=42)

    await settrip.run_settrip(deps, chat, user, fake_send)

    assert sent[0][0] == settrip.TRIP_CCY_TEXT
    assert len(deps.settrip_sessions._sessions) == 1


# -- Stale setups (fixed 2026-11-01): a leftover setup waiting on typed text swallowed
# the group's messages, a receipt photo included, for the rest of its 30 minutes.


async def _to_summary(deps: Deps, context: MagicMock, session_id: str) -> None:
    await settrip.settrip_callback(_callback_update(session_id, "trip_ccy", "JPY"), context)
    await settrip.settrip_callback(_callback_update(session_id, "home_ccy", "SGD"), context)


def _awaiting_text(deps: Deps) -> list[str]:
    return [s.session_id for s in deps.settrip_sessions._sessions.values() if s.awaiting_text_for]


async def test_a_second_settrip_replaces_the_first_left_waiting_on_text() -> None:
    deps = _deps()
    context = _context(deps)
    await settrip.settrip_command(_command_update(), context)
    first = _only_session_id(deps)
    await _to_summary(deps, context, first)
    await settrip.settrip_callback(_callback_update(first, "chg", "tz"), context)
    await settrip.settrip_callback(_callback_update(first, "tz", "type"), context)
    assert _awaiting_text(deps) == [first]

    await settrip.settrip_command(_command_update(), context)
    second = _only_session_id(deps)
    assert second != first
    assert len(deps.settrip_sessions._sessions) == 1
    assert deps.settrip_sessions.find_awaiting_text(-100, 1) is None


async def test_creating_the_trip_drops_every_other_setup_in_the_chat() -> None:
    deps = _deps()
    context = _context(deps)
    # Bo started a setup, pressed Other… and walked away.
    bo = deps.settrip_sessions.create(
        chat_id=-100, initiator_id=2, initiator_name="Bo", trip_name="Trip", step="trip_ccy"
    )
    deps.settrip_sessions.update(bo.session_id, awaiting_text_for="tz")
    # A setup in another chat is left alone.
    elsewhere = deps.settrip_sessions.create(
        chat_id=-200, initiator_id=2, initiator_name="Bo", trip_name="Trip", step="trip_ccy"
    )

    await settrip.settrip_command(_command_update(), context)
    ada = next(s for s in deps.settrip_sessions._sessions.values() if s.initiator_id == 1)
    for step, value in (("trip_ccy", "JPY"), ("home_ccy", "SGD")):
        await settrip.settrip_callback(_callback_update(ada.session_id, step, value), context)
    await settrip.settrip_callback(_callback_update(ada.session_id, "confirm", "yes"), context)

    assert deps.settrip_sessions.find_awaiting_text(-100, 2) is None
    assert list(deps.settrip_sessions._sessions) == [elsewhere.session_id]


def test_a_photo_is_never_taken_by_a_setup_waiting_on_text() -> None:
    from datetime import UTC, datetime

    from telegram import Chat, Message, PhotoSize, Update, User
    from telegram.ext import MessageHandler

    from app.bot.app import build_application

    deps = _deps()
    session = deps.settrip_sessions.create(
        chat_id=-100, initiator_id=1, initiator_name="Ada", trip_name="Trip", step="trip_ccy"
    )
    deps.settrip_sessions.update(session.session_id, awaiting_text_for="tz")
    application = build_application("123456:fake-token", deps)
    (handler,) = [
        h
        for group in application.handlers.values()
        for h in group
        if isinstance(h, MessageHandler) and h.callback is settrip.settrip_text_handler
    ]

    chat = Chat(id=-100, type="supergroup", title="Trip")
    user = User(id=1, first_name="Ada", is_bot=False)
    now = datetime.now(UTC)
    photo = Message(
        message_id=1,
        date=now,
        chat=chat,
        from_user=user,
        photo=(PhotoSize("f", "u", 100, 100),),
    )
    text = Message(message_id=2, date=now, chat=chat, from_user=user, text="thb")
    assert not handler.check_update(Update(update_id=1, message=photo))
    assert handler.check_update(Update(update_id=2, message=text))


# -- Sprint 19: easier setup (spend / pay back wording, Other… page, summary card).


def _buttons(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


def _last_edit(context: MagicMock) -> tuple[str, list[list[str]]]:
    kwargs = context.bot.edit_message_text.call_args.kwargs
    return kwargs["text"], _buttons(kwargs["reply_markup"])


async def _press(context: MagicMock, session_id: str, step: str, value: str, user_id: int = 1):
    update = _callback_update(session_id, step, value, user_id=user_id)
    await settrip.settrip_callback(update, context)
    return update


async def _started(ledger: InMemoryLedgerStore | None = None) -> tuple[Deps, MagicMock, str]:
    deps = _deps(ledger)
    context = _context(deps)
    await settrip.settrip_command(_command_update(), context)
    return deps, context, _only_session_id(deps)


async def _summary(
    trip: str = "JPY", home: str = "SGD"
) -> tuple[Deps, MagicMock, str, InMemoryLedgerStore]:
    ledger = InMemoryLedgerStore()
    deps, context, sid = await _started(ledger)
    await _press(context, sid, "trip_ccy", trip)
    await _press(context, sid, "home_ccy", home)
    return deps, context, sid, ledger


async def test_step_texts_are_pinned_and_flow_goes_straight_to_the_summary_card() -> None:
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    context = _context(deps)
    update = _command_update()
    await settrip.settrip_command(update, context)
    assert update.effective_message.reply_text.call_args.args[0] == (
        "Which currency will you spend on this trip?"
    )
    sid = _only_session_id(deps)

    await _press(context, sid, "trip_ccy", "JPY")
    text, buttons = _last_edit(context)
    assert text == "Which currency do you want to use to pay each other back?"
    assert buttons == [["SGD", "MYR", "JPY"], ["CNY"], ["Other…"]]

    await _press(context, sid, "home_ccy", "SGD")
    text, buttons = _last_edit(context)
    assert text == "<b>Japan Trip</b>\nSpend in: JPY\nPay back in: SGD\nTimezone: Asia/Tokyo"
    assert buttons == [
        ["Change spend currency"],
        ["Change pay-back currency"],
        ["Change timezone"],
        ["Start trip", "Cancel"],
    ]

    confirm = await _press(context, sid, "confirm", "yes")
    assert confirm.callback_query.edit_message_text.call_args.args[0].startswith("Connected ✅")
    trip = await ledger.get_binding(-100)
    assert trip is not None
    assert (trip.trip_currency, trip.home_currency, trip.timezone) == ("JPY", "SGD", "Asia/Tokyo")


async def test_summary_card_escapes_the_trip_name() -> None:
    deps = _deps()
    context = _context(deps)
    await settrip.settrip_command(_command_update(title="A <b> & Co"), context)
    sid = _only_session_id(deps)
    await _press(context, sid, "trip_ccy", "JPY")
    await _press(context, sid, "home_ccy", "SGD")
    assert _last_edit(context)[0].startswith("<b>A &lt;b&gt; &amp; Co</b>")


async def test_other_page_lists_every_supported_code_then_back_restores_short_keyboard() -> None:
    deps, context, sid = await _started()
    page = await _press(context, sid, "trip_ccy", "other")
    page.callback_query.edit_message_text.assert_awaited_once()
    (text,), kwargs = page.callback_query.edit_message_text.call_args
    assert text == settrip.TRIP_CCY_TEXT
    assert _buttons(kwargs["reply_markup"]) == [
        ["CAD", "IDR", "JPY", "SGD"],
        ["THB", "USD"],
        ["◂ Back"],
    ]

    back = await _press(context, sid, "trip_ccy", "back")
    assert back.callback_query.edit_message_text.await_count == 0
    text, buttons = _last_edit(context)
    assert text == settrip.TRIP_CCY_TEXT
    assert buttons == [["THB", "MYR", "JPY"], ["CNY"], ["Other…"]]


async def test_a_code_from_the_other_page_is_saved() -> None:
    deps, context, sid = await _started()
    await _press(context, sid, "trip_ccy", "other")
    await _press(context, sid, "trip_ccy", "USD")
    assert _last_edit(context)[0] == settrip.HOME_CCY_TEXT
    await _press(context, sid, "home_ccy", "other")
    await _press(context, sid, "home_ccy", "IDR")
    text, _ = _last_edit(context)
    assert "Spend in: USD\nPay back in: IDR\nTimezone: America/New_York" in text


async def test_a_typed_currency_is_not_read_during_setup() -> None:
    deps, context, sid = await _started()
    await _press(context, sid, "trip_ccy", "other")
    assert deps.settrip_sessions.find_awaiting_text(-100, 1) is None
    edits_before = context.bot.edit_message_text.await_count

    text_update = MagicMock()
    text_update.effective_message.text = "thb"
    text_update.effective_message.reply_text = _yielding()
    text_update.effective_chat = SimpleNamespace(id=-100)
    text_update.effective_user = SimpleNamespace(id=1)
    await settrip.settrip_text_handler(text_update, context)

    text_update.effective_message.reply_text.assert_not_awaited()
    assert context.bot.edit_message_text.await_count == edits_before


async def test_changing_spend_currency_re_picks_timezone_unless_user_chose_one() -> None:
    deps, context, sid, _ = await _summary()
    await _press(context, sid, "chg", "trip")
    assert _last_edit(context)[0] == settrip.TRIP_CCY_TEXT
    await _press(context, sid, "trip_ccy", "USD")
    text, _ = _last_edit(context)
    assert "Spend in: USD" in text and "Timezone: America/New_York" in text

    await _press(context, sid, "chg", "tz")
    await _press(context, sid, "tzi", "1")
    assert "Timezone: America/Chicago" in _last_edit(context)[0]
    await _press(context, sid, "chg", "trip")
    await _press(context, sid, "trip_ccy", "other")
    await _press(context, sid, "trip_ccy", "CAD")
    text, _ = _last_edit(context)
    assert "Spend in: CAD" in text and "Timezone: America/Chicago" in text


async def test_changing_pay_back_currency_returns_to_the_summary_card() -> None:
    deps, context, sid, _ = await _summary()
    await _press(context, sid, "chg", "home")
    assert _last_edit(context)[0] == settrip.HOME_CCY_TEXT
    await _press(context, sid, "home_ccy", "MYR")
    text, buttons = _last_edit(context)
    assert "Pay back in: MYR" in text and "Spend in: JPY" in text
    assert buttons[0] == ["Change spend currency"]


async def test_change_timezone_on_idr_lists_three_and_saves_makassar() -> None:
    deps, context, sid, ledger = await _summary(trip="IDR")
    await _press(context, sid, "chg", "tz")
    text, buttons = _last_edit(context)
    assert text == "Which timezone? Expense dates use it."
    assert buttons == [
        ["Asia/Jakarta"],
        ["Asia/Makassar (Bali)"],
        ["Asia/Jayapura"],
        ["Type it"],
        ["◂ Back"],
    ]
    await _press(context, sid, "tzi", "1")
    assert "Timezone: Asia/Makassar" in _last_edit(context)[0]
    await _press(context, sid, "confirm", "yes")
    trip = await ledger.get_binding(-100)
    assert trip is not None and trip.timezone == "Asia/Makassar"


async def test_change_timezone_without_a_list_shows_the_default_type_it_and_back() -> None:
    deps, context, sid, _ = await _summary()
    await _press(context, sid, "chg", "tz")
    assert _last_edit(context)[1] == [["Asia/Tokyo"], ["Type it"], ["◂ Back"]]
    summary_before = deps.settrip_sessions.get(sid)
    await _press(context, sid, "tz", "back")
    text, buttons = _last_edit(context)
    assert text.startswith("<b>Japan Trip</b>\nSpend in: JPY")
    assert buttons[0] == ["Change spend currency"]
    assert deps.settrip_sessions.get(sid).timezone == summary_before.timezone


async def _typed(context: MagicMock, text: str) -> MagicMock:
    update = MagicMock()
    update.effective_message.text = text
    update.effective_message.reply_text = _yielding()
    update.effective_chat = SimpleNamespace(id=-100)
    update.effective_user = SimpleNamespace(id=1)
    await settrip.settrip_text_handler(update, context)
    return update


async def test_type_it_accepts_any_case_and_keeps_waiting_on_a_miss() -> None:
    deps, context, sid, _ = await _summary()
    await _press(context, sid, "chg", "tz")
    typing = await _press(context, sid, "tz", "type")
    typing.callback_query.edit_message_text.assert_awaited_once_with(
        "Type the timezone, like Asia/Bangkok.", parse_mode="HTML"
    )

    miss = await _typed(context, "nowhere")
    miss.effective_message.reply_text.assert_awaited_once_with(
        "Unknown timezone. Try again or /cancel.", parse_mode="HTML"
    )
    assert deps.settrip_sessions.find_awaiting_text(-100, 1) is not None

    await _typed(context, "asia/bangkok")
    assert "Timezone: Asia/Bangkok" in _last_edit(context)[0]
    assert deps.settrip_sessions.find_awaiting_text(-100, 1) is None
    assert deps.settrip_sessions.get(sid).timezone_chosen is True


async def test_a_non_starter_pressing_change_gets_the_toast_and_nothing_changes() -> None:
    deps, context, sid, _ = await _summary()
    session_before = deps.settrip_sessions.get(sid)
    edits = context.bot.edit_message_text.await_count
    for step, value in (("chg", "trip"), ("chg", "home"), ("chg", "tz"), ("tz", "back")):
        update = await _press(context, sid, step, value, user_id=999)
        update.callback_query.answer.assert_awaited_once_with("Only Ada can do this")
        update.callback_query.edit_message_text.assert_not_awaited()
    assert deps.settrip_sessions.get(sid) == session_before
    assert context.bot.edit_message_text.await_count == edits


@pytest.mark.parametrize("value", ["9", "-1", "x", ""])
async def test_an_out_of_range_timezone_index_changes_nothing(value: str) -> None:
    deps, context, sid, _ = await _summary()
    await _press(context, sid, "chg", "tz")
    session_before = deps.settrip_sessions.get(sid)
    edits = context.bot.edit_message_text.await_count
    update = await _press(context, sid, "tzi", value)
    update.callback_query.answer.assert_awaited()
    assert deps.settrip_sessions.get(sid) == session_before
    assert context.bot.edit_message_text.await_count == edits
    update.callback_query.edit_message_text.assert_not_awaited()


async def test_an_expired_session_on_a_change_press_gets_the_expired_text() -> None:
    deps, context, sid, _ = await _summary()
    deps.settrip_sessions.delete(sid)
    update = await _press(context, sid, "chg", "trip")
    update.callback_query.edit_message_text.assert_awaited_once_with(
        "This setup expired, run /settrip again"
    )


def test_every_setup_callback_fits_telegrams_64_byte_limit() -> None:
    from ulid import ULID

    from app.bot.keyboards import settrip as kb
    from app.services.currencies import ISO_CODES, TIMEZONE_CHOICES

    sid = str(ULID())
    markups = [
        kb.trip_currency_keyboard(sid),
        kb.home_currency_keyboard(sid),
        kb.summary_keyboard(sid),
        kb.rebind_keyboard(sid),
        kb.currency_page_keyboard(sid, "trip_ccy", sorted(ISO_CODES)),
        kb.currency_page_keyboard(sid, "home_ccy", sorted(ISO_CODES)),
    ]
    markups += [kb.timezone_list_keyboard(sid, names) for names in TIMEZONE_CHOICES.values()]
    longest = max((n for names in TIMEZONE_CHOICES.values() for n in names), key=len)
    markups.append(kb.timezone_list_keyboard(sid, [longest] * 12))
    for markup in markups:
        for row in markup.inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode()) <= 64, button.callback_data


def test_timezone_buttons_carry_the_list_index_and_label_bali() -> None:
    from app.bot.keyboards.settrip import timezone_list_keyboard

    markup = timezone_list_keyboard("S" * 26, ["Asia/Jakarta", "Asia/Makassar"])
    assert markup.inline_keyboard[1][0].text == "Asia/Makassar (Bali)"
    assert markup.inline_keyboard[1][0].callback_data == f"st:{'S' * 26}:tzi:1"
