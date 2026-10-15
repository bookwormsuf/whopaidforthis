"""Tests for app.services.google_sheets, with gspread fully mocked."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound

from app.models.category import Category
from app.models.exchange_rate import ExchangeRate
from app.models.expense import Expense, ExpenseSplit
from app.models.trip import Trip
from app.services.google_sheets import SheetsLedgerStore, _with_retries
from app.services.ledger_store import LedgerUnavailable
from app.services.sheet_layout import TAB_ORDER

_A1_RE = re.compile(r"^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$")


def _col_letters_to_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def _api_error(status_code: int) -> APIError:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {"code": status_code, "message": "boom", "status": "ERR"}
    return APIError(response)


_next_gid = iter(range(1, 100000))


class FakeWorksheet:
    def __init__(self, title: str, rows: list[list[object]] | None = None) -> None:
        self.title = title
        self.rows: list[list[object]] = rows or []
        self.frozen_rows: int | None = None
        self.id = next(_next_gid)
        self.get_all_values_calls: list[object] = []
        self.append_kwargs: list[dict] = []

    def get_values(self, range_name: str | None = None) -> list[list[object]]:
        if range_name == "A1:Z1":
            return [self.rows[0]] if self.rows else []
        if range_name == "A2:G2":
            return [self.rows[1]] if len(self.rows) > 1 else []
        return self.rows

    def get_all_values(self, value_render_option: object = None) -> list[list[object]]:
        self.get_all_values_calls.append(value_render_option)
        return self.rows

    def update(
        self,
        values: list[list[object]],
        range_name: str | None = None,
        value_input_option: object = None,
    ) -> None:
        if range_name is None:
            for i, row in enumerate(values):
                if i < len(self.rows):
                    self.rows[i] = row
                else:
                    self.rows.append(row)
            return
        match = _A1_RE.match(range_name)
        if not match:
            raise ValueError(f"FakeWorksheet.update: unsupported range {range_name!r}")
        col_letters, row_str = match.group(1), match.group(2)
        row_index = int(row_str)
        col_index = _col_letters_to_index(col_letters)
        while len(self.rows) < row_index:
            self.rows.append([])
        row = self.rows[row_index - 1]
        for offset, value in enumerate(values[0]):
            target_col = col_index + offset
            while len(row) <= target_col:
                row.append("")
            row[target_col] = value

    def freeze(self, rows: int | None = None, cols: int | None = None) -> None:
        self.frozen_rows = rows

    def append_row(self, values: list[object], **kwargs: object) -> None:
        self.append_kwargs.append(kwargs)
        self.rows.append(values)


class FakeSpreadsheet:
    def __init__(self, worksheets: list[FakeWorksheet]) -> None:
        self._worksheets = {ws.title: ws for ws in worksheets}
        self.created_order: list[str] = []
        self.deleted: list[str] = []
        self.batch_update_calls: list[dict] = []

    def batch_update(self, body: dict) -> None:
        self.batch_update_calls.append(body)
        for request in body.get("requests", []):
            append = request.get("appendCells")
            if append is None:
                continue
            ws = self._worksheet_by_id(append["sheetId"])
            for row in append["rows"]:
                values = []
                for cell in row["values"]:
                    entered = cell["userEnteredValue"]
                    if "boolValue" in entered:
                        values.append(entered["boolValue"])
                    elif "numberValue" in entered:
                        values.append(entered["numberValue"])
                    else:
                        values.append(entered["stringValue"])
                ws.rows.append(values)

    def _worksheet_by_id(self, sheet_id: int) -> FakeWorksheet:
        for ws in self._worksheets.values():
            if ws.id == sheet_id:
                return ws
        raise KeyError(sheet_id)

    def worksheets(self) -> list[FakeWorksheet]:
        return list(self._worksheets.values())

    def worksheet(self, title: str) -> FakeWorksheet:
        if title not in self._worksheets:
            raise WorksheetNotFound(title)
        return self._worksheets[title]

    def add_worksheet(self, title: str, rows: int, cols: int, index: int | None = None):
        ws = FakeWorksheet(title)
        self._worksheets[title] = ws
        self.created_order.append(title)
        return ws

    def del_worksheet(self, ws: FakeWorksheet) -> None:
        self.deleted.append(ws.title)
        del self._worksheets[ws.title]


async def test_with_retries_does_not_retry_on_400(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(
        "app.services.google_sheets.asyncio.sleep", AsyncMock(side_effect=sleeps.append)
    )
    func = MagicMock(side_effect=_api_error(400))

    with pytest.raises(LedgerUnavailable) as exc_info:
        await _with_retries(func)

    assert func.call_count == 1
    assert sleeps == []
    assert exc_info.value.status == 400


async def test_with_retries_retries_on_429_then_raises_ledger_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("app.services.google_sheets.asyncio.sleep", fake_sleep)
    func = MagicMock(side_effect=_api_error(429))

    with pytest.raises(LedgerUnavailable) as exc_info:
        await _with_retries(func)

    assert func.call_count == 4  # 1 initial attempt + 3 retries
    assert sleeps == [1.0, 2.0, 4.0]
    assert exc_info.value.status == 429


async def test_with_retries_does_not_retry_on_403(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(
        "app.services.google_sheets.asyncio.sleep", AsyncMock(side_effect=sleeps.append)
    )
    func = MagicMock(side_effect=_api_error(403))

    with pytest.raises(LedgerUnavailable) as exc_info:
        await _with_retries(func)

    assert func.call_count == 1
    assert sleeps == []
    assert exc_info.value.status == 403


async def test_with_retries_maps_connection_error_to_ledger_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raw transport failure (DNS/connection refused, no HTTP status at all)
    must not escape as a bare requests exception -- it has to come out as
    LedgerUnavailable like every other failure mode, so callers never need to
    know gspread uses requests under the hood.
    """
    import requests

    sleeps: list[float] = []
    monkeypatch.setattr(
        "app.services.google_sheets.asyncio.sleep", AsyncMock(side_effect=sleeps.append)
    )
    func = MagicMock(side_effect=requests.exceptions.ConnectionError("boom"))

    with pytest.raises(LedgerUnavailable):
        await _with_retries(func)


