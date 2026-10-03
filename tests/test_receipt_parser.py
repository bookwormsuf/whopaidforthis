"""Tests for app.services.receipt_parser and app.services.llm_schemas (sprint 4 pins)."""

from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest
from PIL import Image
from pydantic import ValidationError

from app.models.receipt import CandidateTotal, ReceiptLineItem
from app.models.trip import Trip
from app.services.images import prepare_image
from app.services.llm_schemas import ReceiptExtraction
from app.services.receipt_parser import (
    AnthropicReceiptReader,
    ReceiptUnreadable,
    drop_included_tax,
    needs_reconciliation,
    reconcile,
    resolve_receipt_currency,
    resolve_transaction_date,
    select_total,
    to_receipt,
)

FIXTURES = Path(__file__).parent / "fixtures" / "receipts"


def _fixture(name: str) -> ReceiptExtraction:
    data = json.loads((FIXTURES / name).read_text())
    return ReceiptExtraction.model_validate(data)


def _receipt(name: str):
    return to_receipt(_fixture(name), "JPY")


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


# -- select_total -------------------------------------------------------------


def test_select_total_one_confident_candidate_within_tolerance_saves() -> None:
    receipt = _receipt("clean_konbini_jpy.json")
    decision = select_total(receipt)
    assert decision.status == "confident"
    assert decision.chosen_total_minor == 420


def test_select_total_two_candidates_asks() -> None:
    receipt = _receipt("two_candidates_tax.json")
    decision = select_total(receipt)
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
    decision = select_total(to_receipt(extraction, "JPY"))
    assert decision.status == "confident"
    assert decision.chosen_total_minor == 2400
    assert decision.computed_total_minor is None


def test_empty_candidates_is_unreadable() -> None:
    """An empty `candidate_totals` (the prompt asks for one on a blurry photo or a
    non-receipt) fails validation into the domain `Receipt`.
    """
    with pytest.raises(ReceiptUnreadable):
        to_receipt(_fixture("unreadable.json"), "JPY")


def test_select_total_with_service_charge_is_confident() -> None:
    receipt = _receipt("service_charge.json")
    decision = select_total(receipt)
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
    extraction = _receipt("clean_konbini_jpy.json")
    message_time = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    source, resolved = resolve_transaction_date(extraction, message_time, "Asia/Tokyo")
    assert source == "receipt"
    assert resolved.isoformat() == "2026-09-20"


def test_date_beyond_30_days_falls_back_to_message_time() -> None:
    extraction = _receipt("clean_konbini_jpy.json")  # printed 2026-09-20
    message_time = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    source, resolved = resolve_transaction_date(extraction, message_time, "Asia/Tokyo")
    assert source == "message"
    assert resolved.isoformat() == "2026-11-05"


def test_no_printed_date_falls_back_to_message_time() -> None:
    extraction = _receipt("service_charge.json").model_copy(update={"transaction_datetime": None})
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
            parsed_output=outcome,
            stop_reason="end_turn" if outcome is not None else "refusal",
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
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


async def test_reader_retries_when_no_parsed_output() -> None:
    """A refusal or max_tokens cut-off returns `parsed_output=None` rather than
    raising; it gets the one retry, then gives up as unreadable.
    """
    good = _fixture("clean_konbini_jpy.json")
    client = _FakeAnthropicClient([None, good])
    reader = AnthropicReceiptReader(client)
    assert await reader.read(_tiny_image(), trip=_trip()) == good

    client = _FakeAnthropicClient([None, None])
    with pytest.raises(ReceiptUnreadable):
        await AnthropicReceiptReader(client).read(_tiny_image(), trip=_trip())


def test_unparseable_printed_date_becomes_none() -> None:
    extraction = _fixture("clean_konbini_jpy.json").model_copy(
        update={"transaction_datetime": "20th Sept"}
    )
    assert to_receipt(extraction, "JPY").transaction_datetime is None


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


