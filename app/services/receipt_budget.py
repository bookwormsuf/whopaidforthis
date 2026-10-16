"""`ReceiptReadBudget`: a daily cap on receipt reads, per chat and across all chats.

Spec references: pinned-decisions §20; sprint 12 pins. Pure Python, no Telegram
imports: handlers call `try_take`/`remaining` with an aware `now` they already have,
this class never reads the clock itself. Counters live in memory for the process's
lifetime and reset to zero on restart (accepted, spec §20); within a run they reset
when the calendar day in `Asia/Singapore` rolls over, not on a rolling window.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

_SINGAPORE = ZoneInfo("Asia/Singapore")


class _Result:
    """A small sentinel: `Allowed`, `ChatCapped` or `GlobalCapped` below are the only
    instances. Compare with `is`, not `==`.
    """

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:
        return self._name


Allowed = _Result("Allowed")
ChatCapped = _Result("ChatCapped")
GlobalCapped = _Result("GlobalCapped")


class ReceiptReadBudget:
    """Holds the Singapore calendar date the current counts belong to, a global
    count and a `{chat_id: count}` map. `chat_cap` and `global_cap` are the daily
    limits (0 refuses every read).
    """

    def __init__(self, chat_cap: int = 40, global_cap: int = 400) -> None:
        self.chat_cap = chat_cap
        self.global_cap = global_cap
        self._day: date | None = None
        self._global_count = 0
        self._chat_counts: dict[int, int] = {}

    def _roll(self, now: datetime) -> None:
        """Discards both counts when the Singapore date has moved on from the one
        the current counts belong to (including the first call, when `_day` is
        still unset).
        """
        today = now.astimezone(_SINGAPORE).date()
        if today != self._day:
            self._day = today
            self._global_count = 0
            self._chat_counts = {}

    def try_take(self, chat_id: int, now: datetime) -> _Result:
        """Consumes one receipt read for `chat_id`, or refuses it. Global cap is
        checked before the chat cap (spec §20): when both are hit, `GlobalCapped`
        wins. A refusal never counts.
        """
        self._roll(now)
        if self._global_count >= self.global_cap:
            return GlobalCapped
        if self._chat_counts.get(chat_id, 0) >= self.chat_cap:
            return ChatCapped
        self._global_count += 1
        self._chat_counts[chat_id] = self._chat_counts.get(chat_id, 0) + 1
        return Allowed

    def remaining(self, chat_id: int, now: datetime) -> int:
        """How many more reads this chat can take today, never negative."""
        self._roll(now)
        return max(self.chat_cap - self._chat_counts.get(chat_id, 0), 0)
