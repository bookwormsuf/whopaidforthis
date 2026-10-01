"""The draft flow: `Draft`, `DraftStore`, `InMemoryDraftStore`, `DraftService`.

Spec references: pinned-decisions §8, sprint 2 pins. This sprint only exercises
`Just me` -> `preview` -> `Confirm`/`Cancel`, plus the rate-fetch-failure path and
its `Retry` button. `Everyone`/`Choose people`/participants/split-method steps are
Sprint 5.

`Draft` is source-neutral: its money/description/category fields do not depend on
`ParsedExpense` (a future receipt-based draft can populate the same fields). Every
draft-flow method returns the updated `Draft` or raises a typed error
(`DraftExpired`, `NotInitiator`, `AlreadySaved`, `AlreadyCancelled`,
`DuplicateMessage`, `DraftError`); rendering a `Draft` into card text/keyboard is a
pure bot-layer concern (`app/bot/render/draft.py::render_draft`), not this module's
-- this module must not import `app.bot` or build telegram objects.
"""

from __future__ import annotations

import logging
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
from app.services.conversion import both_amounts, reciprocal_rate
from app.services.exchange_rates import RateService, RateUnavailable
from app.services.expense_parser import ParsedExpense
from app.services.ledger_store import LedgerStore, LedgerUnavailable
from app.services.receipt_parser import (
    line_item_sums,
    needs_reconciliation,
    reconcile,
    select_total,
)
from app.services.splitting import convert_shares, split_equal

logger = logging.getLogger(__name__)

_DRAFT_TTL = timedelta(minutes=30)

