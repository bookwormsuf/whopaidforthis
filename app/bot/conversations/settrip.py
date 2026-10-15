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
from datetime import UTC, datetime
from html import escape
from zoneinfo import available_timezones

from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update, User
from telegram.ext import ContextTypes
from telegram.ext.filters import MessageFilter

from app.bot.deps import Deps, get_deps
from app.bot.keyboards.settrip import (
    confirm_keyboard,
    home_currency_keyboard,
    joined_keyboard,
    rebind_keyboard,
    timezone_keyboard,
    trip_currency_keyboard,
)
from app.bot.send import SendFn, build_send
from app.bot.session_state import SettripSession, SettripSessionStore
from app.models.trip import Trip
from app.models.user import display_name_for
from app.services.currencies import default_timezone_for
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable

logger = logging.getLogger(__name__)

PRIVATE_TEXT = "Run /settrip inside your trip group."
UNAVAILABLE_TEXT = LEDGER_UNAVAILABLE_TEXT
REBIND_PROMPT = "Close the current trip and start a new one?"
KEEP_CURRENT_TEXT = "Keeping the current trip."


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
        sent = await send("Trip currency?", reply_markup=trip_currency_keyboard(session.session_id))
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
        return "Trip currency?", trip_currency_keyboard(session.session_id)
    if session.step == "home_ccy":
        return "Home currency?", home_currency_keyboard(session.session_id)
    if session.step == "tz":
        default_tz = default_timezone_for(session.trip_currency or "") or "UTC"
        return (
            f"Timezone? (default {escape(default_tz)})",
            timezone_keyboard(session.session_id, default_tz),
        )
    if session.step == "confirm":
        text = (
            f"<b>{escape(session.trip_name)}</b>\n"
            f"{escape(session.trip_currency or '')} → {escape(session.home_currency or '')}\n"
            f"Timezone: {escape(session.timezone or '')}"
        )
        return text, confirm_keyboard(session.session_id)
    raise ValueError(f"unknown settrip step: {session.step}")


async def _apply_step_value(
    context: ContextTypes.DEFAULT_TYPE, session: SettripSession, step: str, value: str
) -> None:
    deps = get_deps(context)
    if step == "trip_ccy":
        next_session = deps.settrip_sessions.update(
            session.session_id, trip_currency=value, step="home_ccy", awaiting_text_for=None
        )
    elif step == "home_ccy":
        next_session = deps.settrip_sessions.update(
            session.session_id, home_currency=value, step="tz", awaiting_text_for=None
        )
    elif step == "tz":
        next_session = deps.settrip_sessions.update(
            session.session_id, timezone=value, step="confirm", awaiting_text_for=None
        )
    else:
        return
    if next_session is None:
        return
    text, markup = _render_for_step(next_session)
    await context.bot.edit_message_text(
        chat_id=next_session.chat_id,
        message_id=next_session.message_id,
        text=text,
        reply_markup=markup,
        parse_mode="HTML",
    )


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
    deps.settrip_sessions.delete(session.session_id)
    text = (
        f"Connected ✅ {escape(trip.name)} · {escape(trip.trip_currency)} → "
        f"{escape(trip.home_currency)} · {escape(trip.timezone)}"
    )
    await query.edit_message_text(
        text, parse_mode="HTML", reply_markup=joined_keyboard(trip.chat_id)
    )
    # Sprint 10 amendment (2026-10-15): the moment a trip starts is when the group
    # wants the buttons, so post a fresh welcome with the full menu and pin it. The
    # earlier "Start a trip" welcome is left as it is (old menus are never edited).
    # Local import: menu.py imports run_settrip from this module.
    from app.bot.handlers.menu import post_welcome

    chat = update.effective_chat
    if chat is not None:
        try:
            await post_welcome(deps, context.bot, chat)
        except Exception:  # noqa: BLE001 - the trip is saved; a missing menu must not crash
            logger.exception("settrip: could not post the welcome after the trip started")


def _retry_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Retry", callback_data=f"st:{session_id}:confirm:yes")]]
    )


_OTHER_PROMPTS = {
    "trip_ccy": "Type the trip currency code (e.g. THB).",
    "home_ccy": "Type the home currency code (e.g. AUD).",
    "tz": "Type the IANA timezone name (e.g. Asia/Bangkok).",
}


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

    if step in ("trip_ccy", "home_ccy", "tz"):
        if value == "other":
            deps.settrip_sessions.update(session_id, awaiting_text_for=step)
            await query.edit_message_text(_OTHER_PROMPTS[step], parse_mode="HTML")
            return
        await _apply_step_value(context, session, step, value)
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
    """Captures a free-typed currency/timezone value for a session awaiting one."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or not message.text:
        return
    deps = get_deps(context)
    session = deps.settrip_sessions.find_awaiting_text(chat.id, user.id)
    if session is None:
        return

    step = session.awaiting_text_for
    assert step is not None
    raw = message.text.strip()

    if step in ("trip_ccy", "home_ccy"):
        code = raw.upper()
        supported = await deps.currencies.supported()
        if code not in supported:
            await message.reply_text(
                "Unknown currency code. Try again or /cancel.", parse_mode="HTML"
            )
            return
        await _apply_step_value(context, session, step, code)
        return

    if step == "tz":
        if raw not in available_timezones():
            await message.reply_text("Unknown timezone. Try again or /cancel.", parse_mode="HTML")
            return
        await _apply_step_value(context, session, step, raw)
        return
