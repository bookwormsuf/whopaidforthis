"""Tests for app.services.google_sheets, with gspread fully mocked."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound

from app.models.trip import Trip
from app.services.google_sheets import SheetsLedgerStore, _with_retries
from app.services.ledger_store import LedgerUnavailable
from app.services.sheet_layout import TAB_ORDER


def _api_error(status_code: int) -> APIError:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {"code": status_code, "message": "boom", "status": "ERR"}
    return APIError(response)


class FakeWorksheet:
    def __init__(self, title: str, rows: list[list[object]] | None = None) -> None:
        self.title = title
        self.rows: list[list[object]] = rows or []
        self.frozen_rows: int | None = None

    def get_values(self, range_name: str | None = None) -> list[list[object]]:
        if range_name == "A1:Z1":
            return [self.rows[0]] if self.rows else []
        if range_name == "A2:G2":
            return [self.rows[1]] if len(self.rows) > 1 else []
        return self.rows

    def get_all_values(self) -> list[list[object]]:
        return self.rows

    def update(
        self,
        values: list[list[object]],
        range_name: str | None = None,
        value_input_option: object = None,
    ) -> None:
        if range_name == "A2":
            while len(self.rows) < 2:
                self.rows.append([])
            self.rows[1] = values[0]
            return
        for i, row in enumerate(values):
            if i < len(self.rows):
                self.rows[i] = row
            else:
                self.rows.append(row)

    def freeze(self, rows: int | None = None, cols: int | None = None) -> None:
        self.frozen_rows = rows

    def append_row(self, values: list[object], **kwargs: object) -> None:
        self.rows.append(values)


class FakeSpreadsheet:
    def __init__(self, worksheets: list[FakeWorksheet]) -> None:
        self._worksheets = {ws.title: ws for ws in worksheets}
        self.created_order: list[str] = []
        self.deleted: list[str] = []

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
    assert row[0] == 42
    assert isinstance(row[0], int)
    assert row[5] is True
    assert isinstance(row[5], bool)


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
