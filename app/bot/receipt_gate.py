"""The receipt-read budget gate: Telegram-facing text and plumbing around
`app.services.receipt_budget` (which stays free of `Deps`/Telegram imports).

Spec references: pinned-decisions §20; sprint 12 pins.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from app.bot.deps import Deps
from app.services.receipt_budget import Allowed, ChatCapped, GlobalCapped

logger = logging.getLogger(__name__)

# Sprint 12 pins (docs/sprints/12-receipt-read-budget.md): verbatim refusal and
# warning texts for the daily receipt-read budget.
CHAT_CAPPED_TEXT = (
    "This chat has hit today's limit for receipt photos. Type the amount instead, "
    "like 1200 yen ramen."
)
GLOBAL_CAPPED_TEXT = (
    "Today's limit for receipt photos across all groups has been reached. Type the "
    "amount instead, like 1200 yen ramen."
)
FIVE_LEFT_WARNING_TEXT = (
    "Heads up: 5 receipt photos left in this chat today. Text expenses are unlimited."
)


def take_receipt_read(deps: Deps, chat_id: int, now: datetime) -> tuple[str | None, bool]:
    """Consumes one receipt read against the daily budget (spec §20, sprint 12),
    for a caller that has already passed the trip gate and is about to download
    the photo. Global cap is checked before the chat cap.

    Returns `(refusal_text, warn_five_left)`. `refusal_text` is the verbatim reply
    to send instead of reading the photo, or `None` when the read is allowed.
    `warn_five_left` is `True` on the one allowed attempt per chat per day that
    leaves exactly five reads remaining in that chat (never set alongside a
    refusal, and never when the chat's cap is five or less, since `remaining`
    then never equals five after a take).
    """
    result = deps.receipt_budget.try_take(chat_id, now)
    if result is GlobalCapped:
        logger.warning("receipt read refused: chat=%s cap=global", chat_id)
        return GLOBAL_CAPPED_TEXT, False
    if result is ChatCapped:
        logger.warning("receipt read refused: chat=%s cap=chat", chat_id)
        return CHAT_CAPPED_TEXT, False
    assert result is Allowed
    return None, deps.receipt_budget.remaining(chat_id, now) == 5


async def send_five_left_warning(
    reply: Callable[[str], Awaitable[object]], warn_five_left: bool
) -> None:
    """Sends the once-a-day five-left warning as its own message, if `warn_five_left`
    is set. `reply` is a bound `message.reply_text` (or `query.message.reply_text`).
    """
    if warn_five_left:
        await reply(FIVE_LEFT_WARNING_TEXT)
