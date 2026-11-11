"""The gate in front of a one-off bill's receipt read. Spec §23, sprint 18 (amended at
review). It owns the sequence and returns verdicts; the handler only shows cards.

Beside `app/bot/receipt_gate.py` (the group's budget gate). No Telegram objects here, so
the rules are tested without any.

The sequence for one attempt (a new photo or `Try again`):

1. `begin`, synchronously up to its first `await`: is this user already having a read
   run? Then `Refusal("busy")`. Otherwise the user is marked in flight, before any
   `await`, so two photos or two taps arriving together cannot both pass. Then the user's
   allowance: refused -> the mark is removed and the refusal returned.
2. The caller marks the bill reading, sends the notice and `Reading the receipt…`,
   downloads and prepares the image. None of this counts anywhere.
3. `take_cap`, immediately before the model call: one from the bot-wide cap.
4. The model call, then `record`: success or failure of the user's allowance.
5. `release`, in a `finally`, whatever happened.

Only an attempt that reaches the model counts, on the cap and on the allowance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.services.photo_allowance import (
    Allowed,
    AlreadyRead,
    InFlightReads,
    PhotoAllowance,
)
from app.services.receipt_budget import GlobalCapped, ReceiptReadBudget

RefusalKind = Literal["busy", "user", "tries", "global"]


@dataclass(frozen=True)
class Refusal:
    """Why an attempt was not made. `busy`: this user already has a read running.
    `user`: already had today's read. `tries`: three failed attempts today. `global`:
    the bot-wide cap is reached.
    """

    kind: RefusalKind


@dataclass
class Ticket:
    """An accepted attempt. Hand it back to `take_cap`, `record` and `release`."""

    user_id: int
    is_owner: bool
    released: bool = False


class ReceiptGate:
    def __init__(
        self,
        *,
        allowance: PhotoAllowance,
        budget: ReceiptReadBudget,
        in_flight: InFlightReads,
        owner_user_id: int | None,
    ) -> None:
        self._allowance = allowance
        self._budget = budget
        self._in_flight = in_flight
        self._owner_user_id = owner_user_id

    async def begin(self, user_id: int, now: datetime) -> Ticket | Refusal:
        # Everything up to the allowance's `await` is synchronous: the in-flight mark is
        # set before this coroutine can yield.
        if not self._in_flight.try_enter(user_id):
            return Refusal("busy")
        is_owner = self._owner_user_id is not None and user_id == self._owner_user_id
        try:
            verdict = await self._allowance.may_try(user_id, now, is_owner=is_owner)
        except BaseException:
            self._in_flight.leave(user_id)
            raise
        if verdict is Allowed:
            return Ticket(user_id, is_owner)
        self._in_flight.leave(user_id)
        return Refusal("user" if verdict is AlreadyRead else "tries")

    def take_cap(self, ticket: Ticket, now: datetime) -> Refusal | None:
        """One from the bot-wide cap, immediately before the model call. `None` when
        taken; a refusal takes nothing.
        """
        if self._budget.try_take_global(now) is GlobalCapped:
            return Refusal("global")
        return None

    async def record(self, ticket: Ticket, now: datetime, *, success: bool) -> None:
        """The model was called: a success ends the user's day, a failure is one try."""
        if success:
            await self._allowance.record_success(ticket.user_id, now)
        else:
            await self._allowance.record_failure(ticket.user_id, now)

    def release(self, ticket: Ticket) -> None:
        """Ends the attempt: the user may read again. Safe to call twice."""
        if not ticket.released:
            ticket.released = True
            self._in_flight.leave(ticket.user_id)
