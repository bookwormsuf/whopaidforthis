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
    fmt_signed_money,
    format_expense_amount,
)
from app.bot.keyboards.items import items_keyboard
from app.bot.keyboards.receipt import (
    choose_total_keyboard,
    manual_amount_keyboard,
    reconcile_keyboard,
)
from app.bot.keyboards.split import (
    custom_keyboard,
    detected_keyboard,
    participants_keyboard,
    preview_keyboard,
    resolve_name_keyboard,
    split_method_keyboard,
)
from app.models.trip import Trip
from app.services.drafts import (
    MAX_CLAIMABLE_ITEMS,
    Draft,
    by_items_available,
    claimable_indices,
    draft_items_split,
    draft_shares,
    original_total,
    person_for,
    unclaimed_total,
)
from app.services.splitting import Custom, Items


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
    if draft.state == "resolve_name":
        return _render_resolve_name(draft, trip)
    if draft.state == "participants":
        return _render_participants(draft, trip)
    if draft.state == "split_method":
        return _render_split_method(draft, trip)
    if draft.state == "custom":
        return _render_custom(draft, trip)
    if draft.state == "items":
        return _render_items(draft, trip)
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
        return text, detected_keyboard(draft.draft_id)

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
    return text, detected_keyboard(draft.draft_id)


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
    return text, detected_keyboard(draft.draft_id)


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


def _header(draft: Draft, trip: Trip) -> str:
    """`🧾 <b>Dinner</b> — ¥6,000 (S$48.60)` then `Paid by Ada`, shared by the
    sharing-step cards.
    """
    amount = format_expense_amount(
        trip, draft.currency, draft.trip_amount_minor, draft.home_amount_minor
    )
    return f"🧾 <b>{escape(draft.description)}</b> — {amount}\nPaid by {escape(draft.payer_name)}"


def _names(draft: Draft, user_ids: list[int]) -> str:
    return ", ".join(escape(person_for(draft, pid).display_name) for pid in user_ids)


