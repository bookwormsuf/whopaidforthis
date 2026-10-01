"""Tests for app.services.receipt_parser and app.services.llm_schemas (sprint 4 pins)."""

from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from app.models.trip import Trip
from app.services.images import prepare_image
from app.services.llm_schemas import ReceiptExtraction
from app.services.receipt_parser import (
    AnthropicReceiptReader,
    ReceiptUnreadable,
    reconcile,
    resolve_receipt_currency,
    resolve_transaction_date,
    select_total,
)

FIXTURES = Path(__file__).parent / "fixtures" / "receipts"


def _fixture(name: str) -> ReceiptExtraction:
    data = json.loads((FIXTURES / name).read_text())
    return ReceiptExtraction.model_validate(data)


def _tiny_image():
    img = Image.new("RGB", (10, 10), color=(1, 2, 3))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return prepare_image(buf.getvalue())


def _trip(trip_currency: str = "JPY", home_currency: str = "SGD") -> Trip:
    return Trip(
        chat_id=-100,
        sheet_id="sheet-id-1234567890",
        name="Japan Trip",
        trip_currency=trip_currency,
        home_currency=home_currency,
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )


# -- Schema validation -------------------------------------------------------


def test_schema_rejects_missing_description() -> None:
    with pytest.raises(ValidationError):
        ReceiptExtraction.model_validate(
            {"category": "food", "confidence": 0.5, "line_items": [], "candidate_totals": []}
        )


def test_schema_rejects_negative_line_item_amount() -> None:
    with pytest.raises(ValidationError):
        ReceiptExtraction.model_validate(
            {
                "description": "Expense",
                "category": "food",
                "confidence": 0.5,
                "line_items": [
                    {"description": "Item", "quantity": 1, "amount_minor": -100, "kind": "item"}
                ],
                "candidate_totals": [],
            }
        )


def test_schema_rejects_negative_candidate_total() -> None:
    with pytest.raises(ValidationError):
        ReceiptExtraction.model_validate(
            {
                "description": "Expense",
                "category": "food",
                "confidence": 0.5,
                "line_items": [],
                "candidate_totals": [
                    {"label_as_printed": "Total", "amount_minor": -5, "is_grand_total_guess": True}
                ],
            }
        )


def test_schema_rejects_quantity_below_one() -> None:
    with pytest.raises(ValidationError):
        ReceiptExtraction.model_validate(
            {
                "description": "Expense",
                "category": "food",
                "confidence": 0.5,
                "line_items": [
                    {"description": "Item", "quantity": 0, "amount_minor": 100, "kind": "item"}
                ],
                "candidate_totals": [],
            }
        )


def test_schema_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        ReceiptExtraction.model_validate(
            {
                "description": "Expense",
                "category": "food",
                "confidence": 1.5,
                "line_items": [],
                "candidate_totals": [],
            }
        )


# -- select_total -------------------------------------------------------------


def test_select_total_one_confident_candidate_within_tolerance_saves() -> None:
    extraction = _fixture("clean_konbini_jpy.json")
    decision = select_total(extraction)
    assert decision.status == "confident"
    assert decision.chosen_total_minor == 420


def test_select_total_two_candidates_asks() -> None:
    extraction = _fixture("two_candidates_tax.json")
    decision = select_total(extraction)
    assert decision.status == "ask"
    assert decision.chosen_total_minor is None
    # Grand-total guesses first, then largest.
    assert decision.candidates[0].label_as_printed == "合計(税込)"
    assert len(decision.candidates) <= 3


def test_select_total_no_line_items_uses_the_confident_candidate() -> None:
    extraction = ReceiptExtraction(
        description="Taxi",
        category="transport",
        confidence=0.9,
        line_items=[],
        candidate_totals=[
            {"label_as_printed": "Total", "amount_minor": 2400, "is_grand_total_guess": True}
        ],
    )
    decision = select_total(extraction)
    assert decision.status == "confident"
    assert decision.chosen_total_minor == 2400
    assert decision.computed_total_minor is None


def test_select_total_empty_candidates_raises_unreadable() -> None:
    extraction = _fixture("unreadable.json")
    with pytest.raises(ReceiptUnreadable):
        select_total(extraction)


