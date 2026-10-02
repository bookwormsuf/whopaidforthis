"""Replies to a draft card that are about that card, not new expenses:

- a custom-split card takes `<name> <amount>` lines (sprint 5 pin, spec §8);
- an item card takes `mine 1,4`, `split 5 everyone`, `unclaim 2` (spec §9).

`text_expense_handler` calls these before parsing a message as a new expense, the
same way it calls `handle_manual_amount_reply`, so `Amir 2000` or `mine 1` sent as a
reply to the card never starts a ¥2,000 or ¥1 expense.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.deps import get_deps
from app.bot.handlers.photo_expense import edit_draft_card
from app.services.drafts import DraftError, DraftToast
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


async def handle_item_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns `True` if the message was a reply to a draft in the `items` state
    (handled, or ignored if it could not apply), `False` otherwise. Any joined user
    may claim this way, as with the buttons.
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
    if draft is None or draft.state != "items":
        return False

    try:
        trip = await deps.ledger.get_binding(chat.id)
    except (TripSheetInaccessible, LedgerUnavailable):
        logger.exception("item_reply: could not read the current binding")
        return True
    if trip is None:
        return True

    try:
        updated = await deps.draft_service.apply_item_command(draft.draft_id, user.id, message.text)
    except DraftToast:
        return True  # not joined: nothing to show without a second message
    except (DraftError, LedgerUnavailable):
        logger.exception("item_reply: draft %s error", draft.draft_id)
        return True

    await edit_draft_card(context, chat.id, updated, trip)
    return True
