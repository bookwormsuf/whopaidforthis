"""Tests for app.models.money.Money.format() and app.services.conversion.convert_minor."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.money import Money
from app.services.conversion import convert_minor


@pytest.mark.parametrize(
    ("amount_minor", "currency", "expected"),
    [
        (1200, "JPY", "¥1,200"),
        (1042, "SGD", "S$10.42"),
        (5000, "KRW", "₩5,000"),
        (999, "EUR", "€9.99"),
        (1000000, "JPY", "¥1,000,000"),
    ],
)
def test_format(amount_minor: int, currency: str, expected: str) -> None:
    assert Money(amount_minor=amount_minor, currency=currency).format() == expected


def test_convert_minor_round_half_even_rounds_down_on_tie() -> None:
    # 1 JPY at rate 0.125 -> 12.5 SGD cents; half-even rounds to nearest even (12).
    assert convert_minor(1, "JPY", "SGD", Decimal("0.125")) == 12


def test_convert_minor_round_half_even_rounds_up_on_tie() -> None:
    # 3 JPY at rate 0.125 -> 37.5 SGD cents; half-even rounds to nearest even (38).
    assert convert_minor(3, "JPY", "SGD", Decimal("0.125")) == 38


def test_convert_minor_reverse_direction() -> None:
    # 10.00 SGD at rate 123.456789 JPY per SGD -> 1234.56789 JPY -> rounds to 1235.
    assert convert_minor(1000, "SGD", "JPY", Decimal("123.456789")) == 1235
