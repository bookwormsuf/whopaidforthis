"""Tests for the /settrip conversation."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.conversations import settrip
from app.bot.deps import Deps
from app.models.trip import Trip
from app.services.currencies import StaticCurrencyCatalog
from app.services.ledger_store import InMemoryLedgerStore, LedgerUnavailable

SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0", SHEET_ID),
        (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/", SHEET_ID),
        (SHEET_ID, SHEET_ID),
    ],
)
def test_extract_sheet_id_accepts_three_shapes(raw: str, expected: str) -> None:
    assert settrip.extract_sheet_id(raw) == expected


def test_extract_sheet_id_rejects_garbage() -> None:
    assert settrip.extract_sheet_id("not a sheet id") is None
    assert settrip.extract_sheet_id("short") is None


def _deps(ledger: InMemoryLedgerStore | None = None) -> Deps:
    return Deps(
        ledger=ledger or InMemoryLedgerStore(),
        currencies=StaticCurrencyCatalog(frozenset({"JPY", "SGD", "THB"})),
        service_account_email="whopaid-bot@example.iam.gserviceaccount.com",
        bot_version="test",
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


async def test_settrip_no_arg_shows_usage() -> None:
    deps = _deps()
    context = _context(deps)
    context.args = []
    update = _command_update()

    await settrip.settrip_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        settrip.USAGE_TEXT, parse_mode="HTML"
    )


async def test_settrip_bad_arg_shows_usage() -> None:
    deps = _deps()
    context = _context(deps)
    context.args = ["not-a-sheet-id"]
    update = _command_update()

    await settrip.settrip_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        settrip.USAGE_TEXT, parse_mode="HTML"
    )


async def test_settrip_unshared_sheet_via_real_sheets_ledger_store_sends_exact_error() -> None:
    """Handler-level: a real SheetsLedgerStore whose client denies access (403)."""
    from app.services.google_sheets import SheetsLedgerStore

    store = SheetsLedgerStore(
        service_account_info={"client_email": "whopaid-bot@example.iam.gserviceaccount.com"},
        control_sheet_id="control-sheet-id",
        bot_version="test",
    )
    client = MagicMock()
    client.open_by_key = MagicMock(side_effect=PermissionError())
    store._get_client = AsyncMock(return_value=client)  # type: ignore[method-assign]

    deps = Deps(
        ledger=store,
        currencies=StaticCurrencyCatalog(frozenset({"JPY", "SGD"})),
        service_account_email="whopaid-bot@example.iam.gserviceaccount.com",
        bot_version="test",
    )
    context = _context(deps)
    context.args = [SHEET_ID]
    update = _command_update()

    await settrip.settrip_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        "I can't open that Sheet. Share it with "
        "<code>whopaid-bot@example.iam.gserviceaccount.com</code> as Editor, then try again.",
        parse_mode="HTML",
    )


async def test_settrip_unshared_sheet_sends_exact_error_with_email() -> None:
    ledger = InMemoryLedgerStore(unopenable_sheet_ids=frozenset({SHEET_ID}))
    deps = _deps(ledger)
    context = _context(deps)
    context.args = [SHEET_ID]
    update = _command_update()

    await settrip.settrip_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        f"I can't open that Sheet. Share it with <code>{deps.service_account_email}</code> "
        "as Editor, then try again.",
        parse_mode="HTML",
    )


async def test_settrip_access_check_runs_before_rebind_prompt() -> None:
    """A sheet that can't be opened is an error even for an already-bound chat."""
    ledger = InMemoryLedgerStore(unopenable_sheet_ids=frozenset({SHEET_ID}))
    old_trip = Trip(
        chat_id=-100,
        sheet_id="already-bound-sheet-id",
        name="Old Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )
    await ledger.save_binding(old_trip)
    deps = _deps(ledger)
    context = _context(deps)
    context.args = [SHEET_ID]
    update = _command_update()

    await settrip.settrip_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        f"I can't open that Sheet. Share it with <code>{deps.service_account_email}</code> "
        "as Editor, then try again.",
        parse_mode="HTML",
    )
    assert len(deps.settrip_sessions._sessions) == 0  # no session was started


async def test_settrip_private_chat_replies_and_creates_no_session() -> None:
    deps = _deps()
    context = _context(deps)
    context.args = [SHEET_ID]
    update = _command_update(chat_id=555)
    update.effective_chat = SimpleNamespace(id=555, title=None, type="private")

    await settrip.settrip_private_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        settrip.PRIVATE_TEXT, parse_mode="HTML"
    )
    assert len(deps.settrip_sessions._sessions) == 0


