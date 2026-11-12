"""Feedback helpers: the per-user daily counter, text validation and the message the
owner receives. Spec §21, sprint 16 pins.

Pure Python, no Telegram imports. Like `ReceiptReadBudget`, the counter never reads the
clock: callers pass an aware `now`. Counts live in memory and reset on restart
(accepted, spec §21); within a run they reset when the calendar day in `Asia/Singapore`
rolls over. Feedback text is never stored here and never logged.
"""

from __future__ import annotations

from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo

_SINGAPORE = ZoneInfo("Asia/Singapore")

MAX_FEEDBACK_CHARS = 1000
MAX_FEEDBACK_PER_DAY = 5


class FeedbackLimiter:
    """Delivered feedback messages per user for the current Singapore calendar day.

    Two steps, because only a message actually delivered to the owner counts:
    `is_allowed` before sending, `record` after the send succeeded.
    """

    def __init__(self, per_day: int = MAX_FEEDBACK_PER_DAY) -> None:
        self.per_day = per_day
        self._day: date | None = None
        self._counts: dict[int, int] = {}

    def _roll(self, now: datetime) -> None:
        today = now.astimezone(_SINGAPORE).date()
        if today != self._day:
            self._day = today
            self._counts = {}

    def is_allowed(self, user_id: int, now: datetime) -> bool:
        self._roll(now)
        return self._counts.get(user_id, 0) < self.per_day

    def record(self, user_id: int, now: datetime) -> None:
        self._roll(now)
        self._counts[user_id] = self._counts.get(user_id, 0) + 1


def is_empty(text: str) -> bool:
    return not text.strip()


def is_too_long(text: str) -> bool:
    return len(text) > MAX_FEEDBACK_CHARS


def format_owner_message(
    *, first_name: str, username: str | None, text: str, from_group: bool
) -> str:
    """The one message the owner receives (HTML; names and text escaped). Carries no
    group name, chat ID, user ID or trip data.
    """
    origin = "from a group" if from_group else "from a DM"
    who = escape(first_name)
    if username:
        who += f" (@{escape(username)})"
    return f"💬 <b>Feedback</b> · {origin}\n{who}\n\n{escape(text)}"