async def test_with_retries_retries_on_timeout_then_raises_ledger_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import requests

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("app.services.google_sheets.asyncio.sleep", fake_sleep)
    func = MagicMock(side_effect=requests.exceptions.Timeout("boom"))

    with pytest.raises(LedgerUnavailable):
        await _with_retries(func)

    assert func.call_count == 4  # 1 initial attempt + 3 retries, same as 429/5xx
    assert sleeps == [1.0, 2.0, 4.0]


def _store() -> SheetsLedgerStore:
    return SheetsLedgerStore(
        service_account_info={"client_email": "bot@example.iam.gserviceaccount.com"},
        control_sheet_id="control-sheet-id",
        bot_version="v-test",
    )


def _patch_client(store: SheetsLedgerStore, open_by_key) -> None:
    client = MagicMock()
    client.open_by_key = open_by_key
    store._get_client = AsyncMock(return_value=client)  # type: ignore[method-assign]


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: SpreadsheetNotFound(),
        lambda: PermissionError(),
        lambda: _api_error(403),
        lambda: _api_error(404),
    ],
    ids=["spreadsheet-not-found", "permission-error", "api-403", "api-404"],
)
async def test_can_open_false_for_not_found_or_no_access(make_error) -> None:
    store = _store()
    _patch_client(store, MagicMock(side_effect=make_error()))
    assert await store.can_open("some-id") is False


async def test_can_open_true_when_open_succeeds() -> None:
    store = _store()
    _patch_client(store, MagicMock(return_value=MagicMock()))
    assert await store.can_open("some-id") is True


async def test_can_open_raises_ledger_unavailable_on_500() -> None:
    store = _store()
    _patch_client(store, MagicMock(side_effect=_api_error(500)))
    with pytest.raises(LedgerUnavailable) as exc_info:
        await store.can_open("some-id")
    assert exc_info.value.status == 500


