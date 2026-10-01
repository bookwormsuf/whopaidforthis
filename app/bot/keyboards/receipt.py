"""Inline keyboards for the receipt draft states (`choose_total`, `reconcile`,
`manual_amount`). Spec references: pinned-decisions §7, §8, sprint 4 pins.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.formatting import fmt_money
from app.models.receipt import CandidateTotal


def choose_total_keyboard(
    draft_id: str, candidates: list[CandidateTotal], currency: str
) -> InlineKeyboardMarkup:
    """Up to 3 candidate totals plus `Cancel` (spec §7)."""
    row = [
        InlineKeyboardButton(
            fmt_money(candidate.amount_minor, currency), callback_data=f"{draft_id}:total:{index}"
        )
        for index, candidate in enumerate(candidates)
    ]
    row.append(InlineKeyboardButton("Cancel", callback_data=f"{draft_id}:cancel:-"))
    return InlineKeyboardMarkup([row])


def reconcile_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    """`Use total anyway` / `Cancel` only, this sprint (`Fix items` is sprint 6)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Use total anyway", callback_data=f"{draft_id}:usetotal:-"),
                InlineKeyboardButton("Cancel", callback_data=f"{draft_id}:cancel:-"),
            ]
        ]
    )


def manual_amount_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    """`Try again` / `Enter amount manually`, shown after an unreadable receipt."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Try again", callback_data=f"{draft_id}:retry:-"),
                InlineKeyboardButton("Enter amount manually", callback_data=f"{draft_id}:manual:-"),
            ]
        ]
    )
