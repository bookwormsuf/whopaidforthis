"""The `/settrip` flow: bind a group to a Google Sheet and lay out its tabs.

Spec references: pinned-decisions §2, §10; sprint 1 pins. Not a PTB ConversationHandler:
state lives in a `SettripSessionStore` (on `Deps.settrip_sessions`), edited in place
through one message per session.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from html import escape
from zoneinfo import available_timezones

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes
from telegram.ext.filters import MessageFilter

from app.bot.deps import get_deps
from app.bot.keyboards.settrip import (
    confirm_keyboard,
    home_currency_keyboard,
    rebind_keyboard,
    timezone_keyboard,
    trip_currency_keyboard,
)
from app.bot.session_state import SettripSession, SettripSessionStore
from app.models.trip import Trip
from app.models.user import display_name_for
from app.services.currencies import default_timezone_for
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable

logger = logging.getLogger(__name__)

_SHEET_URL_RE = re.compile(r"/d/([a-zA-Z0-9_-]+)")
_BARE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{20,}$")

USAGE_TEXT = "Usage: /settrip &lt;Sheet ID or URL&gt;"
PRIVATE_TEXT = "Run /settrip inside your trip group."
UNAVAILABLE_TEXT = "Couldn't reach Google Sheets. "


def extract_sheet_id(arg: str) -> str | None:
    """Extract a Sheet ID from a full URL (`/d/<id>/...`) or a bare ID."""
    candidate = arg.strip()
    match = _SHEET_URL_RE.search(candidate)
    if match:
        return match.group(1)
    if _BARE_ID_RE.match(candidate):
        return candidate
    return None


def _not_shared_text(email: str) -> str:
    return (
        f"I can't open that Sheet. Share it with <code>{escape(email)}</code> as "
        "Editor, then try again."
    )


async def settrip_private_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(PRIVATE_TEXT, parse_mode="HTML")


async def settrip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    assert message is not None and chat is not None and user is not None

    if not context.args:
        await message.reply_text(USAGE_TEXT, parse_mode="HTML")
        return
    sheet_id = extract_sheet_id(" ".join(context.args))
    if sheet_id is None:
        await message.reply_text(USAGE_TEXT, parse_mode="HTML")
        return

    deps = get_deps(context)

    # Access check first, regardless of whether this chat is already bound: a sheet
    # that can't be opened is an error even when we're about to ask for a rebind.
    if not await deps.ledger.can_open(sheet_id):
        await message.reply_text(_not_shared_text(deps.service_account_email), parse_mode="HTML")
        return

    trip_name = chat.title or "Trip"
    try:
        existing_trip = await deps.ledger.get_binding(chat.id)
    except LedgerUnavailable:
        logger.exception("settrip: could not read the current binding")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return
    if existing_trip is not None:
        session = deps.settrip_sessions.create(
            chat_id=chat.id,
            initiator_id=user.id,
            initiator_name=display_name_for(user),
            sheet_id=sheet_id,
            trip_name=trip_name,
            step="rebind",
        )
        sent = await message.reply_text(
            "This group is already bound to a Sheet. Rebind?",
            reply_markup=rebind_keyboard(session.session_id),
            parse_mode="HTML",
        )
        deps.settrip_sessions.update(session.session_id, message_id=sent.message_id)
        return

    session = deps.settrip_sessions.create(
        chat_id=chat.id,
        initiator_id=user.id,
        initiator_name=display_name_for(user),
        sheet_id=sheet_id,
        trip_name=trip_name,
        step="trip_ccy",
    )
    sent = await message.reply_text(
        "Trip currency?", reply_markup=trip_currency_keyboard(session.session_id), parse_mode="HTML"
    )
    deps.settrip_sessions.update(session.session_id, message_id=sent.message_id)


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
        sheet_id=session.sheet_id,
        name=session.trip_name,
        trip_currency=session.trip_currency,
        home_currency=session.home_currency,
        timezone=session.timezone,
        created_at=datetime.now(UTC),
    )
    query = update.callback_query
    assert query is not None
    try:
        await deps.ledger.initialise_trip_sheet(trip)
        await deps.ledger.save_binding(trip)
    except LedgerUnavailable:
        logger.exception("settrip: could not create trip sheet")
        await query.edit_message_text(
            UNAVAILABLE_TEXT,
            reply_markup=_retry_keyboard(session.session_id),
            parse_mode="HTML",
        )
        return
    deps.settrip_sessions.delete(session.session_id)
    text = (
        f"Bound ✅ {escape(trip.name)} · {escape(trip.trip_currency)} → "
        f"{escape(trip.home_currency)} · {escape(trip.timezone)}"
    )
    await query.edit_message_text(text, parse_mode="HTML")


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
            await query.edit_message_text("Keeping the current Sheet.", parse_mode="HTML")
            return
        if not await deps.ledger.can_open(session.sheet_id):
            deps.settrip_sessions.delete(session_id)
            await query.edit_message_text(
                _not_shared_text(deps.service_account_email), parse_mode="HTML"
            )
            return
        next_session = deps.settrip_sessions.update(session_id, step="trip_ccy")
        assert next_session is not None
        text, markup = _render_for_step(next_session)
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
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