async def test_initialise_trip_sheet_creates_in_order_and_cleans_sheet1() -> None:
    store = _store()
    existing_users = FakeWorksheet(
        "Users",
        rows=[
            ["Telegram User ID", "Username", "Display Name", "First Seen", "Last Seen", "Joined"]
        ],
    )
    sheet1 = FakeWorksheet("Sheet1", rows=[])
    spreadsheet = FakeSpreadsheet([existing_users, sheet1])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]

    trip = Trip(
        chat_id=-100,
        sheet_id="sheet-id-1234567890",
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )

    await store.initialise_trip_sheet(trip)

    # Users pre-existed with a header row, so it was skipped, in tab-order.
    expected_created = [tab for tab in TAB_ORDER if tab != "Users"]
    assert spreadsheet.created_order == expected_created

    assert existing_users.frozen_rows is None  # never touched
    for tab in expected_created:
        if tab == "Read Me":
            continue  # a plain-text tab; not frozen
        assert spreadsheet._worksheets[tab].frozen_rows == 1

    assert spreadsheet._worksheets["Trip"].rows[1] == [
        -100,
        "Japan Trip",
        "JPY",
        "SGD",
        "Asia/Tokyo",
        trip.created_at.isoformat(),
        "v-test",
    ]

    assert "Sheet1" not in spreadsheet._worksheets
    assert spreadsheet.deleted == ["Sheet1"]


async def test_initialise_trip_sheet_is_idempotent_on_rerun() -> None:
    store = _store()
    spreadsheet = FakeSpreadsheet([])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]
    trip = Trip(
        chat_id=-100,
        sheet_id="sheet-id-1234567890",
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )

    await store.initialise_trip_sheet(trip)
    trip_row_first_run = spreadsheet._worksheets["Trip"].rows[1]
    await store.initialise_trip_sheet(trip)  # re-run must not duplicate anything
    trip_row_second_run = spreadsheet._worksheets["Trip"].rows[1]

    assert trip_row_first_run == trip_row_second_run
    assert len(spreadsheet._worksheets["Trip"].rows) == 2  # header + one fact row
    assert spreadsheet.created_order.count("Trip") == 1


async def test_initialise_trip_sheet_on_rebind_refreshes_trip_row_but_not_headers() -> None:
    store = _store()
    spreadsheet = FakeSpreadsheet([])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]
    trip = Trip(
        chat_id=-100,
        sheet_id="sheet-id-1234567890",
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )
    await store.initialise_trip_sheet(trip)
    users_header_after_first_run = list(spreadsheet._worksheets["Users"].rows[0])

    rebound_trip = Trip(
        chat_id=-100,
        sheet_id="sheet-id-1234567890",
        name="Renamed Trip",
        trip_currency="EUR",
        home_currency="GBP",
        timezone="Europe/Paris",
        created_at=datetime.now(UTC),
    )
    await store.initialise_trip_sheet(rebound_trip)

    assert spreadsheet._worksheets["Trip"].rows[1] == [
        -100,
        "Renamed Trip",
        "EUR",
        "GBP",
        "Europe/Paris",
        rebound_trip.created_at.isoformat(),
        "v-test",
    ]
    # Headers (already written on the first run) are untouched by the rebind.
    assert spreadsheet._worksheets["Users"].rows[0] == users_header_after_first_run


