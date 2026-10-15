"""Inline keyboards for the `/settrip` conversation.

Callback data format: `"st:{session_id}:{step}:{value}"`. ULID is 26 chars, so this
comfortably fits Telegram's 64-byte callback_data limit.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Sprint 9 pin (spec §18): SGD, MYR, JPY, CNY shown first, then "Other…" (a 3-letter
# code by reply). Same order for both the trip- and home-currency keyboards.
_CCY_CHOICES: list[str] = ["SGD", "MYR", "JPY", "CNY"]
TRIP_CCY_CHOICES: list[str] = _CCY_CHOICES
HOME_CCY_CHOICES: list[str] = _CCY_CHOICES

_OTHER = "Other…"
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
                    "Start trip", callback_data=_callback(session_id, "confirm", "yes")
                ),
                InlineKeyboardButton(
                    "Cancel", callback_data=_callback(session_id, "confirm", "cancel")
                ),
            ]
        ]
    )


def rebind_keyboard(session_id: str) -> InlineKeyboardMarkup:
    """Asked when `/settrip` runs again on a group with an open trip (sprint 9
    item 1): `Start new trip` closes the old trip and moves on to the currency
    buttons; `Keep current` ends the conversation with one line.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Start new trip", callback_data=_callback(session_id, "rebind", "yes")
                ),
                InlineKeyboardButton(
                    "Keep current", callback_data=_callback(session_id, "rebind", "no")
                ),
            ]
        ]
    )


def close_trip_keyboard(trip_id: int, settle_presser_id: int) -> InlineKeyboardMarkup:
    """The "Close this trip" button on /settle's reply (sprint 9 item 2). Callback
    data carries the trip id and the id of whoever ran /settle, since either that
    person or the trip's `created_by` may press it; the handler re-reads the trip
    from the store rather than trusting a stale `created_by` baked in here.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Close this trip", callback_data=f"closetrip:{trip_id}:{settle_presser_id}"
                )
            ]
        ]
    )


def joined_button(chat_id: int, joined_count: int = 0) -> InlineKeyboardButton:
    """The "I'm in too" button (spec §3), reused on Connected ✅ and Joined ✅
    messages. Label gets a counter once `joined_count` >= 1: "I'm in too (2 joined)".
    """
    label = "I'm in too" if joined_count < 1 else f"I'm in too ({joined_count} joined)"
    return InlineKeyboardButton(label, callback_data=f"join:{chat_id}:tap")


def everyones_in_button(chat_id: int) -> InlineKeyboardButton:
    """Closes joining for this message (spec §3); any joined user may press it."""
    return InlineKeyboardButton("Everyone's in", callback_data=f"join:{chat_id}:done")


def joined_keyboard(chat_id: int, joined_count: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[joined_button(chat_id, joined_count), everyones_in_button(chat_id)]]
    )
