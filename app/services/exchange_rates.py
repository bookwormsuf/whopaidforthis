"""Exchange rate lookup: provider abstraction, Frankfurter implementation, and the
memory-then-ledger-then-provider cache. Spec references: pinned-decisions §4.

Verified endpoint (2026-09-29): `GET https://api.frankfurter.dev/v1/{date}?from={base}
&to={quote}` returns `{"amount":1.0,"base":"<BASE>","date":"<rate_date>",
"rates":{"<QUOTE>": <rate>}}`. Both `from=/to=` and `base=/symbols=` work; `from=/to=`
is used to match spec §4's naming.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

import httpx

from app.models.exchange_rate import ExchangeRate
from app.models.trip import Trip

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 5.0
_MAX_RETRIES = 2


class RateUnavailable(RuntimeError):
    """The exchange rate provider could not be reached or returned an error."""


class ExchangeRateProvider(Protocol):
    async def get_rate(self, base: str, quote: str, on_date: date) -> ExchangeRate: ...


class FrankfurterProvider:
    """`ExchangeRateProvider` backed by the free Frankfurter API. No API key."""

    _BASE_URL = "https://api.frankfurter.dev/v1"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_rate(self, base: str, quote: str, on_date: date) -> ExchangeRate:
        url = f"{self._BASE_URL}/{on_date.isoformat()}"
        params = {"from": base, "to": quote}
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = await self._client.get(url, params=params, timeout=_TIMEOUT_SECONDS)
            except httpx.TransportError as exc:
                last_error = exc
                if attempt < _MAX_RETRIES:
                    continue
                raise RateUnavailable("Could not reach the exchange rate provider") from exc
            if response.status_code >= 500:
                last_error = RuntimeError(f"status {response.status_code}")
                if attempt < _MAX_RETRIES:
                    continue
                raise RateUnavailable("Could not reach the exchange rate provider")
            if response.status_code >= 400:
                raise RateUnavailable(f"Exchange rate provider returned {response.status_code}")
            try:
                data = response.json()
                rate_value = data["rates"][quote]
                rate_date = date.fromisoformat(data["date"])
            except (KeyError, ValueError) as exc:
                raise RateUnavailable("Unexpected response from exchange rate provider") from exc
            return ExchangeRate(
                base=base,
                quote=quote,
                requested_date=on_date,
                rate_date=rate_date,
                rate=Decimal(str(rate_value)),
                provider="frankfurter",
                retrieved_at=datetime.now(UTC),
            )
        raise RateUnavailable("Could not reach the exchange rate provider") from last_error


class StaticRateProvider:
    """A fixed rate for every lookup, for tests."""

    def __init__(self, rate: Decimal, rate_date: date, provider: str = "static") -> None:
        self._rate = rate
        self._rate_date = rate_date
        self._provider = provider
        self.calls: list[tuple[str, str, date]] = []

    async def get_rate(self, base: str, quote: str, on_date: date) -> ExchangeRate:
        self.calls.append((base, quote, on_date))
        return ExchangeRate(
            base=base,
            quote=quote,
            requested_date=on_date,
            rate_date=self._rate_date,
            rate=self._rate,
            provider=self._provider,
            retrieved_at=datetime.now(UTC),
        )


class _LedgerRateStore(Protocol):
    async def find_rate(
        self, trip: Trip, base: str, quote: str, requested_date: date
    ) -> ExchangeRate | None: ...

    async def save_rate(self, trip: Trip, rate: ExchangeRate) -> None: ...


class RateService:
    """The one entry point for a rate: `rate_for(trip, on_date)`.

    - Same-currency trip (spec §4): returns an identity rate (1.0) without touching
      the provider or the ledger.
    - Otherwise, lookup order is memory -> ledger (the global `exchange_rates`
      table, spec §18) -> provider. The in-memory cache is keyed by
      `(base, quote, requested_date)` and, once a rate is known, also
      `(base, quote, rate_date)` -- so a weekend lookup (which resolves to the
      previous business day) is a hit next time that same requested date is asked
      for, without a second ledger read or provider call. This class is the only
      place that rate-date-as-cache-key rule lives; `LedgerStore.find_rate` is a
      plain lookup by requested date.

    Rates are global, not per trip (spec §18: a rate is a fact about a currency
    pair and a date), so the cache key no longer involves the trip at all. One
    instance serves every trip, so a single, process-wide `Deps` can hold it.
    """

    def __init__(self, provider: ExchangeRateProvider, ledger: _LedgerRateStore) -> None:
        self._provider = provider
        self._ledger = ledger
        self._memory: dict[tuple[str, str, date], ExchangeRate] = {}

    async def rate_for(self, trip: Trip, on_date: date) -> ExchangeRate:
        base, quote = trip.trip_currency, trip.home_currency
        if base == quote:
            return ExchangeRate(
                base=base,
                quote=quote,
                requested_date=on_date,
                rate_date=on_date,
                rate=Decimal(1),
                provider="identity",
                retrieved_at=datetime.now(UTC),
            )

        key = (base, quote, on_date)
        cached = self._memory.get(key)
        if cached is not None:
            return cached

        found = await self._ledger.find_rate(trip, base, quote, on_date)
        if found is not None:
            self._memory[key] = found
            self._memory[(base, quote, found.rate_date)] = found
            return found

        rate = await self._provider.get_rate(base, quote, on_date)
        await self._ledger.save_rate(trip, rate)
        self._memory[key] = rate
        self._memory[(base, quote, rate.rate_date)] = rate
        return rate
