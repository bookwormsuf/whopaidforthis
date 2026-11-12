"""One-off bill: a single bill split among persons in a private chat. Spec §22,
docs/sprints/17-one-off-bill.md.

A *one-off bill* lives in memory only while it is being built and is never saved. Its
*persons* are numbered labels 1..N (person 1 is the user), not trip users. No Telegram
import here, and no clock read: callers pass an aware `now`, as `ReceiptReadBudget` and
the feedback limiter do.

Log lines carry the event name only: no amounts, descriptions or names.
"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal

from app.services.currencies import symbol_for

# `_locate_amount` is the one amount/currency rule; a typed-amounts line needs to know
# whether it consumed every word, which the public wrappers do not say.
from app.services.expense_parser import _locate_amount, parse_expense
from app.services.splitting import SplitError, split_custom, split_equal

logger = logging.getLogger(__name__)

_TTL = timedelta(minutes=30)

MIN_PEOPLE = 2
MAX_PEOPLE = 20
MAX_NAME_CHARS = 30
# A card carries the description, up to 20 names and their amounts; a very long
# description could push it past Telegram's message limit.
MAX_DESCRIPTION_CHARS = 80
DEFAULT_DESCRIPTION = "Bill"
_PARSER_DEFAULT_DESCRIPTION = "Expense"
_BILL_CURRENCY_DEFAULT = "SGD"
_USER_ALIASES = frozenset({"me", "i", "myself"})

BillState = Literal["awaiting_bill", "count", "names", "payer", "method", "amounts", "preview"]
Outcome = Literal["finished", "cancelled"]

_NUMBER_RE = re.compile(r"^-?\d+(?:[.,]\d+)*$")
_COUNT_WITH_WORD_RE = re.compile(r"^(\d+)\s*(?:people|persons|person|pax|ppl)$", re.IGNORECASE)
_NAME_SPLIT_RE = re.compile(r"[,\n]")


class StepError(Exception):
    """An action that does not fit the bill's current step (a stale or double tap)."""


class InputError(Exception):
    """Typed input a step refuses. `kind` says why; `count` carries a number for
    `too_many_names` (how many other people the bill has).
    """

    def __init__(self, kind: str, count: int = 0) -> None:
        super().__init__(kind)
        self.kind = kind
        self.count = count


@dataclass(frozen=True)
class BillLine:
    """What the user typed as the bill: `84.50 dinner`."""

    description: str
    amount_minor: int
    currency: str


@dataclass(frozen=True)
class AmountError:
    """One typed-amounts line that could not be applied. `kind` is `unreadable`
    (`text` is the line), `unknown_person` (`text` is the name) or `wrong_currency`.
    """

    kind: Literal["unreadable", "unknown_person", "wrong_currency"]
    text: str = ""


@dataclass(frozen=True)
class EqualSplit:
    """Split equally: the shares are worked out when the method is chosen."""

    shares: dict[int, int]


@dataclass(frozen=True)
class TypedSplit:
    """Split by typed amounts: what has been typed so far, the lines that could not be
    read, and (once `Confirm` is accepted) the validated shares.
    """

    amounts: dict[int, int] = field(default_factory=dict)
    errors: tuple[AmountError, ...] = ()
    shares: dict[int, int] | None = None


# One kind per split method; Sprint 18 adds a third (by items). Going back sets the
# bill's `split` to None, so nothing here is cleared by name.
Split = EqualSplit | TypedSplit


