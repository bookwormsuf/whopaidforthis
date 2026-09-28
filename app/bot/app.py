"""Builds and runs the Telegram Application."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler

from app.bot.handlers.basic import help_command, start_command
from app.config.settings import Settings

logger = logging.getLogger(__name__)


async def _post_init(application: Application) -> None:
    """Log a startup line once the bot's own identity is known.

    Spec §1: the bot's username is read from getMe at startup, never hardcoded.
    """
    logger.info("bot started as @%s", application.bot.username)


def build_application(token: str) -> Application:
    """Build the PTB Application with the Sprint 0 command handlers registered."""
    application = ApplicationBuilder().token(token).post_init(_post_init).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    return application


def run(settings: Settings) -> None:
    """Build the application and run it with long polling."""
    application = build_application(settings.telegram_bot_token.get_secret_value())
    application.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY])
