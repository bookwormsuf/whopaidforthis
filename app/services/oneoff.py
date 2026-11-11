"""One-off bill: a single bill split among persons in a private chat. Spec §22,
docs/sprints/17-one-off-bill.md.

A *one-off bill* lives in memory only while it is being built and is never saved. Its
*persons* are numbered labels 1..N (person 1 is the user), not trip users. No Telegram
import here, and no clock read: callers pass an aware `now`, as `ReceiptReadBudget` and
the feedback limiter do.

Sprint 18 (spec §23): a bill can start from a receipt photo and be split item by item.
The receipt itself (its line items, candidate totals, currency and the Telegram photo
file ID, never the image) lives on the bill; the claims and the walk position live on the
split. The group draft engine is not imported: where a rule lives in a helper private to
it (one screen per unit, the `Unlisted / rounding` line), this module has its own copy and
`tests/test_oneoff_receipt.py` feeds both the same receipts.

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

from app.models.receipt import CandidateTotal, Receipt, ReceiptLineItem
from app.services.currencies import is_iso_code, symbol_for

# `_locate_amount` is the one amount/currency rule; a typed-amounts line needs to know
# whether it consumed every word, which the public wrappers do not say.
from app.services.expense_parser import _locate_amount, parse_expense
from app.services.receipt_parser import line_item_sums, needs_reconciliation, select_total
from app.services.splitting import (
    Items,
    SplitError,
    split_custom,
    split_equal,
    split_items,
)
from app.services.splitting import ItemsSplit as ItemsBreakdown

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

# The group engine's rules for a receipt, copied (see the module docstring).
MAX_CLAIMABLE_ITEMS = 40
MAX_UNITS_PER_LINE = 10
UNLISTED_DESCRIPTION = "Unlisted / rounding"
RECEIPT_DESCRIPTION = "Receipt"
PICK_FIRST_TEXT = "Pick who had this first"
ITEMS_DONT_ADD_UP_TEXT = "These items don't add up to the receipt total"

BillState = Literal[
    "awaiting_bill",
    "reading",
    "couldnt_read",
    "choose_total",
    "other_amount",
    "reconcile",
    "count",
    "names",
    "payer",
    "method",
    "amounts",
    "item_walk",
    "fix_list",
    "preview",
]
Outcome = Literal["finished", "cancelled"]

_NUMBER_RE = re.compile(r"^-?\d+(?:[.,]\d+)*$")
_COUNT_WITH_WORD_RE = re.compile(r"^(\d+)\s*(?:people|persons|person|pax|ppl)$", re.IGNORECASE)
_NAME_SPLIT_RE = re.compile(r"[,\n]")


class StepError(Exception):
    """An action that does not fit the bill's current step (a stale or double tap)."""


class Toast(Exception):
    """A press refused with a short message the user sees as a toast (`Pick who had
    this first`). The bill is unchanged.
    """

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


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


@dataclass(frozen=True)
class ItemsSplit:
    """Split item by item (sprint 18): what is about the method only. Who had which
    item belongs to the receipt (`OneOffBill.claims`), so `Edit` keeps it. `walk_index`
    is the position among the claimable items; `fixing_one` is set when an item was
    opened from `Fix an item`, so its `Next` returns to the summary. `shares` is set
    once the walk is done.
    """

    walk_index: int = 0
    fixing_one: bool = False
    shares: dict[int, int] | None = None


# One kind per split method. Going back sets the bill's `split` to None, so nothing
# here is cleared by name.
Split = EqualSplit | TypedSplit | ItemsSplit


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
    # A receipt read from a photo (sprint 18). `photo_file_id` is Telegram's ID for the
    # photo, kept for `Try again`; the image itself is never kept. `line_items` are as
    # read until the total is settled, then with the `Unlisted / rounding` line added
    # and, once the item walk starts, one item per unit.
    photo_file_id: str | None = None
    read_problem: str | None = None
    line_items: tuple[ReceiptLineItem, ...] = ()
    candidates: tuple[CandidateTotal, ...] = ()
    # Who had which item, keyed by the item's index in `line_items`; an `Everyone` item
    # is in `everyone`. They belong to the receipt, not to the split method: `Edit` sets
    # `split` to None and keeps them; a new read, `Type the bill instead` and a replaced
    # bill clear them with the receipt.
    claims: dict[int, tuple[int, ...]] = field(default_factory=dict)
    everyone: frozenset[int] = frozenset()

    @property
    def from_receipt(self) -> bool:
        return self.photo_file_id is not None

    @property
    def person_ids(self) -> list[int]:
        return list(range(1, self.count + 1))

    def name_of(self, person_id: int) -> str:
        return self.names[person_id - 1]

    @property
    def method(self) -> Literal["equal", "custom", "items"] | None:
        if isinstance(self.split, EqualSplit):
            return "equal"
        if isinstance(self.split, TypedSplit):
            return "custom"
        return "items" if isinstance(self.split, ItemsSplit) else None

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


