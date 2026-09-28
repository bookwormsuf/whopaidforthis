"""The `Summary` tab's formula grid, written once at `/settrip` and never rewritten.

Spec references: pinned-decisions §11. All formulas that touch Expenses filter on
`Expenses!U:U = "confirmed"` and reference whole columns (`X:X`, for lookups and
SUMIFS/COUNTIF/QUERY) or header-free open ranges (`X2:X`, for formulas that multiply
arrays together, e.g. SUMPRODUCT) so appended rows are included and the header text
never enters arithmetic.

Layout (1-indexed rows/columns):
  A1:E1        per-person header: User ID | Display Name | Paid <H> | Share <H> | Net <H>
  A2:E(n+1)    one row per FILTER-ed joined user (n = person_rows)
  blank row
  A:B          group totals: Total <T>, Total <H>, Expenses (count)
  G1           By category QUERY block
  K1           By day QUERY block
  O1           By payer QUERY block
"""

from __future__ import annotations


def summary_rows(trip_currency: str, home_currency: str, person_rows: int = 20) -> list[list[str]]:
    grid: dict[tuple[int, int], str] = {}

    # Per-person block: columns A-E.
    grid[(1, 1)] = "User ID"
    grid[(1, 2)] = "Display Name"
    grid[(1, 3)] = f"Paid {home_currency}"
    grid[(1, 4)] = f"Share {home_currency}"
    grid[(1, 5)] = f"Net {home_currency}"

    for offset in range(1, person_rows + 1):
        row = offset + 1
        grid[(row, 1)] = f'=IFERROR(INDEX(FILTER(Users!A:A, Users!F:F=TRUE), {offset}), "")'
        grid[(row, 2)] = (
            f'=IF(A{row}="","",IFERROR(INDEX(Users!C:C, MATCH(A{row}, Users!A:A, 0)),""))'
        )
        grid[(row, 3)] = (
            f'=IF(A{row}="","",SUMIFS(Expenses!P:P, Expenses!G:G, A{row}, '
            f'Expenses!U:U, "confirmed"))'
        )
        grid[(row, 4)] = (
            f'=IF(A{row}="","",SUMPRODUCT((Splits!B2:B=A{row})*'
            f'(COUNTIFS(Expenses!A2:A, Splits!A2:A, Expenses!U2:U, "confirmed")>0), '
            f"Splits!F2:F))"
        )
        grid[(row, 5)] = f'=IF(A{row}="","",C{row}-D{row})'

    # Group totals block, two blank-row gap below the person block.
    blank_row = person_rows + 2
    totals_row = blank_row + 1
    grid[(totals_row, 1)] = f"Total {trip_currency}"
    grid[(totals_row, 2)] = '=SUMIFS(Expenses!O:O, Expenses!U:U, "confirmed")'
    grid[(totals_row + 1, 1)] = f"Total {home_currency}"
    grid[(totals_row + 1, 2)] = '=SUMIFS(Expenses!P:P, Expenses!U:U, "confirmed")'
    grid[(totals_row + 2, 1)] = "Expenses"
    grid[(totals_row + 2, 2)] = '=COUNTIF(Expenses!U:U, "confirmed")'

    # By category / by day / by payer, in separate column groups so they never
    # overlap the per-person block.
    grid[(1, 7)] = (
        "=IFERROR(QUERY(Expenses!A:W, "
        "\"select L, sum(P) where U = 'confirmed' group by L "
        f'label sum(P) \'Total {home_currency}\'", 1), "")'
    )
    grid[(1, 11)] = (
        "=IFERROR(QUERY(Expenses!A:W, "
        "\"select C, sum(P) where U = 'confirmed' group by C order by C "
        f'label sum(P) \'Total {home_currency}\'", 1), "")'
    )
    grid[(1, 15)] = (
        "=IFERROR(QUERY(Expenses!A:W, "
        "\"select I, sum(P) where U = 'confirmed' group by I "
        f'label sum(P) \'Total {home_currency}\'", 1), "")'
    )

    max_row = max(row for row, _col in grid)
    max_col = max(col for _row, col in grid)
    return [
        [grid.get((row, col), "") for col in range(1, max_col + 1)] for row in range(1, max_row + 1)
    ]
