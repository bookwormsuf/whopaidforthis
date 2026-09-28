"""Tests for app.services.sheet_layout: headers must match master spec §10 exactly."""

from __future__ import annotations

from app.services.sheet_layout import TAB_ORDER, headers_for


def test_tab_order_matches_spec() -> None:
    assert TAB_ORDER == [
        "Trip",
        "Users",
        "Expenses",
        "Splits",
        "Line Items",
        "Exchange Rates",
        "Summary",
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
