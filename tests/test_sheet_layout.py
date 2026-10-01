"""Tests for app.services.sheet_layout: headers must match master spec §10 exactly."""

from __future__ import annotations

from app.services.currencies import decimals_for
from app.services.sheet_layout import (
    DISPLAY_ORDER,
    HIDDEN_COLUMNS,
    TAB_ORDER,
    headers_for,
    layout_requests,
    read_me_lines,
)


def test_tab_order_matches_spec() -> None:
    assert TAB_ORDER == [
        "Trip",
        "Users",
        "Expenses",
        "Splits",
        "Line Items",
        "Exchange Rates",
        "Summary",
        "Read Me",
    ]


def test_display_order_matches_spec_10a() -> None:
    assert DISPLAY_ORDER == [
        "Summary",
        "Expenses",
        "Splits",
        "Line Items",
        "Users",
        "Exchange Rates",
        "Trip",
        "Read Me",
    ]


def test_headers_for_jpy_sgd() -> None:
    headers = headers_for("JPY", "SGD")
    assert headers["Trip"] == [
        "Chat ID",
        "Trip Name",
        "Trip Currency",
        "Home Currency",
        "Timezone",
        "Created At",
        "Bot Version",
    ]
    assert headers["Users"] == [
        "Telegram User ID",
        "Username",
        "Display Name",
        "First Seen",
        "Last Seen",
        "Joined",
    ]
    assert headers["Expenses"] == [
        "Expense ID",
        "Created At",
        "Transaction Date",
        "Chat ID",
        "Message ID",
        "Created By User ID",
        "Payer User ID",
        "Payer Username",
        "Payer Display Name",
        "Description",
        "Merchant",
        "Category",
        "Original Amount",
        "Original Currency",
        "JPY Amount",
        "SGD Amount",
        "Rate JPY→SGD",
        "Rate Date",
        "Source Type",
        "Split Method",
        "Status",
        "Cancelled At",
        "Notes",
    ]
    assert headers["Splits"] == [
        "Expense ID",
        "Participant User ID",
        "Participant Username",
        "Participant Display Name",
        "Share JPY",
        "Share SGD",
        "Split Method",
    ]
    assert headers["Line Items"] == [
        "Expense ID",
        "Item ID",
        "Description",
        "Quantity",
        "Amount JPY",
        "Assigned User IDs",
        "Allocation Method",
        "Kind",
    ]
    assert headers["Exchange Rates"] == [
        "Requested Date",
        "Rate Date",
        "From",
        "To",
        "Rate",
        "Provider",
        "Retrieved At",
    ]


def test_headers_for_eur_gbp() -> None:
    headers = headers_for("EUR", "GBP")
    assert headers["Expenses"][14:17] == ["EUR Amount", "GBP Amount", "Rate EUR→GBP"]
    assert headers["Splits"][4:6] == ["Share EUR", "Share GBP"]
    assert headers["Line Items"][4] == "Amount EUR"


def _gids() -> dict[str, int]:
    return {title: index for index, title in enumerate(DISPLAY_ORDER, start=1)}


def test_layout_requests_hidden_column_indexes_match_header_positions() -> None:
    gids = _gids()
    requests = layout_requests(gids, "JPY", "SGD")
    headers = headers_for("JPY", "SGD")
    hide_requests = [
        r["updateDimensionProperties"] for r in requests if "updateDimensionProperties" in r
    ]

    for tab, names in HIDDEN_COLUMNS.items():
        for name in names:
            expected_index = headers[tab].index(name)
            matches = [
                r
                for r in hide_requests
                if r["range"]["sheetId"] == gids[tab] and r["range"]["startIndex"] == expected_index
            ]
            assert matches, f"no hide request for {tab}.{name} at index {expected_index}"


def test_layout_requests_summary_is_index_0_and_read_me_last() -> None:
    gids = _gids()
    requests = layout_requests(gids, "JPY", "SGD")
    index_requests = {
        r["updateSheetProperties"]["properties"]["sheetId"]: r["updateSheetProperties"][
            "properties"
        ]["index"]
        for r in requests
        if "updateSheetProperties" in r
    }
    assert index_requests[gids["Summary"]] == 0
    assert index_requests[gids["Read Me"]] == len(DISPLAY_ORDER) - 1


def test_layout_requests_number_format_jpy_vs_sgd() -> None:
    gids = _gids()
    requests = layout_requests(gids, "JPY", "SGD")
    repeat_cells = [r["repeatCell"] for r in requests if "repeatCell" in r]

    headers = headers_for("JPY", "SGD")
    jpy_col = headers["Expenses"].index("JPY Amount")
    sgd_col = headers["Expenses"].index("SGD Amount")

    jpy_request = next(
        r
        for r in repeat_cells
        if r["range"]["sheetId"] == gids["Expenses"] and r["range"]["startColumnIndex"] == jpy_col
    )
    sgd_request = next(
        r
        for r in repeat_cells
        if r["range"]["sheetId"] == gids["Expenses"] and r["range"]["startColumnIndex"] == sgd_col
    )
    assert jpy_request["cell"]["userEnteredFormat"]["numberFormat"]["pattern"] == "#,##0"
    assert sgd_request["cell"]["userEnteredFormat"]["numberFormat"]["pattern"] == "#,##0.00"
    assert decimals_for("JPY") == 0
    assert decimals_for("SGD") == 2


def test_read_me_lines_ends_with_pinned_line() -> None:
    lines = read_me_lines("JPY", "SGD")
    assert lines[-1] == "Do not edit rows by hand; use /undo in Telegram."
    assert any("Summary" == line for line in lines)


def test_hidden_summary_column_index_is_4() -> None:
    """User ID is the LAST column of the per-person block (spec §10a item 2)."""
    headers = headers_for("JPY", "SGD")
    assert headers["Summary"].index("User ID") == 4


def test_repeat_cell_ranges_have_no_end_row_index() -> None:
    """Unbounded downward, so appended rows always get the number format too."""
    gids = _gids()
    requests = layout_requests(gids, "JPY", "SGD")
    repeat_cells = [r["repeatCell"] for r in requests if "repeatCell" in r]
    assert repeat_cells, "expected at least one repeatCell request"
    for request in repeat_cells:
        assert "endRowIndex" not in request["range"]
