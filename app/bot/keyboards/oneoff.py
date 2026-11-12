"""Inline keyboards for a one-off bill. Spec §22.

Callback data is `oneoff:start` (the `Split a bill now` button, which has no bill yet)
or `oneoff:<bill id>:<action>[:<argument>]`. The bill ID is `secrets.token_urlsafe(6)`,
8 characters, so the longest callback (`oneoff:<8>:payer:20`) is 24 bytes, far under
Telegram's 64-byte limit.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.oneoff import OneOffBill

CALLBACK_PREFIX = "oneoff:"
START_CALLBACK = "oneoff:start"
START_LABEL = "Split a bill now"

# The people-count buttons on the how-many card.
COUNT_CHOICES = (2, 3, 4, 5, 6)


def callback_data(bill_id: str, action: str, arg: str | None = None) -> str:
    data = f"{CALLBACK_PREFIX}{bill_id}:{action}"
    return data if arg is None else f"{data}:{arg}"


def _button(bill_id: str, label: str, action: str, arg: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=callback_data(bill_id, action, arg))


def _cancel(bill_id: str) -> InlineKeyboardButton:
    return _button(bill_id, "Cancel", "cancel")


def start_keyboard() -> InlineKeyboardMarkup:
    """The single `Split a bill now` button (also under the stale-bill message)."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(START_LABEL, callback_data=START_CALLBACK)]])


def drop_keyboard(bill_id: str) -> InlineKeyboardMarkup:
    """`[Yes, start again] [No, keep it]` for the bill being dropped."""
    return InlineKeyboardMarkup(
        [
            [
                _button(bill_id, "Yes, start again", "drop", "yes"),
                _button(bill_id, "No, keep it", "drop", "no"),
            ]
        ]
    )


def count_keyboard(bill_id: str) -> InlineKeyboardMarkup:
    """`[2] [3] [4] [5] [6]` in one row, then `[Cancel]`."""
    row = [_button(bill_id, str(n), "count", str(n)) for n in COUNT_CHOICES]
    return InlineKeyboardMarkup([row, [_cancel(bill_id)]])


def names_keyboard(bill_id: str) -> InlineKeyboardMarkup:
    """`[Next] [Cancel]`."""
    return InlineKeyboardMarkup([[_button(bill_id, "Next", "next"), _cancel(bill_id)]])


def payer_keyboard(bill: OneOffBill) -> InlineKeyboardMarkup:
    """One button per person, the user first as `Me`, three per row, then `[Cancel]`."""
    buttons = [
        _button(
            bill.bill_id,
            "Me" if person_id == 1 else bill.name_of(person_id),
            "payer",
            str(person_id),
        )
        for person_id in bill.person_ids
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([_cancel(bill.bill_id)])
    return InlineKeyboardMarkup(rows)


def method_keyboard(bill_id: str) -> InlineKeyboardMarkup:
    """One per row: `Split equally`, `Type amounts instead`, `Cancel`."""
    return InlineKeyboardMarkup(
        [
            [_button(bill_id, "Split equally", "equal")],
            [_button(bill_id, "Type amounts instead", "custom")],
            [_cancel(bill_id)],
        ]
    )


def amounts_keyboard(bill_id: str, balanced: bool) -> InlineKeyboardMarkup:
    """`Confirm` is only offered once the amounts add up exactly."""
    row = [_button(bill_id, "Confirm", "confirm")] if balanced else []
    row.append(_cancel(bill_id))
    return InlineKeyboardMarkup([row])


def preview_keyboard(bill_id: str) -> InlineKeyboardMarkup:
    """`[Done] [Edit]`, then `[Cancel]`."""
    return InlineKeyboardMarkup(
        [
            [_button(bill_id, "Done", "done"), _button(bill_id, "Edit", "edit")],
            [_cancel(bill_id)],
        ]
    )