@dataclass(frozen=True)
class OneOffBill:
    bill_id: str
    user_id: int
    chat_id: int
    first_name: str
    state: BillState = "awaiting_bill"
    message_id: int | None = None
    description: str = DEFAULT_DESCRIPTION
    amount_minor: int = 0
    currency: str = _BILL_CURRENCY_DEFAULT
    count: int = 0
    # names[0] is the user's first name; the rest are person 2.. by position.
    names: tuple[str, ...] = ()
    payer: int | None = None
    split: Split | None = None

    @property
    def person_ids(self) -> list[int]:
        return list(range(1, self.count + 1))

    def name_of(self, person_id: int) -> str:
        return self.names[person_id - 1]

    @property
    def method(self) -> Literal["equal", "custom"] | None:
        if isinstance(self.split, EqualSplit):
            return "equal"
        return "custom" if isinstance(self.split, TypedSplit) else None

    @property
    def amounts(self) -> dict[int, int]:
        return self.split.amounts if isinstance(self.split, TypedSplit) else {}

    @property
    def amount_errors(self) -> tuple[AmountError, ...]:
        return self.split.errors if isinstance(self.split, TypedSplit) else ()

    @property
    def shares(self) -> dict[int, int]:
        return (self.split.shares or {}) if self.split is not None else {}


def default_name(person_id: int) -> str:
    return f"Person {person_id}"


# ---- the store ----------------------------------------------------------------


class OneOffStore:
    """In-memory bills, one per user. A bill expires 30 minutes after it was last put
    (every accepted action puts it), so the clock runs from the last touch.

    A user whose bill expired in this process gets one notice on their next text: the
    store remembers them until `take_expired_notice`.
    """

    def __init__(self, ttl: timedelta = _TTL) -> None:
        self._ttl = ttl
        self._bills: dict[str, OneOffBill] = {}
        self._by_user: dict[int, str] = {}
        self._touched: dict[str, datetime] = {}
        self._expired_users: set[int] = set()

    def _expired(self, bill_id: str, now: datetime) -> bool:
        return now - self._touched[bill_id] > self._ttl

    def _drop(self, bill_id: str) -> None:
        bill = self._bills.pop(bill_id, None)
        self._touched.pop(bill_id, None)
        if bill is not None and self._by_user.get(bill.user_id) == bill_id:
            del self._by_user[bill.user_id]

    def _expire(self, bill_id: str) -> None:
        bill = self._bills[bill_id]
        self._drop(bill_id)
        self._expired_users.add(bill.user_id)
        logger.info("oneoff expired")

    def create(self, *, user_id: int, chat_id: int, first_name: str, now: datetime) -> OneOffBill:
        """A new bill waiting for its bill line. Any earlier bill of this user is
        dropped without a log line: the caller removes it first, with its own outcome.
        """
        previous = self._by_user.get(user_id)
        if previous is not None:
            self._drop(previous)
        bill_id = secrets.token_urlsafe(6)
        while bill_id in self._bills:
            bill_id = secrets.token_urlsafe(6)
        bill = OneOffBill(
            bill_id=bill_id,
            user_id=user_id,
            chat_id=chat_id,
            first_name=_clean_name(first_name),
        )
        self._bills[bill_id] = bill
        self._by_user[user_id] = bill_id
        self._touched[bill_id] = now
        self._expired_users.discard(user_id)
        logger.info("oneoff started")
        return bill

    def get(self, bill_id: str, now: datetime) -> OneOffBill | None:
        bill = self._bills.get(bill_id)
        if bill is None:
            return None
        if self._expired(bill_id, now):
            self._expire(bill_id)
            return None
        return bill

    def for_user(self, user_id: int, now: datetime) -> OneOffBill | None:
        bill_id = self._by_user.get(user_id)
        return None if bill_id is None else self.get(bill_id, now)

    def put(self, bill: OneOffBill, now: datetime, *, touch: bool = True) -> bool:
        """Saves a change and, for an accepted one, restarts the 30 minutes. `touch=False`
        saves without restarting the clock (typed amounts of which none were accepted).

        Returns False, saving nothing, when the bill is no longer in the store (removed,
        replaced or expired): a slow step finishing after `Cancel` must not bring a
        cancelled bill back.
        """
        if bill.bill_id not in self._bills:
            return False
        if self._expired(bill.bill_id, now):
            self._expire(bill.bill_id)
            return False
        self._bills[bill.bill_id] = bill
        if touch:
            self._touched[bill.bill_id] = now
        return True

    def remove(self, bill_id: str, outcome: Outcome) -> None:
        if bill_id in self._bills:
            self._drop(bill_id)
            logger.info("oneoff %s", outcome)

    def sweep(self, now: datetime) -> None:
        """Drops every expired bill; called whenever a new bill starts."""
        for bill_id in [b for b in self._bills if self._expired(b, now)]:
            self._expire(bill_id)

    def take_expired_notice(self, user_id: int) -> bool:
        """True once for a user whose bill expired, then False until the next expiry."""
        if user_id in self._expired_users:
            self._expired_users.discard(user_id)
            return True
        return False

    def has_expired_notice(self, user_id: int) -> bool:
        return user_id in self._expired_users

    def wants_text(self, user_id: int, now: datetime) -> bool:
        """True while the user's bill waits for typed text, or owes them the expiry
        notice. Looking at an expired bill is what expires it.
        """
        bill = self.for_user(user_id, now)
        if bill is not None:
            return bill.state in TEXT_STATES
        return self.has_expired_notice(user_id)

    def wants_photo(self, user_id: int, now: datetime) -> bool:
        """True only at the bill-line step, where a photo gets its own answer."""
        bill = self.for_user(user_id, now)
        return bill is not None and bill.state == "awaiting_bill"

    def has_bill(self, user_id: int, now: datetime) -> bool:
        return self.for_user(user_id, now) is not None


