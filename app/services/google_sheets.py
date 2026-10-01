"""SheetsLedgerStore: the Google Sheets implementation of LedgerStore.

Every gspread call runs in `asyncio.to_thread`, and every one that isn't the
unretried `can_open` access check goes through `_with_retries`, which also
translates gspread's own exceptions into `LedgerUnavailable` so callers never need
to know about gspread. Spec references: pinned-decisions §10, §11.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import gspread
from gspread.exceptions import APIError, SpreadsheetNotFound
from gspread.utils import ValueInputOption, ValueRenderOption, rowcol_to_a1
from gspread.worksheet import Worksheet

from app.models.exchange_rate import ExchangeRate
from app.models.expense import Expense, ExpenseRecord
from app.models.receipt import ReceiptLineItem
from app.models.trip import Trip
from app.models.user import TripUser
from app.services.ledger_store import LedgerUnavailable, TripSheetInaccessible, merge_user
from app.services.sheet_layout import (
    DISPLAY_ORDER,
    TAB_ORDER,
    headers_for,
    layout_requests,
    read_me_lines,
)
from app.services.sheet_rows import (
    expense_to_row,
    line_item_to_row,
    row_to_expense_record,
    split_to_row,
)
from app.services.summary_formulas import summary_rows, title_cells

logger = logging.getLogger(__name__)

_RETRY_DELAYS = (1.0, 2.0, 4.0)
_BINDINGS_TAB = "Bindings"
_BINDINGS_HEADER = ["Chat ID", "Sheet ID", "Bound At"]
_NOT_BOUND_TTL = timedelta(seconds=60)

# open_by_key raises the builtin PermissionError (not a gspread-specific one) on 403.
_NOT_OPENABLE_EXC = (SpreadsheetNotFound, PermissionError)


def _is_retryable_status(status: int) -> bool:
    return status == 429 or 500 <= status < 600


async def _with_retries(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking gspread call in a thread.

    Retries on 429/5xx APIError: 1 initial attempt plus 3 retries, backing off 1s,
    2s, then 4s. Any escaping SpreadsheetNotFound, PermissionError, or APIError (a
    non-retryable one immediately, a retryable one once retries are exhausted) is
    translated to `LedgerUnavailable` so nothing gspread-specific leaks past the
    store boundary.
    """
    last_status: int | None = None
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except _NOT_OPENABLE_EXC as exc:
            raise LedgerUnavailable("Could not reach Google Sheets") from exc
        except APIError as exc:
            status = exc.response.status_code
            last_status = status
            if _is_retryable_status(status) and attempt < len(_RETRY_DELAYS):
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                continue
            raise LedgerUnavailable("Could not reach Google Sheets", status=status) from exc
    raise LedgerUnavailable("Could not reach Google Sheets", status=last_status)


def _is_already_exists_error(exc: LedgerUnavailable) -> bool:
    """True if a LedgerUnavailable was caused by a 400 "already exists" APIError.

    Two callers can race to create the same tab (e.g. /settrip and the seen-user
    upsert both calling get_binding at once); `_with_retries` translates the
    underlying APIError to LedgerUnavailable but keeps it as `__cause__`.
    """
    cause = exc.__cause__
    if not isinstance(cause, APIError) or exc.status != 400:
        return False
    message = cause.error.get("message", "") if isinstance(cause.error, dict) else str(cause)
    return "already exists" in str(message).lower()


def _summary_title_bold_requests(summary_sheet_id: int) -> list[dict]:
    """Bold formatting for the Summary tab's block title cells (spec §10a, part v)."""
    requests: list[dict] = []
    for row, col in title_cells():
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": summary_sheet_id,
                        "startRowIndex": row - 1,
                        "endRowIndex": row,
                        "startColumnIndex": col - 1,
                        "endColumnIndex": col,
                    },
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            }
        )
    return requests


def _row_nonempty(row: list[Any] | None) -> bool:
    return bool(row) and any(str(cell).strip() for cell in row)


