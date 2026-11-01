"""Sprint 13 rules below the handlers: draft expiry runs from the last touch, and a
receipt-total gap becomes one `Unlisted / rounding` adjustment line that keeps the
shares exact (spec §5) whatever the claims.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from app.models.receipt import ReceiptLineItem
from app.services.conversion import both_amounts
from app.services.drafts import (
    UNLISTED_DESCRIPTION,
    InMemoryDraftStore,
    with_unlisted_line,
)
from app.services.receipt_parser import line_item_sums
from app.services.splitting import Items, shares_in_both_currencies, split_items
from tests.test_drafts import _parsed, _setup

# -- Expiry: 30 minutes from the last touch -------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: int) -> None:
        self.now += timedelta(minutes=minutes)


async def _stored_draft(clock: _Clock):
    store = InMemoryDraftStore(now=clock)
    service, _ledger, trip = await _setup()
    draft = await service.start(
        chat_id=-100,
        message_id=1,
        initiator_id=42,
        initiator_name="Ada",
        initiator_username="ada",
        parsed=_parsed(),
        trip=trip,
        transaction_date=clock.now.date(),
    )
    await store.put(draft)
    return store, draft


async def test_a_draft_touched_within_thirty_minutes_survives() -> None:
    clock = _Clock()
    store, draft = await _stored_draft(clock)
    clock.advance(29)
    assert await store.get(draft.draft_id) is not None


async def test_a_draft_idle_for_thirty_one_minutes_expires() -> None:
    clock = _Clock()
    store, draft = await _stored_draft(clock)
    clock.advance(31)
    assert await store.get(draft.draft_id) is None
    clock.advance(-31)
    assert await store.get(draft.draft_id) is None  # and it is gone, not just hidden


async def test_an_old_draft_pressed_recently_survives() -> None:
    clock = _Clock()
    store, draft = await _stored_draft(clock)
    for _ in range(4):  # 80 minutes old, never idle for more than 20
        clock.advance(20)
        await store.put(draft)
    clock.advance(20)
    assert await store.get(draft.draft_id) is not None
    clock.advance(11)
    await store.sweep_expired()
    assert await store.get(draft.draft_id) is None


# -- The Unlisted / rounding line ---------------------------------------------------


def _item(amount: int) -> ReceiptLineItem:
    return ReceiptLineItem(description="Dish", quantity=1, amount_minor=amount, kind="item")


def test_positive_gap_is_a_service_charge_line() -> None:
    lines = with_unlisted_line([_item(1000)], 1029)
    assert lines[-1] == ReceiptLineItem(
        description=UNLISTED_DESCRIPTION, quantity=1, amount_minor=29, kind="service_charge"
    )


def test_negative_gap_is_a_discount_line() -> None:
    lines = with_unlisted_line([_item(1000)], 900)
    assert (lines[-1].kind, lines[-1].amount_minor) == ("discount", 100)


def test_no_gap_and_no_lines_add_nothing_and_it_is_idempotent() -> None:
    assert with_unlisted_line([_item(1000)], 1000) == [_item(1000)]
    assert with_unlisted_line([], 1000) == []
    once = with_unlisted_line([_item(1000)], 1029)
    assert with_unlisted_line(once, 1029) == once


@settings(max_examples=300, deadline=None)
@given(
    amounts=st.lists(st.integers(min_value=1, max_value=50_000), min_size=1, max_size=8),
    tax=st.integers(min_value=0, max_value=5_000),
    gap=st.integers(min_value=-500, max_value=500),
    people=st.integers(min_value=1, max_value=5),
    rate=st.decimals(min_value="0.0001", max_value="50", places=4),
    data=st.data(),
)
def test_gap_line_keeps_shares_exact(amounts, tax, gap, people, rate, data) -> None:
    lines = [_item(a) for a in amounts]
    if tax:
        lines.append(ReceiptLineItem(description="Tax", quantity=1, amount_minor=tax, kind="tax"))
    items_sum, adjustments_sum = line_item_sums(lines)
    total = items_sum + adjustments_sum + gap
    if total <= 0:
        return
    lines = with_unlisted_line(lines, total)
    participants = list(range(1, people + 1))
    claims = {}
    for index, item in enumerate(lines):
        if item.kind != "item":
            continue
        claimants = data.draw(
            st.lists(st.sampled_from(participants), min_size=1, max_size=people, unique=True)
        )
        claims[str(index)] = claimants
    result = split_items(lines, Items(claims=claims), participants, 1, total)
    assert sum(result.shares.values()) == total
    gap_lines = [i for i, line in enumerate(lines) if line.description == UNLISTED_DESCRIPTION]
    assert len(gap_lines) == (gap != 0)
    for index in gap_lines:
        assert sum(result.line_allocations[index].values()) == gap
    # And in both currencies (CLAUDE.md): a JPY receipt settled in SGD.
    trip_amount, home_amount = both_amounts(total, "JPY", "JPY", "SGD", rate)
    trip_shares, home_shares = shares_in_both_currencies(
        result.shares,
        original_currency="JPY",
        trip_currency="JPY",
        home_currency="SGD",
        trip_to_home_rate=rate,
        trip_amount_minor=trip_amount,
        home_amount_minor=home_amount,
        payer_id=1,
    )
    assert sum(trip_shares.values()) == trip_amount == total
    assert sum(home_shares.values()) == home_amount
