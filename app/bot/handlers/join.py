"""`/join` and the group `/start` alias: mark the sender as a joined trip user.

Spec references: pinned-decisions §3, §12; sprint 1 pins; sprint 9 item 3 (closed
trips).

`resolve_trip_status` is the one place that tells an unbound chat (no trip has ever been
started) apart from a closed one (a trip exists, but the latest one is closed):
every gate in the bot -- `require_trip` for commands, `require_trip_for_expense`
for text/photo messages, and the app-wide fallback gate for unknown commands --
goes through it, so the distinction is made once.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.deps import Deps, get_deps
from app.bot.keyboards.settrip import joined_keyboard
from app.models.trip import Trip
from app.models.user import TripUser
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable

logger = logging.getLogger(__name__)

UNBOUND_TEXT = "Run /settrip first"
CLOSED_TRIP_TEXT = "This trip is closed. Run /settrip to start a new one."
UNAVAILABLE_TEXT = LEDGER_UNAVAILABLE_TEXT


async def resolve_trip_status(deps: Deps, chat_id: int) -> tuple[Trip | None, bool]:
    """`(open_trip, latest_is_closed)`. `open_trip` is the chat's open trip, or
    `None` if there isn't one. When it's `None`, `latest_is_closed` says whether
    that's because the chat's latest trip is closed (`True`) or because the chat
    has never had a trip at all (`False`). Raises `LedgerUnavailable` straight
    through; every caller wraps this in its own try/except.
    """
    trip = await deps.ledger.get_binding(chat_id)
    if trip is not None:
        return trip, False
    trips = await deps.ledger.list_trips(chat_id)
    latest_is_closed = bool(trips) and trips[-1].closed_at is not None
    return None, latest_is_closed


async def require_trip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Trip | None:
    """Reply "Run /settrip first" when this chat has never had a trip, or "This
    trip is closed..." when its latest trip is closed and none is open. Returns
    the open trip, or `None` after replying (or on a `LedgerUnavailable`).
    """
    deps = get_deps(context)
    chat = update.effective_chat
    assert chat is not None
    message = update.effective_message
    try:
        trip, latest_is_closed = await resolve_trip_status(deps, chat.id)
    except LedgerUnavailable:
        logger.exception("require_trip: could not read the current binding")
        if message is not None:
            await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return None
    if trip is None:
        if message is not None:
            text = CLOSED_TRIP_TEXT if latest_is_closed else UNBOUND_TEXT
            await message.reply_text(text, parse_mode="HTML")
        return None
    return trip


async def require_trip_for_expense(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Trip | None:
    """Like `require_trip`, but for a text/photo expense message rather than a
    command: an unbound chat (no trip ever) stays silent, matching today's
    behaviour for plain text (spec §13); a closed trip still gets the one-line
    "This trip is closed..." reply, since the sender went to the trouble of typing
    an expense.
    """
    deps = get_deps(context)
    chat = update.effective_chat
    assert chat is not None
    message = update.effective_message
    try:
        trip, latest_is_closed = await resolve_trip_status(deps, chat.id)
    except LedgerUnavailable:
        logger.exception("require_trip_for_expense: could not read the current binding")
        return None
    if trip is None:
        if latest_is_closed and message is not None:
            await message.reply_text(CLOSED_TRIP_TEXT, parse_mode="HTML")
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
