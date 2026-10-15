"""Builds and runs the Telegram Application."""

from __future__ import annotations

import logging
import signal
import sys
from typing import Any

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
from app.bot.handlers.balances import (
    balances_command,
    close_trip_callback,
    mine_command,
    settle_command,
)
from app.bot.handlers.basic import help_command, start_command
from app.bot.handlers.callbacks import join_button_callback
from app.bot.handlers.deletetrip import deletetrip_callback, deletetrip_command
from app.bot.handlers.export import export_command
from app.bot.handlers.join import (
    CLOSED_TRIP_TEXT,
    UNBOUND_TEXT,
    join_command,
    resolve_trip_status,
)
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
from app.config.settings import Settings
from app.services.currencies import FrankfurterCurrencyCatalog
from app.services.drafts import DraftService, InMemoryDraftStore
from app.services.exchange_rates import FrankfurterProvider, RateService
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable
from app.services.postgres_store import PostgresLedgerStore
from app.services.receipt_parser import AnthropicReceiptReader

logger = logging.getLogger(__name__)

SOMETHING_WENT_WRONG_TEXT = "Something went wrong, please try again."

# Commands with their own handler in group 0 (registered from this same tuple in
# build_application, so the two can't drift). Anything else, in a group, falls
# through to the "Run /settrip first" gate below (spec §13, sprint 1 pin
# "Unbound groups").
_COMMANDS: tuple[tuple[str, Any, filters.BaseFilter | None], ...] = (
    ("start", start_command, None),
    ("help", help_command, None),
    ("join", join_command, None),
    ("settrip", settrip_command, filters.ChatType.GROUPS),
    ("settrip", settrip_private_command, filters.ChatType.PRIVATE),
    ("cancel", cancel_command, None),
    ("recent", recent_command, None),
    ("today", today_command, None),
    ("summary", summary_command, None),
    ("undo", undo_command, None),
    ("balances", balances_command, None),
    ("mine", mine_command, None),
    ("settle", settle_command, None),
    ("export", export_command, None),
    ("deletetrip", deletetrip_command, None),
)

_KNOWN_COMMANDS = {name for name, _, _ in _COMMANDS}

# Pinned explicitly (sprint 3) rather than left to PTB's platform-dependent default, so
# SIGTERM is guaranteed to stop polling and exit within fly.toml's kill_timeout.
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGABRT)


async def _post_init(application: Application) -> None:
    """Create the database pool and apply migrations, then log the one startup
    line once the bot's own identity is known (sprint 3 pin).

    Spec §18: fails fast, naming DATABASE_URL, if the pool can't be created or a
    migration fails -- same shape as the missing/invalid env var checks in
    `load_settings`. Spec §1: the bot's username is read from getMe at startup,
    never hardcoded. Exactly one INFO line, message `whopaid started`, fields
    `bot_username` and `version`. Nothing else about config goes in this line.
    """
    deps: Deps = application.bot_data["deps"]
    ledger = deps.ledger
    connect = getattr(ledger, "connect", None)
    if connect is not None:
        try:
            await connect()
        except Exception as exc:  # noqa: BLE001 - this is the fail-fast boundary
            print(
                f"Could not reach the database ({type(exc).__name__}). Check DATABASE_URL.",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc
    logger.info(
        "whopaid started",
        extra={"bot_username": application.bot.username, "version": deps.bot_version},
    )


async def _post_shutdown(application: Application) -> None:
    """Log the one shutdown line once polling has stopped (sprint 3 pin)."""
    logger.info("whopaid stopped")


async def _unbound_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fallback for commands with no dedicated handler yet: gates unbound groups
    ("Run /settrip first") and, since sprint 9 item 3, groups whose latest trip is
    closed ("This trip is closed..."). Every known command (including /export,
    /settrip, /help, /start, which are exempt from the closed-trip gate) has its
    own handler and never reaches here.
    """
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
        trip, latest_is_closed = await resolve_trip_status(deps, chat.id)
    except LedgerUnavailable:
        logger.exception("unbound gate: could not read the current binding")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return
    if trip is None:
        text = CLOSED_TRIP_TEXT if latest_is_closed else UNBOUND_TEXT
        await message.reply_text(text, parse_mode="HTML")


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB-wide fallback: log every unhandled error and give the user one line --
    "Couldn't reach the database" for `LedgerUnavailable`, else "Something went
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

    for name, callback, cmd_filters in _COMMANDS:
        if cmd_filters is None:
            application.add_handler(CommandHandler(name, callback))
        else:
            application.add_handler(CommandHandler(name, callback, filters=cmd_filters))
    application.add_handler(CallbackQueryHandler(settrip_callback, pattern=r"^st:"))
    application.add_handler(CallbackQueryHandler(join_button_callback, pattern=r"^join:"))
    application.add_handler(CallbackQueryHandler(undo_callback, pattern=r"^undo:"))
    application.add_handler(CallbackQueryHandler(close_trip_callback, pattern=r"^closetrip:"))
    application.add_handler(CallbackQueryHandler(deletetrip_callback, pattern=r"^deletetrip:"))
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
    """Build production dependencies and the application, then run it with long
    polling. The database pool is created and migrated in `_post_init` (it must run
    inside the application's own event loop: an `asyncpg` pool is bound to the loop
    it was created in), so a bad or unreachable `DATABASE_URL` fails fast there,
    before polling starts.
    """
    http_client = httpx.AsyncClient()
    ledger = PostgresLedgerStore(settings.database_url.get_secret_value())
    rates = RateService(FrankfurterProvider(http_client), ledger)
    anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
    deps = Deps(
        ledger=ledger,
        currencies=FrankfurterCurrencyCatalog(http_client),
        bot_version=settings.bot_version,
        draft_service=DraftService(ledger, rates, InMemoryDraftStore()),
        receipt_reader=AnthropicReceiptReader(anthropic_client),
    )
    application = build_application(settings.telegram_bot_token.get_secret_value(), deps)
    application.run_polling(
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY],
        stop_signals=STOP_SIGNALS,
    )
