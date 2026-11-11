"""Pure rendering of a one-off bill into card text and keyboard, and into the summary the
user forwards. Bot-layer only (builds telegram objects). Spec §22.

Names and descriptions come from the user and are HTML-escaped. On cards the user is
`<first name> (you)`; on the summary, which other people read, the first name alone.
"""

from __future__ import annotations

from html import escape

from telegram import InlineKeyboardMarkup

from app.bot.formatting import fmt_money, fmt_signed_money
from app.bot.keyboards.oneoff import (
    amounts_keyboard,
    choose_total_keyboard,
    couldnt_read_keyboard,
    count_keyboard,
    fix_list_keyboard,
    method_keyboard,
    names_keyboard,
    payer_keyboard,
    preview_keyboard,
    reconcile_keyboard,
    walk_keyboard,
)
from app.services.oneoff import (
    MAX_NAME_CHARS,
    RECEIPT_DESCRIPTION,
    UNLISTED_DESCRIPTION,
    AmountError,
    ItemsSplit,
    OneOffBill,
    bill_currency_symbol,
    by_items_available,
    claimable_indices,
    is_balanced,
    item_breakdown,
    reconciliation_sums,
    remaining,
    too_many_items,
)

START_PROMPT_TEXT = "Send the bill, like <code>84.50 dinner</code>, or a photo of the receipt."
NO_AMOUNT_TEXT = "I couldn't find an amount. Send it like <code>84.50 dinner</code>."
DROP_QUESTION_TEXT = "Drop the bill you started?"
KEEP_TEXT = "OK. Carry on with the bill above."
CANCELLED_TEXT = "Cancelled."
REPLACED_TEXT = "Replaced by the bill below."
STALE_TEXT = "That bill expired. Tap Split a bill now to start again."
BAD_COUNT_TEXT = "Type a number from 2 to 20."
NAME_TOO_LONG_TEXT = f"Keep each name under {MAX_NAME_CHARS} characters."
DUPLICATE_NAME_TEXT = "Two people can't share a name. Change one."
FORWARD_HINT_TEXT = "Forward the message above to the people on the bill. I haven't saved it."
SPLIT_IN_GROUP_TEXT = (
    "/split works in a private chat with me. In this group, type the expense, "
    "like <code>1200 yen ramen</code>."
)

NOTICE_TEXT = "I read receipts with an AI model. The photo isn't stored."
READING_TEXT = "Reading the receipt…"
ONE_AT_A_TIME_TEXT = "One receipt at a time. Still reading the first."
SEND_AMOUNT_TEXT = "Send the amount."
BAD_AMOUNT_TEXT = "Send just the amount, like 84.50."
ALREADY_READ_TEXT = (
    "You can read 1 receipt photo a day here. Type the bill instead, like "
    "<code>84.50 dinner</code>. For more, add me to a group for a trip."
)
OUT_OF_TRIES_TEXT = "That's 3 tries today. Type the bill instead, like <code>84.50 dinner</code>."
GLOBAL_CAP_TEXT = (
    "Receipt photos are used up for today across the whole bot. Type the bill instead, "
    "like <code>84.50 dinner</code>."
)

_NOT_SGD_HINT = "<i>Not SGD? Send it again with the currency, like 1200 yen ramen.</i>"
_RECEIPT_HINT = "<i>Wrong amount or currency? Type the bill instead, like 1200 yen ramen.</i>"
_MINUS = "−"
_ECHO_CHARS = 30
_MAX_WARNINGS = 5


def _e(text: str) -> str:
    return escape(text, quote=False)


def too_many_names_text(others: int) -> str:
    there = "There is 1 other" if others == 1 else f"There are {others} others"
    return f"That's more names than people. {there} on this bill."


def _title(bill: OneOffBill) -> str:
    return f"🧾 <b>{_e(bill.description)}</b> · {fmt_money(bill.amount_minor, bill.currency)}"


