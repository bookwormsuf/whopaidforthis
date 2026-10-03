"""HTML card text helpers shared by the draft flow and read commands.

Spec references: sprint 2 pins ("Card texts"), pinned-decisions §12.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from html import escape
from typing import Protocol

from app.models.money import Money
from app.models.trip import Trip


def fmt_money(amount_minor: int, currency: str) -> str:
    return Money(amount_minor=amount_minor, currency=currency).format()


def fmt_signed_money(amount_minor: int, currency: str) -> str:
    """Like `fmt_money`, but a negative amount reads `−¥300`, not `¥-300`."""
    if amount_minor < 0:
        return f"−{fmt_money(-amount_minor, currency)}"
    return fmt_money(amount_minor, currency)


def fmt_amount_pair(trip_amount_minor: int, home_amount_minor: int, trip: Trip) -> str:
    """Trip currency then home, e.g. `¥1,200 (S$9.72)` (spec §12: per-expense amounts
    show trip currency first). Internal to `format_expense_amount` below; kept
    public since a couple of call sites that are always trip-currency use it
    directly (no need to pass `currency` just to re-derive the same branch).
    """
    trip_str = fmt_money(trip_amount_minor, trip.trip_currency)
    home_str = fmt_money(home_amount_minor, trip.home_currency)
    return f"{trip_str} ({home_str})"


def format_expense_amount(trip: Trip, currency: str, trip_minor: int, home_minor: int) -> str:
    """The one place that decides how an expense's amount is shown (spec §12).

    An expense entered in the home currency (`currency == trip.home_currency`) is
    shown in the home currency ONLY -- `S$12.00` -- never alongside the derived
    trip-currency equivalent, even though that equivalent is still stored and still
    feeds totals/formulas. An expense entered in the trip currency keeps the
    existing `¥1,200 (S$9.72)` pair. Every per-expense renderer (draft card, Saved
    ✅ line, `/recent`, `/today`, `/mine`, `/undo`) must go through this, so the rule
    lives in one place.
    """
    if currency == trip.home_currency:
        return fmt_money(home_minor, trip.home_currency)
    return fmt_amount_pair(trip_minor, home_minor, trip)


class _ExpenseLine(Protocol):
    """The fields `fmt_expense_bullet` needs; satisfied by `ExpenseRecord`."""

    transaction_date: date
    description: str
    original_currency: str
    trip_amount_minor: int
    home_amount_minor: int
    payer_display_name: str


def fmt_expense_bullet(expense: _ExpenseLine, trip: Trip, detail: str | None = None) -> str:
    """Two lines for `/recent`, `/today` and `/mine` (sprint 8): `• 3 Oct · Ramen
    ¥1,200 (S$9.72)`, then `Ada paid`, plus `detail` (e.g. "your share S$4.86") after a
    dot when given.
    """
    amount = format_expense_amount(
        trip, expense.original_currency, expense.trip_amount_minor, expense.home_amount_minor
    )
    second = f"{escape(first_name(expense.payer_display_name))} paid"
    if detail:
        second += f" · {detail}"
    return (
        f"• {fmt_date_short(expense.transaction_date)} · {escape(expense.description)} "
        f"{amount}\n   {second}"
    )


def fmt_date(value: date | datetime) -> str:
    """`d Mon yyyy`, e.g. "20 Sep 2026"."""
    return value.strftime("%-d %b %Y")


def fmt_date_short(value: date | datetime) -> str:
    """`d Mon`, e.g. "20 Sep", used in /recent, /today and /mine bullets."""
    return value.strftime("%-d %b")


def fmt_rate(rate: Decimal) -> str:
    return f"{rate:.5f}"


def first_name(display_name: str) -> str:
    return display_name.split()[0] if display_name.split() else display_name


def unsupported_currency_text(trip_currency: str, home_currency: str) -> str:
    """Shared by the text and photo expense handlers (spec §4)."""
    return f"I can only record {trip_currency} or {home_currency} for this trip."
