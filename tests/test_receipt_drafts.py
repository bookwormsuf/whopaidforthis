"""Tests for the receipt-sourced draft flow in app.services.drafts.DraftService
(sprint 4 pins): choose_total, reconcile, manual_amount, and the Line Items write
path on commit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.bot.render.draft import render_draft
from app.bot.session_state import Dedup
from app.models.trip import Trip
from app.services.drafts import DraftService, InMemoryDraftStore
from app.services.exchange_rates import RateService, StaticRateProvider
from app.services.ledger_store import InMemoryLedgerStore
from app.services.llm_schemas import ReceiptExtraction


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


async def _setup():
    ledger = InMemoryLedgerStore()
    trip = _trip()
    await ledger.save_binding(trip)
    await ledger.initialise_trip_sheet(trip)
    provider = StaticRateProvider(Decimal("0.0081"), date(2026, 9, 19))
    rates = RateService(provider, ledger)
    drafts = InMemoryDraftStore()
    dedup = Dedup()
    service = DraftService(ledger, rates, drafts, dedup)
    return service, ledger, trip


def _confident_extraction() -> ReceiptExtraction:
    return ReceiptExtraction(
        merchant="Lawson",
        currency="JPY",
        transaction_datetime="2026-09-20T14:00:00",
        line_items=[
            {"description": "Onigiri", "quantity": 2, "amount_minor": 300, "kind": "item"},
        ],
        candidate_totals=[
            {"label_as_printed": "合計", "amount_minor": 300, "is_grand_total_guess": True}
        ],
        description="Konbini Snacks",
        category="groceries",
        confidence=0.95,
    )


async def test_confident_total_goes_straight_to_detected() -> None:
    service, ledger, trip = await _setup()
    extraction = _confident_extraction()

    draft = await service.start_from_receipt(
        chat_id=-100,
        message_id=1,
        photo_file_id="file123",
        initiator_id=42,
        initiator_name="Ada",
        initiator_username="ada",
        trip=trip,
        extraction=extraction,
        currency="JPY",
        transaction_date=date(2026, 9, 20),
        date_source="receipt",
    )
    assert draft.state == "detected"
    assert draft.amount_minor == 300
    assert draft.merchant == "Lawson"
    assert len(draft.line_items) == 1

    text, markup = render_draft(draft, trip)
    assert "Lawson" in text
    assert "1 items found" in text
    assert "from receipt" in text


async def test_ambiguous_total_asks_then_choose_total_proceeds() -> None:
    service, ledger, trip = await _setup()
    extraction = ReceiptExtraction(
        merchant="Ichiran",
        currency="JPY",
        line_items=[{"description": "Ramen", "quantity": 1, "amount_minor": 2380, "kind": "item"}],
        candidate_totals=[
            {"label_as_printed": "小計", "amount_minor": 2380, "is_grand_total_guess": False},
            {"label_as_printed": "合計", "amount_minor": 2618, "is_grand_total_guess": True},
        ],
        description="Ramen",
        category="food",
        confidence=0.8,
    )

    draft = await service.start_from_receipt(
        chat_id=-100,
        message_id=2,
        photo_file_id="file456",
        initiator_id=42,
        initiator_name="Ada",
        initiator_username="ada",
        trip=trip,
        extraction=extraction,
        currency="JPY",
        transaction_date=date(2026, 9, 21),
        date_source="receipt",
    )
    assert draft.state == "choose_total"
    assert len(draft.candidate_totals) == 2

    text, markup = render_draft(draft, trip)
    assert "Which is the final total?" in text

    # candidates are ordered grand-total guesses first: index 0 is 合計 (2618).
    chosen = await service.choose_total(draft.draft_id, presser_id=42, index=0)
    # 2618 != computed (2380) -> reconcile state
    assert chosen.state == "reconcile"

    proceeded = await service.use_total_anyway(chosen.draft_id, presser_id=42)
    assert proceeded.state == "detected"
    assert proceeded.amount_minor == 2618


async def test_reconcile_state_renders_numbers() -> None:
    """A confident selection (diff within the ±5 tolerance) still shows the
    reconciliation prompt if it doesn't exactly match items + adjustments (spec §7:
    the reconciliation check is independent of how the total was chosen).
    """
    service, ledger, trip = await _setup()
    extraction = ReceiptExtraction(
        line_items=[{"description": "Ramen", "quantity": 1, "amount_minor": 2380, "kind": "item"}],
        candidate_totals=[
            {"label_as_printed": "合計", "amount_minor": 2383, "is_grand_total_guess": True}
        ],
        description="Ramen",
        category="food",
        confidence=0.6,
    )
    draft = await service.start_from_receipt(
        chat_id=-100,
        message_id=3,
        photo_file_id="f",
        initiator_id=42,
        initiator_name="Ada",
        initiator_username="ada",
        trip=trip,
        extraction=extraction,
        currency="JPY",
        transaction_date=date(2026, 9, 21),
        date_source="receipt",
    )
    assert draft.state == "reconcile"
    text, markup = render_draft(draft, trip)
    assert "¥2,380" in text
    assert "¥2,383" in text
    assert [b.text for row in markup.inline_keyboard for b in row] == [
        "Use total anyway",
        "Cancel",
    ]


async def test_unreadable_then_manual_amount_produces_text_style_draft() -> None:
    service, ledger, trip = await _setup()
    draft = await service.start_unreadable(
        chat_id=-100,
        message_id=4,
        photo_file_id="file789",
        initiator_id=42,
        initiator_name="Ada",
        initiator_username="ada",
        trip=trip,
        transaction_date=date(2026, 9, 22),
    )
    assert draft.state == "manual_amount"
    text, markup = render_draft(draft, trip)
    assert "Couldn't read that receipt" in text
    assert [b.text for row in markup.inline_keyboard for b in row] == [
        "Try again",
        "Enter amount manually",
    ]

    prompting = await service.request_manual_amount(draft.draft_id, presser_id=42)
    text2, markup2 = render_draft(prompting, trip)
    assert "Reply to this message" in text2
    assert markup2 is None

    settled = await service.set_manual_amount(draft.draft_id, 42, trip, 2400, "JPY")
    assert settled.state == "detected"
    assert settled.amount_minor == 2400
    assert settled.source_type == "receipt"
    assert settled.line_items == []


async def test_line_items_written_on_commit() -> None:
    service, ledger, trip = await _setup()
    extraction = _confident_extraction()
    draft = await service.start_from_receipt(
        chat_id=-100,
        message_id=5,
        photo_file_id="file999",
        initiator_id=42,
        initiator_name="Ada",
        initiator_username="ada",
        trip=trip,
        extraction=extraction,
        currency="JPY",
        transaction_date=date(2026, 9, 20),
        date_source="receipt",
    )
    preview = await service.choose_just_me(draft.draft_id, presser_id=42)
    assert preview.state == "preview"
    committed = await service.confirm(draft.draft_id, presser_id=42)
    assert committed.state == "committed"

    items = ledger.line_items_for(committed.draft_id)
    assert len(items) == 1
    assert items[0].description == "Onigiri"
    assert items[0].kind == "item"