PHOTO_STATES: frozenset[str] = frozenset({"awaiting_bill", "reading"})


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
        """True at the bill-line step, where a photo is read, and while a read is
        running, where a second photo is told to wait. Any other step: no reply.
        """
        bill = self.for_user(user_id, now)
        return bill is not None and bill.state in PHOTO_STATES

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
    """Back to the names step with names kept; payer and split (and so any item picks)
    are chosen again.
    """
    _require(bill, "preview")
    return replace(
        bill,
        state="names",
        payer=None,
        split=None,
    )


# ---- a receipt photo (sprint 18) ----------------------------------------------


def resolve_bill_currency(code: str | None) -> str:
    """The receipt's currency if the reader gave an ISO 4217 code, else SGD. No confirm
    step, no conversion (spec §23).
    """
    cleaned = (code or "").strip().upper()
    return cleaned if is_iso_code(cleaned) else _BILL_CURRENCY_DEFAULT


def begin_read(bill: OneOffBill, photo_file_id: str) -> OneOffBill:
    """A photo at the bill-line step is about to be read. The bill is marked as reading
    before the slow call, so a second photo can be told to wait.
    """
    _require(bill, "awaiting_bill")
    return replace(
        bill,
        state="reading",
        photo_file_id=photo_file_id,
        read_problem=None,
        claims={},
        everyone=frozenset(),
    )


def begin_retry(bill: OneOffBill) -> OneOffBill:
    """`Try again` on the couldn't-read card: the same photo is read again."""
    _require(bill, "couldnt_read")
    return replace(bill, state="reading", read_problem=None)


def abort_read(
    bill: OneOffBill, back_to: Literal["awaiting_bill", "couldnt_read"], read_problem: str | None
) -> OneOffBill:
    """The attempt ended before the model was called and nothing came of it (a send
    failed): the bill goes back to where it was, so the user is not stuck `reading`.
    """
    _require(bill, "reading")
    if back_to == "awaiting_bill":
        return back_to_step_one(bill)
    return replace(bill, state="couldnt_read", read_problem=read_problem)


def back_to_step_one(bill: OneOffBill) -> OneOffBill:
    """Back to the bill-line step with nothing of the receipt: `Type the bill instead`,
    a photo found too large at download, or the bot-wide cap reached.
    """
    return replace(
        bill,
        state="awaiting_bill",
        photo_file_id=None,
        read_problem=None,
        line_items=(),
        candidates=(),
        claims={},
        everyone=frozenset(),
    )


def read_failed(bill: OneOffBill, problem: str | None = None) -> OneOffBill:
    """The read ended without a receipt. `problem` says why when the reader service
    failed rather than the photo.
    """
    _require(bill, "reading")
    return replace(bill, state="couldnt_read", read_problem=problem)


def type_instead(bill: OneOffBill) -> OneOffBill:
    """`Type the bill instead`: back to the bill-line step with nothing of the receipt."""
    _require(bill, "couldnt_read")
    return back_to_step_one(bill)


def _receipt_description(receipt: Receipt) -> str:
    description = _clean_name(receipt.description or "") or RECEIPT_DESCRIPTION
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS].rstrip() + "…"
    return description


def with_unlisted_line(
    line_items: list[ReceiptLineItem], total_minor: int
) -> list[ReceiptLineItem]:
    """`line_items` plus one `Unlisted / rounding` adjustment line for whatever the
    receipt total has that the lines don't (a `service_charge`), or what they have too
    much (a `discount`), so it is shared like tax and never claimed. Idempotent. A
    receipt with no lines gets none. (A copy of the group engine's rule.)
    """
    if not line_items:
        return list(line_items)
    items_sum, adjustments_sum = line_item_sums(line_items)
    difference = total_minor - items_sum - adjustments_sum
    if difference == 0:
        return list(line_items)
    unlisted = ReceiptLineItem(
        description=UNLISTED_DESCRIPTION,
        quantity=1,
        amount_minor=abs(difference),
        kind="service_charge" if difference > 0 else "discount",
    )
    return [*line_items, unlisted]


