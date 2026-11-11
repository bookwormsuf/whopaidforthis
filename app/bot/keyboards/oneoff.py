"""Inline keyboards for a one-off bill. Spec §22.

Callback data is `oneoff:start` (the `Split a bill now` button, which has no bill yet)
or `oneoff:<bill id>:<action>[:<argument>]`. The bill ID is `secrets.token_urlsafe(6)`,
8 characters. The longest callbacks are the item-walk toggles,
`oneoff:<8>:wtoggle:<position>,<person>` (at most 29 bytes with 40 items and 20 persons),
far under Telegram's 64-byte limit. Sprint 18 adds `retry`, `type`, `total:<n>`, `manual`,
`continue`, `items`, `wtoggle`, `weveryone`, `wnext`, `wback`, `fixlist`, `fixpick:<n>` and
`fixback`.
"""

from __future__ import annotations

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.formatting import fmt_money, fmt_signed_money
from app.bot.keyboards.items import initials_for
from app.models.receipt import CandidateTotal
from app.services.names import Person
from app.services.oneoff import (
    ItemsSplit,
    OneOffBill,
    claimable_indices,
    claimants_on_screen,
)

_UNIT_SUFFIX = re.compile(r" \(\d+/\d+\)$")

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


def method_keyboard(bill_id: str, by_items: bool = False) -> InlineKeyboardMarkup:
    """One per row: `🍽 Go item by item ▶` (a receipt with 1 to 40 items), `Split
    equally`, `Type amounts instead`, `Cancel`.
    """
    rows = []
    if by_items:
        rows.append([_button(bill_id, "🍽 Go item by item ▶", "items")])
    rows.append([_button(bill_id, "Split equally", "equal")])
    rows.append([_button(bill_id, "Type amounts instead", "custom")])
    rows.append([_cancel(bill_id)])
    return InlineKeyboardMarkup(rows)


def amounts_keyboard(bill_id: str, balanced: bool) -> InlineKeyboardMarkup:
    """`Confirm` is only offered once the amounts add up exactly."""
    row = [_button(bill_id, "Confirm", "confirm")] if balanced else []
    row.append(_cancel(bill_id))
    return InlineKeyboardMarkup([row])


def preview_keyboard(bill_id: str, by_items: bool = False) -> InlineKeyboardMarkup:
    """`[Done] [Edit]`, then `[Cancel]`; an item split has `[Done] [Fix an item]`, then
    `[Edit] [Cancel]`.
    """
    if by_items:
        return InlineKeyboardMarkup(
            [
                [_button(bill_id, "Done", "done"), _button(bill_id, "Fix an item", "fixlist")],
                [_button(bill_id, "Edit", "edit"), _cancel(bill_id)],
            ]
        )
    return InlineKeyboardMarkup(
        [
            [_button(bill_id, "Done", "done"), _button(bill_id, "Edit", "edit")],
            [_cancel(bill_id)],
        ]
    )


# ---- a receipt photo (sprint 18) ----------------------------------------------


def couldnt_read_keyboard(bill_id: str) -> InlineKeyboardMarkup:
    """`[Try again] [Type the bill instead]`, then `[Cancel]`."""
    return InlineKeyboardMarkup(
        [
            [
                _button(bill_id, "Try again", "retry"),
                _button(bill_id, "Type the bill instead", "type"),
            ],
            [_cancel(bill_id)],
        ]
    )


def choose_total_keyboard(
    bill_id: str, candidates: tuple[CandidateTotal, ...], currency: str
) -> InlineKeyboardMarkup:
    """One button per candidate total (up to 3), then `[Other amount] [Cancel]`."""
    row = [
        _button(bill_id, fmt_money(candidate.amount_minor, currency), "total", str(index))
        for index, candidate in enumerate(candidates)
    ]
    return InlineKeyboardMarkup(
        [row, [_button(bill_id, "Other amount", "manual"), _cancel(bill_id)]]
    )