def _card_name(bill: OneOffBill, person_id: int) -> str:
    name = _e(bill.name_of(person_id))
    return f"{name} (you)" if person_id == 1 else name


def _money(bill: OneOffBill, minor: int) -> str:
    return fmt_money(minor, bill.currency)


def render_card(bill: OneOffBill) -> tuple[str, InlineKeyboardMarkup]:
    """The card for the bill's current step (steps 2 to 8). The prompt before the bill
    line, the summary and the one-line outcomes are not cards.
    """
    if bill.state == "awaiting_bill":
        return START_PROMPT_TEXT, None
    if bill.state == "reading":
        return READING_TEXT, None
    if bill.state == "couldnt_read":
        return _render_couldnt_read(bill)
    if bill.state == "choose_total":
        text = f"{_pre_total_title(bill)}\n\nWhich is the final total?"
        return text, choose_total_keyboard(bill.bill_id, bill.candidates, bill.currency)
    if bill.state == "other_amount":
        return f"{_pre_total_title(bill)}\n\n{SEND_AMOUNT_TEXT}", None
    if bill.state == "reconcile":
        return _render_reconcile(bill)
    if bill.state == "count":
        return _render_count(bill)
    if bill.state == "names":
        return _render_names(bill)
    if bill.state == "payer":
        return f"{_title(bill)}\n\nWho paid?", payer_keyboard(bill)
    if bill.state == "method":
        return _render_method(bill)
    if bill.state == "amounts":
        return _render_amounts(bill)
    if bill.state == "item_walk":
        return _render_walk(bill)
    if bill.state == "fix_list":
        text = f"{_header(bill)}\n\nWhich item do you want to change?"
        return text, fix_list_keyboard(bill)
    if bill.state == "preview":
        return _render_preview(bill)
    raise ValueError(f"no card for bill state {bill.state}")


def _pre_total_title(bill: OneOffBill) -> str:
    """The title line before a total is known: no amount yet, as on the group's cards."""
    return f"🧾 <b>{_e(bill.description)}</b>"


def _header(bill: OneOffBill) -> str:
    """The title line, then who paid; shared by the item-by-item cards."""
    assert bill.payer is not None
    return f"{_title(bill)}\n{_card_name(bill, bill.payer)} paid"


def _render_couldnt_read(bill: OneOffBill) -> tuple[str, InlineKeyboardMarkup]:
    head = f"🧾 <b>{RECEIPT_DESCRIPTION}</b>\n\n"
    if bill.read_problem:
        # The reader service failed, not the photo: say so, so nobody retakes it.
        text = (
            f"{head}Couldn't read that receipt: the receipt reader is "
            f"{_e(bill.read_problem)}, not a problem with your photo. Try again in a "
            "minute, or type the bill."
        )
    else:
        text = f"{head}Couldn't read that receipt"
    return text, couldnt_read_keyboard(bill.bill_id)


def _render_reconcile(bill: OneOffBill) -> tuple[str, InlineKeyboardMarkup]:
    items_sum, adjustments_sum = reconciliation_sums(bill)
    computed = items_sum + adjustments_sum
    difference = abs(bill.amount_minor - computed)
    total_str = _money(bill, bill.amount_minor)
    text = (
        f"{_pre_total_title(bill)}\n"
        f"Items {_money(bill, items_sum)} + adjustments {_money(bill, adjustments_sum)} "
        f"= {_money(bill, computed)}\n"
        f"Receipt total {total_str} (difference {_money(bill, difference)})\n"
        f"({_money(bill, difference)} isn't on any line; it'll be shared like tax)"
    )
    return text, reconcile_keyboard(bill.bill_id, total_str)


def _render_method(bill: OneOffBill) -> tuple[str, InlineKeyboardMarkup]:
    by_items = by_items_available(bill)
    question = "Who had what?" if by_items else "How should it be split?"
    if too_many_items(bill):
        question += "\nToo many items to split individually"
    return f"{_title(bill)}\n\n{question}", method_keyboard(bill.bill_id, by_items)