# ---- reading typed input ------------------------------------------------------


def _clean_name(name: str) -> str:
    """Single spaces only, so a typed `Ali  Khan 20` line can match `Ali Khan`."""
    return " ".join(name.split())


def parse_bill_line(text: str, bot_username: str) -> BillLine | None:
    """`84.50 dinner` as a bill. `None` when there is no amount, or it is zero.
    The currency is what was typed, SGD with none; `paid by`, `split` and @tags are
    ignored. Nothing is converted.
    """
    parsed = parse_expense(text, _BILL_CURRENCY_DEFAULT, _BILL_CURRENCY_DEFAULT, bot_username)
    if parsed is None or parsed.amount_minor <= 0:
        return None
    description = parsed.description
    if description == _PARSER_DEFAULT_DESCRIPTION:
        description = DEFAULT_DESCRIPTION
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS].rstrip() + "…"
    return BillLine(description, parsed.amount_minor, parsed.currency)


def read_count_text(text: str, bot_username: str) -> int | BillLine | None:
    """Typed text at the how-many step: a whole number from 2 to 20 (optionally followed
    by people/persons/person/pax/ppl), a new bill line
    (text that is not a number but parses as a bill), or `None` (anything else,
    including a number outside 2 to 20).
    """
    stripped = text.strip()
    with_word = _COUNT_WITH_WORD_RE.match(stripped)
    if with_word:  # `3 people`, `4 pax`: the count, not a bill
        count = int(with_word.group(1))
        return count if MIN_PEOPLE <= count <= MAX_PEOPLE else None
    if _NUMBER_RE.match(stripped):
        if stripped.isdigit() and MIN_PEOPLE <= int(stripped) <= MAX_PEOPLE:
            return int(stripped)
        return None
    return parse_bill_line(stripped, bot_username)


# ---- transitions --------------------------------------------------------------


def _require(bill: OneOffBill, *states: BillState) -> None:
    if bill.state not in states:
        raise StepError(f"bill is at {bill.state}")


def set_bill_line(bill: OneOffBill, line: BillLine) -> OneOffBill:
    _require(bill, "awaiting_bill")
    return replace(
        bill,
        state="count",
        description=line.description,
        amount_minor=line.amount_minor,
        currency=line.currency,
    )


def set_count(bill: OneOffBill, count: int) -> OneOffBill:
    _require(bill, "count")
    if not MIN_PEOPLE <= count <= MAX_PEOPLE:
        raise InputError("bad_count")
    names = (bill.first_name, *(default_name(n) for n in range(2, count + 1)))
    return replace(bill, state="names", count=count, names=names)


