"""Upserts any user seen in a bound chat, without marking them as joined.

Registered as a `TypeHandler(Update, ...)` in group -1 with `block=False`, so it never
blocks or replaces other handlers. Spec: sprint 1 pins, "Seen-user upsert". Must never
raise to the dispatcher and must never log message text.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.deps import get_deps
from app.models.user import TripUser
from app.services.ledger_store import LedgerUnavailable

logger = logging.getLogger(__name__)


async def upsert_seen_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        chat = update.effective_chat
        user = update.effective_user
        if chat is None or user is None or chat.type not in ("group", "supergroup"):
            return
        now = datetime.now(UTC)
        deps = get_deps(context)
        if not deps.seen_users.should_upsert(chat.id, user.id, now):
            return

        try:
            trip = await deps.ledger.get_binding(chat.id)
        except LedgerUnavailable as exc:
            # Expected operational state (backend hiccup), not a bug: log at INFO
            # with no traceback, so this doesn't spam WARNING on every message in
            # an affected chat.
            logger.info("seen-user upsert skipped: %s", type(exc).__name__)
            return
        if trip is None:
            return

        tg_user = TripUser.from_telegram_user(user, now)
        await deps.ledger.upsert_user(trip, tg_user, joined=None)
        deps.seen_users.mark(chat.id, user.id, now)
    except Exception:
        logger.warning("seen-user upsert failed", exc_info=True)
