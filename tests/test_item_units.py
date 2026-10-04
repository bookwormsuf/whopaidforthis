"""Item lines with a quantity become one claimable item per unit (decided
2026-10-04), within the 40-item cap.
"""

from __future__ import annotations

from app.models.receipt import ReceiptLineItem
from app.services.drafts import _one_button_per_unit


def _line(description: str, quantity: int, amount: int, kind: str = "item") -> ReceiptLineItem:
    return ReceiptLineItem(
        description=description, quantity=quantity, amount_minor=amount, kind=kind
    )


def test_units_add_up_to_the_line() -> None:
    units = _one_button_per_unit([_line("Tea", 3, 1000)])
    assert [(u.description, u.quantity, u.amount_minor) for u in units] == [
        ("Tea (1/3)", 1, 334),
        ("Tea (2/3)", 1, 333),
        ("Tea (3/3)", 1, 333),
    ]


def test_single_items_adjustments_and_big_quantities_stay_as_printed() -> None:
    lines = [
        _line("Cake", 1, 500),
        _line("Tax", 2, 80, "tax"),
        _line("Napkins", 11, 110),
    ]
    assert _one_button_per_unit(lines) == lines


def test_a_line_priced_as_one_unit_stays_whole() -> None:
    line = ReceiptLineItem(
        description="Tea", quantity=3, unit_amount_minor=945, amount_minor=945, kind="item"
    )
    assert _one_button_per_unit([line]) == [line]


def test_lines_stay_whole_if_units_would_pass_forty_items() -> None:
    lines = [_line(f"Dish {n}", 2, 200) for n in range(21)]
    assert _one_button_per_unit(lines) == lines
    assert len(_one_button_per_unit(lines[:20])) == 40


def test_splitting_twice_changes_nothing() -> None:
    once = _one_button_per_unit([_line("Tea", 3, 2835)])
    assert _one_button_per_unit(once) == once