def one_button_per_unit(line_items: list[ReceiptLineItem]) -> list[ReceiptLineItem]:
    """An item line with a quantity of 2 to 10 becomes one claimable item per unit,
    `"<description> (1/3)"`. The units share the line's amount equally, any remainder
    going to the first. Left as printed if the units would make more than 40 items, or
    if the line's amount is its unit price (a misread quantity or amount). Idempotent.
    (A copy of the group engine's rule.)
    """
    result: list[ReceiptLineItem] = []
    for item in line_items:
        count = item.quantity
        one_unit_priced = count > 1 and item.unit_amount_minor == item.amount_minor
        if (
            item.kind != "item"
            or item.amount_minor <= 0
            or not 2 <= count <= MAX_UNITS_PER_LINE
            or one_unit_priced
        ):
            result.append(item)
            continue
        base, extra = divmod(item.amount_minor, count)
        result += [
            ReceiptLineItem(
                description=f"{item.description} ({n}/{count})",
                quantity=1,
                unit_amount_minor=base + (1 if n <= extra else 0),
                amount_minor=base + (1 if n <= extra else 0),
                kind="item",
            )
            for n in range(1, count + 1)
        ]
    if len([i for i in result if i.kind == "item"]) > MAX_CLAIMABLE_ITEMS:
        return list(line_items)
    return result


def claimable_indices(bill: OneOffBill) -> list[int]:
    """Indices into `line_items` of the claimable items, in receipt order: priced
    `item` lines. An item's number ("2 of 4", `Fix an item`) is its position here.
    """
    return [
        i for i, item in enumerate(bill.line_items) if item.kind == "item" and item.amount_minor > 0
    ]


def by_items_available(bill: OneOffBill) -> bool:
    """`Go item by item` is offered for a receipt with 1 to 40 claimable items."""
    return bill.from_receipt and 1 <= len(claimable_indices(bill)) <= MAX_CLAIMABLE_ITEMS


def too_many_items(bill: OneOffBill) -> bool:
    return bill.from_receipt and len(claimable_indices(bill)) > MAX_CLAIMABLE_ITEMS


def _computed_total(bill: OneOffBill) -> int | None:
    if not bill.line_items:
        return None
    items_sum, adjustments_sum = line_item_sums(list(bill.line_items))
    return items_sum + adjustments_sum


def reconciliation_sums(bill: OneOffBill) -> tuple[int, int]:
    """`(items sum, adjustments sum)` of the receipt's lines as read."""
    return line_item_sums(list(bill.line_items))


def _settle_total(bill: OneOffBill, total_minor: int) -> OneOffBill:
    """A total was chosen or typed. More than 5 minor units from the lines asks first
    (`reconcile`); otherwise the gap becomes the `Unlisted / rounding` line and the bill
    goes on to how many people.
    """
    if needs_reconciliation(total_minor, _computed_total(bill)):
        return replace(bill, state="reconcile", amount_minor=total_minor)
    return replace(
        bill,
        state="count",
        amount_minor=total_minor,
        line_items=tuple(with_unlisted_line(list(bill.line_items), total_minor)),
    )


def apply_receipt(bill: OneOffBill, receipt: Receipt) -> OneOffBill:
    """A successful read: the receipt goes on the bill, then the total is chosen
    (`select_total`, as in groups): asked when several are plausible.
    """
    _require(bill, "reading")
    base = replace(
        bill,
        description=_receipt_description(receipt),
        currency=receipt.currency,
        line_items=tuple(receipt.line_items),
        read_problem=None,
        claims={},
        everyone=frozenset(),
    )
    decision = select_total(receipt)
    if decision.status == "ask":
        return replace(base, state="choose_total", candidates=tuple(decision.candidates))
    assert decision.chosen_total_minor is not None
    return _settle_total(base, decision.chosen_total_minor)


