"""Pure rendering of a `Draft` into card text and keyboard. Bot-layer only: builds
telegram objects, so `app/services` must never import this module.

Spec references: pinned-decisions §8; sprint 2 pins ("Card texts").
"""

from __future__ import annotations

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.formatting import (
    fmt_date,
    fmt_date_short,
    fmt_money,
    fmt_rate,
    fmt_signed_money,
    format_expense_amount,
)
from app.bot.keyboards.items import fix_list_keyboard, walk_keyboard
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
    who_paid_keyboard,
)
from app.models.trip import Trip
from app.services.drafts import (
    MAX_CLAIMABLE_ITEMS,
    UNLISTED_DESCRIPTION,
    Draft,
    billed,
    by_items_available,
    claimable_indices,
    draft_items_split,
    draft_shares,
    original_total,
    person_for,
)
from app.services.splitting import Custom, Items

# Spec §10/§14: on a failed save the draft stays in `preview` with a `Retry save`
# button and this error line on the card.
SAVE_ERROR_LINE = "⚠️ Couldn't save. Tap Retry save to try again."


def _buttons(draft_id: str, *pairs: tuple[str, str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(label, callback_data=f"{draft_id}:{action}:-")
                for label, action in pairs
            ]
        ]
    )


def render_draft(
    draft: Draft, trip: Trip, *, save_error: bool = False
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Builds every card, `Saved ✅ ...`/`Cancelled.` included.

    `save_error=True` only applies to a `preview` card (spec §10/§14): the draft
    message is edited with the error line and its keyboard intact, Confirm
    relabelled `Retry save`.
    """
    if draft.state == "who_paid":
        return _render_who_paid(draft, trip)
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
    if draft.state == "item_walk":
        return _render_walk(draft, trip)
    if draft.state == "fix_list":
        return _render_fix_list(draft, trip)
    if draft.state == "preview":
        return _render_preview(draft, trip, save_error=save_error)
    if draft.state == "rate_failed":
        return _render_rate_failed(draft, trip)
    if draft.state == "committed":
        return _render_committed(draft, trip), None
    if draft.state == "cancelled":
        return "Cancelled.", None
    raise ValueError(f"no card for draft state {draft.state}")


def _render_who_paid(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    """ "Who paid?" before anything else, when nobody was tagged (decided
    2026-10-03).
    """
    text = f"{_title(draft, trip)}\n{fmt_date(draft.transaction_date)}\n\nWho paid?"
    return text, who_paid_keyboard(draft)


def _render_detected(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    """What, how much and who paid first (sprint 8); the rate in small print for a
    trip-currency expense (nothing to convert for a home-currency one, spec §12).
    """
    assert draft.rate is not None
    lines = [_title(draft, trip)]
    when = fmt_date(draft.transaction_date)
    if draft.source_type == "receipt":
        if draft.merchant and draft.merchant.casefold() not in draft.description.casefold():
            lines.append(f"at {escape(draft.merchant)}")
        source = "from receipt" if draft.date_source == "receipt" else "from message time"
        when = f"{when} ({source})"
    lines.append(f"{_payer_line(draft)} · {when}")
    if draft.line_items:
        count = len([i for i in draft.line_items if i.kind == "item"])
        lines.append(f"{count} item{'s' if count != 1 else ''} on the receipt")
    if draft.currency != trip.home_currency:
        lines.append(
            f"<i>Rate 1 {trip.trip_currency} = {fmt_rate(draft.rate.rate)} "
            f"{trip.home_currency} ({fmt_date_short(draft.rate.rate_date)})</i>"
        )
    lines += ["", "Who should share this?"]
    return "\n".join(lines), detected_keyboard(draft)


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
        f"Receipt total {total_str} (difference {difference_str})\n"
    )
    if difference < 0 and by_items_available(draft):
        # Likely a group order where this person paid only their part (sprint 14).
        text += "(Paid for part of the order? Go item by item and mark the rest Not on this bill.)"
    else:
        text += f"({difference_str} isn't on any line; it'll be shared like tax)"
    return text, reconcile_keyboard(draft.draft_id, total_str)


def _render_manual_amount(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup | None]:
    desc = escape(draft.description)
    if draft.awaiting_manual_amount:
        text = f"🧾 <b>{desc}</b>\n\nReply to this message with the amount."
        return text, None
    if draft.read_problem:
        # The reader service failed, not the photo: say so, so nobody retakes it.
        text = (
            f"🧾 <b>{desc}</b>\n\nCouldn't read that receipt: the receipt reader is "
            f"{escape(draft.read_problem)}, not a problem with your photo. Try again in a "
            "minute, or enter the amount."
        )
        return text, manual_amount_keyboard(draft.draft_id)
    text = f"🧾 <b>{desc}</b>\n\nCouldn't read that receipt"
    return text, manual_amount_keyboard(draft.draft_id)


def _amount(draft: Draft, trip: Trip) -> str:
    return format_expense_amount(
        trip, draft.currency, draft.trip_amount_minor, draft.home_amount_minor
    )


def _title(draft: Draft, trip: Trip) -> str:
    """`🧾 <b>Ramen</b> · ¥1,200 (S$9.72)`: what and how much, first (sprint 8)."""
    return f"🧾 <b>{escape(draft.description)}</b> · {_amount(draft, trip)}"


def _payer_line(draft: Draft) -> str:
    return f"{escape(draft.payer_name)} paid"


def _header(draft: Draft, trip: Trip) -> str:
    """The title line, then who paid; shared by the sharing-step cards."""
    return f"{_title(draft, trip)}\n{_payer_line(draft)}"


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
    if len(claimable_indices(draft)) > MAX_CLAIMABLE_ITEMS:
        too_many = "\nToo many items to split individually"
    question = "Who had what?" if by_items_available(draft) else "How should it be split?"
    text = (
        f"{_header(draft, trip)}\nShared by {_names(draft, draft.participant_ids)}\n\n"
        f"{question}{too_many}"
    )
    return text, split_method_keyboard(draft.draft_id, by_items_available(draft))


def _render_walk(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    """ "Who had this? (n of N)", then the item and its amount (sprint 13 pin)."""
    positions = claimable_indices(draft)
    index = positions[draft.walk_index]
    item = draft.line_items[index]
    text = (
        f"{_header(draft, trip)}\n\n"
        f"Who had this?  ({draft.walk_index + 1} of {len(positions)})\n"
        f"<b>{escape(item.description)}</b> · {fmt_money(item.amount_minor, draft.currency)}"
    )
    if index in draft.left_out:
        text += "\nNot on this bill"
    return text, walk_keyboard(draft)


def _render_fix_list(draft: Draft, trip: Trip) -> tuple[str, InlineKeyboardMarkup]:
    text = f"{_header(draft, trip)}\n\nWhich item do you want to change?"
    return text, fix_list_keyboard(draft)


def _items_preview_lines(draft: Draft, trip: Trip) -> list[str]:
    """Per person `• Ada ¥1,430 (S$11.58)` then `items ¥1,300 + tax ¥130` (sprint 6
    pin, sprint 8 layout). Service charge shows as its own `+ service` term, and
    the `Unlisted / rounding` line as `+ unlisted` (or `− unlisted`), apart from
    the kind it is stored as (sprint 13). Items left out are not in it (sprint 14).
    """
    draft = billed(draft)
    split = draft_items_split(draft, trip)
    shares = draft_shares(draft, trip)
    ccy = draft.currency
    by_kind: dict[str, dict[int, int]] = {}
    unlisted: dict[int, int] | None = None
    for index, allocation in split.line_allocations.items():
        if draft.line_items[index].description == UNLISTED_DESCRIPTION:
            bucket = unlisted = unlisted if unlisted is not None else {}
        else:
            bucket = by_kind.setdefault(draft.line_items[index].kind, {})
        for uid, part in allocation.items():
            bucket[uid] = bucket.get(uid, 0) + part
    lines = []
    for pid, (_share_trip, share_home) in shares.items():
        name = escape(person_for(draft, pid).display_name)
        terms = f"items {fmt_signed_money(split.subtotals.get(pid, 0), ccy)}"
        for kind, label in (("tax", "tax"), ("service_charge", "service")):
            if kind in by_kind:
                terms += f" + {label} {fmt_money(by_kind[kind].get(pid, 0), ccy)}"
        if "discount" in by_kind:
            discount = -by_kind["discount"].get(pid, 0)
            terms += f" − discount {fmt_money(discount, ccy)}"
        if unlisted is not None:
            part = unlisted.get(pid, 0)
            sign = "−" if part < 0 else "+"
            terms += f" {sign} unlisted {fmt_money(abs(part), ccy)}"
        total = fmt_signed_money(split.shares[pid], ccy)
        if ccy != trip.home_currency:
            total += f" ({fmt_money(share_home, trip.home_currency)})"
        lines.append(f"• {name} {total}\n   {terms}")
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
        f"• {escape(person_for(draft, pid).display_name)} "
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


def _render_preview(
    draft: Draft, trip: Trip, *, save_error: bool = False
) -> tuple[str, InlineKeyboardMarkup]:
    """ "Check before saving": what, how much, who paid, then each person's share
    as a bullet (sprint 8). Both currencies, trip first, except for an expense
    entered in the home currency, which shows the home currency only (spec §12).
    """
    head = f"<b>Check before saving</b>\n{_header(draft, trip)}\n\nEach person's share:"
    if isinstance(draft.split, Items):
        lines = _items_preview_lines(draft, trip)
    else:
        lines = []
        for pid, (share_trip, share_home) in draft_shares(draft, trip).items():
            name = escape(person_for(draft, pid).display_name)
            share = format_expense_amount(trip, draft.currency, share_trip, share_home)
            lines.append(f"• {name} {share}")
    text = head + "\n" + "\n".join(lines)
    if isinstance(draft.split, Items):
        text += _adjustments_line(billed(draft)) + _left_out_line(draft)
    if save_error:
        text += "\n\n" + SAVE_ERROR_LINE
    keyboard = preview_keyboard(
        draft.draft_id, retry=save_error, by_items=isinstance(draft.split, Items)
    )
    return text, keyboard


_LEFT_OUT_SHOWN = 3


def _left_out_line(draft: Draft) -> str:
    """`Not on this bill: Pad thai, Iced tea, Mango rice, …` (sprint 14). Empty when
    nothing was left out.
    """
    names = [
        escape(draft.line_items[i].description)
        for i in claimable_indices(draft)
        if i in draft.left_out
    ]
    if not names:
        return ""
    shown = ", ".join(names[:_LEFT_OUT_SHOWN]) + (", …" if len(names) > _LEFT_OUT_SHOWN else "")
    return f"\nNot on this bill: {shown}"


def _adjustments_line(draft: Draft) -> str:
    """The adjustment lines, under the shares: `Shared in proportion: VAT ฿80.25 ·
    Unlisted / rounding ฿0.29` (sprint 13). Empty when there are none.
    """
    parts = []
    for item in draft.line_items:
        if item.kind == "item":
            continue
        sign = "−" if item.kind == "discount" else ""
        parts.append(
            f"{escape(item.description)} {sign}{fmt_money(abs(item.amount_minor), draft.currency)}"
        )
    return "\n\nShared in proportion: " + " · ".join(parts) if parts else ""


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
    """`Saved ✅ Ramen ¥1,200 (S$9.72) · Ada paid · split 2 ways`
    (sprint 8).
    """
    if isinstance(draft.split, Items):
        how = "split by items"
    elif isinstance(draft.split, Custom):
        how = "split by amounts"
    elif len(draft.participant_ids) == 1:
        only = person_for(draft, draft.participant_ids[0]).first_name
        how = f"just {escape(only)}"
    else:
        how = f"split {len(draft.participant_ids)} ways"
    payer = escape(person_for(draft, draft.payer_id).first_name)
    return f"Saved ✅ {escape(draft.description)} {_amount(draft, trip)} · {payer} paid · {how}"
