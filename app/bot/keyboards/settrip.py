"""Inline keyboards for the `/settrip` conversation.

Callback data format: `"st:{session_id}:{step}:{value}"`. ULID is 26 chars, so this
comfortably fits Telegram's 64-byte callback_data limit.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

TRIP_CCY_CHOICES: list[str] = ["JPY", "KRW", "THB", "EUR", "GBP", "USD", "MYR", "IDR"]
HOME_CCY_CHOICES: list[str] = ["SGD", "JPY", "KRW", "THB", "EUR", "GBP", "USD", "MYR", "IDR"]

_OTHER = "Other"
_ROW_WIDTH = 3


def _callback(session_id: str, step: str, value: str) -> str:
    return f"st:{session_id}:{step}:{value}"


def _currency_keyboard(session_id: str, step: str, choices: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for code in choices:
        row.append(InlineKeyboardButton(code, callback_data=_callback(session_id, step, code)))
        if len(row) == _ROW_WIDTH:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(_OTHER, callback_data=_callback(session_id, step, "other"))])
    return InlineKeyboardMarkup(rows)


def trip_currency_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return _currency_keyboard(session_id, "trip_ccy", TRIP_CCY_CHOICES)


def home_currency_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return _currency_keyboard(session_id, "home_ccy", HOME_CCY_CHOICES)


def timezone_keyboard(session_id: str, default_timezone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    default_timezone, callback_data=_callback(session_id, "tz", default_timezone)
                )
            ],
            [InlineKeyboardButton(_OTHER, callback_data=_callback(session_id, "tz", "other"))],
        ]
    )


def confirm_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Create Sheet", callback_data=_callback(session_id, "confirm", "yes")
                ),
                InlineKeyboardButton(
                    "Cancel", callback_data=_callback(session_id, "confirm", "cancel")
                ),
            ]
        ]
    )


def rebind_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Yes, rebind", callback_data=_callback(session_id, "rebind", "yes")
                ),
                InlineKeyboardButton(
                    "Keep current", callback_data=_callback(session_id, "rebind", "no")
                ),
            ]
        ]
    )