def choose_total(bill: OneOffBill, index: int) -> OneOffBill:
    _require(bill, "choose_total")
    if not 0 <= index < len(bill.candidates):
        raise StepError("no such candidate")
    return _settle_total(bill, bill.candidates[index].amount_minor)


def ask_other_amount(bill: OneOffBill) -> OneOffBill:
    """`Other amount` on the which-total card: the next text is read as the total."""
    _require(bill, "choose_total")
    return replace(bill, state="other_amount")


def continue_total(bill: OneOffBill) -> OneOffBill:
    """`Continue with <total>` on the doesn't-add-up card: the receipt total stands and
    the gap is shared like tax.
    """
    _require(bill, "reconcile")
    return replace(
        bill,
        state="count",
        line_items=tuple(with_unlisted_line(list(bill.line_items), bill.amount_minor)),
    )


# ---- item by item (sprint 18; mirrors sprint 13's walk) -----------------------


def claimants_on_screen(bill: OneOffBill, index: int) -> tuple[int, ...]:
    """Who is ticked for one item: every person for an `Everyone` item, else its
    claimants, in person order.
    """
    if index in bill.everyone:
        return tuple(bill.person_ids)
    claimed = set(bill.claims.get(index, ()))
    return tuple(pid for pid in bill.person_ids if pid in claimed)


def answered(bill: OneOffBill, index: int) -> bool:
    return bool(claimants_on_screen(bill, index))


def unclaimed_indices(bill: OneOffBill) -> list[int]:
    """Claimable items nobody has answered yet: no names and not `Everyone`."""
    return [i for i in claimable_indices(bill) if i not in bill.everyone and not bill.claims.get(i)]


def items_spec(bill: OneOffBill) -> Items:
    """The claims as an `Items` split for `split_items`. Anything unclaimed goes to
    everyone; the walk only reaches the preview once every item has someone.
    """
    unclaimed = set(unclaimed_indices(bill))
    return Items(
        claims={str(i): list(c) for i, c in bill.claims.items() if c},
        everyone=tuple(str(i) for i in sorted(bill.everyone | unclaimed)),
    )


def item_breakdown(bill: OneOffBill) -> ItemsBreakdown:
    """The itemised split in full (subtotals, each adjustment line's allocation, the
    shares), from `split_items`. Raises `SplitError` unless the shares add up to the
    bill amount.
    """
    assert bill.payer is not None
    return split_items(
        list(bill.line_items), items_spec(bill), bill.person_ids, bill.payer, bill.amount_minor
    )


def _items(bill: OneOffBill) -> ItemsSplit:
    if not isinstance(bill.split, ItemsSplit):
        raise StepError("bill has no item split")
    return bill.split


def start_walk(bill: OneOffBill) -> OneOffBill:
    """`Go item by item`. Quantity lines become one item per unit first; the walk opens
    on the first item, with the earlier ticks in place if the bill went `Edit` and back.
    """
    _require(bill, "method")
    if not by_items_available(bill):
        raise StepError("item by item is not available")
    units = tuple(one_button_per_unit(list(bill.line_items)))
    return replace(bill, state="item_walk", line_items=units, split=ItemsSplit())


def _on_screen(bill: OneOffBill, position: int) -> ItemsSplit:
    """The split, if `position` is the item on screen; a press from an older render
    raises `StepError`, so a double tap cannot skip an item.
    """
    _require(bill, "item_walk")
    split = _items(bill)
    if position != split.walk_index:
        raise StepError("not the item on screen")
    return split


def walk_toggle(bill: OneOffBill, position: int, person_id: int) -> OneOffBill:
    """A name on "Who had this?": ticks or unticks that person on this item. Unticking
    someone on an `Everyone` item leaves everyone else ticked.
    """
    _on_screen(bill, position)
    if person_id not in bill.person_ids:
        raise StepError("no such person")
    index = claimable_indices(bill)[position]
    ticked = set(claimants_on_screen(bill, index)) ^ {person_id}
    claims = dict(bill.claims)
    claims[index] = tuple(pid for pid in bill.person_ids if pid in ticked)
    return replace(bill, claims=claims, everyone=bill.everyone - {index})


def walk_everyone(bill: OneOffBill, position: int) -> OneOffBill:
    """`Everyone`: this item goes to all persons, then on to the next."""
    _on_screen(bill, position)
    index = claimable_indices(bill)[position]
    claims = dict(bill.claims)
    claims.pop(index, None)
    return _walk_forward(replace(bill, claims=claims, everyone=bill.everyone | {index}))


