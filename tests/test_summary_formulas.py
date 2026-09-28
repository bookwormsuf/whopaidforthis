"""Tests for app.services.summary_formulas."""

from __future__ import annotations

import re

from app.services.summary_formulas import summary_rows

# A "whole column" reference is either a bare X:X (used by lookups, SUMIFS, COUNTIF,
# QUERY) or a header-free open range like X2:X (used where arrays are multiplied
# together, so the header text never enters the arithmetic). A *bounded* range like
# A2:A500 is neither, and must never appear.
_EXPENSES_OPEN_COL_RE = re.compile(r"Expenses![A-Z]\d*:[A-Z](?!\d)")
_BOUNDED_RANGE_RE = re.compile(r"[A-Z]\d+:[A-Z]\d+")


def _flatten(rows: list[list[str]]) -> list[str]:
    return [cell for row in rows for cell in row if cell]


def test_summary_rows_header() -> None:
    rows = summary_rows("JPY", "SGD")
    assert rows[0][:5] == ["User ID", "Display Name", "Paid SGD", "Share SGD", "Net SGD"]


def test_every_formula_touching_expenses_filters_confirmed_and_uses_whole_columns() -> None:
    rows = summary_rows("JPY", "SGD")
    cells = _flatten(rows)
    expenses_formulas = [cell for cell in cells if "Expenses!" in cell]
    assert expenses_formulas, "expected at least one formula referencing Expenses"
    for formula in expenses_formulas:
        assert "confirmed" in formula
        assert _EXPENSES_OPEN_COL_RE.search(formula) or "Expenses!A:W" in formula


def test_no_formula_uses_a_bounded_range() -> None:
    rows = summary_rows("JPY", "SGD")
    cells = _flatten(rows)
    for cell in cells:
        assert not _BOUNDED_RANGE_RE.search(cell), cell


def test_share_formula_uses_header_free_open_ranges_for_array_multiplication() -> None:
    rows = summary_rows("JPY", "SGD", person_rows=1)
    share_formula = rows[1][3]
    assert "Splits!B2:B" in share_formula
    assert "Splits!F2:F" in share_formula
    assert "Expenses!A2:A" in share_formula
    assert "Expenses!U2:U" in share_formula
    assert "confirmed" in share_formula


def test_group_totals_block_present() -> None:
    rows = summary_rows("JPY", "SGD", person_rows=3)
    cells = _flatten(rows)
    assert any(cell == "Total JPY" for cell in cells)
    assert any(cell == "Total SGD" for cell in cells)
    assert any(cell == "Expenses" for cell in cells)


def test_query_blocks_reference_category_day_payer_columns() -> None:
    rows = summary_rows("JPY", "SGD")
    cells = _flatten(rows)
    by_category = next(cell for cell in cells if "select L" in cell)
    by_day = next(cell for cell in cells if "select C" in cell)
    by_payer = next(cell for cell in cells if "select I" in cell)
    for formula in (by_category, by_day, by_payer):
        assert "confirmed" in formula
        assert "Expenses!A:W" in formula


def test_person_rows_controls_block_size() -> None:
    rows_small = summary_rows("JPY", "SGD", person_rows=2)
    rows_big = summary_rows("JPY", "SGD", person_rows=5)
    assert len(rows_small) < len(rows_big)
