"""Tests for the receipt-sourced draft flow in app.services.drafts.DraftService
(sprint 4 pins): choose_total, reconcile, manual_amount, and the Line Items write
path on commit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.bot.render.draft import render_draft
from app.bot.session_state import Dedup
from app.models.category import Category
from app.models.receipt import Receipt
from app.models.trip import Trip
from app.services.drafts import DraftError, DraftService, InMemoryDraftStore
from app.services.exchange_rates import RateService, StaticRateProvider
from app.services.ledger_store import InMemoryLedgerStore
from app.services.llm_schemas import ReceiptExtraction
from app.services.receipt_parser import to_receipt
from app.services.sheet_rows import line_item_to_row


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


def _receipt(currency: str = "JPY", **fields) -> Receipt:
    return to_receipt(ReceiptExtraction(**fields), currency)


def _confident_receipt() -> Receipt:
    return _receipt(
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
    receipt = _confident_receipt()

    draft = await service.start_from_receipt(
        chat_id=-100,
        message_id=1,
        photo_file_id="file123",
        initiator_id=42,
        initiator_name="Ada",
        initiator_username="ada",
        trip=trip,
        receipt=receipt,
        transaction_date=date(2026, 9, 20),
        date_source="receipt",
    )
    assert draft.state == "detected"
    assert draft.amount_minor == 300
    assert draft.merchant == "Lawson"
    assert len(draft.line_items) == 1

    text, markup = render_draft(draft, trip)
    assert "Lawson" in text
    assert "1 item on the receipt" in text
    assert "from receipt" in text


async def test_ambiguous_total_asks_then_choose_total_proceeds() -> None:
    service, ledger, trip = await _setup()
    receipt = _receipt(
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
        receipt=receipt,
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


async def test_confident_total_within_tolerance_skips_reconciliation() -> None:
    """One confident candidate within ±5 of items + adjustments saves straight to the
    normal card (spec §7, amended 2026-10-01): per-line tax rounding must not cost an
    extra tap.
    """
    service, ledger, trip = await _setup()
    receipt = _receipt(
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
        receipt=receipt,
        transaction_date=date(2026, 9, 21),
        date_source="receipt",
    )
    assert draft.state == "detected"
    assert draft.amount_minor == 2383


async def test_reconcile_state_renders_numbers() -> None:
    """A total picked from the buttons that differs from items + adjustments gets the
    reconciliation prompt, showing items sum, adjustments, total and difference.
    """
    service, ledger, trip = await _setup()
    receipt = _receipt(
        line_items=[
            {"description": "Ramen", "quantity": 1, "amount_minor": 2380, "kind": "item"},
            {"description": "Tax", "quantity": 1, "amount_minor": 100, "kind": "tax"},
        ],
        candidate_totals=[
            {"label_as_printed": "小計", "amount_minor": 2380, "is_grand_total_guess": False},
            {"label_as_printed": "合計", "amount_minor": 2618, "is_grand_total_guess": False},
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
        receipt=receipt,
        transaction_date=date(2026, 9, 21),
        date_source="receipt",
    )
    assert draft.state == "choose_total"
    # No grand-total guesses, so largest first: index 0 is 合計 (2618).
    chosen = await service.choose_total(draft.draft_id, presser_id=42, index=0)
    assert chosen.state == "reconcile"
    text, markup = render_draft(chosen, trip)
    assert "¥2,380" in text  # items
    assert "¥100" in text  # adjustments
    assert "¥2,618" in text  # total
    assert "¥138" in text  # difference
    assert [b.text for row in markup.inline_keyboard for b in row] == [
        "Fix items",
        "Use total anyway",
        "Cancel",
    ]


async def test_choose_total_matching_computed_skips_reconciliation() -> None:
    service, ledger, trip = await _setup()
    receipt = _receipt(
        line_items=[{"description": "Ramen", "quantity": 1, "amount_minor": 2380, "kind": "item"}],
        candidate_totals=[
            {"label_as_printed": "小計", "amount_minor": 2380, "is_grand_total_guess": False},
            {"label_as_printed": "お預り", "amount_minor": 3000, "is_grand_total_guess": False},
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
        receipt=receipt,
        transaction_date=date(2026, 9, 21),
        date_source="receipt",
    )
    chosen = await service.choose_total(draft.draft_id, presser_id=42, index=1)  # 2380
    assert chosen.state == "detected"
    assert chosen.amount_minor == 2380


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
    receipt = _confident_receipt()
    draft = await service.start_from_receipt(
        chat_id=-100,
        message_id=5,
        photo_file_id="file999",
        initiator_id=42,
        initiator_name="Ada",
        initiator_username="ada",
        trip=trip,
        receipt=receipt,
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


async def test_caption_survives_unreadable_then_manual_amount() -> None:
    service, ledger, trip = await _setup()
    draft = await service.start_unreadable(
        chat_id=-100,
        message_id=6,
        photo_file_id="f",
        initiator_id=42,
        initiator_name="Ada",
        initiator_username="ada",
        trip=trip,
        transaction_date=date(2026, 9, 22),
        caption_override=("Dinner", Category.FOOD),
    )
    assert draft.description == "Dinner"
    await service.request_manual_amount(draft.draft_id, presser_id=42)
    settled = await service.set_manual_amount(draft.draft_id, 42, trip, 2400, "JPY")
    assert settled.description == "Dinner"
    assert settled.category == Category.FOOD


async def test_retry_success_keeps_caption_and_refuses_double_press() -> None:
    service, ledger, trip = await _setup()
    draft = await service.start_unreadable(
        chat_id=-100,
        message_id=7,
        photo_file_id="f",
        initiator_id=42,
        initiator_name="Ada",
        initiator_username="ada",
        trip=trip,
        transaction_date=date(2026, 9, 22),
        caption_override=("Dinner", Category.FOOD),
    )
    await service.begin_retry(draft.draft_id, presser_id=42)
    with pytest.raises(DraftError):
        await service.begin_retry(draft.draft_id, presser_id=42)

    retried = await service.retry_receipt(
        draft.draft_id,
        42,
        trip=trip,
        receipt=_confident_receipt(),
        transaction_date=date(2026, 9, 20),
        date_source="receipt",
    )
    assert retried.state == "detected"
    assert retried.description == "Dinner"
    assert retried.merchant == "Lawson"
    assert retried.reading_receipt is False


async def test_retry_failure_allows_another_try() -> None:
    service, ledger, trip = await _setup()
    draft = await service.start_unreadable(
        chat_id=-100,
        message_id=8,
        photo_file_id="f",
        initiator_id=42,
        initiator_name="Ada",
        initiator_username="ada",
        trip=trip,
        transaction_date=date(2026, 9, 22),
    )
    await service.begin_retry(draft.draft_id, presser_id=42)
    again = await service.retry_unreadable(draft.draft_id, presser_id=42)
    assert again.state == "manual_amount"
    await service.begin_retry(draft.draft_id, presser_id=42)  # does not raise


async def test_home_currency_line_items_written_in_trip_currency() -> None:
    """An SGD receipt on a JPY/SGD trip: `Amount <T>` holds each item converted to
    yen at the expense's rate, not the SGD cents read as yen.
    """
    service, ledger, trip = await _setup()
    receipt = _receipt(
        "SGD",
        line_items=[
            {"description": "Chicken Rice", "quantity": 1, "amount_minor": 1250, "kind": "item"}
        ],
        candidate_totals=[
            {"label_as_printed": "Total", "amount_minor": 1250, "is_grand_total_guess": True}
        ],
        description="Lunch",
        category="food",
        confidence=0.9,
    )
    draft = await service.start_from_receipt(
        chat_id=-100,
        message_id=9,
        photo_file_id="f",
        initiator_id=42,
        initiator_name="Ada",
        initiator_username="ada",
        trip=trip,
        receipt=receipt,
        transaction_date=date(2026, 9, 20),
        date_source="receipt",
    )
    await service.choose_just_me(draft.draft_id, presser_id=42)
    committed = await service.confirm(draft.draft_id, presser_id=42)

    expense = ledger.expense_for(committed.draft_id)
    row = line_item_to_row(ledger.line_items_for(committed.draft_id)[0], expense, 1)
    # S$12.50 / 0.0081 = ¥1,543.2... -> 1543
    assert row[4] == 1543.0
    assert row[1] == f"{committed.draft_id}-01"
    assert row[5:] == ["", "unassigned", "item"]
    claimed = line_item_to_row(
        ledger.line_items_for(committed.draft_id)[0], expense, 1, [42, 7], "claimed"
    )
    assert claimed[5:] == ["42,7", "claimed", "item"]


async def test_single_total_far_from_items_asks_to_reconcile() -> None:
    """Only one total printed, but the line items are well off it (e.g. an item the
    reader missed): no total question, but the reconciliation prompt. (An included
    tax read as an extra tax line is dropped before this, see
    `test_receipt_parser.py::test_included_tax_line_is_dropped_when_that_matches_the_total`.)
    """
    service, ledger, trip = await _setup()
    receipt = _receipt(
        line_items=[
            {"description": "Sushi", "quantity": 1, "amount_minor": 1000, "kind": "item"},
            {"description": "Tax", "quantity": 1, "amount_minor": 90, "kind": "tax"},
        ],
        candidate_totals=[
            {"label_as_printed": "合計", "amount_minor": 1200, "is_grand_total_guess": True},
            {"label_as_printed": "現金計", "amount_minor": 1200, "is_grand_total_guess": True},
        ],
        description="Sushi",
        category="food",
        confidence=0.9,
    )
    draft = await service.start_from_receipt(
        chat_id=-100,
        message_id=10,
        photo_file_id="f",
        initiator_id=42,
        initiator_name="Ada",
        initiator_username="ada",
        trip=trip,
        receipt=receipt,
        transaction_date=date(2026, 9, 20),
        date_source="receipt",
    )
    assert draft.state == "reconcile"
    assert draft.amount_minor == 1200
    proceeded = await service.use_total_anyway(draft.draft_id, presser_id=42)
    assert proceeded.state == "detected"
    assert proceeded.amount_minor == 1200
