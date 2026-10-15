"""`/export`: sends one `.xlsx` of a trip's records. Spec references:
docs/sprints/09-neon-store-and-export.md ("Export"), pinned-decisions §18.

Anyone in the group may export, for the open trip or, if the group has none open,
its most recently closed trip. A group with no trip at all gets the same one-line
reply as the unbound-group gate (`app.bot.handlers.join.UNBOUND_TEXT`) -- `/export`
has no special-case message of its own for that.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from telegram import InputFile, Update
from telegram.error import TimedOut
from telegram.ext import ContextTypes

from app.bot.deps import get_deps
from app.bot.handlers.join import UNBOUND_TEXT
from app.models.trip import Trip
from app.services.export import build_workbook, slugify
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable

UPLOAD_TIMEOUT_S = 60
UPLOAD_FAILED_TEXT = "Couldn't send the file, please try /export again."

logger = logging.getLogger(__name__)


async def _pick_trip(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> Trip | None:
    """The open trip if there is one, else the most recently closed trip of this
    chat, else `None` (no trip at all).
    """
    deps = get_deps(context)
    return await deps.ledger.latest_trip(chat_id)


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    assert chat is not None and message is not None

    deps = get_deps(context)
    try:
        trip = await _pick_trip(context, chat.id)
    except LedgerUnavailable:
        logger.exception("export: could not read the current binding")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return
    if trip is None:
        await message.reply_text(UNBOUND_TEXT, parse_mode="HTML")
        return

    try:
        # No `chat_id` filter: a trip belongs to exactly one chat, so every row
        # a trip id scopes to is already this chat's (sprint 9 review item 8).
        users = await deps.ledger.list_users(trip)
        expenses = await deps.ledger.list_expenses(trip)
        splits = await deps.ledger.list_splits(trip)
        line_items = await deps.ledger.list_line_items(trip)
    except LedgerUnavailable:
        logger.exception("export: could not read the ledger")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return

    workbook = build_workbook(trip, users, expenses, splits, line_items)
    today = datetime.now(UTC).astimezone(ZoneInfo(trip.timezone)).date()
    filename = f"whopaid-{slugify(trip.name)}-{today.isoformat()}.xlsx"
    count = len(expenses)
    caption = f"{trip.name} · {count} expense{'s' if count != 1 else ''}"

    # Uploading a file takes longer than a text reply; PTB's default write timeout is
    # tight enough that a slow link times out on the first try. Allow more, then retry once.
    for attempt in (1, 2):
        try:
            await message.reply_document(
                document=InputFile(workbook, filename=filename),
                caption=caption,
                read_timeout=UPLOAD_TIMEOUT_S,
                write_timeout=UPLOAD_TIMEOUT_S,
            )
            return
        except TimedOut:
            if attempt == 2:
                logger.warning("export: upload timed out twice")
                await message.reply_text(UPLOAD_FAILED_TEXT, parse_mode="HTML")
                return
            logger.info("export: upload timed out, retrying once")