def rename(bill: OneOffBill, text: str) -> OneOffBill:
    """Typed names go to person 2 onwards, in order. Splits on commas and line breaks,
    drops empty entries. Nothing changes when any rule fails.
    """
    _require(bill, "names")
    typed = [_clean_name(part) for part in _NAME_SPLIT_RE.split(text)]
    typed = [name for name in typed if name]
    others = bill.count - 1
    if len(typed) > others:
        raise InputError("too_many_names", others)
    if any(len(name) > MAX_NAME_CHARS for name in typed):
        raise InputError("name_too_long")
    names = list(bill.names)
    for offset, name in enumerate(typed):
        names[1 + offset] = name
    folded = [name.casefold() for name in names]
    if len(set(folded)) != len(folded):
        raise InputError("duplicate_name")
    return replace(bill, names=tuple(names))


def to_payer(bill: OneOffBill) -> OneOffBill:
    _require(bill, "names")
    return replace(bill, state="payer")


def set_payer(bill: OneOffBill, payer: int) -> OneOffBill:
    _require(bill, "payer")
    if payer not in bill.person_ids:
        raise StepError("no such person")
    return replace(bill, state="method", payer=payer)


def choose_equal(bill: OneOffBill) -> OneOffBill:
    _require(bill, "method")
    assert bill.payer is not None
    shares = split_equal(bill.amount_minor, bill.person_ids, bill.payer)
    return replace(bill, state="preview", split=EqualSplit(shares))


def choose_custom(bill: OneOffBill) -> OneOffBill:
    _require(bill, "method")
    return replace(bill, state="amounts", split=TypedSplit())


def _typed(bill: OneOffBill) -> TypedSplit:
    if not isinstance(bill.split, TypedSplit):
        raise StepError("bill has no typed amounts")
    return bill.split


def remaining(bill: OneOffBill) -> int:
    """The bill amount minus the typed amounts; zero when they add up."""
    return bill.amount_minor - sum(_typed(bill).amounts.values())


def is_balanced(bill: OneOffBill) -> bool:
    return remaining(bill) == 0


def _name_candidates(bill: OneOffBill) -> list[tuple[str, int]]:
    """Every name a line can start with, longest first: the people's names and `me`,
    `i`, `myself` (the user). A person named like an alias is reached by the alias.
    """
    by_name: dict[str, int] = {bill.name_of(p).casefold(): p for p in bill.person_ids}
    by_name.update({alias: 1 for alias in _USER_ALIASES})
    return sorted(by_name.items(), key=lambda item: len(item[0]), reverse=True)


def _read_amount(bill: OneOffBill, text: str) -> tuple[int, str] | None:
    """`20`, `S$20`, `20 sgd`, `SGD 20`, `500 yen` as `(minor units, currency)`; `None`
    unless the text is exactly an amount (and its currency word), nothing else.
    """
    words = text.split()
    if not words:
        return None
    located = _locate_amount(words, bill.currency, bill.currency)
    if located is None or len(located.consumed) != len(words):
        return None
    return located.amount_minor, located.currency


def _read_amount_line(bill: OneOffBill, line: str) -> tuple[int, int, str] | AmountError:
    """`(person, minor units, currency)` for one line, or why it cannot be read. The
    line starts with the longest known name; the rest is the amount. A line that starts
    with no known name is read as `<name> <amount>` by its last word, so the error can
    name who was not found.
    """
    folded = line.casefold()
    name_matched = False
    for name, person_id in _name_candidates(bill):
        if folded == name or folded.startswith(f"{name} "):
            name_matched = True
            read = _read_amount(bill, line[len(name) :])
            if read is not None:
                return person_id, *read
    if name_matched:
        return AmountError("unreadable", line)
    words = line.split(" ")
    if len(words) < 2 or _read_amount(bill, words[-1]) is None:
        return AmountError("unreadable", line)
    return AmountError("unknown_person", " ".join(words[:-1]))


