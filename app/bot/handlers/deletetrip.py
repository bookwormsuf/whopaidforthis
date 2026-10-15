"""`/deletetrip`: hard-deletes a trip and every expense in it.

Spec references: docs/sprints/09-neon-store-and-export.md item 4. Only the trip's
`created_by` -- the open trip if there is one, else the chat's most recently closed
trip -- may run it or press its confirm button. Deliberately does not go through
`require_trip`'s closed-trip gate (join.py): the whole point is to let the starter
delete a closed trip, so it looks the trip up itself instead.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.deps import get_deps
from app.bot.handlers.join import UNBOUND_TEXT
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable

logger = logging.getLogger(__name__)

NOT_ALLOWED_TEXT = "Only the person who started this trip can delete it."
CONFIRM_TEXT = "Delete this trip and every expense in it? This cannot be undone."
DELETED_TEXT = "Deleted. Run /settrip to start a new trip."
KEPT_TEXT = "Kept."


def _confirm_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Delete everything", callback_data=f"deletetrip:{trip_id}:yes"
                ),
                InlineKeyboardButton("Keep", callback_data=f"deletetrip:{trip_id}:keep"),
            ]
        ]
    )


async def _target_trip(deps, chat_id: int):
    """The chat's open trip, or its latest trip if none is open, or `None` if the
    chat has never had a trip at all.
    """
    trips = await deps.ledger.list_trips(chat_id)
    if not trips:
        return None
    open_trip = next((t for t in trips if t.closed_at is None), None)
    return open_trip if open_trip is not None else trips[-1]


async def deletetrip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    assert message is not None and chat is not None and user is not None

    deps = get_deps(context)
    try:
        trip = await _target_trip(deps, chat.id)
    except LedgerUnavailable:
        logger.exception("deletetrip: could not read trips")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return
    if trip is None:
        await message.reply_text(UNBOUND_TEXT, parse_mode="HTML")
        return
    if user.id != trip.created_by:
        await message.reply_text(NOT_ALLOWED_TEXT, parse_mode="HTML")
        return

    assert trip.id is not None
    await message.reply_text(
        CONFIRM_TEXT, parse_mode="HTML", reply_markup=_confirm_keyboard(trip.id)
    )


async def deletetrip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`deletetrip:{trip_id}:{yes|keep}`."""
    query = update.callback_query
    assert query is not None
    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "deletetrip":
        await query.answer()
        return
    try:
        trip_id = int(parts[1])
    except ValueError:
        await query.answer()
        return
    action = parts[2]

    deps = get_deps(context)
    presser = query.from_user
    chat = update.effective_chat
    if presser is None or chat is None:
        await query.answer()
        return

    try:
        trips = await deps.ledger.list_trips(chat.id)
    except LedgerUnavailable:
        logger.exception("deletetrip_callback: could not read trips")
        await query.answer(LEDGER_UNAVAILABLE_TEXT)
        return
    trip = next((t for t in trips if t.id == trip_id), None)
    if trip is None:
        await query.answer()
        return

    if presser.id != trip.created_by:
        await query.answer(NOT_ALLOWED_TEXT)
        return

    if action == "keep":
        await query.answer()
        await query.edit_message_text(KEPT_TEXT, parse_mode="HTML")
        return

    if action == "yes":
        try:
            await deps.ledger.delete_trips(chat.id)
        except LedgerUnavailable:
            logger.exception("deletetrip_callback: could not delete trips")
            await query.answer(LEDGER_UNAVAILABLE_TEXT)
            return
        await query.answer()
        await query.edit_message_text(DELETED_TEXT, parse_mode="HTML")
        return

    await query.answer()
