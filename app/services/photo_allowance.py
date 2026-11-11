"""`PhotoAllowance`: how many receipt reads one user may have in a private chat each day.
Spec §23, docs/sprints/18-one-off-receipt-photos.md.

A user who is not the owner may have 1 successful read and 3 attempts per calendar day
in `Asia/Singapore`. A successful read ends the day's allowance; a failed one leaves two
more tries. The owner is never refused.

Pure Python, no Telegram import, no clock read: callers pass an aware `now`, as
`ReceiptReadBudget` and `FeedbackLimiter` do. Counts live in memory and reset on restart
or when the Singapore date changes. The caller goes through three **async** calls only
(`may_try`, `record_success`, `record_failure`), so a Postgres-backed class, whose calls
must await the database, can replace this one without touching the caller. This class
has no awaits inside; nothing in the caller may rely on that, which is why the
one-read-at-a-time guard (`InFlightReads`) is marked before the first await and is not
part of the counts.

`PhotoNotices` is the once-per-user-per-process memory of who has seen the AI-model
notice; it is not part of the allowance.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

_SINGAPORE = ZoneInfo("Asia/Singapore")

MAX_SUCCESSES_PER_DAY = 1
MAX_ATTEMPTS_PER_DAY = 3


class _Verdict:
    """A small sentinel, compared with `is`: `Allowed`, `AlreadyRead` or `OutOfTries`."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:
        return self._name


Allowed = _Verdict("Allowed")
AlreadyRead = _Verdict("AlreadyRead")
OutOfTries = _Verdict("OutOfTries")


class PhotoAllowance:
    """In-memory counts for the current Singapore calendar day, per user."""

    def __init__(self) -> None:
        self._day: date | None = None
        self._successes: dict[int, int] = {}
        self._failures: dict[int, int] = {}

    def _roll(self, now: datetime) -> None:
        today = now.astimezone(_SINGAPORE).date()
        if today != self._day:
            self._day = today
            self._successes = {}
            self._failures = {}

    async def may_try(self, user_id: int, now: datetime, *, is_owner: bool = False) -> _Verdict:
        """Whether this user may send a receipt to the model now. Counts nothing: a
        refused attempt counts nowhere, and an allowed one counts when its result is
        recorded. The owner is always allowed.
        """
        if is_owner:
            return Allowed
        self._roll(now)
        if self._successes.get(user_id, 0) >= MAX_SUCCESSES_PER_DAY:
            return AlreadyRead
        attempts = self._failures.get(user_id, 0) + self._successes.get(user_id, 0)
        if attempts >= MAX_ATTEMPTS_PER_DAY:
            return OutOfTries
        return Allowed

    async def record_success(self, user_id: int, now: datetime) -> None:
        self._roll(now)
        self._successes[user_id] = self._successes.get(user_id, 0) + 1

    async def record_failure(self, user_id: int, now: datetime) -> None:
        self._roll(now)
        self._failures[user_id] = self._failures.get(user_id, 0) + 1


class PhotoNotices:
    """Who has been told, since the bot started, that receipts are read by an AI model."""

    def __init__(self) -> None:
        self._seen: set[int] = set()

    def take(self, user_id: int) -> bool:
        """True the first time for a user, False after."""
        if user_id in self._seen:
            return False
        self._seen.add(user_id)
        return True


class InFlightReads:
    """The users who have a receipt read running now: one at a time per user, on any
    bill. `try_enter` is synchronous so a caller can mark the user before its first
    `await`; two photos or taps arriving together cannot both pass.
    """

    def __init__(self) -> None:
        self._users: set[int] = set()

    def try_enter(self, user_id: int) -> bool:
        """True and marks the user, or False if they already have a read running."""
        if user_id in self._users:
            return False
        self._users.add(user_id)
        return True

    def leave(self, user_id: int) -> None:
        self._users.discard(user_id)

    def __contains__(self, user_id: int) -> bool:
        return user_id in self._users