def apply_amount_lines(bill: OneOffBill, text: str) -> OneOffBill:
    """One `<name> <amount>` line per person (see `_read_amount_line`). Lines that apply
    replace that person's earlier amount; the rest become errors, in line order, and
    the other lines still apply.
    """
    _require(bill, "amounts")
    amounts = dict(_typed(bill).amounts)
    errors: list[AmountError] = []
    for raw in text.splitlines():
        line = _clean_name(raw)
        if not line:
            continue
        read = _read_amount_line(bill, line)
        if isinstance(read, AmountError):
            errors.append(read)
            continue
        person_id, amount_minor, currency = read
        if currency != bill.currency:
            errors.append(AmountError("wrong_currency"))
            continue
        amounts[person_id] = amount_minor
    return replace(bill, split=TypedSplit(amounts=amounts, errors=tuple(errors)))


def confirm_amounts(bill: OneOffBill) -> OneOffBill:
    """Moves to the preview once the typed amounts add up exactly; `split_custom`
    validates. People with no amount count as zero.
    """
    _require(bill, "amounts")
    typed = _typed(bill)
    full = {person_id: typed.amounts.get(person_id, 0) for person_id in bill.person_ids}
    try:
        shares = split_custom(full, bill.amount_minor)
    except SplitError as error:
        raise StepError("amounts do not add up") from error
    return replace(bill, state="preview", split=TypedSplit(typed.amounts, (), shares))


def edit(bill: OneOffBill) -> OneOffBill:
    """Back to the names step with names kept; payer and split are chosen again."""
    _require(bill, "preview")
    return replace(
        bill,
        state="names",
        payer=None,
        split=None,
    )


# ---- typed text, one reader per step ------------------------------------------


@dataclass(frozen=True)
class Refused:
    """The text was not accepted. `kind`: `no_amount`, `bad_count`, `too_many_names`
    (`count` = how many others the bill has), `name_too_long` or `duplicate_name`.
    """

    kind: str
    count: int = 0


@dataclass(frozen=True)
class Updated:
    """The text changed the bill; the card is edited in place. `touch` says whether it
    counts as an accepted action that restarts the 30 minutes.
    """

    bill: OneOffBill
    touch: bool = True


@dataclass(frozen=True)
class FirstCard:
    """The bill line was read: the first card goes out as a new message."""

    bill: OneOffBill


@dataclass(frozen=True)
class Replaced:
    """A new bill line at the how-many step replaces the bill."""

    line: BillLine


Reading = Refused | Updated | FirstCard | Replaced


def _read_bill_line(bill: OneOffBill, text: str, bot_username: str) -> Reading:
    line = parse_bill_line(text, bot_username)
    return Refused("no_amount") if line is None else FirstCard(set_bill_line(bill, line))


def _read_count(bill: OneOffBill, text: str, bot_username: str) -> Reading:
    read = read_count_text(text, bot_username)
    if read is None:
        return Refused("bad_count")
    if isinstance(read, BillLine):
        return Replaced(read)
    return Updated(set_count(bill, read))


def _read_names(bill: OneOffBill, text: str, bot_username: str) -> Reading:
    try:
        return Updated(rename(bill, text))
    except InputError as error:
        return Refused(error.kind, error.count)


def _read_amounts(bill: OneOffBill, text: str, bot_username: str) -> Reading:
    applied = apply_amount_lines(bill, text)
    # Only a change to the amounts is an accepted action that restarts the clock.
    return Updated(applied, touch=applied.amounts != bill.amounts)


# The one table of steps that read typed text. `TEXT_STATES` (what the filters listen
# for) and `read_text` (what the handler runs) both come from it, so a new text step
# cannot be heard but ignored, or the reverse.
TEXT_READERS: dict[str, Callable[[OneOffBill, str, str], Reading]] = {
    "awaiting_bill": _read_bill_line,
    "count": _read_count,
    "names": _read_names,
    "amounts": _read_amounts,
}
TEXT_STATES: frozenset[str] = frozenset(TEXT_READERS)


def read_text(bill: OneOffBill, text: str, bot_username: str) -> Reading:
    """Reads typed text at the bill's step. Raises `StepError` for a step that reads none."""
    reader = TEXT_READERS.get(bill.state)
    if reader is None:
        raise StepError(f"bill at {bill.state} reads no text")
    return reader(bill, text, bot_username)


def bill_currency_symbol(bill: OneOffBill) -> str:
    return symbol_for(bill.currency).strip()