def _parse_int(value: Any) -> int:
    if isinstance(value, bool):
        raise TypeError("expected an int, got a bool")
    if isinstance(value, int):
        return value
    return int(str(value).strip())


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() == "TRUE"


def _user_to_row(user: TripUser) -> list[Any]:
    # Telegram User ID and Joined are real numbers/booleans, not text (sheet_layout.py
    # pin), so Sheet-side formula comparisons (e.g. Users!F:F=TRUE) work.
    return [
        user.user_id,
        user.username or "",
        user.display_name,
        user.first_seen.isoformat(),
        user.last_seen.isoformat(),
        user.joined,
    ]


def _row_to_user(row: list[Any]) -> TripUser:
    return TripUser(
        user_id=_parse_int(row[0]),
        username=(row[1] or None) if len(row) > 1 else None,
        display_name=row[2] if len(row) > 2 else "",
        first_seen=datetime.fromisoformat(row[3]),
        last_seen=datetime.fromisoformat(row[4]),
        joined=_parse_bool(row[5]) if len(row) > 5 else False,
    )


class SheetsLedgerStore:
    """LedgerStore backed by a control Sheet (Bindings) and one Sheet per trip."""

    def __init__(
        self, service_account_info: dict[str, Any], control_sheet_id: str, bot_version: str
    ) -> None:
        self._service_account_info = service_account_info
        self._control_sheet_id = control_sheet_id
        self._bot_version = bot_version
        self._client: gspread.Client | None = None
        self._binding_cache: dict[int, Trip] = {}
        self._not_bound_cache: dict[int, datetime] = {}
        self._inaccessible_cache: dict[int, tuple[str, datetime]] = {}
        self._spreadsheet_cache: dict[str, gspread.Spreadsheet] = {}
        self._bindings_ws: Worksheet | None = None
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _get_client(self) -> gspread.Client:
        if self._client is None:
            self._client = await asyncio.to_thread(
                gspread.service_account_from_dict, self._service_account_info
            )
        return self._client

    async def _open(self, sheet_id: str) -> gspread.Spreadsheet:
        """Open (and cache) a Spreadsheet handle, retried and translated to LedgerUnavailable."""
        cached = self._spreadsheet_cache.get(sheet_id)
        if cached is not None:
            return cached
        client = await self._get_client()
        spreadsheet = await _with_retries(client.open_by_key, sheet_id)
        self._spreadsheet_cache[sheet_id] = spreadsheet
        return spreadsheet

    async def can_open(self, sheet_id: str) -> bool:
        """Single, unretried access check: is this Sheet shared with the service account?

        Returns False for "no" (not found / not shared) without retrying, so /settrip
        gets a fast answer. A genuine outage (5xx) still raises LedgerUnavailable.
        """
        try:
            client = await self._get_client()
            await asyncio.to_thread(client.open_by_key, sheet_id)
            return True
        except _NOT_OPENABLE_EXC:
            return False
        except APIError as exc:
            status = exc.response.status_code
            if status in (403, 404):
                return False
            raise LedgerUnavailable("Could not reach Google Sheets", status=status) from exc

    # -- Bindings (control Sheet) -----------------------------------------

    async def _get_or_create_bindings_tab(self, control: gspread.Spreadsheet) -> Worksheet:
        """Public entry point: acquires the control-sheet lock itself."""
        if self._bindings_ws is not None:
            return self._bindings_ws
        async with self._lock_for(self._control_sheet_id):
            return await self._get_or_create_bindings_tab_locked(control)

    async def _get_or_create_bindings_tab_locked(self, control: gspread.Spreadsheet) -> Worksheet:
        """Same as `_get_or_create_bindings_tab`, but assumes the caller already holds
        `self._lock_for(self._control_sheet_id)` (`asyncio.Lock` is not re-entrant, so
        `save_binding` -- which holds that lock for its whole read-then-write -- must
        call this instead of the public method).
        """
        if self._bindings_ws is not None:  # a concurrent caller may have won the race
            return self._bindings_ws
        worksheets = {ws.title: ws for ws in await _with_retries(control.worksheets)}
        ws = worksheets.get(_BINDINGS_TAB)
        if ws is None:
            try:
                ws = await _with_retries(
                    control.add_worksheet,
                    title=_BINDINGS_TAB,
                    rows=200,
                    cols=len(_BINDINGS_HEADER),
                )
                await _with_retries(ws.update, [_BINDINGS_HEADER])
                await _with_retries(ws.freeze, rows=1)
            except LedgerUnavailable as exc:
                if not _is_already_exists_error(exc):
                    raise
                # Another process created it between our list and our add: use theirs.
                worksheets = {w.title: w for w in await _with_retries(control.worksheets)}
                ws = worksheets.get(_BINDINGS_TAB)
                if ws is None:
                    raise
        self._bindings_ws = ws
        return ws

    async def get_binding(self, chat_id: int) -> Trip | None:
        if chat_id in self._binding_cache:
            return self._binding_cache[chat_id]
        not_bound_at = self._not_bound_cache.get(chat_id)
        if not_bound_at is not None and datetime.now(UTC) - not_bound_at < _NOT_BOUND_TTL:
            return None
        inaccessible = self._inaccessible_cache.get(chat_id)
        if inaccessible is not None:
            cached_sheet_id, cached_at = inaccessible
            if datetime.now(UTC) - cached_at < _NOT_BOUND_TTL:
                raise TripSheetInaccessible(cached_sheet_id)
            del self._inaccessible_cache[chat_id]

        control = await self._open(self._control_sheet_id)
        bindings_ws = await self._get_or_create_bindings_tab(control)
        rows = await _with_retries(bindings_ws.get_all_values)
        sheet_id = None
        for row in rows[1:]:
            if row and str(row[0]) == str(chat_id):
                sheet_id = row[1]
                break
        if sheet_id is None:
            self._not_bound_cache[chat_id] = datetime.now(UTC)
            return None

        try:
            trip = await self._read_trip_tab(sheet_id, chat_id)
        except LedgerUnavailable as exc:
            # A 403/404 (or the underlying PermissionError/SpreadsheetNotFound,
            # which _with_retries translates without a status) means the binding
            # itself is stale -- the Sheet exists as a row here but can't be opened
            # any more -- distinct from a transient/backend LedgerUnavailable.
            is_access_error = exc.status in (403, 404) or isinstance(
                exc.__cause__, _NOT_OPENABLE_EXC
            )
            if is_access_error:
                self._inaccessible_cache[chat_id] = (sheet_id, datetime.now(UTC))
                raise TripSheetInaccessible(sheet_id) from exc
            raise
        if trip is not None:
            self._binding_cache[chat_id] = trip
            self._not_bound_cache.pop(chat_id, None)
            self._inaccessible_cache.pop(chat_id, None)
        return trip

    async def save_binding(self, trip: Trip) -> None:
        async with self._lock_for(self._control_sheet_id):
            control = await self._open(self._control_sheet_id)
            bindings_ws = await self._get_or_create_bindings_tab_locked(control)
            rows = await _with_retries(bindings_ws.get_all_values)
            row_index = None
            for index, row in enumerate(rows[1:], start=2):
                if row and str(row[0]) == str(trip.chat_id):
                    row_index = index
                    break
            values = [trip.chat_id, trip.sheet_id, datetime.now(UTC).isoformat()]
            if row_index is not None:
                await _with_retries(
                    bindings_ws.update,
                    [values],
                    f"A{row_index}",
                    value_input_option=ValueInputOption.raw,
                )
            else:
                await _with_retries(bindings_ws.append_row, values)
        self._binding_cache[trip.chat_id] = trip
        self._not_bound_cache.pop(trip.chat_id, None)
        self._inaccessible_cache.pop(trip.chat_id, None)

    async def _read_trip_tab(self, sheet_id: str, chat_id: int) -> Trip | None:
        spreadsheet = await self._open(sheet_id)
        try:
            trip_ws = await _with_retries(spreadsheet.worksheet, "Trip")
        except gspread.exceptions.WorksheetNotFound:
            return None
        rows = await _with_retries(trip_ws.get_values, "A2:G2")
        if not rows or not _row_nonempty(rows[0]):
            return None
        row = rows[0]
        return Trip(
            chat_id=chat_id,
            sheet_id=sheet_id,
            name=row[1],
            trip_currency=row[2],
            home_currency=row[3],
            timezone=row[4],
            created_at=datetime.fromisoformat(row[5]),
        )

    # -- Trip Sheet setup ---------------------------------------------------

    async def initialise_trip_sheet(self, trip: Trip) -> None:
        spreadsheet = await self._open(trip.sheet_id)
        existing = {ws.title: ws for ws in await _with_retries(spreadsheet.worksheets)}
        headers = headers_for(trip.trip_currency, trip.home_currency)

        for tab in TAB_ORDER:
            ws = existing.get(tab)
            if ws is None:
                ws = await _with_retries(spreadsheet.add_worksheet, title=tab, rows=1000, cols=26)
                existing[tab] = ws
                await self._write_tab_contents(ws, tab, trip, headers)
                if tab != "Read Me":
                    await _with_retries(ws.freeze, rows=1)
                continue

            first_row = await _with_retries(ws.get_values, "A1:Z1")
            if _row_nonempty(first_row[0] if first_row else None):
                continue  # idempotent: never touch a tab that already has content
            await self._write_tab_contents(ws, tab, trip, headers)
            if tab != "Read Me":
                await _with_retries(ws.freeze, rows=1)

        # Unlike headers, the Trip fact row is always refreshed: a rebind must be able
        # to update it (spec §2, "rebind" pin), never leaving stale facts behind.
        await self._write_trip_row(existing["Trip"], trip)

        sheet1 = existing.get("Sheet1")
        if sheet1 is not None:
            values = await _with_retries(sheet1.get_all_values)
            if not any(_row_nonempty(row) for row in values):
                await _with_retries(spreadsheet.del_worksheet, sheet1)

        # Readable layout (spec §10a): tab order, hidden columns, number formats.
        # Idempotent to re-apply, so this always runs, even on an already-set-up Sheet.
        sheet_ids_by_title = {title: existing[title].id for title in DISPLAY_ORDER}
        requests = layout_requests(sheet_ids_by_title, trip.trip_currency, trip.home_currency)
        requests += _summary_title_bold_requests(sheet_ids_by_title["Summary"])
        if requests:
            await _with_retries(spreadsheet.batch_update, {"requests": requests})

    async def _write_tab_contents(
        self, ws: Worksheet, tab: str, trip: Trip, headers: dict[str, list[str]]
    ) -> None:
        if tab == "Summary":
            rows = summary_rows(trip.trip_currency, trip.home_currency)
            await _with_retries(ws.update, rows, value_input_option=ValueInputOption.user_entered)
        elif tab == "Read Me":
            lines = read_me_lines(trip.trip_currency, trip.home_currency)
            await _with_retries(ws.update, [[line] for line in lines])
        else:
            await _with_retries(ws.update, [headers[tab]])

    async def _write_trip_row(self, trip_ws: Worksheet, trip: Trip) -> None:
        row = [
            trip.chat_id,
            trip.name,
            trip.trip_currency,
            trip.home_currency,
            trip.timezone,
            trip.created_at.isoformat(),
            self._bot_version,
        ]
        await _with_retries(trip_ws.update, [row], "A2", value_input_option=ValueInputOption.raw)

    # -- Users ----------------------------------------------------------------

    async def _users_worksheet(self, sheet_id: str) -> Worksheet:
        spreadsheet = await self._open(sheet_id)
        return await _with_retries(spreadsheet.worksheet, "Users")

    async def upsert_user(self, trip: Trip, user: TripUser, joined: bool | None) -> TripUser:
        async with self._lock_for(trip.sheet_id):
            ws = await self._users_worksheet(trip.sheet_id)
            rows = await _with_retries(
                ws.get_all_values, value_render_option=ValueRenderOption.unformatted
            )
            existing: TripUser | None = None
            row_index: int | None = None
            for index, row in enumerate(rows[1:], start=2):
                if row and str(row[0]) == str(user.user_id):
                    existing = _row_to_user(row)
                    row_index = index
                    break
            merged = merge_user(existing, user, joined)
            values = _user_to_row(merged)
            if row_index is not None:
                await _with_retries(
                    ws.update,
                    [values],
                    f"A{row_index}",
                    value_input_option=ValueInputOption.raw,
                )
            else:
                await _with_retries(ws.append_row, values, value_input_option=ValueInputOption.raw)
        return merged

    async def list_users(self, trip: Trip, joined_only: bool = False) -> list[TripUser]:
        ws = await self._users_worksheet(trip.sheet_id)
        rows = await _with_retries(
            ws.get_all_values, value_render_option=ValueRenderOption.unformatted
        )
        users = [_row_to_user(row) for row in rows[1:] if _row_nonempty(row)]
        if joined_only:
            return [user for user in users if user.joined]
        return users

    # -- Expenses, splits, rates (Sprint 2) ------------------------------------

    def _cell(self, value: object) -> dict[str, Any]:
        if isinstance(value, bool):
            return {"userEnteredValue": {"boolValue": value}}
        if isinstance(value, (int, float)):
            return {"userEnteredValue": {"numberValue": float(value)}}
        return {"userEnteredValue": {"stringValue": str(value)}}

    def _row_to_cells(self, row: list[Any]) -> dict[str, Any]:
        return {"values": [self._cell(value) for value in row]}

    async def append_expense(
        self, trip: Trip, expense: Expense, line_items: Sequence[ReceiptLineItem] = ()
    ) -> None:
        """One `spreadsheets.batchUpdate` with `appendCells` requests, in order Line
        Items, Splits, Expenses last (spec §10; sprint 4 adds Line Items rows).
        """
        spreadsheet = await self._open(trip.sheet_id)
        splits_ws = await _with_retries(spreadsheet.worksheet, "Splits")
        expenses_ws = await _with_retries(spreadsheet.worksheet, "Expenses")

        requests: list[dict[str, Any]] = []
        if line_items:
            line_items_ws = await _with_retries(spreadsheet.worksheet, "Line Items")
            for index, item in enumerate(line_items, start=1):
                row = line_item_to_row(item, expense, index)
                requests.append(
                    {
                        "appendCells": {
                            "sheetId": line_items_ws.id,
                            "rows": [self._row_to_cells(row)],
                            "fields": "userEnteredValue",
                        }
                    }
                )
        for split in expense.splits:
            row = split_to_row(split, expense.expense_id, trip.trip_currency, trip.home_currency)
            requests.append(
                {
                    "appendCells": {
                        "sheetId": splits_ws.id,
                        "rows": [self._row_to_cells(row)],
                        "fields": "userEnteredValue",
                    }
                }
            )
        requests.append(
            {
                "appendCells": {
                    "sheetId": expenses_ws.id,
                    "rows": [self._row_to_cells(expense_to_row(expense))],
                    "fields": "userEnteredValue",
                }
            }
        )
        await _with_retries(spreadsheet.batch_update, {"requests": requests})

    async def list_expenses(self, trip: Trip) -> list[ExpenseRecord]:
        spreadsheet = await self._open(trip.sheet_id)
        ws = await _with_retries(spreadsheet.worksheet, "Expenses")
        rows = await _with_retries(
            ws.get_all_values, value_render_option=ValueRenderOption.unformatted
        )
        return [
            row_to_expense_record(row, trip.trip_currency, trip.home_currency)
            for row in rows[1:]
            if _row_nonempty(row)
        ]

    async def expense_exists(self, trip: Trip, expense_id: str) -> bool:
        spreadsheet = await self._open(trip.sheet_id)
        ws = await _with_retries(spreadsheet.worksheet, "Expenses")
        rows = await _with_retries(
            ws.get_all_values, value_render_option=ValueRenderOption.unformatted
        )
        headers = headers_for(trip.trip_currency, trip.home_currency)["Expenses"]
        id_col = headers.index("Expense ID")
        return any(
            _row_nonempty(row) and len(row) > id_col and str(row[id_col]) == expense_id
            for row in rows[1:]
        )

    async def cancel_expense(
        self, trip: Trip, expense_id: str, cancelled_at: datetime, note_suffix: str
    ) -> bool:
        """Row update, never deletion (spec §12, `/undo`)."""
        spreadsheet = await self._open(trip.sheet_id)
        ws = await _with_retries(spreadsheet.worksheet, "Expenses")
        rows = await _with_retries(
            ws.get_all_values, value_render_option=ValueRenderOption.unformatted
        )
        headers = headers_for(trip.trip_currency, trip.home_currency)["Expenses"]
        id_col = headers.index("Expense ID")
        status_col = headers.index("Status")
        headers.index("Cancelled At")
        notes_col = headers.index("Notes")

        row_index: int | None = None
        existing_notes = ""
        for index, row in enumerate(rows[1:], start=2):
            if row and len(row) > id_col and str(row[id_col]) == expense_id:
                row_index = index
                existing_notes = row[notes_col] if len(row) > notes_col else ""
                break
        if row_index is None:
            return False

        new_notes = f"{existing_notes} {note_suffix}".strip() if existing_notes else note_suffix
        await _with_retries(
            ws.update,
            [["cancelled", cancelled_at.isoformat(), new_notes]],
            f"{rowcol_to_a1(row_index, status_col + 1)}:{rowcol_to_a1(row_index, notes_col + 1)}",
            value_input_option=ValueInputOption.raw,
        )
        return True

    async def find_rate(
        self, trip: Trip, base: str, quote: str, requested_date: date
    ) -> ExchangeRate | None:
        """Plain lookup by requested date; the rate-date-as-cache-key rule (spec §4)
        lives only in `RateService`'s in-memory cache, not here.
        """
        spreadsheet = await self._open(trip.sheet_id)
        ws = await _with_retries(spreadsheet.worksheet, "Exchange Rates")
        rows = await _with_retries(
            ws.get_all_values, value_render_option=ValueRenderOption.unformatted
        )
        for row in rows[1:]:
            if not _row_nonempty(row):
                continue
            row_requested = date.fromisoformat(str(row[0]))
            if row_requested != requested_date or str(row[2]) != base or str(row[3]) != quote:
                continue
            return ExchangeRate(
                base=str(row[2]),
                quote=str(row[3]),
                requested_date=row_requested,
                rate_date=date.fromisoformat(str(row[1])),
                rate=Decimal(str(row[4])),
                provider=str(row[5]),
                retrieved_at=datetime.fromisoformat(str(row[6])),
            )
        return None

    async def save_rate(self, trip: Trip, rate: ExchangeRate) -> None:
        spreadsheet = await self._open(trip.sheet_id)
        ws = await _with_retries(spreadsheet.worksheet, "Exchange Rates")
        row = [
            rate.requested_date.isoformat(),
            rate.rate_date.isoformat(),
            rate.base,
            rate.quote,
            float(rate.rate),
            rate.provider,
            rate.retrieved_at.isoformat(),
        ]
        await _with_retries(ws.append_row, row, value_input_option=ValueInputOption.raw)
