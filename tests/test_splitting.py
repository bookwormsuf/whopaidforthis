"""Tests for app.services.splitting."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.models.receipt import ReceiptLineItem
from app.services.conversion import both_amounts, convert_minor
from app.services.splitting import (
    SPLIT_SPEC_ADAPTER,
    Custom,
    Equal,
    Items,
    Percent,
    SplitError,
    allocate_proportional,
    convert_shares,
    shares_in_both_currencies,
    split_custom,
    split_equal,
    split_items,
    split_shares,
)


def test_split_equal_just_me() -> None:
    assert split_equal(1200, [1], 1) == {1: 1200}


def test_split_equal_payer_skipped_while_others_remain() -> None:
    # 100 / 3 = 33 remainder 1; the payer (id 1) must not get the extra unit while
    # participant 2 can.
    shares = split_equal(100, [1, 2, 3], payer_id=1)
    assert sum(shares.values()) == 100
    assert shares[1] == 33
    assert shares[2] == 34
    assert shares[3] == 33


def test_split_equal_payer_only_participant_gets_everything() -> None:
    assert split_equal(999, [5], payer_id=5) == {5: 999}


def test_split_equal_remainder_distributed_ascending_by_id() -> None:
    shares = split_equal(10, [30, 10, 20], payer_id=99)  # payer not a participant
    assert sum(shares.values()) == 10
    # base = 3 each, remainder 1 -> lowest id (10) gets it.
    assert shares[10] == 4
    assert shares[20] == 3
    assert shares[30] == 3


@given(
    total_minor=st.integers(min_value=0, max_value=10_000_000),
    participant_ids=st.lists(
        st.integers(min_value=1, max_value=1000), min_size=1, max_size=20, unique=True
    ),
)
def test_split_equal_invariants(total_minor: int, participant_ids: list[int]) -> None:
    payer_id = participant_ids[0]
    shares = split_equal(total_minor, participant_ids, payer_id)

    assert sum(shares.values()) == total_minor
    values = list(shares.values())
    assert max(values) - min(values) <= 1

    if len(participant_ids) > 1 and total_minor % len(participant_ids) != 0:
        base = total_minor // len(participant_ids)
        others_have_extra = any(shares[pid] > base for pid in participant_ids if pid != payer_id)
        if others_have_extra:
            assert shares[payer_id] == base


def test_convert_shares_reconciles_to_exact_total() -> None:
    shares = {1: 400, 2: 400, 3: 400}  # 1200 JPY total
    converted = convert_shares(shares, Decimal("0.0081"), "JPY", "SGD", 10, payer_id=1)
    assert sum(converted.values()) == 10


@given(
    total_minor=st.integers(min_value=0, max_value=1_000_000),
    participant_ids=st.lists(
        st.integers(min_value=1, max_value=1000), min_size=1, max_size=12, unique=True
    ),
    rate=st.decimals(min_value="0.0001", max_value="10000", places=6, allow_nan=False),
)
def test_convert_shares_invariants(
    total_minor: int, participant_ids: list[int], rate: Decimal
) -> None:
    """Realistic input: shares come from split_equal (near-identical, differ by at
    most 1 minor unit), as they do in real use via DraftService.confirm.
    """
    payer_id = participant_ids[0]
    shares = split_equal(total_minor, participant_ids, payer_id)
    total_to_minor = convert_minor(total_minor, "JPY", "JPY", rate)

    converted = convert_shares(shares, rate, "JPY", "JPY", total_to_minor, payer_id)

    assert sum(converted.values()) == total_to_minor
    for pid, amount in shares.items():
        unrounded_value = Decimal(amount) * rate
        assert abs(Decimal(converted[pid]) - unrounded_value) <= 1


def test_split_custom_exact_sum_gate() -> None:
    assert split_custom({1: 4000, 2: 5000}, 9000) == {1: 4000, 2: 5000}
    with pytest.raises(SplitError):
        split_custom({1: 4000, 2: 4999}, 9000)
    with pytest.raises(SplitError):
        split_custom({1: 4000, 2: 5001}, 9000)
    with pytest.raises(SplitError):
        split_custom({1: 10000, 2: -1000}, 9000)


@pytest.mark.parametrize(
    "spec",
    [
        Equal(),
        Custom({1: 4000, 2: 5000}),
        Items({"0": [1, 2]}, everyone=("1",)),
        Percent({1: 0.5, 2: 0.5}),
    ],
)
def test_split_spec_round_trips(spec) -> None:
    dumped = SPLIT_SPEC_ADAPTER.dump_python(spec, mode="json")
    assert dumped["kind"] == spec.kind
    assert SPLIT_SPEC_ADAPTER.validate_python(dumped) == spec


def test_split_shares_dispatches_on_spec() -> None:
    assert split_shares(Equal(), 100, [1, 2, 3], payer_id=1) == {1: 33, 2: 34, 3: 33}
    assert split_shares(Custom({1: 60, 2: 40}), 100, [1, 2], payer_id=1) == {1: 60, 2: 40}
    with pytest.raises(SplitError):
        split_shares(Percent(), 100, [1, 2], payer_id=1)


@given(
    total_minor=st.integers(min_value=1, max_value=5_000_000),
    participant_ids=st.lists(
        st.integers(min_value=1, max_value=1000), min_size=1, max_size=10, unique=True
    ),
    rate=st.decimals(min_value="0.0001", max_value="200", places=6, allow_nan=False),
    original_is_trip=st.booleans(),
    custom=st.booleans(),
    payer_in=st.booleans(),
)
def test_shares_in_both_currencies_sum_to_both_totals(
    total_minor, participant_ids, rate, original_is_trip, custom, payer_in
) -> None:
    """Spec §5 invariant, for equal and custom splits, either original currency,
    payer inside or outside the participants.
    """
    payer_id = participant_ids[0] if payer_in else 5000
    if custom:
        weights = {pid: index + 1 for index, pid in enumerate(participant_ids)}
        spec = Custom(split_equal(total_minor, list(weights), payer_id))
    else:
        spec = Equal()
    original = split_shares(spec, total_minor, participant_ids, payer_id)
    original_ccy = "JPY" if original_is_trip else "SGD"
    trip_total, home_total = both_amounts(total_minor, original_ccy, "JPY", "SGD", rate)
    trip_shares, home_shares = shares_in_both_currencies(
        original,
        original_currency=original_ccy,
        trip_currency="JPY",
        home_currency="SGD",
        trip_to_home_rate=rate,
        trip_amount_minor=trip_total,
        home_amount_minor=home_total,
        payer_id=payer_id,
    )
    assert sum(trip_shares.values()) == trip_total
    assert sum(home_shares.values()) == home_total
    assert trip_shares.keys() == home_shares.keys() == set(participant_ids)


# -- split_items and allocate_proportional (sprint 6, spec §5) -----------------


def _li(amount: int, kind: str = "item") -> ReceiptLineItem:
    return ReceiptLineItem(description=kind, quantity=1, amount_minor=amount, kind=kind)


def test_shared_item_between_two_and_three_with_remainders() -> None:
    items = [_li(1001), _li(1000)]
    spec = Items(claims={"0": [1, 2], "1": [1, 2, 3]})
    result = split_items(items, spec, [1, 2, 3], payer_id=1, total_minor=2001)
    # 1001 / 2: the spare yen skips the payer (1) -> 2. 1000 / 3: 333 r 1 -> 2.
    assert result.subtotals == {1: 500 + 333, 2: 501 + 334, 3: 333}
    assert sum(result.shares.values()) == 2001


def test_proportional_tax_example_from_the_brief() -> None:
    # 2000 / 1000 with 300 tax -> 200 / 100.
    items = [_li(2000), _li(1000), _li(300, "tax")]
    spec = Items(claims={"0": [1], "1": [2]})
    result = split_items(items, spec, [1, 2], payer_id=1, total_minor=3300)
    assert result.adjustments["tax"] == {1: 200, 2: 100}
    assert result.shares == {1: 2200, 2: 1100}


def test_largest_remainder_tie_break_ascending_id_payer_last() -> None:
    # 1 unit across three equal weights: remainders tie; lowest ID that isn't the
    # payer gets it.
    assert allocate_proportional(1, {1: 100, 2: 100, 3: 100}, payer_id=1) == {1: 0, 2: 1, 3: 0}
    assert allocate_proportional(2, {1: 100, 2: 100, 3: 100}, payer_id=2) == {1: 1, 2: 0, 3: 1}
    # The largest remainder wins over the tie-break: 10 * 2/3 = 6.67, 10 * 1/3 = 3.33.
    assert allocate_proportional(10, {1: 1, 2: 2}, payer_id=2) == {1: 3, 2: 7}


def test_zero_subtotal_participant_gets_zero_adjustment() -> None:
    items = [_li(1000), _li(100, "service_charge")]
    spec = Items(claims={"0": [1]})
    result = split_items(items, spec, [1, 2], payer_id=1, total_minor=1100)
    assert result.adjustments["service_charge"] == {1: 100, 2: 0}
    assert result.shares == {1: 1100, 2: 0}


def test_discount_allocation() -> None:
    items = [_li(1500), _li(500), _li(-200, "discount")]
    spec = Items(claims={"0": [1], "1": [2]})
    result = split_items(items, spec, [1, 2], payer_id=1, total_minor=1800)
    assert result.adjustments["discount"] == {1: -150, 2: -50}
    assert result.shares == {1: 1350, 2: 450}


def test_split_items_raises_when_total_does_not_reconcile() -> None:
    with pytest.raises(SplitError):
        split_items([_li(1000)], Items(claims={"0": [1]}), [1], payer_id=1, total_minor=1001)


def test_unclaimed_item_goes_to_everyone() -> None:
    result = split_items([_li(300)], Items(), [1, 2, 3], payer_id=1, total_minor=300)
    assert result.shares == {1: 100, 2: 100, 3: 100}
    assert result.assignments == {0: ([1, 2, 3], "everyone")}


@given(
    item_amounts=st.lists(st.integers(min_value=0, max_value=50_000), min_size=1, max_size=12),
    adjustments=st.lists(
        st.tuples(
            st.sampled_from(["tax", "service_charge", "discount"]),
            st.integers(min_value=0, max_value=5_000),
        ),
        max_size=4,
    ),
    participant_ids=st.lists(
        st.integers(min_value=1, max_value=50), min_size=1, max_size=6, unique=True
    ),
    data=st.data(),
)
def test_split_items_always_sums_to_receipt_total(
    item_amounts, adjustments, participant_ids, data
) -> None:
    items = [_li(a) for a in item_amounts] + [_li(a, k) for k, a in adjustments]
    claims = {
        str(i): data.draw(st.lists(st.sampled_from(participant_ids), max_size=3, unique=True))
        for i in range(len(item_amounts))
    }
    payer = data.draw(st.sampled_from([*participant_ids, 999]))
    total = sum(item_amounts) + sum(-a if k == "discount" else a for k, a in adjustments)
    if sum(item_amounts) == 0 and any(a for _k, a in adjustments):
        with pytest.raises(SplitError):
            split_items(items, Items(claims=claims), participant_ids, payer, total)
        return
    result = split_items(items, Items(claims=claims), participant_ids, payer, total)
    assert sum(result.shares.values()) == total
    for kind, allocation in result.adjustments.items():
        expected = sum(-a if k == "discount" else a for k, a in adjustments if k == kind)
        assert sum(allocation.values()) == expected