async def test_upsert_user_writes_native_int_id_and_bool_joined() -> None:
    from app.models.user import TripUser

    store = _store()
    users_ws = FakeWorksheet(
        "Users",
        rows=[
            ["Telegram User ID", "Username", "Display Name", "First Seen", "Last Seen", "Joined"]
        ],
    )
    spreadsheet = FakeSpreadsheet([users_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]

    now = datetime.now(UTC)
    trip = Trip(
        chat_id=-100,
        sheet_id="sheet-id-1234567890",
        name="Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=now,
    )
    user = TripUser(
        user_id=42, username="ada", display_name="Ada", first_seen=now, last_seen=now, joined=True
    )
    await store.upsert_user(trip, user, joined=True)

    row = users_ws.rows[1]
    assert row[0] == 42  # a number cell, not text
    assert row[5] is True
    assert isinstance(row[5], bool)
    # Written with appendCells, not values.append: that skipped the hidden User ID
    # column, then with an A1 range overwrote the header row (both seen live).
    request = spreadsheet.batch_update_calls[-1]["requests"][0]["appendCells"]
    assert request["sheetId"] == users_ws.id
    assert users_ws.append_kwargs == []


async def test_upsert_user_restores_a_lost_header_row() -> None:
    from app.models.user import TripUser

    now = datetime.now(UTC)
    store = _store()
    # The header was overwritten by a user's row (seen live, 2026-10-02).
    users_ws = FakeWorksheet(
        "Users", rows=[[7, "bo", "Bo", now.isoformat(), now.isoformat(), True]]
    )
    users_ws.insert_row = lambda values, index, **kw: users_ws.rows.insert(index - 1, values)
    spreadsheet = FakeSpreadsheet([users_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]
    trip = _trip_jpy_sgd()

    assert [u.user_id for u in await store.list_users(trip, joined_only=True)] == [7]

    ada = TripUser(
        user_id=42, username="ada", display_name="Ada", first_seen=now, last_seen=now, joined=True
    )
    await store.upsert_user(trip, ada, joined=True)
    assert users_ws.rows[0][0] == "Telegram User ID"
    assert [r[0] for r in users_ws.rows[1:]] == [7, 42]
    assert [u.user_id for u in await store.list_users(trip, joined_only=True)] == [7, 42]


async def test_list_users_skips_an_unreadable_row() -> None:
    now = datetime.now(UTC).isoformat()
    store = _store()
    users_ws = FakeWorksheet(
        "Users",
        rows=[
            ["Telegram User ID", "Username", "Display Name", "First Seen", "Last Seen", "Joined"],
            ["", 42, "ada", "Ada", now, now, True],  # shifted one column right
            [42, "ada", "Ada", now, now, True],
        ],
    )
    store._open = AsyncMock(return_value=FakeSpreadsheet([users_ws]))  # type: ignore[method-assign]
    users = await store.list_users(_trip_jpy_sgd(), joined_only=True)
    assert [u.user_id for u in users] == [42]


async def test_upsert_user_is_serialised_by_a_per_sheet_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two concurrent upserts for the same new user must not both append a row."""
    from app.models.user import TripUser

    store = _store()
    users_ws = FakeWorksheet(
        "Users",
        rows=[
            ["Telegram User ID", "Username", "Display Name", "First Seen", "Last Seen", "Joined"]
        ],
    )
    spreadsheet = FakeSpreadsheet([users_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]

    async def to_thread_with_yield(func, *args, **kwargs):
        if func is users_ws.get_all_values:
            await asyncio.sleep(0.01)  # let the other upsert_user() start its own read
        return func(*args, **kwargs)

    monkeypatch.setattr("app.services.google_sheets.asyncio.to_thread", to_thread_with_yield)

    now = datetime.now(UTC)
    trip = Trip(
        chat_id=-100,
        sheet_id="sheet-id-1234567890",
        name="Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=now,
    )
    user = TripUser(
        user_id=1, username="ada", display_name="Ada", first_seen=now, last_seen=now, joined=False
    )
    await asyncio.gather(
        store.upsert_user(trip, user, joined=True),
        store.upsert_user(trip, user, joined=True),
    )

    data_rows = users_ws.rows[1:]
    assert len(data_rows) == 1


async def test_get_binding_caches_not_bound_for_60_seconds() -> None:
    store = _store()
    bindings_ws = FakeWorksheet("Bindings", rows=[["Chat ID", "Sheet ID", "Bound At"]])
    control = FakeSpreadsheet([bindings_ws])
    store._open = AsyncMock(return_value=control)  # type: ignore[method-assign]

    read_count = {"n": 0}
    original_get_all_values = bindings_ws.get_all_values

    def counting_get_all_values():
        read_count["n"] += 1
        return original_get_all_values()

    bindings_ws.get_all_values = counting_get_all_values  # type: ignore[method-assign]

    first = await store.get_binding(-999)
    second = await store.get_binding(-999)

    assert first is None
    assert second is None
    assert read_count["n"] == 1  # second call served from the "not bound" cache


async def test_concurrent_get_binding_creates_bindings_tab_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces the live-run race: settrip_command and the seen-user upsert both
    call get_binding on the same update, both list worksheets before either creates
    the Bindings tab. The lock in _get_or_create_bindings_tab must serialise them.
    """
    store = _store()
    control = FakeSpreadsheet([])  # no Bindings tab yet
    store._open = AsyncMock(return_value=control)  # type: ignore[method-assign]

    async def to_thread_with_yield(func, *args, **kwargs):
        if func is control.worksheets:
            await asyncio.sleep(0.01)  # let the other get_binding() start its own list
        return func(*args, **kwargs)

    monkeypatch.setattr("app.services.google_sheets.asyncio.to_thread", to_thread_with_yield)

    results = await asyncio.gather(
        store.get_binding(-1),
        store.get_binding(-2),
    )

    assert results == [None, None]
    assert control.created_order.count("Bindings") == 1


async def test_initialise_trip_sheet_applies_layout_batch_update_every_run() -> None:
    store = _store()
    spreadsheet = FakeSpreadsheet([])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]
    trip = Trip(
        chat_id=-100,
        sheet_id="sheet-id-1234567890",
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )

    await store.initialise_trip_sheet(trip)
    await store.initialise_trip_sheet(trip)

    assert len(spreadsheet.batch_update_calls) == 2  # re-applied harmlessly each run
    first_requests = spreadsheet.batch_update_calls[0]["requests"]
    assert any("updateSheetProperties" in r for r in first_requests)
    assert any("updateDimensionProperties" in r for r in first_requests)
    assert any("repeatCell" in r for r in first_requests)


async def test_initialise_trip_sheet_creates_read_me_tab_with_content() -> None:
    store = _store()
    spreadsheet = FakeSpreadsheet([])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]
    trip = Trip(
        chat_id=-100,
        sheet_id="sheet-id-1234567890",
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )

    await store.initialise_trip_sheet(trip)

    read_me = spreadsheet._worksheets["Read Me"]
    assert read_me.rows[-1] == ["Do not edit rows by hand; use /undo in Telegram."]

    # Idempotent: content is not rewritten (and would be harmless if it were).
    read_me.rows.append(["custom note"])
    await store.initialise_trip_sheet(trip)
    assert read_me.rows[-1] == ["custom note"]


