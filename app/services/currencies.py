"""Currency tables and the supported-currency catalog.

Spec references: pinned-decisions §2, §4.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

# Unchanged in sprint 15 on purpose: amounts already saved for a trip in, say, VND
# or ISK use 2 decimals, and changing a code's decimals would misread them 100 times
# off. A receipt read with the wrong scale is caught by the 100-times check (spec §7).
MINOR_UNIT_DECIMALS: dict[str, int] = {"JPY": 0, "KRW": 0}


def decimals_for(code: str) -> int:
    """Minor-unit decimal places for a currency code. Defaults to 2."""
    return MINOR_UNIT_DECIMALS.get(code.upper(), 2)


# Active ISO 4217 currency codes (sprint 15). A receipt or message may be in any of
# them; an expense is recorded in one once its rate to the trip currency is known.
ISO_CODES: frozenset[str] = frozenset(
    """AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL
    BSD BTN BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN
    ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR
    ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL
    MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN
    PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP
    STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV
    WST XAF XCD XOF XPF YER ZAR ZMW ZWL""".split()
)

# Codes Frankfurter (the European Central Bank's reference rates) publishes. Any other
# code needs a rate typed by the payer (sprint 15).
PUBLISHED_RATE_CODES: frozenset[str] = frozenset(
    """AUD BGN BRL CAD CHF CNY CZK DKK EUR GBP HKD HUF IDR ILS INR ISK JPY KRW MXN MYR
    NOK NZD PHP PLN RON SEK SGD THB TRY USD ZAR""".split()
)

# Codes that are also everyday English words: typed in a message they count as a
# currency only in capitals (`20 TRY`), so `3 cup coffee` is not Cuban pesos.
WORD_LIKE_CODES: frozenset[str] = frozenset(
    "ALL BAM BOB CAD COP CUP GEL MAD MOP NAD PEN RON RUB SOS TOP TRY".split()
)


def is_iso_code(code: str) -> bool:
    return code.upper() in ISO_CODES


def has_published_rate(code: str) -> bool:
    return code.upper() in PUBLISHED_RATE_CODES


CURRENCY_NAMES: dict[str, str] = {
    "AED": "UAE dirham",
    "AUD": "Australian dollar",
    "CAD": "Canadian dollar",
    "CHF": "Swiss franc",
    "CNY": "Chinese yuan",
    "EUR": "euro",
    "GBP": "British pound",
    "HKD": "Hong Kong dollar",
    "IDR": "Indonesian rupiah",
    "INR": "Indian rupee",
    "JPY": "Japanese yen",
    "KHR": "Cambodian riel",
    "KRW": "South Korean won",
    "LAK": "Lao kip",
    "MYR": "Malaysian ringgit",
    "NZD": "New Zealand dollar",
    "PHP": "Philippine peso",
    "SGD": "Singapore dollar",
    "THB": "Thai baht",
    "TWD": "Taiwan dollar",
    "USD": "US dollar",
    "VND": "Vietnamese dong",
}


def name_for(code: str) -> str:
    """`Malaysian ringgit (RM)`, or the code alone when no name is tabulated."""
    code = code.upper()
    name = CURRENCY_NAMES.get(code)
    symbol = symbol_for(code).strip()
    if name is None:
        return code
    return f"{name} ({symbol})" if symbol != code else name


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
    "₹": "INR",
    "₱": "PHP",
    "₫": "VND",
    "$": HOME_OR_USD,
    "s$": "SGD",
    "sgd": "SGD",
    "rm": "MYR",
    "rp": "IDR",
}

# `¥` is yen, unless the trip or home currency is the Chinese yuan (sprint 15).
YEN_SIGN = "¥"


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
