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

from app.services.currencies import decimals_for

TAB_ORDER: list[str] = [
    "Trip",
    "Users",
    "Expenses",
    "Splits",
    "Line Items",
    "Exchange Rates",
    "Summary",
    "Read Me",
]

# Tab order as shown to humans (spec §10a). Independent of TAB_ORDER, which is
# creation order; display order is applied via `layout_requests`' index requests.
DISPLAY_ORDER: list[str] = [
    "Summary",
    "Expenses",
    "Splits",
    "Line Items",
    "Users",
    "Exchange Rates",
    "Trip",
    "Read Me",
]

# The Summary tab's real header row (written by summary_formulas.summary_rows) isn't
# in HEADERS below (Summary is formulas-only there); listed separately so hidden
# columns can be derived without drift. User ID is the LAST column (spec §10a).
_SUMMARY_HEADER: list[str] = ["Display Name", "Paid <H>", "Share <H>", "Net <H>", "User ID"]

# Columns hidden from humans (spec §10a); formulas still work, users can unhide.
HIDDEN_COLUMNS: dict[str, list[str]] = {
    "Summary": ["User ID"],
    "Expenses": ["Expense ID", "Chat ID", "Message ID", "Created By User ID", "Payer User ID"],
    "Splits": ["Expense ID", "Participant User ID"],
    "Line Items": ["Expense ID", "Item ID", "Assigned User IDs"],
    "Users": ["Telegram User ID"],
    "Trip": ["Chat ID"],
}

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

    result = {tab: [substitute(header) for header in columns] for tab, columns in HEADERS.items()}
    result["Summary"] = [substitute(header) for header in _SUMMARY_HEADER]
    return result


def _amount_number_format(decimals: int) -> dict[str, str]:
    pattern = "#,##0" if decimals == 0 else "#,##0.00"
    return {"type": "NUMBER", "pattern": pattern}


_RATE_FORMAT = {"type": "NUMBER", "pattern": "0.00000000"}

# tab -> {header name (post-substitution) -> number format dict}, built fresh per
# call in `_number_formats_for` since it depends on the trip's currencies.
#
# No DATE format: Transaction Date/Rate Date are stored as plain `yyyy-mm-dd` text
# (spec §10a), not real Sheets date serials -- that sorts and groups correctly as
# text and needs no number format.


def _number_formats_for(trip_currency: str, home_currency: str) -> dict[str, dict[str, dict]]:
    trip_fmt = _amount_number_format(decimals_for(trip_currency))
    home_fmt = {"type": "NUMBER", "pattern": "#,##0.00"}
    t, h = trip_currency, home_currency
    return {
        "Expenses": {
            f"{t} Amount": trip_fmt,
            f"{h} Amount": home_fmt,
            f"Rate {t}→{h}": _RATE_FORMAT,
        },
        "Splits": {
            f"Share {t}": trip_fmt,
            f"Share {h}": home_fmt,
        },
        "Line Items": {
            f"Amount {t}": trip_fmt,
        },
    }


def layout_requests(
    sheet_ids_by_title: dict[str, int], trip_currency: str, home_currency: str
) -> list[dict]:
    """Pure builder for the single `spreadsheet.batch_update` request list that applies
    tab order, hidden columns, and number formats (spec §10a, parts ii-iv).

    `sheet_ids_by_title` maps every tab title (as created, "Read Me" included) to its
    gid. Column positions are derived from `headers_for` so they cannot drift from the
    header strings.
    """
    headers = headers_for(trip_currency, home_currency)
    requests: list[dict] = []

    for index, title in enumerate(DISPLAY_ORDER):
        sheet_id = sheet_ids_by_title[title]
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sheet_id, "index": index},
                    "fields": "index",
                }
            }
        )

    for title, column_names in HIDDEN_COLUMNS.items():
        sheet_id = sheet_ids_by_title[title]
        for name in column_names:
            column_index = headers[title].index(name)
            requests.append(
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": column_index,
                            "endIndex": column_index + 1,
                        },
                        "properties": {"hiddenByUser": True},
                        "fields": "hiddenByUser",
                    }
                }
            )

    number_formats = _number_formats_for(trip_currency, home_currency)
    for title, column_formats in number_formats.items():
        sheet_id = sheet_ids_by_title[title]
        for name, number_format in column_formats.items():
            column_index = headers[title].index(name)
            requests.append(
                {
                    "repeatCell": {
                        # No endRowIndex: unbounded, the whole column below the
                        # header, so appended rows always get the format too.
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": column_index,
                            "endColumnIndex": column_index + 1,
                        },
                        "cell": {"userEnteredFormat": {"numberFormat": number_format}},
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
            )

    return requests


def _read_me_section(title: str, what_a_row_means: str, when_to_look: str) -> list[str]:
    return [
        f"{title}",
        f"What one row means: {what_a_row_means}",
        "Who writes it: the bot.",
        f"When to look: {when_to_look}",
        "",
    ]


def read_me_lines(trip_currency: str, home_currency: str) -> list[str]:
    """Plain-English content for the `Read Me` tab (spec §10a), three lines per tab."""
    t, h = trip_currency, home_currency
    lines: list[str] = []
    lines += _read_me_section(
        "Summary",
        "there are no rows here, only totals: who owes whom, group totals, and "
        "breakdowns by category, day and payer.",
        "any time you want the current state of the trip.",
    )
    lines += _read_me_section(
        "Expenses",
        f"one confirmed or cancelled payment, with its amount in {t} and {h}.",
        "to see the full history of what was spent.",
    )
    lines += _read_me_section(
        "Splits",
        "one participant's share of one expense, in both currencies.",
        "to see exactly who owed what for a given expense.",
    )
    lines += _read_me_section(
        "Line Items",
        "one priced line from a receipt (an item, tax, service charge, or discount). "
        "Not yet split per person -- everyone pays an equal share of the receipt total "
        'for now, so Assigned User IDs is blank and Allocation Method reads "unassigned".',
        "to see the detail behind a receipt photo.",
    )
    lines += _read_me_section(
        "Users",
        "one person who has been seen or has joined this trip in Telegram.",
        "to check who is counted as part of the trip (Joined = TRUE).",
    )
    lines += _read_me_section(
        "Exchange Rates",
        f"one {t}→{h} rate the bot looked up and reused for later expenses on the same date.",
        "if a converted amount looks off and you want to check the rate used.",
    )
    lines += _read_me_section(
        "Trip",
        "the facts this trip was set up with: currencies, timezone, and when it was created.",
        "to confirm which group and currencies this Sheet belongs to.",
    )
    lines.append("Do not edit rows by hand; use /undo in Telegram.")
    return lines
