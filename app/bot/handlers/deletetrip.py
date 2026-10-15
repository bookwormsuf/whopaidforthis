"""`/deletetrip`: hard-deletes a trip and every expense in it.

Spec references: docs/sprints/09-neon-store-and-export.md item 4. Only the trip's
`created_by` -- the open trip if there is one, else the chat's most recently closed
trip -- may run it or press its confirm button. Deliberately does not go through
`require_trip`'s closed-trip gate (join.py): the whole point is to let the starter
delete a closed trip, so it looks the trip up itself instead.
"""

from __future__ import annotations

import logging

from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.ext import ContextTypes

from app.bot.deps import Deps, get_deps
from app.bot.handlers.join import UNBOUND_TEXT
from app.bot.send import SendFn, build_send
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable

logger = logging.getLogger(__name__)

NOT_ALLOWED_TEXT = "Only the person who started this trip can delete it."
CONFIRM_TEXT = "Delete this trip and every expense in it? This cannot be undone."
DELETED_TEXT = "Deleted. Run /settrip to start a new trip."
KEPT_TEXT = "Kept."
STALE_BUTTON_TEXT = "This button is out of date. Run /deletetrip again."


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
    return await deps.ledger.latest_trip(chat_id)


async def run_deletetrip(deps: Deps, chat: Chat, user: User, send: SendFn) -> None:
    try:
        trip = await _target_trip(deps, chat.id)
    except LedgerUnavailable:
        logger.exception("deletetrip: could not read trips")
        await send(LEDGER_UNAVAILABLE_TEXT)
        return
    if trip is None:
        await send(UNBOUND_TEXT)
        return
    if user.id != trip.created_by:
        await send(NOT_ALLOWED_TEXT)
        return

    assert trip.id is not None
    await send(CONFIRM_TEXT, reply_markup=_confirm_keyboard(trip.id))


async def deletetrip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    assert message is not None and chat is not None and user is not None
    await run_deletetrip(get_deps(context), chat, user, build_send(message))


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
        trip = await _target_trip(deps, chat.id)
    except LedgerUnavailable:
        logger.exception("deletetrip_callback: could not read trips")
        await query.answer(LEDGER_UNAVAILABLE_TEXT)
        return
    if trip is None:
        await query.answer()
        return

    # The button only makes sense for the chat's *current* target trip (the open
    # one, else the latest). If another trip was started or deleted since the
    # button was sent, its trip_id no longer matches -- never delete the wrong
    # trip (sprint 9 review item 2).
    if trip.id != trip_id:
        await query.answer(STALE_BUTTON_TEXT)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001 - editing an already-changed message is not fatal
            logger.debug("deletetrip_callback: could not remove the stale keyboard")
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
