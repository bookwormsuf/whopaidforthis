"""The draft flow: `Draft`, `DraftStore`, `InMemoryDraftStore`, `DraftService`.

Spec references: pinned-decisions §3, §8, sprint 2, 4 and 5 pins. States:

- `detected` -> `Just me` (preview) / `Everyone` / `Choose people` (`participants`);
- `resolve_name`: a "paid by"/"split between" name matched zero or several joined
  users, so the card asks "Who did you mean by 'Jon'?" (never guesses, spec §3);
- `participants` -> `Next` -> `split_method` (`Go item by item` / `Split equally` /
  `Type amounts instead`), or straight to `preview` when exactly one person is
  selected;
- `custom`: the initiator replies with `<name> <amount>` lines until the amounts add
  up, then `Confirm` -> `preview`;
- `item_walk` (sprint 13): one "Who had this?" screen per claimable item, in receipt
  order; the initiator ticks names, then `Next` (or `Everyone`); after the last item
  -> `preview`. `fix_list` (from the itemised preview's `Fix an item`) lists the items
  to pick one and re-answer it, then back to `preview`;
- `preview` -> `Confirm` (commit) / `Edit` (back to `participants`) / `Cancel`;
- receipt-only states `choose_total`, `reconcile`, `manual_amount` (sprint 4) and
  `rate_failed`.

`Draft` is source-neutral. Every draft-flow method returns the updated `Draft` or
raises a typed error (`DraftExpired`, `NotInitiator`, `AlreadySaved`,
`AlreadyCancelled`, `DuplicateMessage`, `DraftToast`, `DraftError`); rendering a
`Draft` into card text/keyboard is a pure bot-layer concern
(`app/bot/render/draft.py::render_draft`), not this module's -- this module must not
import `app.bot` or build telegram objects.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol

from ulid import ULID

from app.models.category import Category
from app.models.exchange_rate import ExchangeRate
from app.models.expense import Expense, ExpenseSplit
from app.models.receipt import CandidateTotal, Receipt, ReceiptLineItem
from app.models.trip import Trip
from app.services.conversion import both_amounts
from app.services.currencies import symbol_for
from app.services.exchange_rates import RateService, RateUnavailable
from app.services.expense_parser import ParsedExpense, parse_custom_line
from app.services.ledger_store import LedgerStore, LedgerUnavailable
from app.services.names import Person, is_everyone, resolve_name, resolve_names
from app.services.receipt_parser import (
    line_item_sums,
    needs_reconciliation,
    reconcile,
    select_total,
)
from app.services.splitting import (
    Custom,
    Equal,
    Items,
    ItemsSplit,
    Percent,
    SplitError,
    shares_in_both_currencies,
    split_items,
    split_shares,
)

logger = logging.getLogger(__name__)

_DRAFT_TTL = timedelta(minutes=30)

DraftState = Literal[
    "who_paid",
    "detected",
    "resolve_name",
    "participants",
    "split_method",
    "custom",
    "item_walk",
    "fix_list",
    "choose_total",
    "reconcile",
    "manual_amount",
    "preview",
    "rate_failed",
    "committing",
    "committed",
    "cancelled",
]

EXPIRED_TEXT = "This expense expired, please send it again"
ALREADY_SAVED_TEXT = "Already saved"
ALREADY_CANCELLED_TEXT = "Already cancelled"


class DraftError(Exception):
    """Base for every draft-flow error the handler needs to react to differently."""


class DraftExpired(DraftError):
    pass


class NotInitiator(DraftError):
    def __init__(self, initiator_name: str) -> None:
        super().__init__(f"only {initiator_name} may act on this draft")
        self.initiator_name = initiator_name


class AlreadySaved(DraftError):
    pass


class AlreadyCancelled(DraftError):
    pass


class DraftUnchanged(DraftError):
    """A press from an older render of the card (a double tap, say): it is ignored
    and the draft is left as it is, so it can never skip an item (sprint 13 pin).
    """


class DraftToast(DraftError):
    """A press that cannot proceed yet; `text` is shown to the presser as a toast
    and the draft is unchanged (e.g. `Next` with nobody selected).
    """

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


NOBODY_SELECTED_TEXT = "Pick at least one person"
PICK_FIRST_TEXT = "Pick who had this first"
MAX_CLAIMABLE_ITEMS = 40
MAX_UNITS_PER_LINE = 10
# The adjustment line for a receipt total the lines don't reach (sprint 13 pin).
UNLISTED_DESCRIPTION = "Unlisted / rounding"
CUSTOM_NOT_BALANCED_TEXT = "The amounts don't add up yet"


@dataclass(frozen=True)
class PendingName:
    """A "paid by"/"split between" name that matched zero or several joined users.
    `candidate_ids` are the buttons offered: the matches, or everyone joined when
    nothing matched.
    """

    role: Literal["payer", "participant"]
    token: str
    candidate_ids: tuple[int, ...]


@dataclass(frozen=True)
class Draft:
    """Source-neutral: `amount_minor`/`currency`/`description`/`category`/`merchant`
    describe the expense itself, independent of whether it came from text or a
    receipt. `pending_participants_text`/`pending_payer_text` carry the text
    parser's "split between ..."/"paid by ..." clauses until they are resolved,
    which happens once the rate is known (so a `rate_failed` draft keeps them).
    """

    draft_id: str
    chat_id: int
    message_id: int
    bot_message_id: int | None
    initiator_id: int
    initiator_name: str
    state: DraftState

    amount_minor: int
    currency: str
    description: str
    category: Category
    merchant: str | None
    source_type: Literal["text", "receipt"]

    payer_id: int
    payer_name: str
    payer_username: str | None

    line_items: list[ReceiptLineItem] = field(default_factory=list)
    split: Equal | Custom | Items | Percent | None = None  # set once a method is chosen

    participant_ids: list[int] = field(default_factory=list)
    # Joined trip users, snapshotted when a participant choice is made (spec §3:
    # Everyone is evaluated at the moment the button is pressed). Rendering and the
    # Splits rows take names from here.
    people: tuple[Person, ...] = ()
    # Name resolution (sprint 5): the names still to ask about, the participants
    # already resolved, and which participant clause the message had.
    name_queue: tuple[PendingName, ...] = ()
    resolved_participant_ids: tuple[int, ...] = ()
    participants_clause: Literal["names", "everyone"] | None = None
    # The last custom-split reply's problems, shown on the card until the next reply.
    custom_error: str | None = None
    # Who had which item (spec §9). Keys are indices into `line_items`; an item in
    # `everyone_items` went to all participants via `Everyone`.
    item_claims: dict[int, tuple[int, ...]] = field(default_factory=dict)
    everyone_items: frozenset[int] = frozenset()
    # Item by item (sprint 13): the position, in `claimable_indices`, of the item on
    # screen; `fixing_one` when it was opened from `Fix an item` on the preview.
    walk_index: int = 0
    fixing_one: bool = False
    rate: ExchangeRate | None = None
    trip_amount_minor: int = 0
    home_amount_minor: int = 0
    transaction_date: date = field(default_factory=lambda: datetime.now(UTC).date())

    pending_participants_text: str | None = None
    pending_payer_text: str | None = None

    # Receipt-sourced drafts only (sprint 4, spec §7/§8):
    photo_file_id: str | None = None
    candidate_totals: list[CandidateTotal] = field(default_factory=list)
    items_sum_minor: int | None = None
    adjustments_sum_minor: int | None = None
    date_source: Literal["receipt", "message"] | None = None
    awaiting_manual_amount: bool = False
    # The photo caption's (description, category), if it gave one (spec §13). Kept
    # on the draft so it still wins after `Try again` or `Enter amount manually`.
    caption_override: tuple[str, Category] | None = None
    # A `Try again` read is in flight; a second press is refused rather than
    # starting a second paid LLM call.
    reading_receipt: bool = False
    # The receipt reader service failed (not the photo): "out of API credit" etc.
    read_problem: str | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def person_for(draft: Draft, user_id: int) -> Person:
    """The snapshotted person, else the payer or initiator as recorded on the draft."""
    for person in draft.people:
        if person.user_id == user_id:
            return person
    if user_id == draft.payer_id:
        return Person(user_id, draft.payer_name, draft.payer_username)
    if user_id == draft.initiator_id:
        return Person(user_id, draft.initiator_name, None)
    return Person(user_id, f"User {user_id}", None)


def original_total(draft: Draft, trip: Trip) -> int:
    """The expense amount in its original currency, minor units."""
    if draft.currency == trip.trip_currency:
        return draft.trip_amount_minor
    return draft.home_amount_minor


def claimable_indices(draft: Draft) -> list[int]:
    """Indices into `line_items` of the claimable items, in receipt order: priced
    `item` lines. An item of zero amount gets no screen (sprint 13 pin). An item's
    number ("2 of 4", `Fix an item`) is its position here, from 1.
    """
    return [
        i
        for i, item in enumerate(draft.line_items)
        if item.kind == "item" and item.amount_minor > 0
    ]


def by_items_available(draft: Draft) -> bool:
    """`Go item by item` is offered for 1 to 40 claimable items (sprint 6 pin)."""
    return 1 <= len(claimable_indices(draft)) <= MAX_CLAIMABLE_ITEMS


def with_unlisted_line(
    line_items: list[ReceiptLineItem], total_minor: int
) -> list[ReceiptLineItem]:
    """`line_items` plus one `Unlisted / rounding` adjustment line for whatever the
    receipt total has that the lines don't (a `service_charge`), or what they have
    too much (a `discount`), so it is shared like tax and never claimed (sprint 13
    pin). Idempotent: once the line is there the lines add up and nothing is added.
    A receipt with no lines gets none.
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