def test_select_total_with_service_charge_is_confident() -> None:
    extraction = _fixture("service_charge.json")
    decision = select_total(extraction)
    assert decision.status == "confident"
    assert decision.chosen_total_minor == 12100
    assert decision.computed_total_minor == 12100


# -- reconcile ------------------------------------------------------------


def test_reconcile_returns_none_when_total_matches_computed() -> None:
    result = reconcile(items_sum_minor=1000, adjustments_sum_minor=100, chosen_total_minor=1100)
    assert result is None


def test_reconcile_numbers_when_total_differs() -> None:
    result = reconcile(items_sum_minor=2380, adjustments_sum_minor=0, chosen_total_minor=2618)
    assert result is not None
    assert result.items_sum_minor == 2380
    assert result.adjustments_sum_minor == 0
    assert result.total_minor == 2618
    assert result.difference_minor == 238


# -- resolve_transaction_date -----------------------------------------------


def test_date_within_30_days_uses_printed_date() -> None:
    extraction = _fixture("clean_konbini_jpy.json")
    message_time = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    source, resolved = resolve_transaction_date(extraction, message_time, "Asia/Tokyo")
    assert source == "receipt"
    assert resolved.isoformat() == "2026-09-20"


def test_date_beyond_30_days_falls_back_to_message_time() -> None:
    extraction = _fixture("clean_konbini_jpy.json")  # printed 2026-09-20
    message_time = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    source, resolved = resolve_transaction_date(extraction, message_time, "Asia/Tokyo")
    assert source == "message"
    assert resolved.isoformat() == "2026-11-05"


def test_no_printed_date_falls_back_to_message_time() -> None:
    extraction = _fixture("unreadable.json")
    message_time = datetime(2026, 9, 25, 23, 30, tzinfo=UTC)  # late UTC -> next day in Tokyo
    source, resolved = resolve_transaction_date(extraction, message_time, "Asia/Tokyo")
    assert source == "message"
    assert resolved.isoformat() == "2026-09-26"


# -- resolve_receipt_currency -----------------------------------------------


def test_missing_currency_defaults_to_trip_currency() -> None:
    assert resolve_receipt_currency(None, _trip()) == "JPY"


def test_trip_or_home_currency_is_accepted() -> None:
    trip = _trip()
    assert resolve_receipt_currency("JPY", trip) == "JPY"
    assert resolve_receipt_currency("sgd", trip) == "SGD"


def test_other_currency_is_refused() -> None:
    assert resolve_receipt_currency("USD", _trip()) is None


# -- AnthropicReceiptReader: retry-once behaviour ----------------------------


class _FakeAnthropicMessages:
    def __init__(self, outcomes: list) -> None:
        self._outcomes = outcomes
        self.calls = 0

    def parse(self, **kwargs):
        outcome = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            parsed_output=outcome, usage=SimpleNamespace(input_tokens=100, output_tokens=50)
        )


class _FakeAnthropicClient:
    def __init__(self, outcomes: list) -> None:
        self.messages = _FakeAnthropicMessages(outcomes)


async def test_reader_retries_once_then_succeeds() -> None:
    good = _fixture("clean_konbini_jpy.json")
    client = _FakeAnthropicClient([ValueError("bad json"), good])
    reader = AnthropicReceiptReader(client)

    result = await reader.read(_tiny_image(), trip=_trip())
    assert result == good
    assert client.messages.calls == 2


async def test_reader_gives_up_after_second_failure() -> None:
    client = _FakeAnthropicClient([ValueError("bad json"), ValueError("still bad")])
    reader = AnthropicReceiptReader(client)

    with pytest.raises(ReceiptUnreadable):
        await reader.read(_tiny_image(), trip=_trip())
    assert client.messages.calls == 2


# -- No image bytes/base64 in logs -------------------------------------------


async def test_no_image_bytes_in_log_records(caplog: pytest.LogCaptureFixture) -> None:
    image = _tiny_image()
    good = _fixture("clean_konbini_jpy.json")
    client = _FakeAnthropicClient([good])
    reader = AnthropicReceiptReader(client)

    with caplog.at_level(logging.DEBUG):
        await reader.read(image, trip=_trip())

    for record in caplog.records:
        formatted = record.getMessage()
        assert image.data_b64 not in formatted
        assert image.data_b64[:40] not in formatted
