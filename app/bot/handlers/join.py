"""`/join` and the group `/start` alias: mark the sender as a joined trip user.

Spec references: pinned-decisions §3, §12; sprint 1 pins.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.deps import get_deps
from app.bot.keyboards.settrip import joined_keyboard
from app.models.trip import Trip
from app.models.user import TripUser
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable

logger = logging.getLogger(__name__)

UNBOUND_TEXT = "Run /settrip first"
UNAVAILABLE_TEXT = LEDGER_UNAVAILABLE_TEXT


async def require_trip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Trip | None:
    """Reply "Run /settrip first" and return None when this chat has no bound trip."""
    deps = get_deps(context)
    chat = update.effective_chat
    assert chat is not None
    message = update.effective_message
    try:
        trip = await deps.ledger.get_binding(chat.id)
    except LedgerUnavailable:
        logger.exception("require_trip: could not read the current binding")
        if message is not None:
            await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return None
    if trip is None:
        if message is not None:
            await message.reply_text(UNBOUND_TEXT, parse_mode="HTML")
        return None
    return trip


async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/join`. Sent as a reply to another (non-bot) user's message, joins that user
    instead of the sender (spec §3, "joining others").
    """
    trip = await require_trip(update, context)
    if trip is None:
        return
    deps = get_deps(context)
    user = update.effective_user
    message = update.effective_message
    assert user is not None and message is not None

    target = user
    reply_to = message.reply_to_message
    if reply_to is not None and reply_to.from_user is not None and not reply_to.from_user.is_bot:
        target = reply_to.from_user

    tg_user = TripUser.from_telegram_user(target, datetime.now(UTC))
    try:
        joined_user = await deps.ledger.upsert_user(trip, tg_user, joined=True)
    except LedgerUnavailable:
        logger.exception("join: could not upsert user")
        await message.reply_text(UNAVAILABLE_TEXT, parse_mode="HTML")
        return
    await message.reply_text(
        f"Joined ✅ {escape(joined_user.display_name)}",
        parse_mode="HTML",
        reply_markup=joined_keyboard(trip.chat_id),
    )
