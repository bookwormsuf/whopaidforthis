"""Tests for app.services.summary_formulas."""

from __future__ import annotations

import re

from app.services.summary_formulas import (
    TITLE_BY_CATEGORY,
    TITLE_BY_DAY,
    TITLE_BY_PAYER,
    TITLE_TOTALS,
    TITLE_WHO_OWES_WHOM,
    summary_rows,
    title_cells,
)

# A "whole column" reference is either a bare X:X (used by lookups, SUMIFS, COUNTIF,
# QUERY) or a header-free open range like X2:X (used where arrays are multiplied
# together, so the header text never enters the arithmetic). A *bounded* range like
# A2:A500 is neither, and must never appear.
_EXPENSES_OPEN_COL_RE = re.compile(r"Expenses![A-Z]\d*:[A-Z](?!\d)")
_BOUNDED_RANGE_RE = re.compile(r"[A-Z]\d+:[A-Z]\d+")


def _flatten(rows: list[list[str]]) -> list[str]:
    return [cell for row in rows for cell in row if cell]


def _cell(rows: list[list[str]], row: int, col: int) -> str:
    """1-indexed lookup into the summary_rows grid."""
    return rows[row - 1][col - 1]


def test_summary_rows_starts_with_who_owes_whom_title_and_note() -> None:
    rows = summary_rows("JPY", "SGD")
    assert _cell(rows, 1, 1) == TITLE_WHO_OWES_WHOM
    assert "Positive" in _cell(rows, 2, 1)
    assert "Negative" in _cell(rows, 2, 1)
    # User ID is the LAST column of the per-person block (spec §10a).
    assert rows[2][:5] == ["Display Name", "Paid SGD", "Share SGD", "Net SGD", "User ID"]


def test_title_cells_are_in_pinned_order_and_columns() -> None:
    rows = summary_rows("JPY", "SGD", person_rows=2)
    cells = title_cells(person_rows=2)
    texts = [_cell(rows, row, col) for row, col in cells]
    assert texts == [
        TITLE_WHO_OWES_WHOM,
        TITLE_TOTALS,
        TITLE_BY_CATEGORY,
        TITLE_BY_DAY,
        TITLE_BY_PAYER,
    ]
    # Titles land in columns A (1), then A/G/J/M (1, 7, 10, 13) for the totals row.
    assert [col for _row, col in cells] == [1, 1, 7, 10, 13]


def test_totals_and_side_by_side_blocks_share_one_row_no_column_overlap() -> None:
    cells = title_cells(person_rows=2)
    totals_row = cells[1][0]
    assert cells[1][0] == cells[2][0] == cells[3][0] == cells[4][0] == totals_row

    # No two of Totals (A/B), By category (G/H), By day (J/K), By payer (M/N) share
    # a column: each 2-column-wide block has at least a 1-column gap before the next.
    column_ranges = [(1, 2), (7, 8), (10, 11), (13, 14)]
    for i, (start_a, end_a) in enumerate(column_ranges):
        for start_b, end_b in column_ranges[i + 1 :]:
            assert end_a < start_b, f"columns {start_a}-{end_a} collide with {start_b}-{end_b}"


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
    # header row is row index 2 (0-indexed); first data row is index 3; Share is col C.
    share_formula = rows[3][2]
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
    assert any(cell == TITLE_TOTALS for cell in cells)


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
