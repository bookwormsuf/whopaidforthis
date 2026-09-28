"""Builds and runs the Telegram Application."""

from __future__ import annotations

import logging

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from app.bot.conversations.settrip import (
    AwaitingSettripText,
    cancel_command,
    settrip_callback,
    settrip_command,
    settrip_private_command,
    settrip_text_handler,
)
from app.bot.deps import Deps, get_deps
from app.bot.handlers.basic import help_command, start_command
from app.bot.handlers.join import UNBOUND_TEXT, join_command
from app.bot.handlers.seen_users import upsert_seen_user
from app.config.settings import Settings
from app.services.currencies import FrankfurterCurrencyCatalog
from app.services.google_sheets import SheetsLedgerStore
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable

logger = logging.getLogger(__name__)

# Commands with their own handler in group 0. Anything else, in a group, falls through
# to the "Run /settrip first" gate below (spec §13, sprint 1 pin "Unbound groups").
_KNOWN_COMMANDS = {"start", "help", "settrip", "join", "cancel"}

BOT_VERSION = "0.1.0"


async def _post_init(application: Application) -> None:
    """Log a startup line once the bot's own identity is known.

    Spec §1: the bot's username is read from getMe at startup, never hardcoded.
    """
    logger.info("bot started as @%s", application.bot.username)


async def _unbound_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fallback for commands with no dedicated handler yet: gate unbound groups."""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or chat.type not in ("group", "supergroup"):
        return
    text = message.text or ""
    command = text.split()[0].lstrip("/").split("@")[0].lower() if text else ""
    if command in _KNOWN_COMMANDS:
        return
    deps = get_deps(context)
    try:
        trip = await deps.ledger.get_binding(chat.id)
    except LedgerUnavailable:
        logger.exception("unbound gate: could not read the current binding")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return
    if trip is None:
        await message.reply_text(UNBOUND_TEXT, parse_mode="HTML")


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB-wide fallback: log every unhandled error, and give the user one line for
    LedgerUnavailable instead of leaving them without a reply (spec §14: the bot never
    raises to the user). Never logs message text.
    """
    error = context.error
    update_id = update.update_id if isinstance(update, Update) else None
    logger.exception("unhandled error on update %s", update_id, exc_info=error)

    if not isinstance(error, LedgerUnavailable):
        return
    message = update.effective_message if isinstance(update, Update) else None
    if message is None:
        return
    try:
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
    except Exception:
        logger.exception("error handler: could not notify user of LedgerUnavailable")


def build_application(token: str, deps: Deps) -> Application:
    """Build the PTB Application with every Sprint 1 handler registered."""
    application = ApplicationBuilder().token(token).post_init(_post_init).build()
    application.bot_data["deps"] = deps
    application.add_error_handler(_error_handler)

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("join", join_command))
    application.add_handler(
        CommandHandler("settrip", settrip_command, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("settrip", settrip_private_command, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(settrip_callback, pattern=r"^st:"))
    application.add_handler(
        MessageHandler(AwaitingSettripText(deps.settrip_sessions), settrip_text_handler)
    )

    # Fallback gate for any other command in an unbound group (spec §13).
    application.add_handler(MessageHandler(filters.COMMAND, _unbound_gate), group=1)

    # Seen-user upsert: never blocks, never replaces another handler.
    application.add_handler(TypeHandler(Update, upsert_seen_user, block=False), group=-1)

    return application


def run(settings: Settings) -> None:
    """Build production dependencies and the application, then run it with long polling."""
    service_account_info = settings.service_account_info()
    http_client = httpx.AsyncClient()
    deps = Deps(
        ledger=SheetsLedgerStore(
            service_account_info=service_account_info,
            control_sheet_id=settings.whopaid_control_sheet_id,
            bot_version=BOT_VERSION,
        ),
        currencies=FrankfurterCurrencyCatalog(http_client),
        service_account_email=service_account_info["client_email"],
        bot_version=BOT_VERSION,
    )
    application = build_application(settings.telegram_bot_token.get_secret_value(), deps)
    application.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY])
