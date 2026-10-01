"""Currency tables and the supported-currency catalog.

Spec references: pinned-decisions §2, §4.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

MINOR_UNIT_DECIMALS: dict[str, int] = {"JPY": 0, "KRW": 0}


def decimals_for(code: str) -> int:
    """Minor-unit decimal places for a currency code. Defaults to 2."""
    return MINOR_UNIT_DECIMALS.get(code.upper(), 2)


TIMEZONE_DEFAULTS: dict[str, str] = {
    "JPY": "Asia/Tokyo",
    "KRW": "Asia/Seoul",
    "THB": "Asia/Bangkok",
    "EUR": "Europe/Paris",
    "GBP": "Europe/London",
    "USD": "America/New_York",
    "MYR": "Asia/Kuala_Lumpur",
    "IDR": "Asia/Jakarta",
    "SGD": "Asia/Singapore",
    "AUD": "Australia/Sydney",
    "HKD": "Asia/Hong_Kong",
    "TWD": "Asia/Taipei",
    "CNY": "Asia/Shanghai",
    "VND": "Asia/Ho_Chi_Minh",
    "PHP": "Asia/Manila",
    "INR": "Asia/Kolkata",
    "CHF": "Europe/Zurich",
    "NZD": "Pacific/Auckland",
    "CAD": "America/Toronto",
}


def default_timezone_for(code: str) -> str | None:
    """The conventional IANA timezone for a currency code, or None if not tabulated."""
    return TIMEZONE_DEFAULTS.get(code.upper())


CURRENCY_SYMBOLS: dict[str, str] = {
    "JPY": "¥",
    "SGD": "S$",
    "KRW": "₩",
    "THB": "฿",
    "EUR": "€",
    "GBP": "£",
    "USD": "US$",
    "MYR": "RM",
    "IDR": "Rp",
    "AUD": "A$",
    "HKD": "HK$",
    "TWD": "NT$",
    "CNY": "CN¥",
    "VND": "₫",
    "PHP": "₱",
    "INR": "₹",
    "CHF": "CHF ",
    "NZD": "NZ$",
    "CAD": "C$",
}


def symbol_for(code: str) -> str:
    """The display symbol for a currency code, e.g. "¥", "S$". Fallback "<CODE> "."""
    return CURRENCY_SYMBOLS.get(code.upper(), f"{code.upper()} ")


# Resolved contextually by the parser (`app.services.expense_parser`), not to a
# fixed code here: spec §4, "$ maps to the home currency when home is SGD or USD,
# else USD."
HOME_OR_USD = "HOME_OR_USD"

CURRENCY_ALIASES: dict[str, str] = {
    "yen": "JPY",
    "¥": "JPY",
    "won": "KRW",
    "₩": "KRW",
    "baht": "THB",
    "฿": "THB",
    "€": "EUR",
    "£": "GBP",
    "$": HOME_OR_USD,
    "s$": "SGD",
    "sgd": "SGD",
    "rm": "MYR",
    "rp": "IDR",
}


def resolve_alias(token: str) -> str | None:
    """Resolve a free-typed currency token via the alias table, case-insensitively."""
    return CURRENCY_ALIASES.get(token.lower())


class CurrencyCatalog(Protocol):
    """The set of currency codes the bot will accept."""

    async def supported(self) -> frozenset[str]: ...


class StaticCurrencyCatalog:
    """A fixed set of currency codes, for tests."""

    def __init__(self, codes: frozenset[str] | set[str]) -> None:
        self._codes = frozenset(codes)

    async def supported(self) -> frozenset[str]:
        return self._codes


class FrankfurterCurrencyCatalog:
    """Fetches the Frankfurter currency list once and caches it for the process lifetime.

    Verified endpoint: https://api.frankfurter.dev/v1/currencies returns a JSON object of
    code -> name. On fetch failure, falls back to the codes tabulated in TIMEZONE_DEFAULTS
    and logs a warning (never logging message text).
    """

    _ENDPOINT = "https://api.frankfurter.dev/v1/currencies"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._cache: frozenset[str] | None = None

    async def supported(self) -> frozenset[str]:
        if self._cache is not None:
            return self._cache
        try:
            response = await self._client.get(self._ENDPOINT)
            response.raise_for_status()
            data = response.json()
            self._cache = frozenset(data.keys())
        except Exception:
            logger.warning("Frankfurter currency list fetch failed; using fallback currency list")
            self._cache = frozenset(TIMEZONE_DEFAULTS.keys())
        return self._cache
