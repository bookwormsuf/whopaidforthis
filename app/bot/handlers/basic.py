"""Handlers for /start and /help."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.handlers.join import join_command

HELP_TEXT = (
    "<b>whopaid</b> — records trip expenses into a shared Google Sheet.\n"
    "Say what you spent, and it converts it to your home currency at the "
    "day's rate, splits the receipt between whoever was there, and tracks "
    "who owes whom.\n\n"
    "<b>Commands</b>\n"
    "/join — join this trip\n"
    "/settrip — bind this group to a Google Sheet\n"
    "/recent — last 5 expenses\n"
    "/today — today's expenses\n"
    "/summary — group totals\n"
    "/mine — what you paid and owe\n"
    "/balances — everyone's balances\n"
    "/settle — who pays whom\n"
    "/undo — cancel your last expense\n"
    "/help — this message\n\n"
    "You can also just type an amount and a description, like "
    "<code>1200 yen ramen</code>, or send a photo of a receipt.\n\n"
    "Only /join, /settrip, /cancel, /start and /help work so far — the rest is coming."
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with the one-screen summary of what the bot does."""
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """In a group, /start behaves as /join (spec §12). In private, it shows help."""
    chat = update.effective_chat
    if chat is not None and chat.type in ("group", "supergroup"):
        await join_command(update, context)
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")
