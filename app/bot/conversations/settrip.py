"""The `/settrip` flow: start a trip for this group with buttons only.

Spec references: pinned-decisions §2, §18; docs/sprints/09-neon-store-and-export.md.
Not a PTB ConversationHandler: state lives in a `SettripSessionStore` (on
`Deps.settrip_sessions`), edited in place through one message per session.

No Sheet link, no access check (Sprint 9 pin): `/settrip` in a group with no open
trip goes straight to the currency buttons. A group that already has an open trip
is asked "Close the current trip and start a new one?" first (sprint 9 item 1);
`Start new trip` moves on to the currency buttons (`save_binding` closes the old
trip in the same transaction once a new one is confirmed), `Keep current` ends the
conversation with one line.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from html import escape

from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update, User
from telegram.ext import ContextTypes
from telegram.ext.filters import MessageFilter

from app.bot.deps import Deps, get_deps
from app.bot.keyboards.settrip import (
    currency_page_keyboard,
    home_currency_keyboard,
    joined_keyboard,
    rebind_keyboard,
    summary_keyboard,
    timezone_list_keyboard,
    trip_currency_keyboard,
)
from app.bot.send import SendFn, build_send
from app.bot.session_state import SettripSession, SettripSessionStore
from app.models.trip import Trip
from app.models.user import display_name_for
from app.services.currencies import default_timezone_for, match_timezone, timezone_choices_for
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable

logger = logging.getLogger(__name__)

PRIVATE_TEXT = "Run /settrip inside your trip group."
UNAVAILABLE_TEXT = LEDGER_UNAVAILABLE_TEXT
REBIND_PROMPT = "Close the current trip and start a new one?"
KEEP_CURRENT_TEXT = "Keeping the current trip."
TRIP_CCY_TEXT = "Which currency will you spend on this trip?"
HOME_CCY_TEXT = "Which currency do you want to use to pay each other back?"
TZ_LIST_TEXT = "Which timezone? Expense dates use it."
TZ_TYPE_TEXT = "Type the timezone, like Asia/Bangkok."
UNKNOWN_TZ_TEXT = "Unknown timezone. Try again or /cancel."


async def settrip_private_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(PRIVATE_TEXT, parse_mode="HTML")


async def run_settrip(deps: Deps, chat: Chat, user: User, send: SendFn) -> None:
    """Starts (or restarts) the group `/settrip` flow: the currency/timezone
    buttons that follow are driven by `settrip_callback` (`st:`), not by this
    coroutine again, so this only covers the first message -- same as the
    typed command today.
    """
    trip_name = chat.title or "Trip"

    try:
        existing = await deps.ledger.get_binding(chat.id)
    except LedgerUnavailable:
        logger.exception("settrip: could not check for an existing open trip")
        await send(UNAVAILABLE_TEXT)
        return

    first_step = "rebind" if existing is not None else "trip_ccy"
    # A second `/settrip` or `Start a trip` replaces this person's earlier setup.
    deps.settrip_sessions.discard_for(chat.id, user.id)
    session = deps.settrip_sessions.create(
        chat_id=chat.id,
        initiator_id=user.id,
        initiator_name=display_name_for(user),
        trip_name=trip_name,
        step=first_step,
    )
    if existing is not None:
        sent = await send(REBIND_PROMPT, reply_markup=rebind_keyboard(session.session_id))
    else:
        sent = await send(TRIP_CCY_TEXT, reply_markup=trip_currency_keyboard(session.session_id))
    deps.settrip_sessions.update(session.session_id, message_id=sent.message_id)


async def settrip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    assert message is not None and chat is not None and user is not None
    await run_settrip(get_deps(context), chat, user, build_send(message))


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        return
    deps = get_deps(context)
    session = deps.settrip_sessions.find_pending_for(chat.id, user.id)
    if session is not None:
        deps.settrip_sessions.delete(session.session_id)
        message = update.effective_message
        if message is not None:
            await message.reply_text("Cancelled.", parse_mode="HTML")


def _render_for_step(session: SettripSession) -> tuple[str, InlineKeyboardMarkup]:
    if session.step == "trip_ccy":
        return TRIP_CCY_TEXT, trip_currency_keyboard(session.session_id)
    if session.step == "home_ccy":
        return HOME_CCY_TEXT, home_currency_keyboard(session.session_id)
    if session.step == "tz":
        choices = timezone_choices_for(session.trip_currency or "")
        return TZ_LIST_TEXT, timezone_list_keyboard(session.session_id, choices)
    if session.step == "confirm":
        text = (
            f"<b>{escape(session.trip_name)}</b>\n"
            f"Spend in: {escape(session.trip_currency or '')}\n"
            f"Pay back in: {escape(session.home_currency or '')}\n"
            f"Timezone: {escape(session.timezone or '')}"
        )
        return text, summary_keyboard(session.session_id)
    raise ValueError(f"unknown settrip step: {session.step}")


async def _show(context: ContextTypes.DEFAULT_TYPE, session: SettripSession) -> None:
    text, markup = _render_for_step(session)
    await context.bot.edit_message_text(
        chat_id=session.chat_id,
        message_id=session.message_id,
        text=text,
        reply_markup=markup,
        parse_mode="HTML",
    )


async def _apply_currency(
    context: ContextTypes.DEFAULT_TYPE, session: SettripSession, step: str, code: str
) -> None:
    """A currency picked from the short keyboard or the full page. Returns to the
    summary card when the step was opened from it, else moves to the next question."""
    store = get_deps(context).settrip_sessions
    changes: dict[str, object] = {"awaiting_text_for": None}
    if step == "trip_ccy":
        changes["trip_currency"] = code
        if not session.timezone_chosen:
            changes["timezone"] = default_timezone_for(code)
    else:
        changes["home_currency"] = code
    if session.from_summary or step == "home_ccy":
        changes.update(step="confirm", from_summary=False)
    else:
        changes["step"] = "home_ccy"
    updated = store.update(session.session_id, **changes)
    if updated is not None:
        await _show(context, updated)


async def _apply_timezone(
    context: ContextTypes.DEFAULT_TYPE, session: SettripSession, timezone: str
) -> None:
    updated = get_deps(context).settrip_sessions.update(
        session.session_id,
        timezone=timezone,
        timezone_chosen=True,
        step="confirm",
        awaiting_text_for=None,
    )
    if updated is not None:
        await _show(context, updated)


async def _create_trip(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: SettripSession
) -> None:
    deps = get_deps(context)
    assert session.trip_currency and session.home_currency and session.timezone
    trip = Trip(
        chat_id=session.chat_id,
        name=session.trip_name,
        trip_currency=session.trip_currency,
        home_currency=session.home_currency,
        timezone=session.timezone,
        created_by=session.initiator_id,
        created_at=datetime.now(UTC),
    )
    query = update.callback_query
    assert query is not None
    try:
        trip = await deps.ledger.save_binding(trip)
    except LedgerUnavailable:
        logger.exception("settrip: could not save the new trip")
        await query.edit_message_text(
            UNAVAILABLE_TEXT,
            reply_markup=_retry_keyboard(session.session_id),
            parse_mode="HTML",
        )
        return
    # The trip exists: any other setup in this chat is stale, including one left
    # waiting on typed text, which would otherwise swallow the next messages.
    deps.settrip_sessions.discard_for(session.chat_id)
    text = (
        f"Connected ✅ {escape(trip.name)} · {escape(trip.trip_currency)} → "
        f"{escape(trip.home_currency)} · {escape(trip.timezone)}"
    )
    await query.edit_message_text(
        text, parse_mode="HTML", reply_markup=joined_keyboard(trip.chat_id)
    )
    await _post_commands_card(context, trip.chat_id)


async def _post_commands_card(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Sprint 20: right after `Connected ✅`, post and pin the commands card. A
    failure is logged and swallowed; the trip stays created. Local import:
    menu.py imports this module.
    """
    from app.bot.handlers.menu import post_commands_card

    try:
        await post_commands_card(context.bot, chat_id)
    except Exception:  # noqa: BLE001 - the trip exists; a missing card must not crash
        logger.exception("settrip: could not post the commands card")


