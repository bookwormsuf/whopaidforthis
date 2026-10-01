"""Tests for app.services.exchange_rates: RateService and FrankfurterProvider."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.models.exchange_rate import ExchangeRate
from app.services.exchange_rates import (
    FrankfurterProvider,
    RateService,
    RateUnavailable,
    StaticRateProvider,
)


def _trip(trip_currency: str = "JPY", home_currency: str = "SGD", sheet_id: str = "sheet-1"):
    return SimpleNamespace(
        sheet_id=sheet_id, trip_currency=trip_currency, home_currency=home_currency
    )


class _FakeLedger:
    """Scoped by `trip.sheet_id`, like the real ledger's per-trip Exchange Rates tab."""

    def __init__(self) -> None:
        self.saved: list[ExchangeRate] = []
        self.stored: dict[str, dict[tuple[str, str, date], ExchangeRate]] = {}
        self.find_calls = 0

    async def find_rate(self, trip, base, quote, requested_date):
        self.find_calls += 1
        return self.stored.get(trip.sheet_id, {}).get((base, quote, requested_date))

    async def save_rate(self, trip, rate: ExchangeRate) -> None:
        self.saved.append(rate)
        self.stored.setdefault(trip.sheet_id, {})[(rate.base, rate.quote, rate.requested_date)] = (
            rate
        )


async def test_rate_service_hits_memory_second_time_without_ledger_or_provider() -> None:
    provider = StaticRateProvider(Decimal("0.0081"), date(2026, 9, 19))
    ledger = _FakeLedger()
    service = RateService(provider, ledger)
    trip = _trip()

    first = await service.rate_for(trip, date(2026, 9, 20))
    second = await service.rate_for(trip, date(2026, 9, 20))

    assert first == second
    assert len(provider.calls) == 1  # provider hit only once
    assert len(ledger.saved) == 1


async def test_rate_service_weekend_requested_date_hits_rate_date_cache() -> None:
    """A rate requested for a weekend resolves to the previous business day; a later
    lookup for that same requested date is a memory hit, no ledger/provider call.
    """
    provider = StaticRateProvider(Decimal("0.0081"), rate_date=date(2026, 9, 18))
    ledger = _FakeLedger()
    service = RateService(provider, ledger)
    trip = _trip()

    weekend_date = date(2026, 9, 20)  # a Sunday, say
    await service.rate_for(trip, weekend_date)
    await service.rate_for(trip, weekend_date)

    assert len(provider.calls) == 1


async def test_rate_service_reads_ledger_before_provider() -> None:
    provider = StaticRateProvider(Decimal("999"), date(2026, 9, 19))
    ledger = _FakeLedger()
    trip = _trip()
    stored_rate = ExchangeRate(
        base="JPY",
        quote="SGD",
        requested_date=date(2026, 9, 20),
        rate_date=date(2026, 9, 19),
        rate=Decimal("0.0081"),
        provider="frankfurter",
        retrieved_at=datetime.now(UTC),
    )
    ledger.stored[trip.sheet_id] = {("JPY", "SGD", date(2026, 9, 20)): stored_rate}

    service = RateService(provider, ledger)
    result = await service.rate_for(trip, date(2026, 9, 20))

    assert result == stored_rate
    assert len(provider.calls) == 0
    assert ledger.saved == []


async def test_rate_service_identity_when_trip_currency_equals_home_currency() -> None:
    provider = StaticRateProvider(Decimal("999"), date(2026, 9, 19))
    ledger = _FakeLedger()
    trip = _trip(trip_currency="SGD", home_currency="SGD")
    service = RateService(provider, ledger)

    rate = await service.rate_for(trip, date(2026, 9, 20))

    assert rate.rate == Decimal(1)
    assert rate.provider == "identity"
    assert rate.rate_date == date(2026, 9, 20)
    assert len(provider.calls) == 0
    assert ledger.saved == []
    assert ledger.find_calls == 0


async def test_rate_service_caches_per_trip_sheet_id() -> None:
    provider = StaticRateProvider(Decimal("0.0081"), date(2026, 9, 19))
    ledger = _FakeLedger()
    service = RateService(provider, ledger)
    trip_a = _trip(sheet_id="sheet-a")
    trip_b = _trip(sheet_id="sheet-b")

    await service.rate_for(trip_a, date(2026, 9, 20))
    await service.rate_for(trip_b, date(2026, 9, 20))

    assert len(provider.calls) == 2  # each trip's cache is independent


async def test_frankfurter_provider_parses_response() -> None:
    client = MagicMock(spec=httpx.AsyncClient)
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "amount": 1.0,
        "base": "JPY",
        "date": "2026-09-18",
        "rates": {"SGD": 0.0081},
    }
    client.get = AsyncMock(return_value=response)

    provider = FrankfurterProvider(client)
    rate = await provider.get_rate("JPY", "SGD", date(2026, 9, 20))

    assert rate.rate == Decimal("0.0081")
    assert rate.rate_date == date(2026, 9, 18)
    assert rate.requested_date == date(2026, 9, 20)
    client.get.assert_awaited_once()
    args, kwargs = client.get.call_args
    assert args[0] == "https://api.frankfurter.dev/v1/2026-09-20"
    assert kwargs["params"] == {"from": "JPY", "to": "SGD"}


async def test_frankfurter_provider_raises_rate_unavailable_on_repeated_transport_error() -> None:
    client = MagicMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=httpx.ConnectTimeout("boom"))

    provider = FrankfurterProvider(client)
    with pytest.raises(RateUnavailable):
        await provider.get_rate("JPY", "SGD", date(2026, 9, 20))

    assert client.get.await_count == 3  # 1 initial + 2 retries


async def test_frankfurter_provider_does_not_retry_on_4xx() -> None:
    client = MagicMock(spec=httpx.AsyncClient)
    response = MagicMock()
    response.status_code = 404
    client.get = AsyncMock(return_value=response)

    provider = FrankfurterProvider(client)
    with pytest.raises(RateUnavailable):
        await provider.get_rate("JPY", "SGD", date(2026, 9, 20))

    assert client.get.await_count == 1


async def test_frankfurter_provider_malformed_json_raises_rate_unavailable() -> None:
    """response.json() itself failing (e.g. a non-JSON body) must be caught inside
    the guarded block and translated to RateUnavailable, not escape raw.
    """
    client = MagicMock(spec=httpx.AsyncClient)
    response = MagicMock()
    response.status_code = 200
    response.json.side_effect = ValueError("not json")
    client.get = AsyncMock(return_value=response)

    provider = FrankfurterProvider(client)
    with pytest.raises(RateUnavailable):
        await provider.get_rate("JPY", "SGD", date(2026, 9, 20))


async def test_frankfurter_provider_called_with_transaction_date_not_today() -> None:
    """The provider must be asked for the *transaction* date, never today's date."""
    client = MagicMock(spec=httpx.AsyncClient)
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "amount": 1.0,
        "base": "JPY",
        "date": "2026-01-02",
        "rates": {"SGD": 0.0081},
    }
    client.get = AsyncMock(return_value=response)

    provider = FrankfurterProvider(client)
    historical_date = date(2026, 1, 3)  # not "today" for this test run
    await provider.get_rate("JPY", "SGD", historical_date)

    args, _kwargs = client.get.call_args
    assert args[0] == "https://api.frankfurter.dev/v1/2026-01-03"
