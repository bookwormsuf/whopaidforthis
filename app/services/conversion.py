"""Currency conversion between minor units, at a fixed `Decimal` rate.

Spec references: pinned-decisions §4, sprint 2 pins. `Decimal` is used only inside
this module; the rest of the domain works in `int` minor units.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from app.services.currencies import decimals_for


def convert_minor(amount_minor: int, from_ccy: str, to_ccy: str, rate: Decimal) -> int:
    """Convert `amount_minor` of `from_ccy` to `to_ccy` minor units at `rate`
    (from_ccy -> to_ccy), rounding half-even to the target currency's decimals.
    """
    from_decimals = decimals_for(from_ccy)
    to_decimals = decimals_for(to_ccy)
    major = Decimal(amount_minor) / (Decimal(10) ** from_decimals)
    converted_major = major * rate
    converted_minor = converted_major * (Decimal(10) ** to_decimals)
    return int(converted_minor.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def reciprocal_rate(rate: Decimal) -> Decimal:
    """`1 / rate`, or `1` for a zero rate (the "no rate available" placeholder).
    The one place the home->trip reciprocal division happens, so callers (e.g.
    `app.services.drafts`) never perform `Decimal` division themselves.
    """
    return Decimal(1) / rate if rate != 0 else Decimal(1)


def convert_minor_reciprocal(amount_minor: int, from_ccy: str, to_ccy: str, rate: Decimal) -> int:
    """`convert_minor` using the reciprocal of `rate`: `rate` is `to_ccy` -> `from_ccy`
    (e.g. the trip->home rate, used here to go home->trip), so this converts
    `amount_minor` of `from_ccy` to `to_ccy` at `1 / rate`.
    """
    return convert_minor(amount_minor, from_ccy, to_ccy, reciprocal_rate(rate))


def both_amounts(
    amount_minor: int,
    amount_ccy: str,
    trip_currency: str,
    home_currency: str,
    trip_to_home_rate: Decimal,
) -> tuple[int, int]:
    """Given an amount already known in `amount_ccy` (the trip or the home currency)
    and the trip->home rate, returns `(trip_amount_minor, home_amount_minor)`. The
    only place callers outside this module need to reach for a currency conversion
    when building a draft or expense (keeps `Decimal` arithmetic out of the bot and
    draft-flow layers, per spec §4).
    """
    if amount_ccy == trip_currency:
        return amount_minor, convert_minor(
            amount_minor, trip_currency, home_currency, trip_to_home_rate
        )
    return convert_minor_reciprocal(
        amount_minor, home_currency, trip_currency, trip_to_home_rate
    ), amount_minor


def expense_amounts(
    amount_minor: int,
    amount_ccy: str,
    trip_currency: str,
    home_currency: str,
    trip_to_home_rate: Decimal,
    original_to_trip_rate: Decimal | None = None,
) -> tuple[int, int]:
    """`(trip_amount_minor, home_amount_minor)` for an expense in any currency. In
    the trip or home currency this is `both_amounts`. In a third currency (sprint
    15) the amount goes to the trip currency at `original_to_trip_rate`, then on to
    home at the trip->home rate, as a trip-currency expense's would.
    """
    if amount_ccy in (trip_currency, home_currency):
        return both_amounts(
            amount_minor, amount_ccy, trip_currency, home_currency, trip_to_home_rate
        )
    if original_to_trip_rate is None:
        raise ValueError(f"no {amount_ccy} -> {trip_currency} rate for a third-currency amount")
    trip_minor = convert_minor(amount_minor, amount_ccy, trip_currency, original_to_trip_rate)
    return trip_minor, convert_minor(trip_minor, trip_currency, home_currency, trip_to_home_rate)


def amount_in_trip_currency(
    amount_minor: int,
    amount_ccy: str,
    trip_currency: str,
    home_currency: str,
    trip_to_home_rate: Decimal,
    original_to_trip_rate: Decimal | None = None,
) -> int:
    """One amount (a receipt line, say) in the trip currency, whatever its currency."""
    return expense_amounts(
        amount_minor,
        amount_ccy,
        trip_currency,
        home_currency,
        trip_to_home_rate,
        original_to_trip_rate,
    )[0]


_RATE_RE = re.compile(r"^\d+(?:\.\d+)?$|^\.\d+$")


def parse_rate(text: str) -> Decimal | None:
    """A typed rate (sprint 15): a bare positive number, `0.0013`, `7.08` or
    `.25`. `None` for anything else (a currency, a word, zero).
    """
    stripped = text.strip()
    if not _RATE_RE.match(stripped):
        return None
    try:
        rate = Decimal(stripped)
    except InvalidOperation:
        return None
    return rate if rate > 0 else None


def rate_from_amounts(from_minor: int, from_ccy: str, to_minor: int, to_ccy: str) -> Decimal | None:
    """The `from_ccy -> to_ccy` rate that turns one amount into the other, e.g. a
    typed trip-currency total for a third-currency expense (sprint 15).
    """
    if from_minor <= 0 or to_minor <= 0:
        return None
    from_major = Decimal(from_minor) / (Decimal(10) ** decimals_for(from_ccy))
    to_major = Decimal(to_minor) / (Decimal(10) ** decimals_for(to_ccy))
    return to_major / from_major


def rescale_minor(amount_minor: int, from_ccy: str, to_ccy: str) -> int:
    """The same printed amount in another currency's minor units: `100000` read as
    VND (no decimals) is `10000000` satang read as THB (sprint 15).
    """
    return convert_minor(amount_minor, from_ccy, to_ccy, Decimal(1))
