"""Tests for app.services.currencies."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.currencies import (
    TIMEZONE_DEFAULTS,
    FrankfurterCurrencyCatalog,
    StaticCurrencyCatalog,
    decimals_for,
    default_timezone_for,
    resolve_alias,
)


def test_decimals_for_jpy_and_krw_are_zero() -> None:
    assert decimals_for("JPY") == 0
    assert decimals_for("KRW") == 0


def test_decimals_for_default_is_two() -> None:
    assert decimals_for("SGD") == 2
    assert decimals_for("eur") == 2


def test_default_timezone_for_known_currencies() -> None:
    assert default_timezone_for("JPY") == "Asia/Tokyo"
    assert default_timezone_for("SGD") == "Asia/Singapore"
    assert default_timezone_for("sgd") == "Asia/Singapore"


def test_default_timezone_for_unknown_currency_is_none() -> None:
    assert default_timezone_for("XYZ") is None


def test_resolve_alias_is_case_insensitive() -> None:
    assert resolve_alias("Yen") == "JPY"
    assert resolve_alias("¥") == "JPY"
    assert resolve_alias("SGD") == "SGD"
    assert resolve_alias("unknown-token") is None


async def test_static_currency_catalog_returns_given_codes() -> None:
    catalog = StaticCurrencyCatalog({"JPY", "SGD"})
    assert await catalog.supported() == frozenset({"JPY", "SGD"})


async def test_frankfurter_catalog_fetches_once_and_caches() -> None:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"JPY": "Japanese Yen", "SGD": "Singapore Dollar"})
    client = MagicMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=response)

    catalog = FrankfurterCurrencyCatalog(client)
    first = await catalog.supported()
    second = await catalog.supported()

    assert first == frozenset({"JPY", "SGD"})
    assert second == first
    client.get.assert_awaited_once()


async def test_frankfurter_catalog_falls_back_on_failure() -> None:
    client = MagicMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))

    catalog = FrankfurterCurrencyCatalog(client)
    codes = await catalog.supported()

    assert codes == frozenset(TIMEZONE_DEFAULTS.keys())


@pytest.mark.parametrize("code", sorted(TIMEZONE_DEFAULTS))
def test_every_timezone_default_is_a_real_iana_name(code: str) -> None:
    from zoneinfo import available_timezones

    assert TIMEZONE_DEFAULTS[code] in available_timezones()
