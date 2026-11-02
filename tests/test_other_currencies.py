"""Sprint 15: an expense in a third currency (neither the trip's nor the home
currency) is confirmed, converted at its own rate, and split; shares stay exact in
both the trip and home currencies.

Trip in every test: THB, home SGD. Rates: 1 THB = 0.0406 SGD, 1 MYR = 7.08 THB,
1 USD = 33.5 THB. VND has no published rate.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.models.exchange_rate import ExchangeRate
from app.services.conversion import expense_amounts
from app.services.exchange_rates import RateUnavailable
from app.services.llm_schemas import ReceiptExtraction
from app.services.splitting import shares_in_both_currencies, split_equal
from tests.group_sim import ADA, BO, CY, Card, FakeGroup

RATES = {
    ("THB", "SGD"): Decimal("0.0406"),
    ("MYR", "THB"): Decimal("7.08"),
    ("USD", "THB"): Decimal("33.5"),
}


class _PairRates:
    """A rate per currency pair; a missing pair is a fetch failure."""

    def __init__(self, rates: dict[tuple[str, str], Decimal]) -> None:
        self.rates = dict(rates)
        self.calls: list[tuple[str, str]] = []

    async def get_rate(self, base: str, quote: str, on_date: date) -> ExchangeRate:
        from datetime import UTC, datetime

        self.calls.append((base, quote))
        if (base, quote) not in self.rates:
            raise RateUnavailable("no rate")
        return ExchangeRate(
            base=base,
            quote=quote,
            requested_date=on_date,
            rate_date=on_date,
            rate=self.rates[(base, quote)],
            provider="static",
            retrieved_at=datetime.now(UTC),
        )


async def _group(*members, rates=RATES) -> FakeGroup:
    group = await FakeGroup.create(*members, trip_currency="THB")
    group.deps.draft_service._rates._provider = _PairRates(rates)
    return group


def _receipt(currency: str, items: list[tuple[str, int]], total: int) -> ReceiptExtraction:
    return ReceiptExtraction(
        merchant="Kedai",
        currency=currency,
        transaction_datetime=None,
        line_items=[
            {"description": d, "quantity": 1, "amount_minor": a, "kind": "item"} for d, a in items
        ],
        candidate_totals=[
            {"label_as_printed": "Total", "amount_minor": total, "is_grand_total_guess": True}
        ],
        description="Nasi lemak",
        category="food",
        confidence=0.9,
    )


MYR_RECEIPT = _receipt("MYR", [("Nasi lemak", 7000), ("Teh tarik", 5000)], 12000)


async def _confirmed_myr_receipt(group: FakeGroup) -> Card:
    card = await group.send_receipt(ADA, MYR_RECEIPT)
    assert "This receipt looks like it's in Malaysian ringgit (RM). Is that right?" in card.text
    assert card.buttons == ["Yes, MYR", "It's THB", "It's SGD", "Cancel"]
    await group.press(ADA, card, "Yes, MYR")
    if card.text.endswith("Who paid?"):
        await group.press(ADA, card, "Me")
    return card


# -- Receipts ----------------------------------------------------------------------


async def test_a_ringgit_receipt_is_confirmed_converted_and_split() -> None:
    group = await _group(ADA, BO)
    card = await _confirmed_myr_receipt(group)
    # RM 120.00 x 7.08 = ฿849.60; x 0.0406 = S$34.49.
    assert "RM120.00 (฿849.60 · S$34.49)" in card.text
    assert "Rate 1 MYR = 7.08000 THB (20 Sep)" in card.text
    assert "1 THB = 0.04060 SGD" in card.text
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Split equally")
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    assert (expense.original_currency, expense.original_amount_minor) == ("MYR", 12000)
    assert (expense.trip_amount_minor, expense.home_amount_minor) == (84960, 3449)
    assert expense.original_rate == Decimal("7.08")
    assert expense.original_rate_provider == "static"
    assert sum(s.share_trip_minor for s in expense.splits) == 84960
    assert sum(s.share_home_minor for s in expense.splits) == 3449


async def test_item_by_item_on_a_ringgit_receipt_stays_exact() -> None:
    group = await _group(ADA, BO, CY)
    card = await _confirmed_myr_receipt(group)
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Go item by item ▶")
    assert "<b>Nasi lemak</b> · RM70.00" in card.text
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Cy")
    await group.press(ADA, card, "Next ▶")
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    shares = {s.user_id: (s.share_trip_minor, s.share_home_minor) for s in expense.splits}
    assert sum(t for t, _ in shares.values()) == expense.trip_amount_minor
    assert sum(h for _, h in shares.values()) == expense.home_amount_minor
    assert shares[ADA.id][0] > shares[BO.id][0]  # RM70 vs RM25


async def test_its_thb_rereads_the_receipt_in_baht() -> None:
    group = await _group(ADA, BO)
    card = await group.send_receipt(ADA, MYR_RECEIPT)
    await group.press(ADA, card, "It's THB")
    if card.text.endswith("Who paid?"):
        await group.press(ADA, card, "Me")
    assert "฿120.00 (S$4.87)" in card.text
    assert "MYR" not in card.text


async def test_its_thb_rescales_when_the_decimals_differ() -> None:
    """Read as dong (no decimals), it was baht: 50,000 is ฿50,000.00, not ฿500."""
    group = await _group(ADA, BO)
    card = await group.send_receipt(ADA, _receipt("VND", [("Pho", 50000)], 50000))
    assert "Vietnamese dong (₫)" in card.text
    await group.press(ADA, card, "It's THB")
    if card.text.endswith("Who paid?"):
        await group.press(ADA, card, "Me")
    assert "฿50,000.00" in card.text


async def test_only_the_sender_can_answer_the_currency_question() -> None:
    group = await _group(ADA, BO)
    card = await group.send_receipt(ADA, MYR_RECEIPT)
    for label in ("Yes, MYR", "It's THB", "Cancel"):
        assert await group.press(BO, card, label) == "Only Ada can edit this"
    assert "Is that right?" in card.text


async def test_a_baht_receipt_is_not_asked_about() -> None:
    group = await _group(ADA, BO)
    card = await group.send_receipt(ADA, _receipt("THB", [("Pad thai", 12000)], 12000))
    assert "Is that right?" not in card.text
    assert "฿120.00" in card.text


# -- Typed expenses ----------------------------------------------------------------


async def test_fifty_dollars_on_a_baht_trip() -> None:
    group = await _group(ADA, BO)
    card = await group.send(ADA, "50 usd taxi")
    # US$50 x 33.5 = ฿1,675.00; x 0.0406 = S$68.005, half-even S$68.00.
    assert "US$50.00 (฿1,675.00 · S$68.00)" in card.text
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Split equally")
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    assert "Saved ✅ Taxi US$50.00 (฿1,675.00 · S$68.00)" in group.cards[card.message_id].text
    expense = group.ledger.expense_for(draft_id)
    assert {s.user_id: s.share_home_minor for s in expense.splits} == {ADA.id: 3400, BO.id: 3400}


async def test_custom_amounts_are_in_the_expenses_own_currency() -> None:
    group = await _group(ADA, BO)
    card = await group.send(ADA, "50 usd taxi")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Type amounts instead")
    await group.send(ADA, "Ada 20\nBo 30", reply_to=card)
    assert "Remaining: US$0.00 ✅" in group.cards[card.message_id].text
    await group.send(ADA, "Ada 100 baht", reply_to=card)
    assert "Use US$ amounts for this expense" in group.cards[card.message_id].text


async def test_a_rate_fetch_failure_offers_retry() -> None:
    group = await _group(ADA, BO, rates={("THB", "SGD"): Decimal("0.0406")})
    card = await group.send(ADA, "30 eur museum")
    assert "Couldn't fetch the exchange rate" in card.text
    assert card.buttons == ["Retry", "Cancel"]
    group.deps.draft_service._rates._provider.rates[("EUR", "THB")] = Decimal("38")
    await group.press(ADA, card, "Retry")
    assert "€30.00 (฿1,140.00 · S$46.28)" in card.text


# -- No published rate: typed ------------------------------------------------------


async def test_dong_asks_for_a_rate_and_takes_one() -> None:
    group = await _group(ADA, BO)
    card = await group.send(ADA, "100000 vnd pho")
    assert "No exchange rate for VND." in card.text
    assert card.buttons == ["Cancel"]
    await group.send(ADA, "0.0013", reply_to=card)
    card = group.cards[card.message_id]
    if card.text.endswith("Who paid?"):
        await group.press(ADA, card, "Me")
    # ₫100,000 x 0.0013 = ฿130.00; x 0.0406 = S$5.28.
    assert "₫100,000 (฿130.00 · S$5.28)" in card.text
    assert "Rate 1 VND = 0.00130 THB (typed)" in card.text
    await group.press(ADA, card, "Just me")
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    assert expense.original_rate_provider == "typed"
    assert expense.trip_amount_minor == 13000


async def test_dong_takes_the_total_in_baht_instead() -> None:
    group = await _group(ADA, BO)
    card = await group.send(ADA, "100000 vnd pho")
    await group.send(ADA, "฿130", reply_to=card)
    card = group.cards[card.message_id]
    assert "₫100,000 (฿130.00 · S$5.28)" in card.text


async def test_an_unreadable_rate_says_so_and_waits() -> None:
    group = await _group(ADA, BO)
    card = await group.send(ADA, "100000 vnd pho")
    await group.send(ADA, "about a dollar", reply_to=card)
    card = group.cards[card.message_id]
    assert "Couldn't read 'about a dollar'" in card.text
    assert "No exchange rate for VND." in card.text


async def test_someone_elses_rate_reply_is_ignored() -> None:
    group = await _group(ADA, BO)
    card = await group.send(ADA, "100000 vnd pho")
    assert await group.send(BO, "0.0013", reply_to=card) is None
    assert "No exchange rate for VND." in group.cards[card.message_id].text


# -- The maths ---------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(
    amount=st.integers(1, 10_000_000),
    people=st.integers(1, 6),
    original=st.sampled_from(["VND", "MYR", "USD", "KWD"]),
    trip=st.sampled_from(["THB", "JPY"]),
    home=st.sampled_from(["SGD", "KRW"]),
    original_rate=st.decimals(min_value="0.0001", max_value="5000", places=4),
    trip_rate=st.decimals(min_value="0.0001", max_value="50", places=4),
)
def test_third_currency_shares_are_exact_in_both(
    amount, people, original, trip, home, original_rate, trip_rate
) -> None:
    trip_amount, home_amount = expense_amounts(
        amount, original, trip, home, trip_rate, original_rate
    )
    participants = list(range(1, people + 1))
    shares = split_equal(amount, participants, 1)
    trip_shares, home_shares = shares_in_both_currencies(
        shares,
        original_currency=original,
        trip_currency=trip,
        home_currency=home,
        trip_to_home_rate=trip_rate,
        trip_amount_minor=trip_amount,
        home_amount_minor=home_amount,
        payer_id=1,
        original_to_trip_rate=original_rate,
    )
    assert sum(trip_shares.values()) == trip_amount
    assert sum(home_shares.values()) == home_amount
