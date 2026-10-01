"""The `Summary` tab's formula grid, written once at `/settrip` and never rewritten.

Spec references: pinned-decisions §11, §10a. All formulas that touch Expenses filter on
`Expenses!U:U = "confirmed"` and reference whole columns (`X:X`, for lookups and
SUMIFS/COUNTIF/QUERY) or header-free open ranges (`X2:X`, for formulas that multiply
arrays together, e.g. SUMPRODUCT) so appended rows are included and the header text
never enters arithmetic.

Layout (1-indexed rows/columns), spec §10a/§11:
  Block 1 "Who owes whom" (+ note row): columns A Display Name | B Paid <H> |
    C Share <H> | D Net <H> | E User ID (last column, hidden -- spec §10a).
  Block 2 "Totals": column A/B, same row as blocks 3-5's titles.
  Blocks 3-5 "By category"/"By day"/"By payer": side by side in separate column
  groups (G, J, M) starting at the Totals title row, each with its own bold title
  row and the QUERY directly below (2 columns of dynamic output per block, so a
  3-column gap between groups means growth never collides with the next block).
"""

from __future__ import annotations

TITLE_WHO_OWES_WHOM = "Who owes whom"
TITLE_TOTALS = "Totals"
TITLE_BY_CATEGORY = "By category"
TITLE_BY_DAY = "By day"
TITLE_BY_PAYER = "By payer"

_NOTE = "Positive = should receive money · Negative = owes money"

# 1-indexed column numbers for the side-by-side blocks.
_COL_A, _COL_B = 1, 2
_COL_G, _COL_J, _COL_M = 7, 10, 13


def _totals_title_row(person_rows: int) -> int:
    header_row = 3
    last_data_row = header_row + person_rows
    return last_data_row + 2  # one blank row below the person block


def title_cells(person_rows: int = 20) -> list[tuple[int, int]]:
    """1-indexed (row, col) pairs of every bold title cell, in block order."""
    totals_row = _totals_title_row(person_rows)
    return [
        (1, _COL_A),
        (totals_row, _COL_A),
        (totals_row, _COL_G),
        (totals_row, _COL_J),
        (totals_row, _COL_M),
    ]


def summary_rows(trip_currency: str, home_currency: str, person_rows: int = 20) -> list[list[str]]:
    grid: dict[tuple[int, int], str] = {}

    # Block 1: "Who owes whom", with its note row, then the per-person table.
    grid[(1, _COL_A)] = TITLE_WHO_OWES_WHOM
    grid[(2, _COL_A)] = _NOTE
    header_row = 3
    grid[(header_row, 1)] = "Display Name"
    grid[(header_row, 2)] = f"Paid {home_currency}"
    grid[(header_row, 3)] = f"Share {home_currency}"
    grid[(header_row, 4)] = f"Net {home_currency}"
    grid[(header_row, 5)] = "User ID"

    for offset in range(1, person_rows + 1):
        row = header_row + offset
        # Column E (User ID) is computed first; A-D look it up.
        grid[(row, 5)] = f'=IFERROR(INDEX(FILTER(Users!A:A, Users!F:F=TRUE), {offset}), "")'
        grid[(row, 1)] = (
            f'=IF(E{row}="","",IFERROR(INDEX(Users!C:C, MATCH(E{row}, Users!A:A, 0)),""))'
        )
        grid[(row, 2)] = (
            f'=IF(E{row}="","",SUMIFS(Expenses!P:P, Expenses!G:G, E{row}, '
            f'Expenses!U:U, "confirmed"))'
        )
        grid[(row, 3)] = (
            f'=IF(E{row}="","",SUMPRODUCT((Splits!B2:B=E{row})*'
            f'(COUNTIFS(Expenses!A2:A, Splits!A2:A, Expenses!U2:U, "confirmed")>0), '
            f"Splits!F2:F))"
        )
        grid[(row, 4)] = f'=IF(E{row}="","",B{row}-C{row})'

    # Block 2 ("Totals", column A/B) and blocks 3-5 ("By category"/"By day"/
    # "By payer", columns G/J/M) share the same title row.
    totals_row = _totals_title_row(person_rows)
    grid[(totals_row, _COL_A)] = TITLE_TOTALS
    grid[(totals_row + 1, _COL_A)] = f"Total {trip_currency}"
    grid[(totals_row + 1, _COL_B)] = '=SUMIFS(Expenses!O:O, Expenses!U:U, "confirmed")'
    grid[(totals_row + 2, _COL_A)] = f"Total {home_currency}"
    grid[(totals_row + 2, _COL_B)] = '=SUMIFS(Expenses!P:P, Expenses!U:U, "confirmed")'
    grid[(totals_row + 3, _COL_A)] = "Expenses"
    grid[(totals_row + 3, _COL_B)] = '=COUNTIF(Expenses!U:U, "confirmed")'

    grid[(totals_row, _COL_G)] = TITLE_BY_CATEGORY
    grid[(totals_row + 1, _COL_G)] = (
        "=IFERROR(QUERY(Expenses!A:W, "
        "\"select L, sum(P) where U = 'confirmed' group by L "
        f'label sum(P) \'Total {home_currency}\'", 1), "")'
    )

    grid[(totals_row, _COL_J)] = TITLE_BY_DAY
    grid[(totals_row + 1, _COL_J)] = (
        "=IFERROR(QUERY(Expenses!A:W, "
        "\"select C, sum(P) where U = 'confirmed' group by C order by C "
        f'label sum(P) \'Total {home_currency}\'", 1), "")'
    )

    grid[(totals_row, _COL_M)] = TITLE_BY_PAYER
    grid[(totals_row + 1, _COL_M)] = (
        "=IFERROR(QUERY(Expenses!A:W, "
        "\"select I, sum(P) where U = 'confirmed' group by I "
        f'label sum(P) \'Total {home_currency}\'", 1), "")'
    )

    max_row = max(row for row, _col in grid)
    max_col = max(col for _row, col in grid)
    return [
        [grid.get((row, col), "") for col in range(1, max_col + 1)] for row in range(1, max_row + 1)
    ]
