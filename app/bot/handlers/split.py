"""Replies to a draft card that are about that card, not new expenses:

- a custom-split card takes `<name> <amount>` lines (sprint 5 pin, spec §8);
- a reply to an item screen is swallowed so it never becomes a new expense; items
  are assigned by buttons only (sprint 13);
- `<name> <amount>` lines sent without replying go to the sender's open custom card;
- a reply to a card whose draft is gone (expired, or the bot restarted) gets an
  "expired" note rather than becoming a new expense (both fixed 2026-11-01).

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
from app.services.drafts import DraftError
from app.services.ledger_store import (
    LEDGER_UNAVAILABLE_TEXT,
    LedgerUnavailable,
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
    except LedgerUnavailable:
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


EXPIRED_CARD_TEXT = (
    "That card has expired. Unfinished expenses are cleared after 30 minutes, or when "
    "the bot is updated. Please send the expense again."
)


async def handle_unreplied_custom_lines(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """`True` if a message sent without replying was `<name> <amount>` lines for the
    sender's open custom card, and they were applied to it.
    """
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or not message.text:
        return False
    if message.reply_to_message is not None:
        return False
    deps = get_deps(context)
    draft = await deps.draft_service.open_custom_card(chat.id, user.id)
    if draft is None:  # the usual case: no store read for every group message
        return False
    try:
        trip = await deps.ledger.get_binding(chat.id)
    except LedgerUnavailable:
        return False  # read as usual, which reports the outage
    if trip is None or not deps.draft_service.custom_lines_fit(draft, trip, message.text):
        return False
    try:
        updated = await deps.draft_service.apply_custom_reply(
            draft.draft_id, user.id, trip, message.text
        )
    except DraftError:
        logger.exception("custom lines: draft %s error", draft.draft_id)
        return False
    await edit_draft_card(context, chat.id, updated, trip)
    return True


async def handle_expired_card_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """`True` if the message replies to one of the bot's cards that still shows
    buttons but whose draft is gone; the sender is told it expired. Such a reply is
    about that card (custom amounts, typically), never a new expense.
    """
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.text:
        return False
    replied = message.reply_to_message
    if replied is None:
        return False
    sender = getattr(replied, "from_user", None)
    markup = getattr(replied, "reply_markup", None)
    if sender is None or sender.id != context.bot.id:
        return False
    if markup is None or not getattr(markup, "inline_keyboard", None):
        return False
    deps = get_deps(context)
    if await deps.draft_service.find_by_bot_message(chat.id, replied.message_id) is not None:
        return False
    await message.reply_text(EXPIRED_CARD_TEXT)
    return True


async def handle_item_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns `True` if the message was a reply to a draft on an item screen
    (`item_walk` or `fix_list`), so it is not read as a new expense; it is otherwise
    ignored, since items are assigned by buttons only (sprint 13 pin). `False`
    otherwise.
    """
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.text:
        return False
    if message.reply_to_message is None:
        return False
    deps = get_deps(context)
    draft = await deps.draft_service.find_by_bot_message(
        chat.id, message.reply_to_message.message_id
    )
    return draft is not None and draft.state in ("item_walk", "fix_list")