def _one_button_per_unit(line_items: list[ReceiptLineItem]) -> list[ReceiptLineItem]:
    """An item line with a quantity of 2 to 10 becomes one claimable item per unit,
    `"<description> (1/3)"`, so three teas can go 2 and 1 rather than only equally
    (decided 2026-10-04). The units share the line's amount equally, any remainder
    going to the first. Left as printed if the units would make more than 40 items,
    or if the line's amount is its unit price (a misread quantity or amount).
    Idempotent: units have a quantity of 1, so walking again after `Edit` keeps picks.
    """
    result: list[ReceiptLineItem] = []
    for item in line_items:
        count = item.quantity
        one_unit_priced = count > 1 and item.unit_amount_minor == item.amount_minor
        if (
            item.kind != "item"
            or item.amount_minor <= 0
            or not 2 <= count <= MAX_UNITS_PER_LINE
            or one_unit_priced  # a misread: the amount is one unit's, not the line's
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


def unclaimed_indices(draft: Draft) -> list[int]:
    return [
        i
        for i in claimable_indices(draft)
        if i not in draft.everyone_items and not draft.item_claims.get(i)
    ]


def claimants_on_screen(draft: Draft, index: int) -> tuple[int, ...]:
    """Who is ticked for one item: every participant for an `Everyone` item, else
    its claimants, in participant order.
    """
    if index in draft.everyone_items:
        return tuple(draft.participant_ids)
    claimed = set(draft.item_claims.get(index, ()))
    return tuple(pid for pid in draft.participant_ids if pid in claimed)


def items_spec(draft: Draft) -> Items:
    """The draft's claims as an `Items` split. Anything unclaimed (only an item of
    zero amount, which gets no screen) goes to everyone.
    """
    unclaimed = set(unclaimed_indices(draft))
    return Items(
        claims={str(i): list(c) for i, c in draft.item_claims.items() if c},
        everyone=tuple(str(i) for i in sorted(draft.everyone_items | unclaimed)),
    )


def draft_items_split(draft: Draft, trip: Trip) -> ItemsSplit:
    assert isinstance(draft.split, Items)
    return split_items(
        draft.line_items,
        draft.split,
        draft.participant_ids,
        draft.payer_id,
        original_total(draft, trip),
    )


def draft_shares(draft: Draft, trip: Trip) -> dict[int, tuple[int, int]]:
    """Each participant's `(share_trip, share_home)` for the draft's split, in
    participant order. Both columns sum exactly to the expense amounts (spec §4/§5).
    """
    spec = draft.split or Equal()
    shares = split_shares(
        spec,
        original_total(draft, trip),
        draft.participant_ids,
        draft.payer_id,
        line_items=draft.line_items,
    )
    trip_shares, home_shares = shares_in_both_currencies(
        shares,
        original_currency=draft.currency,
        trip_currency=trip.trip_currency,
        home_currency=trip.home_currency,
        trip_to_home_rate=draft.rate.rate if draft.rate is not None else Decimal(1),
        trip_amount_minor=draft.trip_amount_minor,
        home_amount_minor=draft.home_amount_minor,
        payer_id=draft.payer_id,
    )
    return {pid: (trip_shares[pid], home_shares[pid]) for pid in shares}


class DraftStore(Protocol):
    async def get(self, draft_id: str) -> Draft | None: ...

    async def put(self, draft: Draft) -> None: ...

    async def delete(self, draft_id: str) -> None: ...

    async def sweep_expired(self) -> None: ...

    async def find_by_bot_message(self, chat_id: int, bot_message_id: int) -> Draft | None: ...


class InMemoryDraftStore:
    """In-memory `DraftStore`. A draft expires 30 minutes after it was last saved,
    and every accepted press or reply saves it, so the clock runs from the last
    touch, not from creation (sprint 13 pin). Drafts lost on restart are acceptable
    (spec §8); their buttons then reply "This expense expired, please send it again".
    `now` is injectable for tests.
    """

    def __init__(
        self,
        ttl: timedelta = _DRAFT_TTL,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ttl = ttl
        self._now = now
        self._drafts: dict[str, Draft] = {}
        self._touched: dict[str, datetime] = {}

    def _expired(self, draft_id: str, now: datetime) -> bool:
        return now - self._touched[draft_id] > self._ttl

    async def get(self, draft_id: str) -> Draft | None:
        draft = self._drafts.get(draft_id)
        if draft is None:
            return None
        if self._expired(draft_id, self._now()):
            await self.delete(draft_id)
            return None
        return draft

    async def put(self, draft: Draft) -> None:
        self._drafts[draft.draft_id] = draft
        self._touched[draft.draft_id] = self._now()

    async def delete(self, draft_id: str) -> None:
        self._drafts.pop(draft_id, None)
        self._touched.pop(draft_id, None)

    async def sweep_expired(self) -> None:
        now = self._now()
        for draft_id in [d for d in self._drafts if self._expired(d, now)]:
            await self.delete(draft_id)

    async def find_by_bot_message(self, chat_id: int, bot_message_id: int) -> Draft | None:
        for draft in self._drafts.values():
            if draft.chat_id == chat_id and draft.bot_message_id == bot_message_id:
                return await self.get(draft.draft_id)
        return None


class DraftService:
    """Owns the draft lifecycle: `start`, `choose_just_me`, `confirm`, `cancel`,
    `retry_rate`. Only the initiator may act on a draft (spec §8); this is enforced
    on every call via `_get_active`. One expense per Telegram message is the
    store's own unique constraint now (spec §18) -- there is no in-memory dedup
    here any more; `confirm` just inserts and trusts the store to no-op on a
    repeat.
    """

    def __init__(
        self,
        ledger: LedgerStore,
        rates: RateService,
        drafts: DraftStore,
    ) -> None:
        self._ledger = ledger
        self._rates = rates
        self._drafts = drafts

    async def _trip_for(self, draft: Draft) -> Trip:
        trip = await self._ledger.get_binding(draft.chat_id)
        if trip is None:
            raise DraftError("trip is no longer connected")
        return trip

    async def _get_active(self, draft_id: str, presser_id: int) -> Draft:
        draft = await self._drafts.get(draft_id)
        if draft is None:
            raise DraftExpired()
        if draft.state == "committed":
            raise AlreadySaved()
        if draft.state == "cancelled":
            raise AlreadyCancelled()
        if presser_id != draft.initiator_id:
            raise NotInitiator(draft.initiator_name)
        return draft

    async def start(
        self,
        *,
        chat_id: int,
        message_id: int,
        initiator_id: int,
        initiator_name: str,
        initiator_username: str | None,
        parsed: ParsedExpense,
        trip: Trip,
        transaction_date: date,
    ) -> Draft:

        draft_id = str(ULID())
        common = dict(
            draft_id=draft_id,
            chat_id=chat_id,
            message_id=message_id,
            bot_message_id=None,
            initiator_id=initiator_id,
            initiator_name=initiator_name,
            amount_minor=parsed.amount_minor,
            currency=parsed.currency,
            description=parsed.description,
            category=parsed.category,
            merchant=None,
            source_type="text",
            payer_id=initiator_id,
            payer_name=initiator_name,
            payer_username=initiator_username,
            transaction_date=transaction_date,
            pending_participants_text=parsed.participants_text,
            pending_payer_text=parsed.payer_text,
        )

        try:
            rate = await self._rates.rate_for(trip, transaction_date)
        except RateUnavailable:
            draft = Draft(state="rate_failed", participant_ids=[initiator_id], **common)
            await self._drafts.put(draft)
            return draft

        trip_amount_minor, home_amount_minor = both_amounts(
            parsed.amount_minor, parsed.currency, trip.trip_currency, trip.home_currency, rate.rate
        )
        draft = Draft(
            state="detected",
            rate=rate,
            trip_amount_minor=trip_amount_minor,
            home_amount_minor=home_amount_minor,
            participant_ids=[initiator_id],
            **common,
        )
        return await self._apply_clauses(draft, trip)

    async def choose_just_me(self, draft_id: str, presser_id: int) -> Draft:
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "detected":
            raise DraftError(f"cannot choose Just me from state {draft.state}")
        updated = replace(
            draft, state="preview", participant_ids=[draft.initiator_id], split=Equal()
        )
        await self._drafts.put(updated)
        return updated

    def _build_expense(self, draft: Draft, trip: Trip) -> Expense:
        spec = draft.split or Equal()
        shares = draft_shares(draft, trip)
        splits = []
        for pid, (share_trip, share_home) in shares.items():
            person = person_for(draft, pid)
            splits.append(
                ExpenseSplit(
                    user_id=pid,
                    username=person.username,
                    display_name=person.display_name,
                    share_trip_minor=share_trip,
                    share_home_minor=share_home,
                    split_method=spec.kind,
                )
            )

        rate_value = draft.rate.rate if draft.rate is not None else Decimal(0)
        rate_date = draft.rate.rate_date if draft.rate is not None else draft.transaction_date

        return Expense(
            expense_id=draft.draft_id,
            created_at=datetime.now(UTC),
            transaction_date=draft.transaction_date,
            chat_id=draft.chat_id,
            message_id=draft.message_id,
            created_by_user_id=draft.initiator_id,
            payer_user_id=draft.payer_id,
            payer_username=draft.payer_username,
            payer_display_name=draft.payer_name,
            description=draft.description,
            merchant=draft.merchant,
            category=draft.category,
            original_amount_minor=draft.amount_minor,
            original_currency=draft.currency,
            trip_currency=trip.trip_currency,
            home_currency=trip.home_currency,
            trip_amount_minor=draft.trip_amount_minor,
            home_amount_minor=draft.home_amount_minor,
            rate=rate_value,
            rate_date=rate_date,
            source_type=draft.source_type,
            split_method=spec.kind,
            status="confirmed",
            cancelled_at=None,
            notes=None,
            splits=splits,
        )

    @staticmethod
    def _assignments(draft: Draft, trip: Trip) -> dict[int, tuple[list[int], str]] | None:
        """Line Items `Assigned User IDs` and `Allocation Method` for an itemised
        split; `None` for any other split (rows stay `unassigned`).
        """
        if not isinstance(draft.split, Items):
            return None
        return draft_items_split(draft, trip).assignments

    async def confirm(self, draft_id: str, presser_id: int) -> Draft:
        """Idempotent: the store's own unique constraints on `expenses.id` and
        `(chat_id, message_id)` are the save's idempotency check (spec §18) -- a
        retry after a previous attempt's row actually landed server-side just
        inserts again, and the store treats that as "already saved" and returns
        normally instead of raising.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "preview":
            raise DraftError(f"cannot confirm from state {draft.state}")
        trip = await self._trip_for(draft)
        expense = self._build_expense(draft, trip)

        await self._drafts.put(replace(draft, state="committing"))
        try:
            await self._ledger.append_expense(
                trip, expense, draft.line_items, self._assignments(draft, trip)
            )
        except LedgerUnavailable:
            logger.exception("draft %s: could not append expense", draft.draft_id)
            await self._drafts.put(replace(draft, state="preview"))
            raise

        committed = replace(draft, state="committed")
        await self._drafts.put(committed)
        return committed

    async def get(self, draft_id: str) -> Draft | None:
        """The current draft with no initiator/state check -- used to re-render
        the card after `confirm` raises LedgerUnavailable (the presser was
        already authorized by that call's own `_get_active`).
        """
        return await self._drafts.get(draft_id)

    async def set_bot_message_id(self, draft_id: str, bot_message_id: int) -> None:
        """Records the message ID of the card once it has been sent."""
        draft = await self._drafts.get(draft_id)
        if draft is not None:
            await self._drafts.put(replace(draft, bot_message_id=bot_message_id))

    async def cancel(self, draft_id: str, presser_id: int) -> Draft:
        draft = await self._get_active(draft_id, presser_id)
        updated = replace(draft, state="cancelled")
        await self._drafts.put(updated)
        return updated

    async def retry_rate(self, draft_id: str, presser_id: int) -> Draft:
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "rate_failed":
            raise DraftError(f"cannot retry_rate from state {draft.state}")
        trip = await self._trip_for(draft)
        try:
            rate = await self._rates.rate_for(trip, draft.transaction_date)
        except RateUnavailable:
            return draft

        trip_amount_minor, home_amount_minor = both_amounts(
            draft.amount_minor,
            draft.currency,
            trip.trip_currency,
            trip.home_currency,
            rate.rate,
        )
        updated = replace(
            draft,
            state="detected",
            rate=rate,
            trip_amount_minor=trip_amount_minor,
            home_amount_minor=home_amount_minor,
        )
        return await self._apply_clauses(updated, trip)

    # -- Participants, split method, names (sprint 5, spec §3/§8) -------------

    async def _joined_people(self, trip: Trip, draft: Draft) -> tuple[Person, ...]:
        """Everyone joined right now (spec §3), sorted by name for stable buttons.
        The initiator and the payer are always included: sending an expense joins
        the sender, and a payer is only ever set from a joined user.
        """
        users = await self._ledger.list_users(trip, joined_only=True)
        people = {u.user_id: Person.from_trip_user(u) for u in users}
        for pid in (draft.initiator_id, draft.payer_id):
            people.setdefault(pid, person_for(draft, pid))
        return tuple(sorted(people.values(), key=lambda p: (p.display_name.casefold(), p.user_id)))

    async def _apply_clauses(self, draft: Draft, trip: Trip, payer_known: bool = False) -> Draft:
        """Resolves the message's "paid by" and "split ..." clauses (spec §3, §6).
        An exact payer replaces the sender; "split everyone"/"split all" selects
        Everyone; named participants are pre-selected and the participants card is
        skipped. Any name that is not one exact match is queued for the "Who did you
        mean" card. A draft with neither clause stays `detected`.

        Nobody tagged and no "paid by": ask who paid first, when there is anyone else
        it could be (decided 2026-10-03), even with a "split ..." clause, which is
        kept and applied once the payer is picked (`payer_known`).
        """
        payer_text = draft.pending_payer_text
        participants_text = draft.pending_participants_text
        people = await self._joined_people(trip, draft)
        if not payer_text and not payer_known and len(people) > 1:
            draft = replace(draft, people=people, state="who_paid")
            await self._drafts.put(draft)
            return draft
        if not payer_text and not participants_text:
            draft = replace(draft, people=people)
            await self._drafts.put(draft)
            return draft

        everyone_ids = tuple(p.user_id for p in people)
        draft = replace(
            draft, people=people, pending_payer_text=None, pending_participants_text=None
        )
        queue: list[PendingName] = []
        if payer_text:
            match = resolve_name(payer_text, people, draft.initiator_id)
            if match.status == "exact":
                draft = self._with_payer(draft, match.user_ids[0])
            else:
                queue.append(PendingName("payer", payer_text, match.user_ids or everyone_ids))

        resolved: list[int] = []
        clause: Literal["names", "everyone"] | None = None
        if participants_text:
            if is_everyone(participants_text):
                clause = "everyone"
            else:
                clause = "names"
                for match in resolve_names(participants_text, people, draft.initiator_id):
                    if match.status == "exact":
                        resolved.append(match.user_ids[0])
                    else:
                        queue.append(
                            PendingName("participant", match.token, match.user_ids or everyone_ids)
                        )
        draft = replace(
            draft,
            name_queue=tuple(queue),
            resolved_participant_ids=tuple(resolved),
            participants_clause=clause,
        )
        return await self._advance_names(draft)

    @staticmethod
    def _with_payer(draft: Draft, payer_id: int) -> Draft:
        person = person_for(draft, payer_id)
        return replace(
            draft,
            payer_id=person.user_id,
            payer_name=person.display_name,
            payer_username=person.username,
        )

    async def _advance_names(self, draft: Draft) -> Draft:
        """After a name is settled: ask the next one, or apply the participants
        clause, or fall back to the `detected` card.
        """
        if draft.name_queue:
            updated = replace(draft, state="resolve_name")
            await self._drafts.put(updated)
            return updated
        if draft.participants_clause == "everyone":
            ids = [p.user_id for p in draft.people]
        elif draft.participants_clause == "names":
            ids = list(dict.fromkeys(draft.resolved_participant_ids))
        else:
            ids = []
        draft = replace(draft, participants_clause=None, resolved_participant_ids=())
        if not ids:
            updated = replace(draft, state="detected")
            await self._drafts.put(updated)
            return updated
        return await self._after_participants(replace(draft, participant_ids=ids))

    async def _after_participants(self, draft: Draft) -> Draft:
        """With exactly one participant there is nothing to split: straight to the
        preview (sprint 5 pin); otherwise ask the split method.
        """
        if len(draft.participant_ids) == 1:
            updated = replace(draft, state="preview", split=Equal())
        else:
            updated = replace(draft, state="split_method", split=None)
        await self._drafts.put(updated)
        return updated

    async def _to_participants(self, draft: Draft, trip: Trip, selected: list[int]) -> Draft:
        people = await self._joined_people(trip, draft)
        known = {p.user_id for p in people}
        updated = replace(
            draft,
            state="participants",
            people=people,
            participant_ids=[pid for pid in dict.fromkeys(selected) if pid in known],
            split=None,
            custom_error=None,
            name_queue=(),
            resolved_participant_ids=(),
            participants_clause=None,
        )
        await self._drafts.put(updated)
        return updated

    async def set_payer(self, draft_id: str, presser_id: int, user_id: int) -> Draft:
        """The "Who paid?" step (decided 2026-10-03): shown when nobody was tagged and
        the message didn't say `paid by`; the initiator picks the payer, then the
        card asks who shares it.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "who_paid":
            raise DraftError(f"cannot pick the payer in state {draft.state}")
        if user_id not in {p.user_id for p in draft.people} and user_id != draft.initiator_id:
            raise DraftError("not one of the people on this card")
        updated = replace(self._with_payer(draft, user_id), state="detected")
        if updated.pending_participants_text:
            # A "split ..." clause waited for the payer; apply it now.
            trip = await self._trip_for(updated)
            return await self._apply_clauses(updated, trip, payer_known=True)
        await self._drafts.put(updated)
        return updated

    async def choose_everyone(self, draft_id: str, presser_id: int) -> Draft:
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "detected":
            raise DraftError(f"cannot choose Everyone from state {draft.state}")
        trip = await self._trip_for(draft)
        people = await self._joined_people(trip, draft)
        return await self._after_participants(
            replace(draft, people=people, participant_ids=[p.user_id for p in people])
        )

    async def choose_people(self, draft_id: str, presser_id: int) -> Draft:
        """`Choose people`: the toggle card, initiator pre-selected (sprint 5 pin)."""
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "detected":
            raise DraftError(f"cannot choose people from state {draft.state}")
        trip = await self._trip_for(draft)
        return await self._to_participants(draft, trip, [draft.initiator_id])

    async def toggle_participant(self, draft_id: str, presser_id: int, user_id: int) -> Draft:
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "participants":
            raise DraftError(f"cannot toggle from state {draft.state}")
        if user_id not in {p.user_id for p in draft.people}:
            raise DraftError("not one of the people on this card")
        selected = set(draft.participant_ids) ^ {user_id}
        ordered = [p.user_id for p in draft.people if p.user_id in selected]
        updated = replace(draft, participant_ids=ordered)
        await self._drafts.put(updated)
        return updated

    async def participants_next(self, draft_id: str, presser_id: int) -> Draft:
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "participants":
            raise DraftError(f"cannot press Next from state {draft.state}")
        if not draft.participant_ids:
            raise DraftToast(NOBODY_SELECTED_TEXT)
        return await self._after_participants(draft)

    async def choose_equal(self, draft_id: str, presser_id: int) -> Draft:
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "split_method":
            raise DraftError(f"cannot choose Equally from state {draft.state}")
        updated = replace(draft, state="preview", split=Equal())
        await self._drafts.put(updated)
        return updated

    async def choose_custom(self, draft_id: str, presser_id: int) -> Draft:
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "split_method":
            raise DraftError(f"cannot choose Custom from state {draft.state}")
        updated = replace(draft, state="custom", split=Custom({}), custom_error=None)
        await self._drafts.put(updated)
        return updated

    async def apply_custom_reply(
        self, draft_id: str, presser_id: int, trip: Trip, text: str
    ) -> Draft:
        """The initiator's reply to the custom card: one `<name> <amount>` per line
        (sprint 5 pin). Each matched line sets that person's amount, replacing an
        earlier one; a joined user not yet selected is added. Lines that cannot be
        used are listed on the card; the rest still apply.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "custom" or not isinstance(draft.split, Custom):
            raise DraftError(f"cannot take custom amounts in state {draft.state}")
        amounts = dict(draft.split.amounts_by_participant)
        selected = list(draft.participant_ids)
        problems: list[str] = []
        for line in (raw.strip() for raw in text.splitlines()):
            if not line:
                continue
            parsed = parse_custom_line(line, trip.trip_currency, trip.home_currency, draft.currency)
            if parsed is None:
                problems.append(f"Couldn't read '{line}'")
                continue
            if parsed.currency != draft.currency:
                problems.append(f"Use {symbol_for(draft.currency)} amounts for this expense")
                continue
            match = resolve_name(parsed.name_text, draft.people, draft.initiator_id)
            if match.status != "exact":
                problems.append(f"Who is '{parsed.name_text}'?")
                continue
            pid = match.user_ids[0]
            amounts[pid] = parsed.amount_minor
            if pid not in selected:
                selected.append(pid)
        updated = replace(
            draft,
            split=Custom(amounts),
            participant_ids=selected,
            custom_error="; ".join(problems) or None,
        )
        await self._drafts.put(updated)
        return updated

    async def finish_custom(self, draft_id: str, presser_id: int) -> Draft:
        """`Confirm` on the custom card: only when the amounts add up exactly. People
        left at zero are dropped from the participants (sprint 5 pin).
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "custom" or not isinstance(draft.split, Custom):
            raise DraftError(f"cannot finish custom from state {draft.state}")
        trip = await self._trip_for(draft)
        amounts = draft.split.amounts_by_participant
        if sum(amounts.values()) != original_total(draft, trip):
            raise DraftToast(CUSTOM_NOT_BALANCED_TEXT)
        kept = [pid for pid in draft.participant_ids if amounts.get(pid, 0) > 0]
        updated = replace(
            draft,
            state="preview",
            participant_ids=kept,
            split=Custom({pid: amounts[pid] for pid in kept}),
            custom_error=None,
        )
        await self._drafts.put(updated)
        return updated

    async def edit(self, draft_id: str, presser_id: int) -> Draft:
        """`Edit` on the preview: back to the participants card with the current
        participants selected; amount, description and payer are kept (spec §8).
        Item picks stay on the draft; `start_walk` drops those of anyone no longer
        taking part and resumes at the first item with nobody (sprint 13 pin).
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "preview":
            raise DraftError(f"cannot edit from state {draft.state}")
        trip = await self._trip_for(draft)
        return await self._to_participants(draft, trip, list(draft.participant_ids))

    # -- Item by item (sprint 13, spec §9) -------------------------------------

    async def start_walk(self, draft_id: str, presser_id: int) -> Draft:
        """`Go item by item`. Quantity lines become one item per unit first. Picks
        kept from before an `Edit` lose anyone no longer taking part; the walk opens
        at the first item with nobody, or, when every item has someone, on the
        preview.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "split_method":
            raise DraftError(f"cannot go item by item from state {draft.state}")
        if not by_items_available(draft):
            raise DraftError("item by item is not available for this draft")
        keep = set(draft.participant_ids)
        claims = {
            index: kept
            for index, claimants in draft.item_claims.items()
            if (kept := tuple(pid for pid in claimants if pid in keep))
        }
        draft = replace(
            draft,
            line_items=_one_button_per_unit(draft.line_items),
            item_claims=claims,
            split=None,
            fixing_one=False,
        )
        empty = unclaimed_indices(draft)
        if not empty:
            return await self._walk_done(draft)
        updated = replace(
            draft, state="item_walk", walk_index=claimable_indices(draft).index(empty[0])
        )
        await self._drafts.put(updated)
        return updated

    async def _on_screen(self, draft_id: str, presser_id: int, position: int) -> Draft:
        """The draft, if `position` is the item on screen; a press from an older
        render raises `DraftUnchanged`.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "item_walk":
            raise DraftUnchanged()
        if position != draft.walk_index:
            raise DraftUnchanged()
        return draft

    async def walk_toggle(
        self, draft_id: str, presser_id: int, position: int, user_id: int
    ) -> Draft:
        """A name on "Who had this?": ticks or unticks that person on this item.
        Unticking someone on an `Everyone` item leaves everyone else ticked.
        """
        draft = await self._on_screen(draft_id, presser_id, position)
        if user_id not in draft.participant_ids:
            raise DraftError("not one of the people on this card")
        index = claimable_indices(draft)[position]
        ticked = set(claimants_on_screen(draft, index)) ^ {user_id}
        claims = dict(draft.item_claims)
        claims[index] = tuple(pid for pid in draft.participant_ids if pid in ticked)
        updated = replace(draft, item_claims=claims, everyone_items=draft.everyone_items - {index})
        await self._drafts.put(updated)
        return updated

    async def walk_everyone(self, draft_id: str, presser_id: int, position: int) -> Draft:
        """`Everyone`: this item goes to all participants, then on to the next."""
        draft = await self._on_screen(draft_id, presser_id, position)
        index = claimable_indices(draft)[position]
        claims = dict(draft.item_claims)
        claims.pop(index, None)
        draft = replace(draft, item_claims=claims, everyone_items=draft.everyone_items | {index})
        return await self._walk_forward(draft)

    async def walk_next(self, draft_id: str, presser_id: int, position: int) -> Draft:
        """`Next`: refused with a toast while nobody is ticked."""
        draft = await self._on_screen(draft_id, presser_id, position)
        if not claimants_on_screen(draft, claimable_indices(draft)[position]):
            raise DraftToast(PICK_FIRST_TEXT)
        return await self._walk_forward(draft)

    async def walk_back(self, draft_id: str, presser_id: int, position: int) -> Draft:
        """`Back`: the previous item, picks kept; from an item opened by `Fix an
        item`, back to that list.
        """
        draft = await self._on_screen(draft_id, presser_id, position)
        if draft.fixing_one:
            updated = replace(draft, state="fix_list", fixing_one=False)
        elif position == 0:
            raise DraftUnchanged()
        else:
            updated = replace(draft, walk_index=position - 1)
        await self._drafts.put(updated)
        return updated

    async def _walk_forward(self, draft: Draft) -> Draft:
        """On to the next item with nobody yet, or the preview when there is none
        after this one, or straight back to it from `Fix an item`.
        """
        if draft.fixing_one:
            return await self._walk_done(draft)
        positions = claimable_indices(draft)
        empty = set(unclaimed_indices(draft))
        for position in range(draft.walk_index + 1, len(positions)):
            if positions[position] in empty:
                updated = replace(draft, walk_index=position)
                await self._drafts.put(updated)
                return updated
        return await self._walk_done(draft)

    async def _walk_done(self, draft: Draft) -> Draft:
        trip = await self._trip_for(draft)
        updated = replace(draft, state="preview", split=items_spec(draft), fixing_one=False)
        try:
            draft_items_split(updated, trip)
        except SplitError:
            logger.exception("draft %s: itemised split does not reconcile", draft.draft_id)
            raise DraftToast("These items don't add up to the receipt total") from None
        await self._drafts.put(updated)
        return updated

    async def fix_list(self, draft_id: str, presser_id: int) -> Draft:
        """`Fix an item` on an itemised preview: the list of items to pick from."""
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "preview" or not isinstance(draft.split, Items):
            raise DraftError(f"cannot fix an item from state {draft.state}")
        updated = replace(draft, state="fix_list")
        await self._drafts.put(updated)
        return updated

    async def fix_pick(self, draft_id: str, presser_id: int, position: int) -> Draft:
        """An item on the `Fix an item` list: its "Who had this?" screen, which goes
        back to the preview when answered.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "fix_list":
            raise DraftUnchanged()
        if not 0 <= position < len(claimable_indices(draft)):
            raise DraftError("not an item on the list")
        updated = replace(
            draft, state="item_walk", walk_index=position, fixing_one=True, split=None
        )
        await self._drafts.put(updated)
        return updated

    async def fix_back(self, draft_id: str, presser_id: int) -> Draft:
        """`Back to summary` on the `Fix an item` list."""
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "fix_list":
            raise DraftUnchanged()
        return await self._walk_done(draft)

    async def pick_name(self, draft_id: str, presser_id: int, user_id: int) -> Draft:
        """A candidate button on the "Who did you mean" card."""
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "resolve_name" or not draft.name_queue:
            raise DraftError(f"cannot pick a name from state {draft.state}")
        pending, rest = draft.name_queue[0], draft.name_queue[1:]
        if user_id not in pending.candidate_ids:
            raise DraftError("not one of the offered names")
        if pending.role == "payer":
            draft = self._with_payer(draft, user_id)
        else:
            draft = replace(
                draft, resolved_participant_ids=(*draft.resolved_participant_ids, user_id)
            )
        return await self._advance_names(replace(draft, name_queue=rest))

    async def name_unsure(self, draft_id: str, presser_id: int) -> Draft:
        """`Not sure, choose people`: the participants card, with the initiator and
        whoever was already resolved selected (everyone, for "split everyone").
        Asked about the payer, the payer stays the sender; any names still queued
        are dropped, since the person now picks participants by hand.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "resolve_name" or not draft.name_queue:
            raise DraftError(f"cannot answer Not sure from state {draft.state}")
        trip = await self._trip_for(draft)
        if draft.participants_clause == "everyone":
            selected = [p.user_id for p in draft.people]
        else:
            selected = [draft.initiator_id, *draft.resolved_participant_ids]
        return await self._to_participants(draft, trip, selected)

    # -- Receipt flow (sprint 4, spec §7/§8) ---------------------------------

    async def find_by_bot_message(self, chat_id: int, bot_message_id: int) -> Draft | None:
        return await self._drafts.find_by_bot_message(chat_id, bot_message_id)

    @staticmethod
    def _receipt_fields(receipt: Receipt, caption_override: tuple[str, Category] | None) -> dict:
        """The draft fields a successful receipt reading sets. A caption override
        replaces the description and category (spec §13).
        """
        if caption_override is not None:
            description, category = caption_override
        else:
            description, category = receipt.description or "Expense", receipt.category
        items_sum_minor = adjustments_sum_minor = None
        if receipt.line_items:
            items_sum_minor, adjustments_sum_minor = line_item_sums(receipt.line_items)
        return dict(
            description=description,
            category=category,
            merchant=receipt.merchant,
            line_items=list(receipt.line_items),
            items_sum_minor=items_sum_minor,
            adjustments_sum_minor=adjustments_sum_minor,
            currency=receipt.currency,
        )

    async def _start_total_selection(self, base: dict, trip: Trip, receipt: Receipt) -> Draft:
        decision = select_total(receipt)
        if decision.status == "ask":
            draft = Draft(
                state="choose_total",
                amount_minor=0,
                participant_ids=[base["initiator_id"]],
                candidate_totals=decision.candidates,
                **base,
            )
            await self._drafts.put(draft)
            return draft
        chosen = decision.chosen_total_minor
        assert chosen is not None
        # Within ±5 of the line items goes straight to the card; further off (only
        # possible when the receipt printed a single total) asks first (spec §7).
        if needs_reconciliation(chosen, decision.computed_total_minor):
            draft = Draft(
                state="reconcile",
                amount_minor=chosen,
                participant_ids=[base["initiator_id"]],
                **base,
            )
            await self._drafts.put(draft)
            return draft
        return await self._finalize_receipt_amount(base, trip, chosen)

    async def start_from_receipt(
        self,
        *,
        chat_id: int,
        message_id: int,
        photo_file_id: str,
        initiator_id: int,
        initiator_name: str,
        initiator_username: str | None,
        trip: Trip,
        receipt: Receipt,
        transaction_date: date,
        date_source: Literal["receipt", "message"],
        caption_override: tuple[str, Category] | None = None,
        participants_text: str | None = None,
        payer_text: str | None = None,
    ) -> Draft:
        base = dict(
            draft_id=str(ULID()),
            chat_id=chat_id,
            message_id=message_id,
            bot_message_id=None,
            initiator_id=initiator_id,
            initiator_name=initiator_name,
            source_type="receipt",
            payer_id=initiator_id,
            payer_name=initiator_name,
            payer_username=initiator_username,
            transaction_date=transaction_date,
            photo_file_id=photo_file_id,
            date_source=date_source,
            caption_override=caption_override,
            pending_participants_text=participants_text,
            pending_payer_text=payer_text,
            **self._receipt_fields(receipt, caption_override),
        )
        return await self._start_total_selection(base, trip, receipt)

    async def start_unreadable(
        self,
        *,
        chat_id: int,
        message_id: int,
        photo_file_id: str,
        initiator_id: int,
        initiator_name: str,
        initiator_username: str | None,
        trip: Trip,
        transaction_date: date,
        caption_override: tuple[str, Category] | None = None,
        participants_text: str | None = None,
        payer_text: str | None = None,
        read_problem: str | None = None,
    ) -> Draft:
        """The receipt could not be read (spec §7): "Couldn't read that receipt" with
        `Try again` / `Enter amount manually`. `read_problem` says why when the reader
        service failed rather than the photo.
        """
        description, category = caption_override or ("Expense", Category.OTHER)
        draft = Draft(
            draft_id=str(ULID()),
            chat_id=chat_id,
            message_id=message_id,
            bot_message_id=None,
            initiator_id=initiator_id,
            initiator_name=initiator_name,
            state="manual_amount",
            amount_minor=0,
            currency=trip.trip_currency,
            description=description,
            category=category,
            merchant=None,
            source_type="receipt",
            payer_id=initiator_id,
            payer_name=initiator_name,
            payer_username=initiator_username,
            transaction_date=transaction_date,
            photo_file_id=photo_file_id,
            participant_ids=[initiator_id],
            caption_override=caption_override,
            pending_participants_text=participants_text,
            pending_payer_text=payer_text,
            read_problem=read_problem,
        )
        await self._drafts.put(draft)
        return draft

    def _base_from_draft(self, draft: Draft) -> dict:
        return dict(
            draft_id=draft.draft_id,
            chat_id=draft.chat_id,
            message_id=draft.message_id,
            bot_message_id=draft.bot_message_id,
            initiator_id=draft.initiator_id,
            initiator_name=draft.initiator_name,
            description=draft.description,
            category=draft.category,
            merchant=draft.merchant,
            source_type=draft.source_type,
            payer_id=draft.payer_id,
            payer_name=draft.payer_name,
            payer_username=draft.payer_username,
            transaction_date=draft.transaction_date,
            line_items=draft.line_items,
            photo_file_id=draft.photo_file_id,
            date_source=draft.date_source,
            items_sum_minor=draft.items_sum_minor,
            adjustments_sum_minor=draft.adjustments_sum_minor,
            currency=draft.currency,
            caption_override=draft.caption_override,
            pending_participants_text=draft.pending_participants_text,
            pending_payer_text=draft.pending_payer_text,
            created_at=draft.created_at,
        )

    async def _finalize_receipt_amount(self, base: dict, trip: Trip, amount_minor: int) -> Draft:
        """The total is settled: any gap to the lines becomes the `Unlisted /
        rounding` adjustment line (sprint 13 pin), then the rate.
        """
        base = {**base, "line_items": with_unlisted_line(base["line_items"], amount_minor)}
        try:
            rate = await self._rates.rate_for(trip, base["transaction_date"])
        except RateUnavailable:
            draft = Draft(
                state="rate_failed",
                amount_minor=amount_minor,
                participant_ids=[base["initiator_id"]],
                **base,
            )
            await self._drafts.put(draft)
            return draft

        trip_amount_minor, home_amount_minor = both_amounts(
            amount_minor, base["currency"], trip.trip_currency, trip.home_currency, rate.rate
        )
        draft = Draft(
            state="detected",
            rate=rate,
            amount_minor=amount_minor,
            trip_amount_minor=trip_amount_minor,
            home_amount_minor=home_amount_minor,
            participant_ids=[base["initiator_id"]],
            **base,
        )
        # A caption's "split ..."/"paid by ..." apply now, as for a text expense.
        return await self._apply_clauses(draft, trip)

    async def choose_total(self, draft_id: str, presser_id: int, index: int) -> Draft:
        """The initiator picked one of the candidate totals. A picked total that does
        not match the line-item sum exactly gets the reconciliation prompt (spec §7).
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "choose_total":
            raise DraftError(f"cannot choose_total from state {draft.state}")
        if index < 0 or index >= len(draft.candidate_totals):
            raise DraftError("invalid candidate index")
        trip = await self._trip_for(draft)
        chosen = draft.candidate_totals[index].amount_minor
        base = self._base_from_draft(draft)
        if draft.items_sum_minor is not None:
            recon = reconcile(draft.items_sum_minor, draft.adjustments_sum_minor or 0, chosen)
            if recon is not None:
                updated = Draft(
                    state="reconcile",
                    amount_minor=chosen,
                    participant_ids=[draft.initiator_id],
                    **base,
                )
                await self._drafts.put(updated)
                return updated
        return await self._finalize_receipt_amount(base, trip, chosen)

    async def continue_total(self, draft_id: str, presser_id: int) -> Draft:
        """`Continue with <total>` on the reconciliation prompt (sprint 13 pin): the
        receipt total stands, the gap is shared like tax, and the draft goes on as
        any other, item by item still on offer.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "reconcile":
            raise DraftError(f"cannot continue from state {draft.state}")
        trip = await self._trip_for(draft)
        return await self._finalize_receipt_amount(
            self._base_from_draft(draft), trip, draft.amount_minor
        )

    async def check_retry(self, draft_id: str, presser_id: int) -> Draft:
        """Validates a `Try again` press (initiator, right state, no read already in
        flight) without mutating the draft. Lets a caller check whether `begin_retry`
        would succeed before spending anything (such as a budgeted receipt read) on
        the strength of it.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "manual_amount":
            raise DraftError(f"cannot retry from state {draft.state}")
        if draft.reading_receipt:
            raise DraftError("a receipt read is already in flight")
        return draft

    async def begin_retry(self, draft_id: str, presser_id: int) -> Draft:
        """Validates a `Try again` press via `check_retry` and marks the read as in
        flight. The caller re-downloads `photo_file_id` and finishes with
        `retry_receipt` or `retry_unreadable`, both of which clear the mark.
        """
        draft = await self.check_retry(draft_id, presser_id)
        updated = replace(draft, reading_receipt=True, awaiting_manual_amount=False)
        await self._drafts.put(updated)
        return updated

    async def retry_receipt(
        self,
        draft_id: str,
        presser_id: int,
        *,
        trip: Trip,
        receipt: Receipt,
        transaction_date: date,
        date_source: Literal["receipt", "message"],
    ) -> Draft:
        """Re-runs total selection after a successful `Try again` read. Keeps the
        same draft id (the card is edited in place, spec §8), unlike
        `start_from_receipt`, and keeps the draft's caption override.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "manual_amount":
            raise DraftError(f"cannot retry_receipt from state {draft.state}")
        base = self._base_from_draft(draft)
        base.update(
            transaction_date=transaction_date,
            date_source=date_source,
            **self._receipt_fields(receipt, draft.caption_override),
        )
        return await self._start_total_selection(base, trip, receipt)

    async def retry_unreadable(
        self, draft_id: str, presser_id: int, read_problem: str | None = None
    ) -> Draft:
        """`Try again` read failed again: stays in `manual_amount`, still offering
        both buttons.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "manual_amount":
            raise DraftError(f"cannot retry_unreadable from state {draft.state}")
        updated = replace(
            draft, awaiting_manual_amount=False, reading_receipt=False, read_problem=read_problem
        )
        await self._drafts.put(updated)
        return updated

    async def request_manual_amount(self, draft_id: str, presser_id: int) -> Draft:
        """`Enter amount manually` on an unreadable receipt, or `Other amount` on
        "Which is the final total?" when none of the candidates is right: the card
        then asks for a reply with the amount.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state not in ("manual_amount", "choose_total"):
            raise DraftError(f"cannot request_manual_amount from state {draft.state}")
        updated = replace(draft, state="manual_amount", awaiting_manual_amount=True)
        await self._drafts.put(updated)
        return updated

    async def set_manual_amount(
        self, draft_id: str, presser_id: int, trip: Trip, amount_minor: int, currency: str
    ) -> Draft:
        """The initiator replied to the `manual_amount` card with an amount (spec
        §8). The typed amount is the total. A receipt that was read keeps its line
        items when the amount is in the receipt's currency, and gets the
        reconciliation prompt if the lines don't add up to it, as for a picked
        total; otherwise this is a normal text-style draft with no line items. The
        description stays the caption's, if there was one.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "manual_amount":
            raise DraftError(f"cannot set_manual_amount from state {draft.state}")
        base = self._base_from_draft(draft)
        keep_items = bool(draft.line_items) and currency == draft.currency
        if not keep_items:
            base.update(
                line_items=[], items_sum_minor=None, adjustments_sum_minor=None, currency=currency
            )
        elif draft.items_sum_minor is not None and reconcile(
            draft.items_sum_minor, draft.adjustments_sum_minor or 0, amount_minor
        ):
            updated = Draft(
                state="reconcile",
                amount_minor=amount_minor,
                participant_ids=[draft.initiator_id],
                **base,
            )
            await self._drafts.put(updated)
            return updated
        return await self._finalize_receipt_amount(base, trip, amount_minor)