def walk_next(bill: OneOffBill, position: int) -> OneOffBill:
    """`Next`: refused with a toast while nobody is ticked."""
    _on_screen(bill, position)
    if not answered(bill, claimable_indices(bill)[position]):
        raise Toast(PICK_FIRST_TEXT)
    return _walk_forward(bill)


def walk_back(bill: OneOffBill, position: int) -> OneOffBill:
    """`Back`: the previous item with its picks kept; from an item opened by `Fix an
    item`, back to that list, but not with nobody ticked.
    """
    split = _on_screen(bill, position)
    if split.fixing_one:
        if not answered(bill, claimable_indices(bill)[position]):
            raise Toast(PICK_FIRST_TEXT)
        return replace(bill, state="fix_list", split=replace(split, fixing_one=False))
    if position == 0:
        raise StepError("already at the first item")
    return replace(bill, split=replace(split, walk_index=position - 1))


def _walk_forward(bill: OneOffBill) -> OneOffBill:
    """On to the next item in receipt order, or straight back to the summary from
    `Fix an item`. After the last item: the first item still with nobody, if `Back`
    left one so, else the summary.
    """
    split = _items(bill)
    if split.fixing_one:
        return _walk_done(bill)
    positions = claimable_indices(bill)
    if split.walk_index + 1 < len(positions):
        position = split.walk_index + 1
    elif empty := unclaimed_indices(bill):
        position = positions.index(empty[0])
    else:
        return _walk_done(bill)
    return replace(bill, split=replace(split, walk_index=position))


def _walk_done(bill: OneOffBill) -> OneOffBill:
    """To the summary, with the shares worked out. `split_items` raises unless they add
    up to the bill amount; the user is told so rather than shown wrong shares.
    """
    split = _items(bill)
    done = replace(bill, state="preview", split=replace(split, fixing_one=False, shares=None))
    try:
        shares = item_breakdown(done).shares
    except SplitError:
        raise Toast(ITEMS_DONT_ADD_UP_TEXT) from None
    return replace(done, split=replace(_items(done), shares=dict(shares)))


def fix_list(bill: OneOffBill) -> OneOffBill:
    """`Fix an item` on the summary: the list of items to pick from."""
    _require(bill, "preview")
    _items(bill)
    return replace(bill, state="fix_list")


def fix_pick(bill: OneOffBill, position: int) -> OneOffBill:
    """An item on the `Fix an item` list: its "Who had this?" screen, which returns to
    the summary when answered.
    """
    _require(bill, "fix_list")
    split = _items(bill)
    if not 0 <= position < len(claimable_indices(bill)):
        raise StepError("not an item on the list")
    return replace(
        bill,
        state="item_walk",
        split=replace(split, walk_index=position, fixing_one=True, shares=None),
    )


def fix_back(bill: OneOffBill) -> OneOffBill:
    """`Back to summary` on the `Fix an item` list."""
    _require(bill, "fix_list")
    return _walk_done(bill)


# ---- typed text, one reader per step ------------------------------------------


@dataclass(frozen=True)
class Refused:
    """The text was not accepted. `kind`: `no_amount`, `bad_count`, `bad_amount`,
    `too_many_names` (`count` = how many others the bill has), `name_too_long` or
    `duplicate_name`.
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


def _read_other_amount(bill: OneOffBill, text: str, bot_username: str) -> Reading:
    """`Other amount` on the which-total card: the text is the total, in the receipt's
    currency. Anything else (no amount, zero, a different currency) is refused.
    """
    read = _read_amount(bill, text)
    if read is None or read[1] != bill.currency or read[0] <= 0:
        return Refused("bad_amount")
    return Updated(_settle_total(bill, read[0]))


# The one table of steps that read typed text. `TEXT_STATES` (what the filters listen
# for) and `read_text` (what the handler runs) both come from it, so a new text step
# cannot be heard but ignored, or the reverse.
TEXT_READERS: dict[str, Callable[[OneOffBill, str, str], Reading]] = {
    "awaiting_bill": _read_bill_line,
    "count": _read_count,
    "names": _read_names,
    "amounts": _read_amounts,
    "other_amount": _read_other_amount,
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
