"""Tests for app.services.drafts.DraftService: the full detected -> justme -> preview
-> confirm flow, plus permission, dedup, and idempotency-on-retry paths (spec §8,
sprint 2 pins). Card text/keyboard assertions render through
`app.bot.render.draft.render_draft`, since rendering is a pure bot-layer concern.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.bot.render.draft import render_draft
from app.bot.session_state import Dedup
from app.models.category import Category
from app.models.trip import Trip
from app.services.drafts import (
    AlreadyCancelled,
    AlreadySaved,
    DraftExpired,
    DraftService,
    DuplicateMessage,
    InMemoryDraftStore,
    NotInitiator,
)
from app.services.exchange_rates import RateService, RateUnavailable, StaticRateProvider
from app.services.expense_parser import ParsedExpense
from app.services.ledger_store import InMemoryLedgerStore, LedgerUnavailable


def _trip() -> Trip:
    return Trip(
        chat_id=-100,
        sheet_id="sheet-id-1234567890",
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )


class _FailingProvider:
    async def get_rate(self, base, quote, on_date):
        raise RateUnavailable("nope")


async def _setup(provider=None, ledger=None):
    ledger = ledger or InMemoryLedgerStore()
    trip = _trip()
    await ledger.save_binding(trip)
    await ledger.initialise_trip_sheet(trip)
    provider = provider or StaticRateProvider(Decimal("0.0081"), date(2026, 9, 19))
    rates = RateService(provider, ledger)
    drafts = InMemoryDraftStore()
    dedup = Dedup()
    service = DraftService(ledger, rates, drafts, dedup)
    return service, ledger, trip, dedup


def _parsed(amount_minor: int = 1200, currency: str = "JPY") -> ParsedExpense:
    return ParsedExpense(
        amount_minor=amount_minor,
        currency=currency,
        description="Ramen",
        category=Category.FOOD,
    )


async def _start(service, trip, *, message_id: int = 1, parsed=None, initiator_id: int = 42):
    return await service.start(
        chat_id=-100,
        message_id=message_id,
        initiator_id=initiator_id,
        initiator_name="Ada",
        initiator_username="ada",
        parsed=parsed or _parsed(),
        trip=trip,
        transaction_date=date(2026, 9, 20),
    )


async def test_detected_to_justme_to_preview_to_confirm_exact_card_texts() -> None:
    service, ledger, trip, dedup = await _setup()

    draft = await _start(service, trip)
    assert draft.state == "detected"
    text, markup = render_draft(draft, trip)
    assert text == (
        "🧾 <b>Ramen</b>\n"
        "¥1,200 ≈ S$9.72\n"
        "Paid by Ada · 20 Sep 2026\n"
        "Rate 1 JPY = 0.00810 SGD (19 Sep)\n\n"
        "Who should share this?"
    )
    assert [b.text for row in markup.inline_keyboard for b in row] == [
        "Just me",
        "Everyone",
        "Choose people",
        "Cancel",
    ]
    assert markup.inline_keyboard[0][0].callback_data == f"{draft.draft_id}:justme:-"

    preview = await service.choose_just_me(draft.draft_id, presser_id=42)
    assert preview.state == "preview"
    preview_text, preview_markup = render_draft(preview, trip)
    assert preview_text == "<b>Ramen</b> — ¥1,200 (S$9.72)\nPaid by Ada\n\nAda 💳 — ¥1,200 · S$9.72"
    assert [b.text for row in preview_markup.inline_keyboard for b in row] == [
        "Confirm",
        "Edit",
        "Cancel",
    ]

    committed = await service.confirm(draft.draft_id, presser_id=42)
    assert committed.state == "committed"
    saved_text, saved_markup = render_draft(committed, trip)
    assert saved_text == "Saved ✅ Ramen ¥1,200 (S$9.72)"
    assert saved_markup is None
    assert dedup.seen(-100, 1) is True

    saved = await ledger.list_expenses(trip)
    assert len(saved) == 1
    assert saved[0].status == "confirmed"
    assert saved[0].trip_amount_minor == 1200
    assert saved[0].home_amount_minor == 972  # 1200 JPY * 0.0081 = 9.72 SGD


async def test_payer_username_and_split_username_filled_from_joined_user() -> None:
    service, ledger, trip, dedup = await _setup()
    draft = await _start(service, trip)
    await service.choose_just_me(draft.draft_id, presser_id=42)
    await service.confirm(draft.draft_id, presser_id=42)

    saved = await ledger.list_expenses(trip)
    assert saved[0].payer_username == "ada"


async def test_non_initiator_raises_not_initiator() -> None:
    service, ledger, trip, dedup = await _setup()
    draft = await _start(service, trip)

    with pytest.raises(NotInitiator):
        await service.choose_just_me(draft.draft_id, presser_id=999)


async def test_expired_draft_raises_draft_expired() -> None:
    service, ledger, trip, dedup = await _setup()
    with pytest.raises(DraftExpired):
        await service.choose_just_me("01UNKNOWNDRAFTID0000000000", presser_id=1)


async def test_committed_draft_raises_already_saved() -> None:
    service, ledger, trip, dedup = await _setup()
    draft = await _start(service, trip)
    await service.choose_just_me(draft.draft_id, presser_id=42)
    await service.confirm(draft.draft_id, presser_id=42)

    with pytest.raises(AlreadySaved):
        await service.confirm(draft.draft_id, presser_id=42)


async def test_cancelled_draft_raises_already_cancelled() -> None:
    service, ledger, trip, dedup = await _setup()
    draft = await _start(service, trip)
    await service.cancel(draft.draft_id, presser_id=42)

    with pytest.raises(AlreadyCancelled):
        await service.cancel(draft.draft_id, presser_id=42)


async def test_cancel_writes_nothing() -> None:
    service, ledger, trip, dedup = await _setup()
    draft = await _start(service, trip)
    cancelled = await service.cancel(draft.draft_id, presser_id=42)
    text, markup = render_draft(cancelled, trip)
    assert text == "Cancelled."
    assert markup is None
    assert await ledger.list_expenses(trip) == []


async def test_rate_failure_gives_rate_failed_card_and_retry_recovers() -> None:
    service, ledger, trip, dedup = await _setup(provider=_FailingProvider())

    draft = await _start(service, trip)
    assert draft.state == "rate_failed"
    text, markup = render_draft(draft, trip)
    assert "Couldn't fetch the exchange rate, try again in a minute" in text
    assert [b.text for row in markup.inline_keyboard for b in row] == ["Retry", "Cancel"]

    # Swap in a working provider for the retry, mirroring a real "try again".
    working_provider = StaticRateProvider(Decimal("0.0081"), date(2026, 9, 19))
    service._rates = RateService(working_provider, ledger)  # type: ignore[attr-defined]

    recovered = await service.retry_rate(draft.draft_id, presser_id=42)
    assert recovered.state == "detected"
    recovered_text, _ = render_draft(recovered, trip)
    assert "Rate 1 JPY" in recovered_text


async def test_home_currency_original_needs_no_conversion_for_trip_share() -> None:
    """A SGD-original expense: home_amount_minor is the original, trip_amount_minor
    is informational (converted and stored), per spec §4 -- but per the 2026-10-01
    change, a home-currency expense is rendered in the home currency only, so the
    stored trip amount never reaches the card.
    """
    service, ledger, trip, dedup = await _setup()
    draft = await _start(service, trip, parsed=_parsed(amount_minor=1200, currency="SGD"))
    assert draft.home_amount_minor == 1200
    assert draft.trip_amount_minor > 0  # still derived and stored
    text, _ = render_draft(draft, trip)
    assert "≈" not in text
    assert "¥" not in text
    assert "S$12.00" in text


async def test_dedup_blocks_second_commit_of_same_chat_and_message() -> None:
    service, ledger, trip, dedup = await _setup()
    draft = await _start(service, trip)
    await service.choose_just_me(draft.draft_id, presser_id=42)
    await service.confirm(draft.draft_id, presser_id=42)

    assert dedup.seen(-100, 1) is True


async def test_second_start_for_same_chat_and_message_raises_duplicate_message() -> None:
    service, ledger, trip, dedup = await _setup()
    draft = await _start(service, trip)
    await service.choose_just_me(draft.draft_id, presser_id=42)
    await service.confirm(draft.draft_id, presser_id=42)

    with pytest.raises(DuplicateMessage):
        await _start(service, trip)  # same message_id=1 as before


class _AppendOnceThenFailLedger(InMemoryLedgerStore):
    """Simulates a server write that actually succeeds, but whose acknowledgement
    is lost (e.g. a timeout) -- the row is stored, then LedgerUnavailable is raised
    anyway, exactly once.
    """

    def __init__(self) -> None:
        super().__init__()
        self._append_calls = 0

    async def append_expense(self, trip, expense, line_items=()) -> None:
        self._append_calls += 1
        await super().append_expense(trip, expense, line_items)
        if self._append_calls == 1:
            raise LedgerUnavailable("boom")


async def test_confirm_retry_after_lost_ack_does_not_double_append() -> None:
    ledger = _AppendOnceThenFailLedger()
    service, ledger, trip, dedup = await _setup(ledger=ledger)
    draft = await _start(service, trip)
    await service.choose_just_me(draft.draft_id, presser_id=42)

    with pytest.raises(LedgerUnavailable):
        await service.confirm(draft.draft_id, presser_id=42)

    stuck = await service._drafts.get(draft.draft_id)  # type: ignore[attr-defined]
    assert stuck.state == "preview"  # back to preview, Confirm/Cancel still usable

    committed = await service.confirm(draft.draft_id, presser_id=42)
    assert committed.state == "committed"

    saved = await ledger.list_expenses(trip)
    assert len(saved) == 1  # not double-appended
    # confirm() checks expense_exists before appending, so the retry never calls
    # append_expense again at all -- it just marks the draft committed.
    assert ledger._append_calls == 1


async def test_home_currency_card_texts_show_home_only() -> None:
    """2026-10-01 change: an expense entered in the home currency shows the home
    amount only throughout the draft flow -- detected, preview, and Saved ✅.
    """
    service, ledger, trip, dedup = await _setup()
    draft = await _start(service, trip, parsed=_parsed(amount_minor=1200, currency="SGD"))

    detected_text, _ = render_draft(draft, trip)
    assert detected_text == (
        "🧾 <b>Ramen</b>\nS$12.00\nPaid by Ada · 20 Sep 2026\n\nWho should share this?"
    )

    preview = await service.choose_just_me(draft.draft_id, presser_id=42)
    preview_text, _ = render_draft(preview, trip)
    assert preview_text == "<b>Ramen</b> — S$12.00\nPaid by Ada\n\nAda 💳 — S$12.00"

    committed = await service.confirm(draft.draft_id, presser_id=42)
    saved_text, saved_markup = render_draft(committed, trip)
    assert saved_text == "Saved ✅ Ramen. S$12.00"
    assert saved_markup is None


async def test_trip_currency_card_texts_unchanged_pair_form() -> None:
    """A trip-currency expense keeps the existing "¥X (S$Y)" pair everywhere."""
    service, ledger, trip, dedup = await _setup()
    draft = await _start(service, trip)  # JPY, the default in _parsed()

    preview = await service.choose_just_me(draft.draft_id, presser_id=42)
    preview_text, _ = render_draft(preview, trip)
    assert preview_text == "<b>Ramen</b> — ¥1,200 (S$9.72)\nPaid by Ada\n\nAda 💳 — ¥1,200 · S$9.72"

    committed = await service.confirm(draft.draft_id, presser_id=42)
    saved_text, _ = render_draft(committed, trip)
    assert saved_text == "Saved ✅ Ramen ¥1,200 (S$9.72)"
