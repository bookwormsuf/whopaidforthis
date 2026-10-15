"""Handlers for /start and /help.

Sprint 10 (docs/sprints/10-start-menu.md, pinned-decisions §19): in a group,
both commands now post the welcome menu instead of /help's old text or (for
/start) joining the sender; /start in a group no longer behaves as /join. In
private chat they still show the help text, now with an `Add me to a group`
URL button.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.deps import get_deps
from app.bot.handlers.menu import post_welcome, private_start_keyboard

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


async def _group_welcome_or_private_help(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """In a group, posts the welcome menu. In private, shows the help text with
    the `Add me to a group` button (sprint 10 pin).
    """
    chat = update.effective_chat
    if chat is not None and chat.type in ("group", "supergroup"):
        try:
            await post_welcome(get_deps(context), context.bot, chat)
        except Exception:  # noqa: BLE001 - a failed welcome must not crash the handler
            logger.exception("could not post the welcome message")
        return
    await update.message.reply_text(
        _help_text(context),
        parse_mode="HTML",
        reply_markup=private_start_keyboard(context.bot.username),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """In a group, posts the welcome menu (sprint 10 pin). In private, replies
    with the one-screen summary of what the bot does.
    """
    await _group_welcome_or_private_help(update, context)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """In a group, posts the welcome menu; /start no longer behaves as /join
    (sprint 10 pin, superseding spec §12). In private, it shows help.
    """
    await _group_welcome_or_private_help(update, context)