def _render_walk(bill: OneOffBill) -> tuple[str, InlineKeyboardMarkup]:
    """ "Who had this? (n of N)", then the item and its amount."""
    split = bill.split
    assert isinstance(split, ItemsSplit)
    positions = claimable_indices(bill)
    item = bill.line_items[positions[split.walk_index]]
    text = (
        f"{_header(bill)}\n\n"
        f"Who had this?  ({split.walk_index + 1} of {len(positions)})\n"
        f"<b>{_e(item.description)}</b> · {_money(bill, item.amount_minor)}"
    )
    return text, walk_keyboard(bill)


def _render_count(bill: OneOffBill) -> tuple[str, InlineKeyboardMarkup]:
    if bill.from_receipt:
        # A receipt: how many items it has, and the hint always (its amount or
        # currency may be a misread, whatever the currency).
        # The group card's line: every `item` line, "1 item"; it has none for a receipt
        # with no lines at all.
        count = len([i for i in bill.line_items if i.kind == "item"])
        items_line = (
            f"\n{count} item{'s' if count != 1 else ''} on the receipt" if bill.line_items else ""
        )
        text = f"{_title(bill)}{items_line}\n\nHow many people, including you?\n{_RECEIPT_HINT}"
        return text, count_keyboard(bill.bill_id)
    text = f"{_title(bill)}\n\nHow many people, including you?"
    if bill.currency == "SGD":
        text += f"\n{_NOT_SGD_HINT}"
    return text, count_keyboard(bill.bill_id)


def _render_names(bill: OneOffBill) -> tuple[str, InlineKeyboardMarkup]:
    people = ", ".join(_card_name(bill, person_id) for person_id in bill.person_ids)
    text = (
        f"{_title(bill)}\n\n{people}\n\n"
        "Send names to rename them, like <code>Ali, Mei</code>, or tap Next."
    )
    return text, names_keyboard(bill.bill_id)


def _clip(text: str) -> str:
    """User text echoed in a warning, cut so a long paste cannot push the card past
    Telegram's 4096 characters.
    """
    return text if len(text) <= _ECHO_CHARS else text[:_ECHO_CHARS] + "…"


def _amount_error_text(bill: OneOffBill, error: AmountError) -> str:
    if error.kind == "unreadable":
        return f"Couldn't read '{_e(_clip(error.text))}'"
    if error.kind == "unknown_person":
        return f"Who is '{_e(_clip(error.text))}'?"
    return f"Use {_e(bill_currency_symbol(bill))} amounts for this bill"


def _render_amounts(bill: OneOffBill) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"• {_card_name(bill, person_id)} "
        + (_money(bill, bill.amounts[person_id]) if person_id in bill.amounts else "–")
        for person_id in bill.person_ids
    ]
    left = remaining(bill)
    if left == 0:
        remaining_line = f"Remaining: {_money(bill, 0)} ✅"
    else:
        sign = _MINUS if left < 0 else ""
        remaining_line = f"Remaining: {sign}{_money(bill, abs(left))} ❌"
    parts = [
        _title(bill),
        "",
        f"Send one line per person, e.g. <code>{_e(bill.name_of(2))} 20</code>",
        *lines,
        f"Total {_money(bill, sum(bill.amounts.values()))} of {_money(bill, bill.amount_minor)}",
        remaining_line,
    ]
    if bill.amount_errors:
        shown = [_amount_error_text(bill, e) for e in bill.amount_errors[:_MAX_WARNINGS]]
        if len(bill.amount_errors) > _MAX_WARNINGS:
            shown.append(f"…and {len(bill.amount_errors) - _MAX_WARNINGS} more")
        parts.append("⚠️ " + "; ".join(shown))
    return "\n".join(parts), amounts_keyboard(bill.bill_id, is_balanced(bill))


