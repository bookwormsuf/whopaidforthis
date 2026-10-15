"""Handlers for /start and /help.

Sprint 10 (docs/sprints/10-start-menu.md, pinned-decisions §19, amended
2026-10-15 after review): in a group, `/start` posts the welcome menu (both
on being added and when typed); `/help` in a group posts the help text with
the menu underneath it, not pinned. In private chat both still show the help
text, now with an `Add me to a group` URL button.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.deps import get_deps
from app.bot.handlers.join import resolve_trip_status
from app.bot.handlers.menu import _state_for, menu_keyboard, post_welcome, private_start_keyboard
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "<b>whopaid</b> — records trip expenses for the group.\n"
    "Say what you spent, and it converts it to your home currency at the "
    "day's rate, splits the receipt between whoever was there, and tracks "
    "who owes whom.\n\n"
    "You can also just type an amount and a description, like "
    "<code>1200 yen ramen</code>, or send a photo of a receipt. Add "
    "<code>split everyone</code>, <code>split between me Amir</code> or "
    "<code>paid by Amir</code> to share it.\n\n"
    "<b>Commands</b>\n"
    "/join — join this trip\n"
    "/settrip — start a trip for this group\n"
    "/recent — last 5 expenses\n"
    "/today — today's expenses\n"
    "/summary — group totals\n"
    "/mine — what you paid and owe\n"
    "/balances — everyone's balances\n"
    "/settle — who pays whom\n"
    "/undo — cancel your last expense\n"
    "/export — download this trip's records\n"
    "/deletetrip — delete this trip and its expenses\n"
    "/help — this message"
)


def _help_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    """HELP_TEXT with the running build's version appended (sprint 3 pin)."""
    version = get_deps(context).bot_version
    return f"{HELP_TEXT}\n\nwhopaid {version}"


async def _private_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the help text with the `Add me to a group` button (sprint 10 pin)."""
    await update.message.reply_text(
        _help_text(context),
        parse_mode="HTML",
        reply_markup=private_start_keyboard(context.bot.username),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """In a group, posts the help text with the menu underneath it, not pinned
    (pinned-decisions §19, amended 2026-10-15 after review). In private, replies
    with the one-screen summary of what the bot does.
    """
    chat = update.effective_chat
    if chat is not None and chat.type in ("group", "supergroup"):
        deps = get_deps(context)
        try:
            trip, latest_is_closed = await resolve_trip_status(deps, chat.id)
        except LedgerUnavailable:
            logger.exception("help_command: could not read the current binding")
            await context.bot.send_message(chat.id, LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
            return
        state = _state_for(trip, latest_is_closed)
        await context.bot.send_message(
            chat.id,
            _help_text(context),
            parse_mode="HTML",
            reply_markup=menu_keyboard(state, 1),
        )
        return
    await _private_help(update, context)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """In a group, posts the welcome menu; /start no longer behaves as /join
    (sprint 10 pin, superseding spec §12). In private, it shows help.

    Telegram's `?startgroup=true` "Add me to a group" link adds the bot (which
    already triggers the welcome via `bot_added_to_group`) and then immediately
    sends `/start@bot true` into the group; a non-empty `context.args` here
    means this is that follow-up message, so skip to avoid a double welcome
    (review fix).
    """
    chat = update.effective_chat
    if chat is not None and chat.type in ("group", "supergroup"):
        if context.args:
            return
        try:
            await post_welcome(get_deps(context), context.bot, chat)
        except Exception:  # noqa: BLE001 - a failed welcome must not crash the handler
            logger.exception("could not post the welcome message")
        return
    await _private_help(update, context)