def _trip_jpy_sgd() -> Trip:
    return Trip(
        chat_id=-100,
        sheet_id="sheet-id-1234567890",
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )


def _expense() -> Expense:
    return Expense(
        expense_id="01EXPENSE00000000000000000",
        created_at=datetime.now(UTC),
        transaction_date=date(2026, 9, 20),
        chat_id=-100,
        message_id=7,
        created_by_user_id=1,
        payer_user_id=1,
        payer_username="ada",
        payer_display_name="Ada Lovelace",
        description="Ramen",
        merchant=None,
        category=Category.FOOD,
        original_amount_minor=1200,
        original_currency="JPY",
        trip_currency="JPY",
        home_currency="SGD",
        trip_amount_minor=1200,
        home_amount_minor=10,
        rate=Decimal("0.0081"),
        rate_date=date(2026, 9, 19),
        source_type="text",
        split_method="equal",
        status="confirmed",
        cancelled_at=None,
        notes=None,
        splits=[
            ExpenseSplit(
                user_id=1,
                username="ada",
                display_name="Ada Lovelace",
                share_trip_minor=1200,
                share_home_minor=10,
                split_method="equal",
            )
        ],
    )


def _expenses_and_splits_worksheets() -> tuple[FakeWorksheet, FakeWorksheet]:
    from app.services.sheet_layout import headers_for

    headers = headers_for("JPY", "SGD")
    return (
        FakeWorksheet("Expenses", rows=[headers["Expenses"]]),
        FakeWorksheet("Splits", rows=[headers["Splits"]]),
    )


class _AppendThenRetryableErrorSpreadsheet(FakeSpreadsheet):
    """Simulates Google actually applying appendCells (so a subsequent read finds
    the row) but the response coming back as a retryable 429/5xx -- the real
    failure mode behind the duplicate-write bug: appendCells is not idempotent,
    so blindly retrying the whole batch_update would append the expense twice.
    """

    def __init__(self, worksheets: list[FakeWorksheet]) -> None:
        super().__init__(worksheets)
        self._calls = 0

    def batch_update(self, body: dict) -> None:
        self._calls += 1
        super().batch_update(body)  # the write actually lands server-side
        if self._calls == 1:
            raise _api_error(500)


