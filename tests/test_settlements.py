"""Tests for app.services.settlements (spec §12, sprint 5)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.models.category import Category
from app.models.expense import ExpenseRecord, SplitRecord
from app.services.settlements import Balance, Transfer, compute_balances, settle


def _expense(expense_id: str, payer: int, home: int, status: str = "confirmed") -> ExpenseRecord:
    return ExpenseRecord(
        expense_id=expense_id,
        created_at=datetime(2026, 9, 20, tzinfo=UTC),
        transaction_date=date(2026, 9, 20),
        chat_id=-100,
        message_id=1,
        created_by_user_id=payer,
        payer_user_id=payer,
        payer_username=None,
        payer_display_name="x",
        description="d",
        merchant=None,
        category=Category.FOOD,
        original_amount_minor=home,
        original_currency="SGD",
        trip_amount_minor=home * 100,
        home_amount_minor=home,
        rate=Decimal("0.01"),
        rate_date=date(2026, 9, 19),
        source_type="text",
        split_method="equal",
        status=status,
        cancelled_at=None,
        notes=None,
    )


def _split(expense_id: str, user_id: int, home: int) -> SplitRecord:
    return SplitRecord(
        expense_id=expense_id,
        user_id=user_id,
        username=None,
        display_name="x",
        share_trip_minor=home * 100,
        share_home_minor=home,
        split_method="equal",
    )


def test_balances_from_fixture_ledger_match_hand_computed() -> None:
    expenses = [
        _expense("e1", payer=1, home=9000),  # 1 pays 90.00 for 1, 2, 3
        _expense("e2", payer=2, home=3000),  # 2 pays 30.00 for 2, 3
        _expense("e3", payer=3, home=5000, status="cancelled"),  # ignored
    ]
    splits = [
        _split("e1", 1, 3000),
        _split("e1", 2, 3000),
        _split("e1", 3, 3000),
        _split("e2", 2, 1500),
        _split("e2", 3, 1500),
        _split("e3", 1, 5000),
        _split("orphan", 1, 999),  # no Expenses row: ignored (spec §10)
    ]
    balances = compute_balances(expenses, splits)
    assert balances == {
        1: Balance(1, 9000, 3000),
        2: Balance(2, 3000, 4500),
        3: Balance(3, 0, 4500),
    }
    assert [b.net_minor for b in balances.values()] == [6000, -1500, -4500]


def test_settle_example_yields_two_transfers() -> None:
    # The original brief's /settle example is not in the repo; this three-person
    # stand-in has the same shape: one creditor, two debtors, two transfers.
    assert settle({1: 6000, 2: -1500, 3: -4500}) == [
        Transfer(3, 1, 4500),
        Transfer(2, 1, 1500),
    ]


def test_settle_all_square() -> None:
    assert settle({1: 0, 2: 0}) == []
    assert settle({}) == []


def test_settle_rejects_unbalanced_nets() -> None:
    with pytest.raises(ValueError):
        settle({1: 100, 2: -99})


@st.composite
def _nets(draw) -> dict[int, int]:
    values = draw(st.lists(st.integers(-1_000_000, 1_000_000), min_size=1, max_size=12))
    values.append(-sum(values))
    return {user_id: value for user_id, value in enumerate(values, start=1)}


@given(_nets())
def test_settle_invariants(nets: dict[int, int]) -> None:
    assert sum(nets.values()) == 0
    transfers = settle(nets)
    remaining = dict(nets)
    for t in transfers:
        assert t.amount_minor > 0 and t.from_user_id != t.to_user_id
        remaining[t.from_user_id] += t.amount_minor
        remaining[t.to_user_id] -= t.amount_minor
    assert all(value == 0 for value in remaining.values())
    assert len(transfers) <= max(len([v for v in nets.values() if v]) - 1, 0)


@given(
    st.lists(
        st.tuples(
            st.integers(1, 6),  # payer
            st.lists(st.integers(0, 100_000), min_size=1, max_size=6),  # shares
        ),
        max_size=15,
    )
)
def test_sum_of_nets_is_zero(rows) -> None:
    expenses, splits = [], []
    for index, (payer, shares) in enumerate(rows):
        expense_id = f"e{index}"
        expenses.append(_expense(expense_id, payer, sum(shares)))
        splits += [_split(expense_id, uid, share) for uid, share in enumerate(shares, 1)]
    balances = compute_balances(expenses, splits)
    assert sum(b.net_minor for b in balances.values()) == 0
