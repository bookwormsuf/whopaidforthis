"""Replies to a custom-split card: `<name> <amount>` lines (sprint 5 pin, spec §8).

`text_expense_handler` calls `handle_custom_split_reply` before parsing a message as
a new expense, the same way it calls `handle_manual_amount_reply`, so a line such as
`Amir 2000` sent as a reply to the card sets Amir's amount instead of starting a
¥2,000 expense.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.deps import get_deps
from app.bot.handlers.photo_expense import edit_draft_card
from app.services.drafts import DraftError
from app.services.ledger_store import (
    LEDGER_UNAVAILABLE_TEXT,
    LedgerUnavailable,
    TripSheetInaccessible,
)

logger = logging.getLogger(__name__)


async def handle_custom_split_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns `True` if the message was a reply to a draft in the `custom` state
    and has been handled, `False` otherwise. A reply from anyone but the initiator
    is swallowed silently: it is about this card, so it must not start a new expense,
    and only the initiator may change the split (spec §8).
    """
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or not message.text:
        return False
    if message.reply_to_message is None:
        return False

    deps = get_deps(context)
    draft = await deps.draft_service.find_by_bot_message(
        chat.id, message.reply_to_message.message_id
    )
    if draft is None or draft.state != "custom":
        return False
    if user.id != draft.initiator_id:
        return True

    try:
        trip = await deps.ledger.get_binding(chat.id)
    except (TripSheetInaccessible, LedgerUnavailable):
        logger.exception("custom_split_reply: could not read the current binding")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return True
    if trip is None:
        return True

    try:
        updated = await deps.draft_service.apply_custom_reply(
            draft.draft_id, user.id, trip, message.text
        )
    except DraftError:
        logger.exception("custom_split_reply: draft %s error", draft.draft_id)
        return True

    await edit_draft_card(context, chat.id, updated, trip)
    return True
