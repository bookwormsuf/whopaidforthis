"""Tests for app.services.currencies."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.currencies import (
    TIMEZONE_CHOICES,
    TIMEZONE_DEFAULTS,
    FrankfurterCurrencyCatalog,
    StaticCurrencyCatalog,
    decimals_for,
    default_timezone_for,
    match_timezone,
    resolve_alias,
    timezone_choices_for,
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


def test_default_timezone_for_unknown_currency_is_utc() -> None:
    assert default_timezone_for("XYZ") == "UTC"
    assert default_timezone_for("IDR") == "Asia/Jakarta"


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


def test_every_timezone_choice_is_a_valid_iana_name() -> None:
    from zoneinfo import available_timezones

    for code, names in TIMEZONE_CHOICES.items():
        assert names, code
        for name in names:
            assert name in available_timezones(), (code, name)


def test_each_timezone_list_starts_with_the_default() -> None:
    for code, names in TIMEZONE_CHOICES.items():
        if code in TIMEZONE_DEFAULTS:
            assert names[0] == TIMEZONE_DEFAULTS[code], code
    assert TIMEZONE_DEFAULTS["MXN"] == "America/Mexico_City"
    assert TIMEZONE_DEFAULTS["BRL"] == "America/Sao_Paulo"


def test_timezone_choices_for_listed_unlisted_and_unknown_codes() -> None:
    assert timezone_choices_for("IDR") == ["Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura"]
    assert timezone_choices_for("idr")[0] == "Asia/Jakarta"
    assert timezone_choices_for("JPY") == ["Asia/Tokyo"]
    assert timezone_choices_for("XYZ") == ["UTC"]


def test_timezone_choices_for_returns_a_copy() -> None:
    timezone_choices_for("USD").clear()
    assert len(TIMEZONE_CHOICES["USD"]) == 5


@pytest.mark.parametrize("raw", ["asia/tokyo", "ASIA/TOKYO", "Asia/Tokyo", " asia/tokyo "])
def test_match_timezone_ignores_case(raw: str) -> None:
    assert match_timezone(raw) == "Asia/Tokyo"


def test_match_timezone_rejects_unknown_names() -> None:
    assert match_timezone("Mars/Base") is None
    assert match_timezone("") is None
