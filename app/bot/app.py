"""Builds and runs the Telegram Application."""

from __future__ import annotations

import logging
import signal

import anthropic
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
from app.bot.handlers.balances import balances_command, mine_command, settle_command
from app.bot.handlers.basic import help_command, start_command
from app.bot.handlers.callbacks import join_button_callback
from app.bot.handlers.join import UNBOUND_TEXT, join_command, lost_access_text
from app.bot.handlers.photo_expense import PHOTO_EXPENSE_FILTER, photo_expense_handler
from app.bot.handlers.reads import (
    recent_command,
    summary_command,
    today_command,
    undo_callback,
    undo_command,
)
from app.bot.handlers.seen_users import upsert_seen_user
from app.bot.handlers.text_expense import (
    DRAFT_CALLBACK_PATTERN,
    TEXT_EXPENSE_FILTER,
    draft_callback,
    text_expense_handler,
)
from app.bot.session_state import Dedup
from app.config.settings import Settings
from app.services.currencies import FrankfurterCurrencyCatalog
from app.services.drafts import DraftService, InMemoryDraftStore
from app.services.exchange_rates import FrankfurterProvider, RateService
from app.services.google_sheets import SheetsLedgerStore
from app.services.ledger_store import (
    LEDGER_UNAVAILABLE_TEXT,
    LedgerUnavailable,
    TripSheetInaccessible,
)
from app.services.receipt_parser import AnthropicReceiptReader

logger = logging.getLogger(__name__)

SOMETHING_WENT_WRONG_TEXT = "Something went wrong, please try again."

# Commands with their own handler in group 0. Anything else, in a group, falls through
# to the "Run /settrip first" gate below (spec §13, sprint 1 pin "Unbound groups").
_KNOWN_COMMANDS = {
    "start",
    "help",
    "settrip",
    "join",
    "cancel",
    "recent",
    "today",
    "summary",
    "undo",
}

# Pinned explicitly (sprint 3) rather than left to PTB's platform-dependent default, so
# SIGTERM is guaranteed to stop polling and exit within fly.toml's kill_timeout.
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGABRT)


async def _post_init(application: Application) -> None:
    """Log the one startup line once the bot's own identity is known (sprint 3 pin).

    Spec §1: the bot's username is read from getMe at startup, never hardcoded.
    Exactly one INFO line, message `whopaid started`, fields `bot_username` and
    `version`. Nothing else about config goes in this line.
    """
    deps: Deps = application.bot_data["deps"]
    logger.info(
        "whopaid started",
        extra={"bot_username": application.bot.username, "version": deps.bot_version},
    )


async def _post_shutdown(application: Application) -> None:
    """Log the one shutdown line once polling has stopped (sprint 3 pin)."""
    logger.info("whopaid stopped")


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
    except TripSheetInaccessible:
        await message.reply_text(lost_access_text(deps.service_account_email), parse_mode="HTML")
        return
    except LedgerUnavailable:
        logger.exception("unbound gate: could not read the current binding")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return
    if trip is None:
        await message.reply_text(UNBOUND_TEXT, parse_mode="HTML")


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB-wide fallback: log every unhandled error and give the user one line --
    "Couldn't reach Google Sheets" for `LedgerUnavailable`, else "Something went
    wrong" -- instead of leaving them without a reply (spec §14: the bot never
    raises to the user). A button press gets it as a toast. Never logs message text.
    """
    error = context.error
    update_id = update.update_id if isinstance(update, Update) else None
    logger.exception("unhandled error on update %s", update_id, exc_info=error)

    if isinstance(update, Update) and update.callback_query is not None:
        # A button press: a toast, so the button stops spinning.
        try:
            await update.callback_query.answer(
                LEDGER_UNAVAILABLE_TEXT
                if isinstance(error, LedgerUnavailable)
                else SOMETHING_WENT_WRONG_TEXT
            )
        except Exception:
            logger.exception("error handler: could not answer the callback query")
        return
    message = update.effective_message if isinstance(update, Update) else None
    if message is None:
        return
    # Only commands, expense candidates, photos and draft replies reach a handler
    # that can fail here (the seen-users handler catches its own errors), so a
    # reply never answers ordinary chat.
    text = (
        LEDGER_UNAVAILABLE_TEXT
        if isinstance(error, LedgerUnavailable)
        else SOMETHING_WENT_WRONG_TEXT
    )
    try:
        await message.reply_text(text, parse_mode="HTML")
    except Exception:
        logger.exception("error handler: could not notify the user")


def build_application(token: str, deps: Deps) -> Application:
    """Build the PTB Application with every Sprint 1 handler registered."""
    application = (
        ApplicationBuilder()
        .token(token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
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
    application.add_handler(CommandHandler("recent", recent_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("summary", summary_command))
    application.add_handler(CommandHandler("undo", undo_command))
    application.add_handler(CommandHandler("balances", balances_command))
    application.add_handler(CommandHandler("mine", mine_command))
    application.add_handler(CommandHandler("settle", settle_command))
    application.add_handler(CallbackQueryHandler(settrip_callback, pattern=r"^st:"))
    application.add_handler(CallbackQueryHandler(join_button_callback, pattern=r"^join:"))
    application.add_handler(CallbackQueryHandler(undo_callback, pattern=r"^undo:"))
    # A receipt's `Try again` re-reads the photo: non-blocking for the same reason
    # as photo_expense_handler. Registered before the general draft handler, which
    # would otherwise match it first.
    application.add_handler(
        CallbackQueryHandler(
            draft_callback, pattern=DRAFT_CALLBACK_PATTERN + r"retry:", block=False
        )
    )
    application.add_handler(CallbackQueryHandler(draft_callback, pattern=DRAFT_CALLBACK_PATTERN))
    application.add_handler(
        MessageHandler(AwaitingSettripText(deps.settrip_sessions), settrip_text_handler)
    )
    # Text-expense candidates ("1200 yen ramen"): registered after the settrip
    # awaiting-text handler, so a session mid-/settrip still wins (sprint 2 pin).
    application.add_handler(MessageHandler(TEXT_EXPENSE_FILTER, text_expense_handler))
    # Receipt photos (sprint 4 pin, spec §7/§13): every photo in a bound group.
    # Non-blocking: a receipt read takes seconds to tens of seconds, and PTB handles
    # updates one at a time, so a blocking read would stall every other chat.
    application.add_handler(
        MessageHandler(PHOTO_EXPENSE_FILTER, photo_expense_handler, block=False)
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
    ledger = SheetsLedgerStore(
        service_account_info=service_account_info,
        control_sheet_id=settings.whopaid_control_sheet_id,
        bot_version=settings.bot_version,
    )
    rates = RateService(FrankfurterProvider(http_client), ledger)
    anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
    deps = Deps(
        ledger=ledger,
        currencies=FrankfurterCurrencyCatalog(http_client),
        service_account_email=service_account_info["client_email"],
        bot_version=settings.bot_version,
        draft_service=DraftService(ledger, rates, InMemoryDraftStore(), Dedup()),
        receipt_reader=AnthropicReceiptReader(anthropic_client),
    )
    application = build_application(settings.telegram_bot_token.get_secret_value(), deps)
    application.run_polling(
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY],
        stop_signals=STOP_SIGNALS,
    )
