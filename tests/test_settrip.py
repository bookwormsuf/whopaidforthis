"""Tests for the /settrip conversation.

Sprint 9 (spec §18): no Sheet link, no access check. `/settrip` in a group with no
open trip goes straight to the currency buttons. A group with an open trip is asked
"Close the current trip and start a new one?" first (sprint 9 item 1); `Start new
trip` moves on to the currency buttons and `save_binding` closes the old trip,
`Keep current` ends the conversation with one line.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.conversations import settrip
from app.bot.deps import Deps
from app.services.currencies import StaticCurrencyCatalog
from app.services.ledger_store import InMemoryLedgerStore, LedgerUnavailable
from tests.helpers import make_deps


def _deps(ledger: InMemoryLedgerStore | None = None) -> Deps:
    return make_deps(
        ledger or InMemoryLedgerStore(),
        currencies=StaticCurrencyCatalog(frozenset({"JPY", "SGD", "THB"})),
    )


def _context(deps: Deps) -> MagicMock:
    context = MagicMock()
    context.application.bot_data = {"deps": deps}
    context.bot.edit_message_text = AsyncMock()
    return context


def _command_update(chat_id: int = -100, title: str = "Japan Trip") -> MagicMock:
    update = MagicMock()
    update.effective_message.reply_text = AsyncMock(return_value=SimpleNamespace(message_id=42))
    update.effective_chat = SimpleNamespace(id=chat_id, title=title, type="supergroup")
    update.effective_user = SimpleNamespace(id=1, first_name="Ada", last_name=None, username="ada")
    return update


def _only_session_id(deps: Deps) -> str:
    return next(iter(deps.settrip_sessions._sessions))


def _callback_update(session_id: str, step: str, value: str, user_id: int = 1) -> MagicMock:
    cb_update = MagicMock()
    cb_update.callback_query.data = f"st:{session_id}:{step}:{value}"
    cb_update.callback_query.from_user = SimpleNamespace(id=user_id)
    cb_update.callback_query.answer = AsyncMock()
    cb_update.callback_query.edit_message_text = AsyncMock()
    return cb_update


async def test_settrip_goes_straight_to_trip_currency_buttons() -> None:
    deps = _deps()
    context = _context(deps)
    update = _command_update()

    await settrip.settrip_command(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    (text,), kwargs = update.effective_message.reply_text.call_args
    assert text == "Trip currency?"
    buttons = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    # Spec §18 pin: SGD, MYR, JPY, CNY shown first, then Other….
    assert buttons == ["SGD", "MYR", "JPY", "CNY", "Other…"]
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
    await settrip.settrip_callback(_callback_update(session_id, "tz", "Asia/Tokyo"), context)

    confirm_update = _callback_update(session_id, "confirm", "yes")
    await settrip.settrip_callback(confirm_update, context)

    confirm_update.callback_query.edit_message_text.assert_awaited_once()
    (text,), kwargs = confirm_update.callback_query.edit_message_text.call_args
    assert text.startswith("Connected ✅ Japan Trip")
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
    await settrip.settrip_callback(_callback_update(session_id, "tz", "Asia/Tokyo"), context)
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
    assert text == "Trip currency?"
    buttons = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert buttons == ["SGD", "MYR", "JPY", "CNY", "Other…"]


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
    await settrip.settrip_callback(_callback_update(session_id, "tz", "Asia/Tokyo"), context)
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


async def test_settrip_other_currency_waits_for_text_then_validates() -> None:
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    context = _context(deps)
    update = _command_update()
    await settrip.settrip_command(update, context)
    session_id = _only_session_id(deps)

    other_update = _callback_update(session_id, "trip_ccy", "other")
    await settrip.settrip_callback(other_update, context)

    text_update = MagicMock()
    text_update.effective_message.text = "unknown-code"
    text_update.effective_message.reply_text = AsyncMock()
    text_update.effective_chat = SimpleNamespace(id=-100)
    text_update.effective_user = SimpleNamespace(id=1)
    await settrip.settrip_text_handler(text_update, context)
    text_update.effective_message.reply_text.assert_awaited_once_with(
        "Unknown currency code. Try again or /cancel.", parse_mode="HTML"
    )

    text_update.effective_message.text = "thb"
    await settrip.settrip_text_handler(text_update, context)
    context.bot.edit_message_text.assert_awaited()


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

    store.update(session.session_id, awaiting_text_for="trip_ccy")
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

    assert sent[0][0] == "Trip currency?"
    assert len(deps.settrip_sessions._sessions) == 1
