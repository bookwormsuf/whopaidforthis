"""Currency conversion between minor units, at a fixed `Decimal` rate.

Spec references: pinned-decisions §4, sprint 2 pins. `Decimal` is used only inside
this module; the rest of the domain works in `int` minor units.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

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