async def test_append_expense_does_not_duplicate_on_retried_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.google_sheets.asyncio.sleep", AsyncMock(return_value=None))
    store = _store()
    expenses_ws, splits_ws = _expenses_and_splits_worksheets()
    spreadsheet = _AppendThenRetryableErrorSpreadsheet([expenses_ws, splits_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]

    trip = _trip_jpy_sgd()
    expense = _expense()
    await store.append_expense(trip, expense)

    # The write already landed on the one failed attempt; the fix must notice via
    # expense_exists and never call batch_update again.
    assert spreadsheet._calls == 1
    assert len(expenses_ws.rows) == 2  # header + exactly one expense row, not two
    assert len(splits_ws.rows) == 2  # header + exactly one split row, not two


async def test_append_expense_writes_splits_then_expenses_with_correct_sheet_ids() -> None:
    store = _store()
    expenses_ws, splits_ws = _expenses_and_splits_worksheets()
    spreadsheet = FakeSpreadsheet([expenses_ws, splits_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]

    trip = _trip_jpy_sgd()
    expense = _expense()
    await store.append_expense(trip, expense)

    assert len(spreadsheet.batch_update_calls) == 1
    requests = spreadsheet.batch_update_calls[0]["requests"]
    append_sheet_ids = [r["appendCells"]["sheetId"] for r in requests]
    assert append_sheet_ids == [splits_ws.id, expenses_ws.id]  # Splits before Expenses

    assert len(splits_ws.rows) == 2  # header + one split row
    assert splits_ws.rows[1][0] == expense.expense_id
    assert len(expenses_ws.rows) == 2
    assert expenses_ws.rows[1][0] == expense.expense_id
    assert expenses_ws.rows[1][20] == "confirmed"  # Status column


async def test_list_expenses_round_trips() -> None:
    store = _store()
    expenses_ws, splits_ws = _expenses_and_splits_worksheets()
    spreadsheet = FakeSpreadsheet([expenses_ws, splits_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]

    trip = _trip_jpy_sgd()
    expense = _expense()
    await store.append_expense(trip, expense)

    records = await store.list_expenses(trip)
    assert len(records) == 1
    assert records[0].expense_id == expense.expense_id
    assert records[0].status == "confirmed"


async def test_list_splits_round_trips() -> None:
    store = _store()
    expenses_ws, splits_ws = _expenses_and_splits_worksheets()
    spreadsheet = FakeSpreadsheet([expenses_ws, splits_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]

    trip = _trip_jpy_sgd()
    expense = _expense()
    await store.append_expense(trip, expense)

    records = await store.list_splits(trip)
    assert [(r.expense_id, r.user_id) for r in records] == [
        (expense.expense_id, s.user_id) for s in expense.splits
    ]
    assert [(r.share_trip_minor, r.share_home_minor) for r in records] == [
        (s.share_trip_minor, s.share_home_minor) for s in expense.splits
    ]


async def test_list_expenses_includes_message_id_for_dedup() -> None:
    """DraftService now owns the dedup lazy-load via list_expenses (item 14); there
    is no dedicated list_message_keys method any more.
    """
    store = _store()
    expenses_ws, splits_ws = _expenses_and_splits_worksheets()
    spreadsheet = FakeSpreadsheet([expenses_ws, splits_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]

    trip = _trip_jpy_sgd()
    await store.append_expense(trip, _expense())

    records = await store.list_expenses(trip)
    assert [r.message_id for r in records] == [7]


async def test_cancel_expense_updates_row_not_delete() -> None:
    store = _store()
    expenses_ws, splits_ws = _expenses_and_splits_worksheets()
    spreadsheet = FakeSpreadsheet([expenses_ws, splits_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]

    trip = _trip_jpy_sgd()
    expense = _expense()
    await store.append_expense(trip, expense)

    ok = await store.cancel_expense(
        trip, expense.expense_id, datetime(2026, 9, 21, tzinfo=UTC), "Cancelled via /undo by Ada"
    )
    assert ok is True
    assert len(expenses_ws.rows) == 2  # never deleted
    assert expenses_ws.rows[1][20] == "cancelled"
    assert expenses_ws.rows[1][21] == "2026-09-21T00:00:00+00:00"
    assert expenses_ws.rows[1][22] == "Cancelled via /undo by Ada"


async def test_cancel_expense_unknown_id_returns_false() -> None:
    store = _store()
    expenses_ws, splits_ws = _expenses_and_splits_worksheets()
    spreadsheet = FakeSpreadsheet([expenses_ws, splits_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]

    trip = _trip_jpy_sgd()
    ok = await store.cancel_expense(trip, "nope", datetime.now(UTC), "note")
    assert ok is False


async def test_find_rate_and_save_rate() -> None:
    store = _store()
    rates_ws = FakeWorksheet(
        "Exchange Rates",
        rows=[["Requested Date", "Rate Date", "From", "To", "Rate", "Provider", "Retrieved At"]],
    )
    spreadsheet = FakeSpreadsheet([rates_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]

    trip = _trip_jpy_sgd()
    assert await store.find_rate(trip, "JPY", "SGD", date(2026, 9, 20)) is None

    rate = ExchangeRate(
        base="JPY",
        quote="SGD",
        requested_date=date(2026, 9, 20),
        rate_date=date(2026, 9, 18),
        rate=Decimal("0.0081"),
        provider="frankfurter",
        retrieved_at=datetime.now(UTC),
    )
    await store.save_rate(trip, rate)

    found = await store.find_rate(trip, "JPY", "SGD", date(2026, 9, 20))
    assert found is not None
    assert found.rate == Decimal("0.0081")

    # find_rate is a plain lookup by requested date only (item 15): the rate-date
    # cache-key rule now lives solely in RateService's in-memory cache, not here.
    assert await store.find_rate(trip, "JPY", "SGD", date(2026, 9, 18)) is None


async def test_list_expenses_reads_with_unformatted_value_render_option() -> None:
    """Numbers must be requested unformatted (raw int/float, or comma-free strings),
    never Sheets' locale-formatted display text -- otherwise "1,200" can't be parsed.
    """
    from gspread.utils import ValueRenderOption

    store = _store()
    expenses_ws, splits_ws = _expenses_and_splits_worksheets()
    spreadsheet = FakeSpreadsheet([expenses_ws, splits_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]
    trip = _trip_jpy_sgd()
    await store.append_expense(trip, _expense())

    await store.list_expenses(trip)

    assert expenses_ws.get_all_values_calls == [ValueRenderOption.unformatted]


async def test_list_expenses_parses_int_and_float_amounts() -> None:
    """A fake worksheet returning raw int (JPY) and float (SGD) amounts, as the
    unformatted render option would, parses correctly without any comma-stripping.
    """
    store = _store()
    from app.services.sheet_layout import headers_for

    headers = headers_for("JPY", "SGD")
    row = [None] * len(headers["Expenses"])
    row[0] = "01EXPENSE00000000000000000"
    row[1] = datetime.now(UTC).isoformat()
    row[2] = "2026-09-20"
    row[3] = -100
    row[4] = 7
    row[5] = 1
    row[6] = 1
    row[7] = "ada"
    row[8] = "Ada Lovelace"
    row[9] = "Ramen"
    row[10] = ""
    row[11] = "food"
    row[12] = 1200  # raw int, as unformatted JPY amount
    row[13] = "JPY"
    row[14] = 1200  # raw int
    row[15] = 9.72  # raw float, as unformatted SGD amount
    row[16] = 0.0081
    row[17] = "2026-09-19"
    row[18] = "text"
    row[19] = "equal"
    row[20] = "confirmed"
    row[21] = ""
    row[22] = ""

    expenses_ws = FakeWorksheet("Expenses", rows=[headers["Expenses"], row])
    splits_ws = FakeWorksheet("Splits", rows=[headers["Splits"]])
    spreadsheet = FakeSpreadsheet([expenses_ws, splits_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]
    trip = _trip_jpy_sgd()

    records = await store.list_expenses(trip)

    assert len(records) == 1
    assert records[0].trip_amount_minor == 1200
    assert records[0].home_amount_minor == 972


async def test_list_expenses_parses_comma_formatted_string_amounts() -> None:
    """Even if a fake/misconfigured worksheet returned formatted text like "1,200"
    (what the *default* render option would give for a large JPY amount), the store
    must still parse it -- proving the fix doesn't silently depend on formatting.
    """
    store = _store()
    from app.services.sheet_layout import headers_for

    headers = headers_for("JPY", "SGD")
    row = [None] * len(headers["Expenses"])
    row[0] = "01EXPENSE00000000000000000"
    row[1] = datetime.now(UTC).isoformat()
    row[2] = "2026-09-20"
    row[3] = -100
    row[4] = 7
    row[5] = 1
    row[6] = 1
    row[7] = "ada"
    row[8] = "Ada Lovelace"
    row[9] = "Big Dinner"
    row[10] = ""
    row[11] = "food"
    row[12] = "1,200"
    row[13] = "JPY"
    row[14] = "1,200"
    row[15] = "9.72"
    row[16] = "0.0081"
    row[17] = "2026-09-19"
    row[18] = "text"
    row[19] = "equal"
    row[20] = "confirmed"
    row[21] = ""
    row[22] = ""

    expenses_ws = FakeWorksheet("Expenses", rows=[headers["Expenses"], row])
    splits_ws = FakeWorksheet("Splits", rows=[headers["Splits"]])
    spreadsheet = FakeSpreadsheet([expenses_ws, splits_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]
    trip = _trip_jpy_sgd()

    records = await store.list_expenses(trip)

    assert len(records) == 1
    assert records[0].trip_amount_minor == 1200
    assert records[0].home_amount_minor == 972


async def test_expense_exists() -> None:
    store = _store()
    expenses_ws, splits_ws = _expenses_and_splits_worksheets()
    spreadsheet = FakeSpreadsheet([expenses_ws, splits_ws])
    store._open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]
    trip = _trip_jpy_sgd()
    expense = _expense()

    assert await store.expense_exists(trip, expense.expense_id) is False
    await store.append_expense(trip, expense)
    assert await store.expense_exists(trip, expense.expense_id) is True
    assert await store.expense_exists(trip, "not-a-real-id") is False


async def test_get_binding_raises_trip_sheet_inaccessible_on_403_and_caches() -> None:
    """A binding row exists, but the trip Sheet it points to now 403s (unshared or
    deleted): get_binding raises TripSheetInaccessible, not LedgerUnavailable, and
    caches that outcome so a second call makes no further Google call at all.
    """
    from app.services.ledger_store import TripSheetInaccessible

    store = _store()
    bindings_ws = FakeWorksheet(
        "Bindings",
        rows=[
            ["Chat ID", "Sheet ID", "Bound At"],
            [-100, "trip-sheet-id", "2026-01-01T00:00:00+00:00"],
        ],
    )
    control = FakeSpreadsheet([bindings_ws])
    call_log: list[str] = []

    async def fake_open(sheet_id: str):
        call_log.append(sheet_id)
        if sheet_id == "control-sheet-id":
            return control
        raise LedgerUnavailable("Could not reach Google Sheets", status=403)

    store._open = fake_open  # type: ignore[method-assign]

    with pytest.raises(TripSheetInaccessible) as exc_info:
        await store.get_binding(-100)
    assert exc_info.value.sheet_id == "trip-sheet-id"
    calls_after_first = len(call_log)
    assert calls_after_first == 2  # control, then the trip sheet that 403s

    with pytest.raises(TripSheetInaccessible):
        await store.get_binding(-100)
    assert len(call_log) == calls_after_first  # fully served from the cache


async def test_get_binding_raises_trip_sheet_inaccessible_on_permission_error() -> None:
    """The same path via the builtin PermissionError (no HTTP status attached)."""
    from app.services.ledger_store import TripSheetInaccessible

    store = _store()
    bindings_ws = FakeWorksheet(
        "Bindings",
        rows=[
            ["Chat ID", "Sheet ID", "Bound At"],
            [-100, "trip-sheet-id", "2026-01-01T00:00:00+00:00"],
        ],
    )
    control = FakeSpreadsheet([bindings_ws])

    async def fake_open(sheet_id: str):
        if sheet_id == "control-sheet-id":
            return control
        raise LedgerUnavailable("Could not reach Google Sheets") from PermissionError()

    store._open = fake_open  # type: ignore[method-assign]

    with pytest.raises(TripSheetInaccessible):
        await store.get_binding(-100)