def reconcile_keyboard(bill_id: str, total_label: str) -> InlineKeyboardMarkup:
    """`[Continue with <total>]`, then `[Cancel]`."""
    return InlineKeyboardMarkup(
        [[_button(bill_id, f"Continue with {total_label}", "continue")], [_cancel(bill_id)]]
    )


_MAX_DESCRIPTION_CHARS = 24
_EVERYONE_MARK = "all"


def person_initials(bill: OneOffBill) -> dict[int, str]:
    """Initials for `Fix an item`. A person still called `Person 3` is `P3`, so default
    names do not all collapse to `Pe`; clashes are told apart as in groups.
    """
    people = [
        Person(
            pid,
            f"P{pid}" if bill.name_of(pid) == f"Person {pid}" else bill.name_of(pid),
            None,
        )
        for pid in bill.person_ids
    ]
    return initials_for(people)


def _item_button_text(
    bill: OneOffBill, split: ItemsSplit, number: int, index: int, initials: dict[int, str]
) -> str:
    """`"{n} {desc} {amount} · {initials}"`, `all` for everyone (the group's text)."""
    item = bill.line_items[index]
    description = item.description
    if len(description) > _MAX_DESCRIPTION_CHARS:
        # Keep a unit's "(1/3)": it is what tells the units of a line apart.
        match = _UNIT_SUFFIX.search(description)
        suffix = match.group(0) if match else ""
        keep = max(_MAX_DESCRIPTION_CHARS - len(suffix) - 1, 1)
        description = description[: len(description) - len(suffix)][:keep] + "…" + suffix
    text = f"🍽 {number} {description} {fmt_signed_money(item.amount_minor, bill.currency)}"
    claimants = bill.claims.get(index, ())
    everyone = set(bill.person_ids)
    if index in bill.everyone or (len(everyone) > 1 and everyone <= set(claimants)):
        return f"{text} · {_EVERYONE_MARK}"
    if claimants:
        return f"{text} · {''.join(initials[pid] for pid in claimants)}"
    return text


def walk_keyboard(bill: OneOffBill) -> InlineKeyboardMarkup:
    """One toggle per person, three per row, `✓ ` when ticked, the user as `Me`;
    `Everyone`; `◀ Back` (not on the first item, unless opened from `Fix an item`) and
    `Next ▶`; `Cancel`. Every button carries the item's position, so a press from an
    older render changes nothing.
    """
    split = bill.split
    assert isinstance(split, ItemsSplit)
    position = split.walk_index
    index = claimable_indices(bill)[position]
    ticked = set(claimants_on_screen(bill, index))
    toggles = [
        _button(
            bill.bill_id,
            ("✓ " if pid in ticked else "") + ("Me" if pid == 1 else bill.name_of(pid)),
            "wtoggle",
            f"{position},{pid}",
        )
        for pid in bill.person_ids
    ]
    rows = [toggles[i : i + 3] for i in range(0, len(toggles), 3)]
    rows.append([_button(bill.bill_id, "Everyone", "weveryone", str(position))])
    moves = []
    if position > 0 or split.fixing_one:
        moves.append(_button(bill.bill_id, "◀ Back", "wback", str(position)))
    moves.append(_button(bill.bill_id, "Next ▶", "wnext", str(position)))
    rows.append(moves)
    rows.append([_cancel(bill.bill_id)])
    return InlineKeyboardMarkup(rows)


def fix_list_keyboard(bill: OneOffBill) -> InlineKeyboardMarkup:
    """`Fix an item`: one button per claimable item with who has it, then
    `◀ Back to summary`.
    """
    split = bill.split
    assert isinstance(split, ItemsSplit)
    initials = person_initials(bill)
    rows = [
        [
            _button(
                bill.bill_id,
                _item_button_text(bill, split, number, index, initials),
                "fixpick",
                str(number - 1),
            )
        ]
        for number, index in enumerate(claimable_indices(bill), start=1)
    ]
    rows.append([_button(bill.bill_id, "◀ Back to summary", "fixback")])
    return InlineKeyboardMarkup(rows)
