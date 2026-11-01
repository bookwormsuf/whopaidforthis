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
    """Up to 3 candidate totals, then `Other amount` (type the total if none of
    them is right) and `Cancel` (spec §7, amended 2026-10-02).
    """
    row = [
        InlineKeyboardButton(
            fmt_money(candidate.amount_minor, currency), callback_data=f"{draft_id}:total:{index}"
        )
        for index, candidate in enumerate(candidates)
    ]
    return InlineKeyboardMarkup(
        [
            row,
            [
                InlineKeyboardButton("Other amount", callback_data=f"{draft_id}:manual:-"),
                InlineKeyboardButton("Cancel", callback_data=f"{draft_id}:cancel:-"),
            ],
        ]
    )


def reconcile_keyboard(draft_id: str, total_label: str) -> InlineKeyboardMarkup:
    """`Continue with <total>` / `Cancel` (sprint 13 pin): the gap is shared like
    tax, so there is nothing else to choose.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Continue with {total_label}", callback_data=f"{draft_id}:continue:-"
                )
            ],
            [InlineKeyboardButton("Cancel", callback_data=f"{draft_id}:cancel:-")],
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