def _adjustments_line(bill: OneOffBill) -> str:
    """The adjustment lines under the shares: `Shared in proportion: VAT S$1.00 ·
    Unlisted / rounding S$0.29`. Empty when there are none.
    """
    parts = []
    for item in bill.line_items:
        if item.kind == "item":
            continue
        sign = "−" if item.kind == "discount" else ""
        parts.append(f"{_e(item.description)} {sign}{_money(bill, abs(item.amount_minor))}")
    return "\n\nShared in proportion: " + " · ".join(parts) if parts else ""


def _items_preview_lines(bill: OneOffBill) -> list[str]:
    """Per person `• Ada S$14.30` then `items S$13.00 + tax S$1.30`: tax, service,
    discount and the `Unlisted / rounding` part (`+ unlisted` or `− unlisted`) are
    left out when that person's part is zero. `items` stays, so the line is never empty.
    """
    breakdown = item_breakdown(bill)
    by_kind: dict[str, dict[int, int]] = {}
    unlisted: dict[int, int] = {}
    for index, allocation in breakdown.line_allocations.items():
        item = bill.line_items[index]
        bucket = unlisted if item.description == UNLISTED_DESCRIPTION else None
        if bucket is None:
            bucket = by_kind.setdefault(item.kind, {})
        for pid, part in allocation.items():
            bucket[pid] = bucket.get(pid, 0) + part
    lines = []
    for pid in bill.person_ids:
        subtotal = breakdown.subtotals.get(pid, 0)
        terms = []
        for kind, label in (("tax", "tax"), ("service_charge", "service")):
            part = by_kind.get(kind, {}).get(pid, 0)
            if part:
                terms.append(f"+ {label} {_money(bill, part)}")
        discount = -by_kind.get("discount", {}).get(pid, 0)
        if discount:
            terms.append(f"− discount {_money(bill, discount)}")
        part = unlisted.get(pid, 0)
        if part:
            terms.append(f"{'−' if part < 0 else '+'} unlisted {_money(bill, abs(part))}")
        items = (
            f"items {fmt_signed_money(subtotal, bill.currency)}" if subtotal or not terms else ""
        )
        line = " ".join(filter(None, [items, *terms]))
        if line.startswith("+ "):
            line = line[2:]  # no items part: the first term needs no leading plus
        total = fmt_signed_money(breakdown.shares[pid], bill.currency)
        lines.append(f"• {_card_name(bill, pid)} {total}\n   {line}")
    return lines


def _render_preview(bill: OneOffBill) -> tuple[str, InlineKeyboardMarkup]:
    assert bill.payer is not None
    if isinstance(bill.split, ItemsSplit):
        text = "\n".join(
            [
                _header(bill),
                "",
                "Check before sending",
                "Each person's share:",
                *_items_preview_lines(bill),
            ]
        ) + _adjustments_line(bill)
        return text, preview_keyboard(bill.bill_id, by_items=True)
    lines = [
        f"• {_card_name(bill, person_id)} {_money(bill, bill.shares[person_id])}"
        for person_id in bill.person_ids
    ]
    text = "\n".join(
        [
            _title(bill),
            f"{_card_name(bill, bill.payer)} paid",
            "",
            "Check before sending",
            *lines,
        ]
    )
    return text, preview_keyboard(bill.bill_id)


def render_summary(bill: OneOffBill, bot_username: str) -> str:
    """The message the user forwards. Names the user by first name only; lists who owes
    the payer what (people other than the payer whose share is above zero).
    """
    assert bill.payer is not None
    payer = _e(bill.name_of(bill.payer))
    how = {
        "equal": f"split {bill.count} ways",
        "custom": "split by amounts",
        "items": "split by items",
    }[bill.method or "equal"]
    owing = [
        f"• {_e(bill.name_of(person_id))} owes {payer} {_money(bill, bill.shares[person_id])}"
        for person_id in bill.person_ids
        if person_id != bill.payer and bill.shares[person_id] > 0
    ]
    return "\n".join(
        [
            _title(bill),
            f"{payer} paid · {how}",
            "",
            *(owing or ["Nobody owes anything."]),
            "",
            f"Split with @{_e(bot_username)}",
        ]
    )