async def test_settrip_happy_path_binds_trip() -> None:
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    context = _context(deps)
    context.args = [SHEET_ID]
    update = _command_update()

    await settrip.settrip_command(update, context)
    session_id = _only_session_id(deps)

    def callback_update(step: str, value: str) -> MagicMock:
        cb_update = MagicMock()
        cb_update.callback_query.data = f"st:{session_id}:{step}:{value}"
        cb_update.callback_query.from_user = SimpleNamespace(id=1)
        cb_update.callback_query.answer = AsyncMock()
        cb_update.callback_query.edit_message_text = AsyncMock()
        return cb_update

    await settrip.settrip_callback(callback_update("trip_ccy", "JPY"), context)
    await settrip.settrip_callback(callback_update("home_ccy", "SGD"), context)
    await settrip.settrip_callback(callback_update("tz", "Asia/Tokyo"), context)

    confirm_update = callback_update("confirm", "yes")
    await settrip.settrip_callback(confirm_update, context)

    confirm_update.callback_query.edit_message_text.assert_awaited_once()
    (text,), kwargs = confirm_update.callback_query.edit_message_text.call_args
    assert text.startswith("Bound ✅ Japan Trip")
    assert "JPY → SGD" in text
    assert "Asia/Tokyo" in text

    trip = await ledger.get_binding(-100)
    assert trip is not None
    assert trip.sheet_id == SHEET_ID
    assert trip.trip_currency == "JPY"
    assert trip.home_currency == "SGD"
    assert SHEET_ID in ledger.initialised_sheet_ids


async def test_settrip_confirm_ledger_unavailable_shows_retry_button() -> None:
    ledger = InMemoryLedgerStore()
    ledger.initialise_trip_sheet = AsyncMock(side_effect=LedgerUnavailable("boom"))  # type: ignore[method-assign]
    deps = _deps(ledger)
    context = _context(deps)
    context.args = [SHEET_ID]
    update = _command_update()
    await settrip.settrip_command(update, context)
    session_id = _only_session_id(deps)

    def callback_update(step: str, value: str) -> MagicMock:
        cb_update = MagicMock()
        cb_update.callback_query.data = f"st:{session_id}:{step}:{value}"
        cb_update.callback_query.from_user = SimpleNamespace(id=1)
        cb_update.callback_query.answer = AsyncMock()
        cb_update.callback_query.edit_message_text = AsyncMock()
        return cb_update

    await settrip.settrip_callback(callback_update("trip_ccy", "JPY"), context)
    await settrip.settrip_callback(callback_update("home_ccy", "SGD"), context)
    await settrip.settrip_callback(callback_update("tz", "Asia/Tokyo"), context)
    confirm_update = callback_update("confirm", "yes")

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
    context.args = [SHEET_ID]
    update = _command_update()
    await settrip.settrip_command(update, context)
    session_id = _only_session_id(deps)

    cb_update = MagicMock()
    cb_update.callback_query.data = f"st:{session_id}:trip_ccy:JPY"
    cb_update.callback_query.from_user = SimpleNamespace(id=999)  # not the initiator
    cb_update.callback_query.answer = AsyncMock()

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


async def test_settrip_rebind_prompt_when_already_bound_and_accessible() -> None:
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    await ledger.save_binding(
        Trip(
            chat_id=-100,
            sheet_id="already-bound-sheet-id",
            name="Old Trip",
            trip_currency="JPY",
            home_currency="SGD",
            timezone="Asia/Tokyo",
            created_at=datetime.now(UTC),
        )
    )
    context = _context(deps)
    context.args = [SHEET_ID]
    update = _command_update()

    await settrip.settrip_command(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    (text,), kwargs = update.effective_message.reply_text.call_args
    assert text == "This group is already bound to a Sheet. Rebind?"


async def test_settrip_other_currency_waits_for_text_then_validates() -> None:
    ledger = InMemoryLedgerStore()
    deps = _deps(ledger)
    context = _context(deps)
    context.args = [SHEET_ID]
    update = _command_update()
    await settrip.settrip_command(update, context)
    session_id = _only_session_id(deps)

    other_update = MagicMock()
    other_update.callback_query.data = f"st:{session_id}:trip_ccy:other"
    other_update.callback_query.from_user = SimpleNamespace(id=1)
    other_update.callback_query.answer = AsyncMock()
    other_update.callback_query.edit_message_text = AsyncMock()
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
        sheet_id=SHEET_ID,
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