def _render_resolve_name(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    pending = draft.name_queue[0]
    role = " (paid by)" if pending.role == "payer" else ""
    text = f"{_header(draft, trip)}\n\nWho did you mean by '{escape(pending.token)}'?{role}"
    return text, resolve_name_keyboard(draft, pending)


def _render_participants(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    text = f"{_header(draft, trip)}\n\nWho shares this? Tap names, then Next."
    return text, participants_keyboard(draft)


def _render_split_method(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    too_many = ""
    if draft.items_allowed and len(claimable_indices(draft)) > MAX_CLAIMABLE_ITEMS:
        too_many = "\nToo many items to split individually"
    text = (
        f"{_header(draft, trip)}\nShared by {_names(draft, draft.participant_ids)}\n\n"
        f"How should it be split?{too_many}"
    )
    return text, split_method_keyboard(draft.draft_id, by_items_available(draft))


_ADJUSTMENT_LABELS = {"tax": "Tax", "service_charge": "Service", "discount": "Discount"}


def _render_items(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    """The item card (spec §9): running "Unclaimed" in the header, adjustment lines
    listed in the text (they are never buttons).
    """
    lines = [
        _header(draft, trip),
        f"Unclaimed: {fmt_signed_money(unclaimed_total(draft), draft.currency)}",
    ]
    for item in draft.line_items:
        if item.kind == "item":
            continue
        amount = abs(item.amount_minor)
        sign = "−" if item.kind == "discount" else ""
        lines.append(
            f"{_ADJUSTMENT_LABELS[item.kind]}: {escape(item.description)} "
            f"{sign}{fmt_money(amount, draft.currency)} (shared in proportion)"
        )
    lines += [
        "",
        "Everyone taps what they had; tap a shared dish each. Or reply "
        "<code>mine 1,4</code>, <code>split 5 everyone</code>, <code>unclaim 2</code>.",
    ]
    if draft.items_error:
        lines.append(f"⚠️ {escape(draft.items_error, quote=False)}")
    return "\n".join(lines), items_keyboard(draft)


def _items_preview_lines(draft: Draft, trip: Trip) -> list[str]:
    """Per person `items ¥a + tax ¥b − discount ¥c = ¥d` (sprint 6 pin), then the
    home amount of their share. Service charge shows as its own `+ service` term.
    """
    split = draft_items_split(draft, trip)
    shares = draft_shares(draft, trip)
    ccy = draft.currency
    lines = []
    for pid, (_share_trip, share_home) in shares.items():
        name = escape(person_for(draft, pid).display_name)
        marker = " 💳" if pid == draft.payer_id else ""
        terms = f"items {fmt_signed_money(split.subtotals.get(pid, 0), ccy)}"
        for kind, label in (("tax", "tax"), ("service_charge", "service")):
            if kind in split.adjustments:
                terms += f" + {label} {fmt_money(split.adjustments[kind].get(pid, 0), ccy)}"
        if "discount" in split.adjustments:
            discount = -split.adjustments["discount"].get(pid, 0)
            terms += f" − discount {fmt_money(discount, ccy)}"
        line = f"{name}{marker}: {terms} = {fmt_signed_money(split.shares[pid], ccy)}"
        if ccy != trip.home_currency:
            line += f" · {fmt_money(share_home, trip.home_currency)}"
        lines.append(line)
    return lines


def _render_custom(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    """Each person's amount, the running total and the remaining line (sprint 5 pin).
    Amounts are in the currency the expense was entered in.
    """
    assert isinstance(draft.split, Custom)
    amounts = draft.split.amounts_by_participant
    total = original_total(draft, trip)
    entered = sum(amounts.values())
    remaining = total - entered
    lines = [
        f"{escape(person_for(draft, pid).display_name)} — "
        + (fmt_money(amounts[pid], draft.currency) if pid in amounts else "–")
        for pid in draft.participant_ids
    ]
    if remaining == 0:
        remaining_line = f"Remaining: {fmt_money(0, draft.currency)} ✅"
    else:
        sign = "−" if remaining < 0 else ""
        remaining_line = f"Remaining: {sign}{fmt_money(abs(remaining), draft.currency)} ❌"
    parts = [
        _header(draft, trip),
        "",
        "Reply to this message with one line per person, e.g. <code>Amir 2000</code>",
        "",
        *lines,
        f"Total {fmt_money(entered, draft.currency)} of {fmt_money(total, draft.currency)}",
        remaining_line,
    ]
    if draft.custom_error:
        parts.append(f"⚠️ {escape(draft.custom_error, quote=False)}")
    return "\n".join(parts), custom_keyboard(draft.draft_id, balanced=remaining == 0)


def _render_preview(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    """Every participant's share, payer marked (sprint 5 pin). Shares show both
    currencies, trip first, except for an expense entered in the home currency,
    which shows the home currency only (spec §12).
    """
    desc = escape(draft.description)
    payer = escape(draft.payer_name)
    amount = format_expense_amount(
        trip, draft.currency, draft.trip_amount_minor, draft.home_amount_minor
    )
    if isinstance(draft.split, Items):
        lines = _items_preview_lines(draft, trip)
        text = f"<b>{desc}</b> — {amount}\nPaid by {payer}\n\n" + "\n".join(lines)
        return text, preview_keyboard(draft.draft_id)
    lines = []
    for pid, (share_trip, share_home) in draft_shares(draft, trip).items():
        name = escape(person_for(draft, pid).display_name)
        marker = " 💳" if pid == draft.payer_id else ""
        if draft.currency == trip.home_currency:
            share = fmt_money(share_home, trip.home_currency)
        else:
            share = (
                f"{fmt_money(share_trip, trip.trip_currency)} · "
                f"{fmt_money(share_home, trip.home_currency)}"
            )
        lines.append(f"{name}{marker} — {share}")
    text = f"<b>{desc}</b> — {amount}\nPaid by {payer}\n\n" + "\n".join(lines)
    return text, preview_keyboard(draft.draft_id)


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
