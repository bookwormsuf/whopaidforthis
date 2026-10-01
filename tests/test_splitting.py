"""Tests for app.services.splitting."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.services.conversion import convert_minor
from app.services.splitting import convert_shares, split_equal


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