DraftState = Literal[
    "detected",
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


class DuplicateMessage(DraftError):
    """The (chat_id, message_id) this draft would be started from was already
    committed; the caller should stay silent (spec §10 dedup).
    """


@dataclass(frozen=True)
class Draft:
    """Source-neutral: `amount_minor`/`currency`/`description`/`category`/`merchant`
    describe the expense itself, independent of whether it came from text (this
    sprint) or a receipt (later). `pending_participants_text`/`pending_payer_text`
    carry the text parser's un-resolved "split between ..."/"paid by ..." clauses
    for Sprint 5's name resolution; unused this sprint.
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
    split: object = None  # a splitting.SplitSpec; Equal() once chosen, Sprint 5+

    participant_ids: list[int] = field(default_factory=list)
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

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class DraftStore(Protocol):
    async def get(self, draft_id: str) -> Draft | None: ...

    async def put(self, draft: Draft) -> None: ...

    async def delete(self, draft_id: str) -> None: ...

    async def sweep_expired(self) -> None: ...

    async def find_by_bot_message(self, chat_id: int, bot_message_id: int) -> Draft | None: ...


class InMemoryDraftStore:
    """In-memory `DraftStore`, TTL 30 minutes. Drafts lost on restart are acceptable
    (spec §8); their buttons then reply "This expense expired, please send it again".
    """

    def __init__(self, ttl: timedelta = _DRAFT_TTL) -> None:
        self._ttl = ttl
        self._drafts: dict[str, Draft] = {}

    async def get(self, draft_id: str) -> Draft | None:
        draft = self._drafts.get(draft_id)
        if draft is None:
            return None
        if datetime.now(UTC) - draft.created_at > self._ttl:
            del self._drafts[draft_id]
            return None
        return draft

    async def put(self, draft: Draft) -> None:
        self._drafts[draft.draft_id] = draft

    async def delete(self, draft_id: str) -> None:
        self._drafts.pop(draft_id, None)

    async def sweep_expired(self) -> None:
        now = datetime.now(UTC)
        expired = [
            draft_id
            for draft_id, draft in self._drafts.items()
            if now - draft.created_at > self._ttl
        ]
        for draft_id in expired:
            del self._drafts[draft_id]

    async def find_by_bot_message(self, chat_id: int, bot_message_id: int) -> Draft | None:
        for draft in self._drafts.values():
            if draft.chat_id == chat_id and draft.bot_message_id == bot_message_id:
                return await self.get(draft.draft_id)
        return None


class Dedup(Protocol):
    def is_loaded(self, chat_id: int) -> bool: ...

    def load_many(self, chat_id: int, message_ids: object) -> None: ...

    def seen(self, chat_id: int, message_id: int) -> bool: ...

    def add(self, chat_id: int, message_id: int) -> None: ...


class DraftService:
    """Owns the draft lifecycle: `start`, `choose_just_me`, `confirm`, `cancel`,
    `retry_rate`. Only the initiator may act on a draft (spec §8); this is enforced
    on every call via `_get_active`. Also owns the dedup lazy-load and check (spec
    §10): `start` is the only place a `(chat_id, message_id)` is checked.
    """

    def __init__(
        self,
        ledger: LedgerStore,
        rates: RateService,
        drafts: DraftStore,
        dedup: Dedup,
    ) -> None:
        self._ledger = ledger
        self._rates = rates
        self._drafts = drafts
        self._dedup = dedup

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

    async def _ensure_not_duplicate(self, trip: Trip, chat_id: int, message_id: int) -> None:
        if not self._dedup.is_loaded(chat_id):
            expenses = await self._ledger.list_expenses(trip)
            message_ids = [e.message_id for e in expenses if e.chat_id == chat_id]
            self._dedup.load_many(chat_id, message_ids)
        if self._dedup.seen(chat_id, message_id):
            raise DuplicateMessage()

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
        await self._ensure_not_duplicate(trip, chat_id, message_id)

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
        await self._drafts.put(draft)
        return draft

    async def choose_just_me(self, draft_id: str, presser_id: int) -> Draft:
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "detected":
            raise DraftError(f"cannot choose Just me from state {draft.state}")
        updated = replace(draft, state="preview", participant_ids=[draft.initiator_id])
        await self._drafts.put(updated)
        return updated

    def _build_expense(self, draft: Draft, trip: Trip) -> Expense:
        if draft.currency == trip.trip_currency:
            trip_shares = split_equal(
                draft.trip_amount_minor, draft.participant_ids, draft.payer_id
            )
            home_shares = convert_shares(
                trip_shares,
                draft.rate.rate if draft.rate is not None else Decimal(1),
                trip.trip_currency,
                trip.home_currency,
                draft.home_amount_minor,
                draft.payer_id,
            )
        else:
            home_shares = split_equal(
                draft.home_amount_minor, draft.participant_ids, draft.payer_id
            )
            rate_for_shares = (
                reciprocal_rate(draft.rate.rate) if draft.rate is not None else Decimal(1)
            )
            trip_shares = convert_shares(
                home_shares,
                rate_for_shares,
                trip.home_currency,
                trip.trip_currency,
                draft.trip_amount_minor,
                draft.payer_id,
            )

        splits = [
            ExpenseSplit(
                user_id=pid,
                username=draft.payer_username if pid == draft.payer_id else None,
                display_name=draft.payer_name if pid == draft.payer_id else str(pid),
                share_trip_minor=trip_shares[pid],
                share_home_minor=home_shares[pid],
                split_method="equal",
            )
            for pid in draft.participant_ids
        ]

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
            split_method="equal",
            status="confirmed",
            cancelled_at=None,
            notes=None,
            splits=splits,
        )

    async def confirm(self, draft_id: str, presser_id: int) -> Draft:
        """Idempotent: if a previous attempt's `append_expense` raised after the row
        was actually written server-side, a retry does not double-append -- it
        checks `expense_exists` (keyed by the draft's own ULID) before appending.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "preview":
            raise DraftError(f"cannot confirm from state {draft.state}")
        trip = await self._trip_for(draft)
        expense = self._build_expense(draft, trip)

        await self._drafts.put(replace(draft, state="committing"))
        if not await self._ledger.expense_exists(trip, expense.expense_id):
            try:
                await self._ledger.append_expense(trip, expense, draft.line_items)
            except LedgerUnavailable:
                logger.exception("draft %s: could not append expense", draft.draft_id)
                await self._drafts.put(replace(draft, state="preview"))
                raise

        self._dedup.add(draft.chat_id, draft.message_id)
        committed = replace(draft, state="committed")
        await self._drafts.put(committed)
        return committed

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
        await self._drafts.put(updated)
        return updated

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
    ) -> Draft:
        await self._ensure_not_duplicate(trip, chat_id, message_id)
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
    ) -> Draft:
        """The receipt could not be read (spec §7): "Couldn't read that receipt" with
        `Try again` / `Enter amount manually`.
        """
        await self._ensure_not_duplicate(trip, chat_id, message_id)
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
            created_at=draft.created_at,
        )

    async def _finalize_receipt_amount(self, base: dict, trip: Trip, amount_minor: int) -> Draft:
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
        await self._drafts.put(draft)
        return draft

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

    async def use_total_anyway(self, draft_id: str, presser_id: int) -> Draft:
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "reconcile":
            raise DraftError(f"cannot use_total_anyway from state {draft.state}")
        trip = await self._trip_for(draft)
        return await self._finalize_receipt_amount(
            self._base_from_draft(draft), trip, draft.amount_minor
        )

    async def begin_retry(self, draft_id: str, presser_id: int) -> Draft:
        """Validates a `Try again` press (initiator, right state, no read already in
        flight) and marks the read as in flight. The caller re-downloads
        `photo_file_id` and finishes with `retry_receipt` or `retry_unreadable`,
        both of which clear the mark.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "manual_amount":
            raise DraftError(f"cannot retry from state {draft.state}")
        if draft.reading_receipt:
            raise DraftError("a receipt read is already in flight")
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

    async def retry_unreadable(self, draft_id: str, presser_id: int) -> Draft:
        """`Try again` read failed again: stays in `manual_amount`, still offering
        both buttons.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "manual_amount":
            raise DraftError(f"cannot retry_unreadable from state {draft.state}")
        updated = replace(draft, awaiting_manual_amount=False, reading_receipt=False)
        await self._drafts.put(updated)
        return updated

    async def request_manual_amount(self, draft_id: str, presser_id: int) -> Draft:
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "manual_amount":
            raise DraftError(f"cannot request_manual_amount from state {draft.state}")
        updated = replace(draft, awaiting_manual_amount=True)
        await self._drafts.put(updated)
        return updated

    async def set_manual_amount(
        self, draft_id: str, presser_id: int, trip: Trip, amount_minor: int, currency: str
    ) -> Draft:
        """The initiator replied to the `manual_amount` card with an amount (spec
        §8): produces a normal text-style draft, source_type `receipt`, no line items.
        The description stays the caption's, if there was one.
        """
        draft = await self._get_active(draft_id, presser_id)
        if draft.state != "manual_amount":
            raise DraftError(f"cannot set_manual_amount from state {draft.state}")
        base = self._base_from_draft(draft)
        base.update(
            line_items=[], items_sum_minor=None, adjustments_sum_minor=None, currency=currency
        )
        return await self._finalize_receipt_amount(base, trip, amount_minor)