def _same_total_three_times(**extra) -> ReceiptExtraction:
    """A receipt printing one total on three lines (e.g. 内税品計, 合計, 現金計), two of
    them flagged as the grand total, plus whatever `extra` candidates are given.
    """
    lines = [("内税品計", False), ("合計", True), ("現金計", True)]
    candidates = [
        {"label_as_printed": label, "amount_minor": 1000, "is_grand_total_guess": guess}
        for label, guess in lines
    ] + extra.get("more", [])
    return ReceiptExtraction(
        line_items=[{"description": "Sushi", "quantity": 1, "amount_minor": 1000, "kind": "item"}],
        candidate_totals=candidates,
        description="Sushi",
        category="food",
        confidence=0.9,
    )


def test_same_amount_on_several_lines_is_one_confident_total() -> None:
    decision = select_total(to_receipt(_same_total_three_times(), "JPY"))
    assert decision.status == "confident"
    assert decision.chosen_total_minor == 1000


def test_ask_shows_each_amount_once() -> None:
    cash = {"label_as_printed": "お預り", "amount_minor": 1500, "is_grand_total_guess": True}
    decision = select_total(to_receipt(_same_total_three_times(more=[cash]), "JPY"))
    assert decision.status == "ask"
    assert sorted(c.amount_minor for c in decision.candidates) == [1000, 1500]


def test_needs_reconciliation_only_beyond_tolerance() -> None:
    assert not needs_reconciliation(1000, None)
    assert not needs_reconciliation(1000, 1005)
    assert needs_reconciliation(1000, 1006)


# -- Included tax read as an extra tax line (decided 2026-10-02) ----------------


def _li(description: str, amount: int, kind: str = "item") -> ReceiptLineItem:
    return ReceiptLineItem(description=description, quantity=1, amount_minor=amount, kind=kind)


def _total(amount: int, guess: bool = True) -> CandidateTotal:
    return CandidateTotal(label_as_printed="合計", amount_minor=amount, is_grand_total_guess=guess)


def test_included_tax_line_is_dropped_when_that_matches_the_total() -> None:
    lines = [_li("寿司", 1455), _li("寿司", 162), _li("消費税等(内税)", 119, "tax")]
    assert drop_included_tax(lines, [_total(1617)]) == lines[:2]


def test_real_tax_line_is_kept() -> None:
    lines = [_li("Ramen", 1000), _li("Tax", 100, "tax")]
    assert drop_included_tax(lines, [_total(1100)]) == lines


def test_tax_kept_when_dropping_it_matches_only_a_subtotal() -> None:
    lines = [_li("Ramen", 1000), _li("Tax", 100, "tax")]
    # Lines make 1,100, the total says 1,150; 1,000 is only a subtotal, not a guess.
    assert drop_included_tax(lines, [_total(1150), _total(1000, guess=False)]) == lines


def test_to_receipt_applies_it() -> None:
    extraction = ReceiptExtraction(
        merchant="Sushi",
        currency="JPY",
        transaction_datetime=None,
        line_items=[
            {"description": "寿司", "quantity": 1, "amount_minor": 1617, "kind": "item"},
            {"description": "消費税等(内税)", "quantity": 1, "amount_minor": 147, "kind": "tax"},
        ],
        candidate_totals=[
            {"label_as_printed": "合計", "amount_minor": 1617, "is_grand_total_guess": True}
        ],
        description="Sushi",
        category="food",
        confidence=0.9,
    )
    receipt = to_receipt(extraction, "JPY")
    assert [i.kind for i in receipt.line_items] == ["item"]


# -- Amounts read 100 times off (found at go-live, 2026-10-02) ---------------------


def _scaled_extraction(item: int, total: int, printed: str | None) -> ReceiptExtraction:
    return ReceiptExtraction(
        merchant="bills",
        currency="JPY",
        transaction_datetime=None,
        line_items=[
            {
                "description": "Orecchiette",
                "quantity": 1,
                "unit_amount_minor": item,
                "amount_minor": item,
                "kind": "item",
            }
        ],
        candidate_totals=[
            {
                "label_as_printed": "合計",
                "amount_minor": total,
                "is_grand_total_guess": True,
                "amount_as_printed": printed,
            }
        ],
        description="Lunch",
        category="food",
        confidence=0.9,
    )


def test_yen_read_as_cents_is_rescaled() -> None:
    receipt = to_receipt(_scaled_extraction(685000, 685000, "¥6,850"), "JPY")
    assert receipt.candidate_totals[0].amount_minor == 6850
    assert receipt.line_items[0].amount_minor == 6850
    assert receipt.line_items[0].unit_amount_minor == 6850