def _retry_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Retry", callback_data=f"st:{session_id}:confirm:yes")]]
    )


async def settrip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query is not None
    data = query.data or ""
    parts = data.split(":", 3)
    if len(parts) != 4 or parts[0] != "st":
        await query.answer()
        return
    _, session_id, step, value = parts

    deps = get_deps(context)
    session = deps.settrip_sessions.get(session_id)
    if session is None:
        await query.answer()
        try:
            await query.edit_message_text("This setup expired, run /settrip again")
        except Exception:  # noqa: BLE001 - editing an already-changed message is not fatal
            logger.debug("could not edit expired settrip message")
        return

    if query.from_user is None or query.from_user.id != session.initiator_id:
        await query.answer(f"Only {session.initiator_name} can do this")
        return
    await query.answer()

    if step == "rebind":
        if value == "no":
            deps.settrip_sessions.delete(session_id)
            await query.edit_message_text(KEEP_CURRENT_TEXT, parse_mode="HTML")
            return
        if value == "yes":
            next_session = deps.settrip_sessions.update(
                session_id, step="trip_ccy", awaiting_text_for=None
            )
            if next_session is None:
                return
            text, markup = _render_for_step(next_session)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        return

    if step in ("trip_ccy", "home_ccy"):
        if value == "other":
            deps.settrip_sessions.update(session_id, awaiting_text_for=None)
            codes = list(await deps.currencies.supported())
            await query.edit_message_text(
                TRIP_CCY_TEXT if step == "trip_ccy" else HOME_CCY_TEXT,
                reply_markup=currency_page_keyboard(session_id, step, codes),
                parse_mode="HTML",
            )
            return
        if value == "back":
            deps.settrip_sessions.update(session_id, awaiting_text_for=None)
            await _show(context, replace(session, step=step, awaiting_text_for=None))
            return
        await _apply_currency(context, session, step, value)
        return

    if step == "chg":
        if value == "trip":
            changed = deps.settrip_sessions.update(
                session_id, step="trip_ccy", from_summary=True, awaiting_text_for=None
            )
        elif value == "home":
            changed = deps.settrip_sessions.update(
                session_id, step="home_ccy", from_summary=True, awaiting_text_for=None
            )
        elif value == "tz":
            changed = deps.settrip_sessions.update(session_id, step="tz", awaiting_text_for=None)
        else:
            return
        if changed is not None:
            await _show(context, changed)
        return

    if step == "tz":
        if value == "back":
            back = deps.settrip_sessions.update(session_id, step="confirm", awaiting_text_for=None)
            if back is not None:
                await _show(context, back)
        elif value == "type":
            deps.settrip_sessions.update(session_id, awaiting_text_for="tz")
            await query.edit_message_text(TZ_TYPE_TEXT, parse_mode="HTML")
        return

    if step == "tzi":
        choices = timezone_choices_for(session.trip_currency or "")
        if not value.isdecimal() or int(value) >= len(choices):
            return  # a stale button: nothing changes
        await _apply_timezone(context, session, choices[int(value)])
        return

    if step == "confirm":
        if value == "cancel":
            deps.settrip_sessions.delete(session_id)
            await query.edit_message_text("Cancelled.", parse_mode="HTML")
            return
        if value == "yes":
            await _create_trip(update, context, session)
            return


class AwaitingSettripText(MessageFilter):
    """True only when this chat/user has a settrip session waiting on free text.

    Holds a direct reference to the SettripSessionStore (constructed once, from
    `Deps.settrip_sessions`, at application build time) because a `filters.MessageFilter`
    only receives the `Message`, not the handler `context`.
    """

    def __init__(self, store: SettripSessionStore) -> None:
        super().__init__(name="AwaitingSettripText")
        self.store = store

    def filter(self, message: Message) -> bool:
        chat = message.chat
        user = message.from_user
        if chat is None or user is None:
            return False
        return self.store.find_awaiting_text(chat.id, user.id) is not None


async def settrip_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captures a typed timezone for a session that is waiting on one."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or not message.text:
        return
    deps = get_deps(context)
    session = deps.settrip_sessions.find_awaiting_text(chat.id, user.id)
    if session is None:
        return

    if session.awaiting_text_for != "tz":
        return
    timezone = match_timezone(message.text)
    if timezone is None:
        await message.reply_text(UNKNOWN_TZ_TEXT, parse_mode="HTML")
        return
    await _apply_timezone(context, session, timezone)
