"""Pure rendering of a `Draft` into card text and keyboard. Bot-layer only: builds
telegram objects, so `app/services` must never import this module.

Spec references: pinned-decisions §8; sprint 2 pins ("Card texts").
"""

from __future__ import annotations

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.formatting import (
    fmt_amount_pair,
    fmt_date,
    fmt_date_short,
    fmt_money,
    fmt_rate,
    format_expense_amount,
)
from app.bot.keyboards.receipt import (
    choose_total_keyboard,
    manual_amount_keyboard,
    reconcile_keyboard,
)
from app.models.trip import Trip
from app.services.drafts import Draft


def _buttons(draft_id: str, *pairs: tuple[str, str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(label, callback_data=f"{draft_id}:{action}:-")
                for label, action in pairs
            ]
        ]
    )


def render_draft(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup | None]:
    """Builds every card, `Saved ✅ ...`/`Cancelled.` included."""
    if draft.state == "detected":
        return _render_detected(draft, trip)
    if draft.state == "choose_total":
        return _render_choose_total(draft, trip)
    if draft.state == "reconcile":
        return _render_reconcile(draft, trip)
    if draft.state == "manual_amount":
        return _render_manual_amount(draft, trip)
    if draft.state == "preview":
        return _render_preview(draft, trip)
    if draft.state == "rate_failed":
        return _render_rate_failed(draft, trip)
    if draft.state == "committed":
        return _render_committed(draft, trip), None
    if draft.state == "cancelled":
        return "Cancelled.", None
    raise ValueError(f"no card for draft state {draft.state}")


def _render_detected(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    assert draft.rate is not None
    if draft.source_type == "receipt":
        return _render_detected_receipt(draft, trip)
    desc = escape(draft.description)
    payer = escape(draft.payer_name)
    date_str = fmt_date(draft.transaction_date)

    if draft.currency == trip.home_currency:
        # Entered in the home currency: home amount only, no rate line (spec §12,
        # 2026-10-01 change) -- there is nothing to approximate against.
        amounts_line = fmt_money(draft.home_amount_minor, trip.home_currency)
        text = (
            f"🧾 <b>{desc}</b>\n{amounts_line}\nPaid by {payer} · {date_str}\n\n"
            "Who should share this?"
        )
        return text, _buttons(draft.draft_id, ("Just me", "justme"), ("Cancel", "cancel"))

    trip_str = fmt_money(draft.trip_amount_minor, trip.trip_currency)
    home_str = fmt_money(draft.home_amount_minor, trip.home_currency)
    amounts_line = f"{trip_str} ≈ {home_str}"
    rate_line = (
        f"Rate 1 {trip.trip_currency} = {fmt_rate(draft.rate.rate)} {trip.home_currency} "
        f"({fmt_date_short(draft.rate.rate_date)})"
    )
    text = (
        f"🧾 <b>{desc}</b>\n{amounts_line}\nPaid by {payer} · {date_str}\n{rate_line}\n\n"
        "Who should share this?"
    )
    return text, _buttons(draft.draft_id, ("Just me", "justme"), ("Cancel", "cancel"))


def _render_detected_receipt(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    """Receipt detected card (sprint 4 pin): merchant, total in both currencies,
    date source, "N items found" when items exist.
    """
    assert draft.rate is not None
    desc = escape(draft.description)
    payer = escape(draft.payer_name)
    merchant_line = f"{escape(draft.merchant)}\n" if draft.merchant else ""
    date_str = fmt_date(draft.transaction_date)
    date_source_str = "from receipt" if draft.date_source == "receipt" else "from message time"
    items_line = f"\n{len(draft.line_items)} items found" if draft.line_items else ""
    amount = fmt_amount_pair(draft.trip_amount_minor, draft.home_amount_minor, trip)
    text = (
        f"🧾 {merchant_line}<b>{desc}</b>\n{amount}\nPaid by {payer} · "
        f"{date_str} ({date_source_str}){items_line}\n\nWho should share this?"
    )
    return text, _buttons(draft.draft_id, ("Just me", "justme"), ("Cancel", "cancel"))


def _render_choose_total(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    desc = escape(draft.description)
    text = f"🧾 <b>{desc}</b>\n\nWhich is the final total?"
    keyboard = choose_total_keyboard(draft.draft_id, draft.candidate_totals, draft.currency)
    return text, keyboard


def _render_reconcile(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    assert draft.items_sum_minor is not None and draft.adjustments_sum_minor is not None
    desc = escape(draft.description)
    items_str = fmt_money(draft.items_sum_minor, draft.currency)
    adjustments_str = fmt_money(draft.adjustments_sum_minor, draft.currency)
    computed = draft.items_sum_minor + draft.adjustments_sum_minor
    computed_str = fmt_money(computed, draft.currency)
    total_str = fmt_money(draft.amount_minor, draft.currency)
    difference = draft.amount_minor - computed
    difference_str = fmt_money(abs(difference), draft.currency)
    text = (
        f"🧾 <b>{desc}</b>\n"
        f"Items {items_str} + adjustments {adjustments_str} = {computed_str}\n"
        f"Receipt total {total_str} (difference {difference_str})\n\n"
        "Use the receipt total anyway?"
    )
    return text, reconcile_keyboard(draft.draft_id)


def _render_manual_amount(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup | None]:
    desc = escape(draft.description)
    if draft.awaiting_manual_amount:
        text = f"🧾 <b>{desc}</b>\n\nReply to this message with the amount."
        return text, None
    text = f"🧾 <b>{desc}</b>\n\nCouldn't read that receipt"
    return text, manual_amount_keyboard(draft.draft_id)


def _render_preview(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    desc = escape(draft.description)
    payer = escape(draft.payer_name)
    amount = format_expense_amount(
        trip, draft.currency, draft.trip_amount_minor, draft.home_amount_minor
    )
    # The share line shows a single amount, in whichever currency was entered --
    # never the parenthetical pair (that's only on the title line above).
    share_str = fmt_money(draft.amount_minor, draft.currency)
    text = f"<b>{desc}</b> — {amount}\nPaid by {payer}\n\n{payer} — {share_str}"
    return text, _buttons(draft.draft_id, ("Confirm", "confirm"), ("Cancel", "cancel"))


def _render_rate_failed(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    desc = escape(draft.description)
    payer = escape(draft.payer_name)
    date_str = fmt_date(draft.transaction_date)
    amount_str = fmt_money(draft.amount_minor, draft.currency)
    text = (
        f"🧾 <b>{desc}</b>\n{amount_str}\nPaid by {payer} · {date_str}\n\n"
        "⚠️ Couldn't fetch the exchange rate, try again in a minute"
    )
    return text, _buttons(draft.draft_id, ("Retry", "retry_rate"), ("Cancel", "cancel"))


def _render_committed(draft: Draft, trip: Trip) -> str:
    desc = escape(draft.description)
    amount = format_expense_amount(
        trip, draft.currency, draft.trip_amount_minor, draft.home_amount_minor
    )
    if draft.currency == trip.home_currency:
        # "Saved ✅ Grab. S$12.00" -- a period separates description from a single
        # amount with no parenthetical to visually break it up otherwise.
        return f"Saved ✅ {desc}. {amount}"
    return f"Saved ✅ {desc} {amount}"