def test_cents_read_as_dollars_is_rescaled() -> None:
    receipt = to_receipt(_scaled_extraction(12, 12, "S$12.00"), "SGD")
    assert receipt.candidate_totals[0].amount_minor == 1200
    assert receipt.line_items[0].amount_minor == 1200


def test_correct_or_unprinted_amounts_are_left_alone() -> None:
    assert (
        to_receipt(_scaled_extraction(6850, 6850, "¥6,850"), "JPY").line_items[0].amount_minor
        == 6850
    )
    assert (
        to_receipt(_scaled_extraction(685000, 685000, None), "JPY").line_items[0].amount_minor
        == 685000
    )
    assert (
        to_receipt(_scaled_extraction(6850, 6850, "合計"), "JPY").line_items[0].amount_minor == 6850
    )


def test_prompt_explains_minor_units() -> None:
    from app.services.receipt_parser import SYSTEM_PROMPT_TEMPLATE

    assert "never 685000" in SYSTEM_PROMPT_TEMPLATE
    assert "amount_as_printed" in SYSTEM_PROMPT_TEMPLATE


# -- The reader service failing, not the photo (added 2026-10-03) --------------


def test_service_problem_names_api_failures() -> None:
    import httpx2

    from app.services.receipt_parser import _service_problem

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def status_error(status: int, message: str) -> anthropic.APIStatusError:
        response = httpx2.Response(status, request=request)
        return anthropic.APIStatusError(message, response=response, body=None)

    assert _service_problem(status_error(401, "invalid x-api-key")) == "refusing the API key"
    assert (
        _service_problem(status_error(400, "Your credit balance is too low")) == "out of API credit"
    )
    assert _service_problem(status_error(429, "rate limited")) == "busy"
    assert _service_problem(status_error(529, "overloaded")) == "temporarily down"
    assert _service_problem(anthropic.APIConnectionError(request=request)) == "unreachable"
    assert _service_problem(ValueError("bad JSON")) is None


# -- Forgiving readings (decided 2026-10-03) -----------------------------------


def test_negative_amounts_and_zero_quantities_do_not_reject_the_reading() -> None:
    extraction = ReceiptExtraction(
        merchant="MOON",
        currency="JPY",
        transaction_datetime=None,
        line_items=[
            {"description": "Ice drip", "quantity": 1, "amount_minor": 1300, "kind": "item"},
            {"description": "Hot drip", "quantity": 0, "amount_minor": 3000, "kind": "item"},
            {"description": "¥200 off coupon", "quantity": 1, "amount_minor": -200, "kind": "item"},
        ],
        candidate_totals=[
            {"label_as_printed": "合計", "amount_minor": 4300, "is_grand_total_guess": True},
            {"label_as_printed": "お釣り", "amount_minor": 0, "is_grand_total_guess": False},
        ],
        description="Coffee",
        category="drinks",
        confidence=1.2,
    )
    receipt = to_receipt(extraction, "JPY")
    assert [(i.kind, i.amount_minor, i.quantity) for i in receipt.line_items] == [
        ("item", 1300, 1),
        ("item", 3000, 1),
        ("discount", 200, 1),
    ]
    assert [c.amount_minor for c in receipt.candidate_totals] == [4300]
    assert receipt.confidence == 1.0


def test_long_reader_titles_fall_back_to_the_shop_name() -> None:
    from app.services.receipt_parser import _short_title

    long = "Coffee purchase at Moon Coffee Osaka - Colombia iced drip and hot drip"
    assert _short_title(long, "MOON COFFEE OSAKA") == "MOON COFFEE OSAKA"
    assert _short_title("Moon Coffee", "MOON COFFEE OSAKA") == "Moon Coffee"
    assert _short_title(long, None) == "Coffee purchase at Moon"
    assert _short_title("  ", "Moon Coffee") == "Moon Coffee"
    assert _short_title("", None) == ""
    shop = "Moon Coffee Roasters Osaka Namba Parks Branch Store"
    assert _short_title(long, shop) == "Moon Coffee Roasters Osaka Namba Parks…"
