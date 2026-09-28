"""Tab order and header rows for a trip Sheet.

Spec references: pinned-decisions §10. `<T>` / `<H>` are replaced with the trip's
trip-currency and home-currency codes at `/settrip` time.

Pin for every tab, every sprint: IDs are written as numbers, booleans as real
booleans, in every tab. Never write "TRUE"/"FALSE" or numeric-looking strings; use
`value_input_option=RAW` with real Python `bool`/`int` values so Sheets stores real
booleans and numbers, not text -- formulas that compare or sum those columns depend
on this.
"""

from __future__ import annotations

TAB_ORDER: list[str] = [
    "Trip",
    "Users",
    "Expenses",
    "Splits",
    "Line Items",
    "Exchange Rates",
    "Summary",
]

HEADERS: dict[str, list[str]] = {
    "Trip": [
        "Chat ID",
        "Trip Name",
        "Trip Currency",
        "Home Currency",
        "Timezone",
        "Created At",
        "Bot Version",
    ],
    "Users": [
        "Telegram User ID",
        "Username",
        "Display Name",
        "First Seen",
        "Last Seen",
        "Joined",
    ],
    "Expenses": [
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
        "<T> Amount",
        "<H> Amount",
        "Rate <T>→<H>",
        "Rate Date",
        "Source Type",
        "Split Method",
        "Status",
        "Cancelled At",
        "Notes",
    ],
    "Splits": [
        "Expense ID",
        "Participant User ID",
        "Participant Username",
        "Participant Display Name",
        "Share <T>",
        "Share <H>",
        "Split Method",
    ],
    "Line Items": [
        "Expense ID",
        "Item ID",
        "Description",
        "Quantity",
        "Amount <T>",
        "Assigned User IDs",
        "Allocation Method",
        "Kind",
    ],
    "Exchange Rates": [
        "Requested Date",
        "Rate Date",
        "From",
        "To",
        "Rate",
        "Provider",
        "Retrieved At",
    ],
    # Summary is formulas only; see app/services/summary_formulas.py.
    "Summary": [],
}


def headers_for(trip_currency: str, home_currency: str) -> dict[str, list[str]]:
    """Header rows for every tab with `<T>`/`<H>` substituted for this trip's currencies."""

    def substitute(text: str) -> str:
        return text.replace("<T>", trip_currency).replace("<H>", home_currency)

    return {tab: [substitute(header) for header in columns] for tab, columns in HEADERS.items()}
