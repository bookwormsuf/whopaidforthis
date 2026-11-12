"""Tests for /feedback, the wait, the owner message and the close-trip prompt
(spec §21, sprint 16)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, MessageEntity, PhotoSize, Update, User
from telegram.ext import MessageHandler

from app.bot.app import build_application
from app.bot.handlers.balances import close_trip_callback
from app.bot.handlers.basic import help_command
from app.bot.handlers.feedback import (
    FEEDBACK_ASK_TEXT,
    FEEDBACK_FAILED_TEXT,
    FEEDBACK_GROUP_HINT_TEXT,
    FEEDBACK_LIMIT_TEXT,
    FEEDBACK_OFF_TEXT,
    FEEDBACK_PROMPT_TEXT,
    FEEDBACK_SENT_TEXT,
    FEEDBACK_TEXT_ONLY_TEXT,
    FEEDBACK_TOO_LONG_TEXT,
    drop_feedback_wait,
    feedback_command,
    feedback_non_text_handler,
    feedback_text_handler,
)
from app.bot.handlers.text_expense import text_expense_handler
from app.bot.session_state import FeedbackWaitStore
from app.models.trip import Trip
from app.services.feedback import FeedbackLimiter, format_owner_message
from app.services.ledger_store import InMemoryLedgerStore
from tests.helpers import make_deps

OWNER_ID = 999000111
CHAT_ID = -100
STARTER_ID = 5


def _update(
    text: str | None,
    *,
    user_id: int = 1,
    first_name: str = "Ada",
    username: str | None = "ada",
    chat_type: str = "supergroup",
) -> MagicMock:
    update = MagicMock()
    chat_id = user_id if chat_type == "private" else CHAT_ID
    update.effective_chat = SimpleNamespace(id=chat_id, type=chat_type)
    update.effective_user = SimpleNamespace(
        id=user_id, first_name=first_name, last_name=None, username=username
    )
    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    update.effective_message = message
    update.message = message
    return update


def _context(deps) -> MagicMock:
    context = MagicMock()
    context.application.bot_data = {"deps": deps}
    context.bot.send_message = AsyncMock()
    context.bot.username = "whopaidbot"
    return context


def _replies(update: MagicMock) -> list[str]:
    return [c.args[0] for c in update.effective_message.reply_text.await_args_list]


def _deps(**kwargs):
    return make_deps(owner_user_id=OWNER_ID, **kwargs)


# ---- sending ----------------------------------------------------------------


async def test_feedback_from_a_group_reaches_the_owner() -> None:
    deps, update = _deps(), _update("/feedback hello there")
    context = _context(deps)

    await feedback_command(update, context)

    context.bot.send_message.assert_awaited_once_with(
        OWNER_ID,
        "💬 <b>Feedback</b> · from a group\nAda (@ada)\n\nhello there",
        parse_mode="HTML",
    )
    assert _replies(update) == [FEEDBACK_SENT_TEXT] == ["Sent. Thank you."]


async def test_feedback_from_a_dm_reaches_the_owner() -> None:
    deps, update = _deps(), _update("/feedback@whopaidbot hello", chat_type="private")
    context = _context(deps)

    await feedback_command(update, context)

    (owner, body), _ = context.bot.send_message.await_args
    assert owner == OWNER_ID
    assert body == "💬 <b>Feedback</b> · from a DM\nAda (@ada)\n\nhello"
    assert _replies(update) == ["Sent. Thank you."]


async def test_no_username_means_first_name_only() -> None:
    deps, update = _deps(), _update("/feedback hi", username=None)
    context = _context(deps)

    await feedback_command(update, context)

    body = context.bot.send_message.await_args.args[1]
    assert body.splitlines()[1] == "Ada"


def test_owner_message_escapes_name_and_text() -> None:
    body = format_owner_message(
        first_name="A<b>&", username="a&b", text="x < y & <i>z</i>", from_group=True
    )
    assert body == (
        "💬 <b>Feedback</b> · from a group\n"
        "A&lt;b&gt;&amp; (@a&amp;b)\n\n"
        "x &lt; y &amp; &lt;i&gt;z&lt;/i&gt;"
    )


async def test_owner_message_has_no_ids_or_chat_names() -> None:
    deps, update = _deps(), _update("/feedback hi", user_id=4242)
    context = _context(deps)

    await feedback_command(update, context)

    body = context.bot.send_message.await_args.args[1]
    assert "4242" not in body and str(CHAT_ID) not in body


# ---- bare /feedback ----------------------------------------------------------


async def test_bare_feedback_in_a_group_gives_the_hint_and_waits_for_nothing() -> None:
    deps, update = _deps(), _update("/feedback")
    context = _context(deps)

    await feedback_command(update, context)

    assert _replies(update) == [FEEDBACK_GROUP_HINT_TEXT]
    assert (
        FEEDBACK_GROUP_HINT_TEXT == "Send it in one line, like <code>/feedback your message</code>."
    )
    assert not deps.feedback_waits.is_waiting(1)
    context.bot.send_message.assert_not_awaited()


def _application(deps):
    return build_application("123456:fake-token", deps)


def _matching_callbacks(application, update: Update) -> list:
    """What PTB would run for `update`: the first matching handler of each group."""
    found = []
    for group in sorted(application.handlers):
        for handler in application.handlers[group]:
            if handler.check_update(update):
                found.append(handler.callback)
                break
    return found


def _real_text_update(text: str, *, chat_type: str, user_id: int = 1, command: bool = False):
    chat = Chat(id=user_id if chat_type == "private" else CHAT_ID, type=chat_type)
    user = User(id=user_id, first_name="Ada", is_bot=False)
    entities = None
    if command:
        entities = (MessageEntity(MessageEntity.BOT_COMMAND, 0, len(text.split()[0])),)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text=text,
        entities=entities,
    )
    message.set_bot(SimpleNamespace(username="whopaidbot"))  # type: ignore[arg-type]
    return Update(update_id=1, message=message)


def test_after_a_group_hint_the_next_text_is_still_an_expense() -> None:
    deps = _deps()
    application = _application(deps)
    # Even a stale wait for this user cannot touch a group message.
    deps.feedback_waits.start(1)

    update = _real_text_update("1200 yen ramen", chat_type="supergroup")

    assert text_expense_handler in _matching_callbacks(application, update)
    assert feedback_text_handler not in _matching_callbacks(application, update)


async def test_bare_feedback_in_a_dm_asks_then_takes_the_next_text() -> None:
    deps = _deps()
    context = _context(deps)
    ask = _update("/feedback", chat_type="private")

    await feedback_command(ask, context)

    assert _replies(ask) == [FEEDBACK_ASK_TEXT] == ["What's your feedback? Send it as one message."]
    assert deps.feedback_waits.is_waiting(1)

    answer = _update("the app is great", chat_type="private")
    await feedback_text_handler(answer, context)

    assert _replies(answer) == ["Sent. Thank you."]
    assert context.bot.send_message.await_args.args[1].endswith("the app is great")
    assert not deps.feedback_waits.is_waiting(1)

    # A second plain text: the wait is gone, so it is not sent as feedback (it gets the
    # private `/start` intro like any other stray message).
    application = _application(deps)
    second = _real_text_update("anything", chat_type="private")
    assert feedback_text_handler not in _matching_callbacks(application, second)


def test_the_wait_routes_private_text_to_the_feedback_handler() -> None:
    deps = _deps()
    application = _application(deps)
    deps.feedback_waits.start(1)

    text = _real_text_update("hello", chat_type="private")
    assert feedback_text_handler in _matching_callbacks(application, text)
    # Another user in another private chat is not waiting.
    other = _real_text_update("hello", chat_type="private", user_id=2)
    assert feedback_text_handler not in _matching_callbacks(application, other)


def test_a_photo_while_waiting_gets_text_only_and_the_wait_continues() -> None:
    deps = _deps()
    application = _application(deps)
    deps.feedback_waits.start(1)
    chat = Chat(id=1, type="private")
    user = User(id=1, first_name="Ada", is_bot=False)
    photo = Message(
        message_id=2,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        photo=(PhotoSize("f", "u", 100, 100),),
    )

    callbacks = _matching_callbacks(application, Update(update_id=2, message=photo))

    assert feedback_non_text_handler in callbacks
    assert deps.feedback_waits.is_waiting(1)


async def test_non_text_handler_replies_text_only() -> None:
    update = _update(None, chat_type="private")

    await feedback_non_text_handler(update, _context(_deps()))

    assert _replies(update) == [FEEDBACK_TEXT_ONLY_TEXT] == ["Text only for now."]


async def _route_and_run(application, deps, text: str, *, chat_type: str = "private") -> list:
    """Match `text` against the built application as PTB would (one handler per group, in
    group order), then run the matched callbacks in that order with a mock update.
    """
    real = _real_text_update(text, chat_type=chat_type, command=text.startswith("/"))
    callbacks = _matching_callbacks(application, real)
    mock = _update(text, chat_type=chat_type)
    context = _context(deps)
    for callback in callbacks:
        await callback(mock, context)
    return callbacks


@pytest.mark.parametrize(
    ("command", "own_callback"),
    [("/cancel", "cancel_command"), ("/feedback", "feedback_command"), ("/help", "help_command")],
)
async def test_a_private_command_drops_the_wait_before_its_own_handler(
    command: str, own_callback: str
) -> None:
    from app.bot.conversations import settrip
    from app.bot.handlers import basic

    own = {
        "cancel_command": settrip.cancel_command,
        "feedback_command": feedback_command,
        "help_command": basic.help_command,
    }[own_callback]
    deps = _deps()
    application = _application(deps)
    deps.feedback_waits.start(1)

    callbacks = await _route_and_run(application, deps, command)

    assert drop_feedback_wait in callbacks and own in callbacks
    assert callbacks.index(drop_feedback_wait) < callbacks.index(own)
    # A bare /feedback ends the old wait and then opens a fresh one; the rest leave none.
    assert deps.feedback_waits.is_waiting(1) is (command == "/feedback")


async def test_settrip_command_also_drops_the_wait() -> None:
    from app.bot.conversations import settrip

    deps = _deps()
    application = _application(deps)
    deps.feedback_waits.start(1)
    real = _real_text_update("/settrip", chat_type="private", command=True)

    callbacks = _matching_callbacks(application, real)

    assert callbacks.index(drop_feedback_wait) < callbacks.index(settrip.settrip_private_command)


def test_plain_text_and_group_commands_never_reach_the_drop() -> None:
    application = _application(_deps())
    assert drop_feedback_wait not in _matching_callbacks(
        application, _real_text_update("hello", chat_type="private")
    )
    assert drop_feedback_wait not in _matching_callbacks(
        application, _real_text_update("/help", chat_type="supergroup", command=True)
    )


def test_the_wait_expires_after_30_minutes() -> None:
    store = FeedbackWaitStore()
    start = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    store.start(7, now=start)

    assert store.is_waiting(7, now=start + timedelta(minutes=29))
    assert not store.is_waiting(7, now=start + timedelta(minutes=31))
    assert not store.is_waiting(7, now=start)  # evicted, stays gone


def test_an_expired_wait_is_not_routed() -> None:
    deps = _deps()
    deps.feedback_waits.start(1, now=datetime.now(UTC) - timedelta(minutes=31))
    application = _application(deps)

    update = _real_text_update("late", chat_type="private")

    assert feedback_text_handler not in _matching_callbacks(application, update)


# ---- length ------------------------------------------------------------------


async def test_1001_characters_is_refused_not_sent_not_counted() -> None:
    deps = _deps()
    context = _context(deps)
    update = _update("/feedback " + "a" * 1001)

    await feedback_command(update, context)

    assert _replies(update) == [FEEDBACK_TOO_LONG_TEXT] == ["Keep it under 1,000 characters."]
    context.bot.send_message.assert_not_awaited()
    assert deps.feedback_limiter.is_allowed(1, datetime.now(UTC))
    assert deps.feedback_limiter._counts == {}


async def test_exactly_1000_characters_is_sent() -> None:
    deps = _deps()
    context = _context(deps)
    update = _update("/feedback " + "a" * 1000)

    await feedback_command(update, context)

    assert _replies(update) == [FEEDBACK_SENT_TEXT]
    context.bot.send_message.assert_awaited_once()


async def test_too_long_in_a_dm_wait_keeps_the_wait() -> None:
    deps = _deps()
    context = _context(deps)
    deps.feedback_waits.start(1)
    update = _update("a" * 1001, chat_type="private")

    await feedback_text_handler(update, context)

    assert _replies(update) == [FEEDBACK_TOO_LONG_TEXT]
    assert deps.feedback_waits.is_waiting(1)
    context.bot.send_message.assert_not_awaited()


# ---- daily limit -------------------------------------------------------------

NOW = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)


def test_sixth_message_in_a_singapore_day_is_refused() -> None:
    limiter = FeedbackLimiter()
    for _ in range(5):
        assert limiter.is_allowed(1, NOW)
        limiter.record(1, NOW)
    assert not limiter.is_allowed(1, NOW)
    assert limiter.is_allowed(2, NOW)  # per user


def test_limit_resets_at_singapore_midnight() -> None:
    limiter = FeedbackLimiter()
    late = datetime(2026, 11, 12, 15, 59, tzinfo=UTC)  # 23:59 Singapore
    after = datetime(2026, 11, 12, 16, 1, tzinfo=UTC)  # 00:01 next day Singapore
    for _ in range(5):
        limiter.record(1, late)
    assert not limiter.is_allowed(1, late)
    assert limiter.is_allowed(1, after)


async def test_sixth_handler_call_gets_the_pinned_limit_text() -> None:
    deps = _deps()
    context = _context(deps)
    for _ in range(5):
        await feedback_command(_update("/feedback hi"), context)
    sixth = _update("/feedback hi")

    await feedback_command(sixth, context)

    assert _replies(sixth) == [FEEDBACK_LIMIT_TEXT]
    assert (
        FEEDBACK_LIMIT_TEXT == "There's a limit of 5 feedback messages a day. Try again tomorrow."
    )
    assert context.bot.send_message.await_count == 5
    other = _update("/feedback hi", user_id=2)
    await feedback_command(other, context)
    assert _replies(other) == [FEEDBACK_SENT_TEXT]


# ---- failure and off ----------------------------------------------------------


async def test_owner_send_failure_replies_and_does_not_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    deps = _deps()
    context = _context(deps)
    context.bot.send_message.side_effect = RuntimeError(f"chat {OWNER_ID} blocked the bot")
    update = _update("/feedback secret words")

    with caplog.at_level(logging.DEBUG):
        await feedback_command(update, context)

    assert _replies(update) == [FEEDBACK_FAILED_TEXT] == ["Couldn't send that. Try again later."]
    assert deps.feedback_limiter._counts == {}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings] == ["feedback failed"]
    assert warnings[0].exc_info is None


async def test_failed_send_in_a_dm_wait_ends_the_wait() -> None:
    deps = _deps()
    context = _context(deps)
    context.bot.send_message.side_effect = RuntimeError("boom")
    deps.feedback_waits.start(1)

    await feedback_text_handler(_update("hi", chat_type="private"), context)

    assert not deps.feedback_waits.is_waiting(1)


async def test_owner_unset_replies_not_set_up() -> None:
    deps = make_deps()  # owner_user_id None
    context = _context(deps)
    for text, chat_type in (("/feedback hi", "supergroup"), ("/feedback", "private")):
        update = _update(text, chat_type=chat_type)
        await feedback_command(update, context)
        assert _replies(update) == [FEEDBACK_OFF_TEXT] == ["Feedback isn't set up on this bot."]
    context.bot.send_message.assert_not_awaited()


async def test_feedback_works_with_no_trip_open_or_closed() -> None:
    # The ledger is empty: no trip has ever been started in this chat.
    deps = _deps(ledger=InMemoryLedgerStore())
    context = _context(deps)
    update = _update("/feedback hi")

    await feedback_command(update, context)

    assert _replies(update) == [FEEDBACK_SENT_TEXT]


# ---- logging -----------------------------------------------------------------


async def test_no_log_record_contains_the_feedback_text(caplog: pytest.LogCaptureFixture) -> None:
    deps = _deps()
    context = _context(deps)
    secret = "zebra-quartz-feedback-text"
    with caplog.at_level(logging.DEBUG):
        await feedback_command(_update(f"/feedback {secret}"), context)  # sent
        context.bot.send_message.side_effect = RuntimeError("boom")
        await feedback_command(_update(f"/feedback {secret}", user_id=2), context)  # failed
        for _ in range(5):
            deps.feedback_limiter.record(3, datetime.now(UTC))
        await feedback_command(_update(f"/feedback {secret}", user_id=3), context)  # limit
    assert caplog.records
    for record in caplog.records:
        assert secret not in record.getMessage()
        assert secret not in str(record.__dict__)
        assert str(OWNER_ID) not in str(record.__dict__)
    assert {r.getMessage() for r in caplog.records} >= {
        "feedback sent",
        "feedback failed",
        "feedback refused: limit",
    }


# ---- close trip prompt --------------------------------------------------------


async def _bound_ledger() -> tuple[InMemoryLedgerStore, Trip]:
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
    return ledger, await ledger.save_binding(trip)


def _callback_update(trip_id: int | None, presser_id: int) -> MagicMock:
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    query = update.callback_query
    query.data = f"closetrip:{trip_id}:{STARTER_ID}"
    query.from_user = SimpleNamespace(id=presser_id)
    query.answer = AsyncMock()
    query.message.text_html = "To settle up"
    query.edit_message_text = AsyncMock()
    return update


async def test_prompt_posted_once_after_a_successful_close() -> None:
    ledger, trip = await _bound_ledger()
    context = _context(_deps(ledger=ledger))

    await close_trip_callback(_callback_update(trip.id, STARTER_ID), context)

    context.bot.send_message.assert_awaited_once_with(
        CHAT_ID, FEEDBACK_PROMPT_TEXT, parse_mode="HTML"
    )
    assert FEEDBACK_PROMPT_TEXT == (
        "How was Who Paid For This on this trip? Send <code>/feedback</code> and your "
        "message. It goes straight to the person who built it."
    )


async def test_prompt_not_posted_on_a_refused_press() -> None:
    ledger, trip = await _bound_ledger()
    context = _context(_deps(ledger=ledger))

    await close_trip_callback(_callback_update(trip.id, 77), context)

    context.bot.send_message.assert_not_awaited()


async def test_prompt_not_posted_on_a_second_press() -> None:
    ledger, trip = await _bound_ledger()
    context = _context(_deps(ledger=ledger))
    await close_trip_callback(_callback_update(trip.id, STARTER_ID), context)
    context.bot.send_message.reset_mock()

    await close_trip_callback(_callback_update(trip.id, STARTER_ID), context)

    context.bot.send_message.assert_not_awaited()


async def test_prompt_still_posted_when_the_edit_raises() -> None:
    ledger, trip = await _bound_ledger()
    context = _context(_deps(ledger=ledger))
    update = _callback_update(trip.id, STARTER_ID)
    update.callback_query.edit_message_text.side_effect = RuntimeError("message gone")

    await close_trip_callback(update, context)

    context.bot.send_message.assert_awaited_once()


async def test_failing_prompt_does_not_undo_the_close() -> None:
    ledger, trip = await _bound_ledger()
    context = _context(_deps(ledger=ledger))
    context.bot.send_message.side_effect = RuntimeError("telegram down")

    await close_trip_callback(_callback_update(trip.id, STARTER_ID), context)

    assert (await ledger.list_trips(CHAT_ID))[0].closed_at is not None


async def test_no_prompt_when_the_owner_is_unset() -> None:
    ledger, trip = await _bound_ledger()
    context = _context(make_deps(ledger))

    await close_trip_callback(_callback_update(trip.id, STARTER_ID), context)

    context.bot.send_message.assert_not_awaited()
    assert (await ledger.list_trips(CHAT_ID))[0].closed_at is not None


async def test_settrip_closing_a_trip_posts_no_prompt() -> None:
    from app.bot.conversations import settrip
    from app.services.currencies import StaticCurrencyCatalog

    ledger, trip = await _bound_ledger()
    deps = make_deps(
        ledger,
        owner_user_id=OWNER_ID,
        currencies=StaticCurrencyCatalog(frozenset({"JPY", "SGD", "THB"})),
    )
    context = _context(deps)
    context.bot.edit_message_text = AsyncMock()
    update = _update("/settrip", user_id=STARTER_ID)
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup", title="Trip")
    await settrip.settrip_command(update, context)
    (session,) = deps.settrip_sessions._sessions.values()

    def press(step: str, value: str) -> MagicMock:
        cb = MagicMock()
        cb.callback_query.data = f"st:{session.session_id}:{step}:{value}"
        cb.callback_query.from_user = SimpleNamespace(id=STARTER_ID)
        cb.callback_query.answer = AsyncMock()
        cb.callback_query.edit_message_text = AsyncMock()
        return cb

    for step, value in (
        ("rebind", "yes"),
        ("trip_ccy", "THB"),
        ("home_ccy", "SGD"),
        ("tz", "Asia/Bangkok"),
        ("confirm", "yes"),
    ):
        await settrip.settrip_callback(press(step, value), context)

    # The old trip was closed by the new one, and no feedback prompt went out.
    trips = await ledger.list_trips(CHAT_ID)
    assert len(trips) == 2 and sum(t.closed_at is not None for t in trips) == 1
    context.bot.send_message.assert_not_awaited()


# ---- help --------------------------------------------------------------------


@pytest.mark.parametrize("chat_type", ["supergroup", "private"])
async def test_help_names_the_bot_and_lists_feedback(chat_type: str) -> None:
    ledger, _ = await _bound_ledger()
    deps = make_deps(ledger, bot_version="abc1234")
    context = _context(deps)
    update = _update("/help", chat_type=chat_type)
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type=chat_type)

    await help_command(update, context)

    if chat_type == "private":
        text = update.message.reply_text.await_args.args[0]
    else:
        text = context.bot.send_message.await_args.args[1]
    assert text.startswith("<b>Who Paid For This</b> — records trip expenses for the group.")
    assert "/feedback — tell the person who built this what to fix\n/help — this message" in text
    assert text.endswith("Who Paid For This abc1234")
    assert "whopaid" not in text


def test_registration_puts_feedback_ahead_of_settrip_text_and_registers_the_command() -> None:
    application = _application(_deps())
    callbacks = [h.callback for g in sorted(application.handlers) for h in application.handlers[g]]
    assert feedback_command in callbacks
    assert callbacks.index(feedback_text_handler) < callbacks.index(text_expense_handler)
    assert isinstance(application.handlers[-2][0], MessageHandler)
    assert application.handlers[-2][0].callback is drop_feedback_wait


# ---- review fixes --------------------------------------------------------------


async def test_failed_reply_does_not_chain_the_owner_send_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.bot.app import _error_handler

    deps = _deps()
    context = _context(deps)
    context.bot.send_message.side_effect = RuntimeError(f"chat {OWNER_ID} blocked the bot")
    update = _update("/feedback hi")
    update.effective_message.reply_text.side_effect = RuntimeError("reply failed")

    with pytest.raises(RuntimeError, match="reply failed") as raised:
        await feedback_command(update, context)

    error: BaseException | None = raised.value
    while error is not None:
        assert str(OWNER_ID) not in str(error)
        error = error.__cause__ or error.__context__

    # Routed through the app's own error handler, nothing in the log has the ID.
    ptb_update = MagicMock(spec=Update)
    ptb_update.update_id = 1
    ptb_update.callback_query = None
    ptb_update.effective_message = None
    context.error = raised.value
    with caplog.at_level(logging.DEBUG):
        await _error_handler(ptb_update, context)
    assert caplog.records
    for record in caplog.records:
        assert str(OWNER_ID) not in str(record.__dict__)
        assert str(OWNER_ID) not in caplog.text


async def test_group_feedback_leaves_a_private_wait_open() -> None:
    deps = _deps()
    context = _context(deps)
    deps.feedback_waits.start(1)

    await feedback_command(_update("/feedback hi", chat_type="supergroup"), context)

    context.bot.send_message.assert_awaited_once()
    assert deps.feedback_waits.is_waiting(1)


async def test_private_feedback_with_text_during_a_wait_sends_and_leaves_no_wait() -> None:
    deps = _deps()
    application = _application(deps)
    deps.feedback_waits.start(1)

    callbacks = await _route_and_run(application, deps, "/feedback straight away")

    assert feedback_command in callbacks
    assert not deps.feedback_waits.is_waiting(1)


def test_edited_messages_are_not_taken_as_feedback() -> None:
    deps = _deps()
    application = _application(deps)
    deps.feedback_waits.start(1)
    chat = Chat(id=1, type="private")
    user = User(id=1, first_name="Ada", is_bot=False)
    edited = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text="an old message, edited",
        edit_date=datetime.now(UTC),
    )
    photo = Message(
        message_id=2,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        photo=(PhotoSize("f", "u", 100, 100),),
        edit_date=datetime.now(UTC),
    )

    for message in (edited, photo):
        callbacks = _matching_callbacks(application, Update(update_id=1, edited_message=message))
        assert feedback_text_handler not in callbacks
        assert feedback_non_text_handler not in callbacks
    assert deps.feedback_waits.is_waiting(1)


@pytest.mark.parametrize("blank", ["\u3000", "   ", "\u3000 \n"])
async def test_blank_text_in_a_wait_asks_again_and_sends_nothing(blank: str) -> None:
    deps = _deps()
    context = _context(deps)
    deps.feedback_waits.start(1)
    update = _update(blank, chat_type="private")

    await feedback_text_handler(update, context)

    assert _replies(update) == [FEEDBACK_ASK_TEXT]
    context.bot.send_message.assert_not_awaited()
    assert deps.feedback_waits.is_waiting(1)
    assert deps.feedback_limiter._counts == {}


def test_pinned_limits_match_the_hardcoded_reply_texts() -> None:
    from app.services.feedback import MAX_FEEDBACK_CHARS, MAX_FEEDBACK_PER_DAY

    # The pinned reply texts ("under 1,000 characters", "limit of 5") hard-code these.
    assert MAX_FEEDBACK_CHARS == 1000
    assert MAX_FEEDBACK_PER_DAY == 5
