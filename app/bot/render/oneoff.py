"""Pure rendering of a one-off bill into card text and keyboard, and into the summary the
user forwards. Bot-layer only (builds telegram objects). Spec §22.

Names and descriptions come from the user and are HTML-escaped. On cards the user is
`<first name> (you)`; on the summary, which other people read, the first name alone.
"""

from __future__ import annotations

from html import escape

from telegram import InlineKeyboardMarkup

from app.bot.formatting import fmt_money
from app.bot.keyboards.oneoff import (
    amounts_keyboard,
    count_keyboard,
    method_keyboard,
    names_keyboard,
    payer_keyboard,
    preview_keyboard,
)
from app.services.oneoff import (
    MAX_NAME_CHARS,
    AmountError,
    OneOffBill,
    bill_currency_symbol,
    is_balanced,
    remaining,
)

START_PROMPT_TEXT = "Send the bill, like <code>84.50 dinner</code>."
NO_AMOUNT_TEXT = "I couldn't find an amount. Send it like <code>84.50 dinner</code>."
PHOTO_TEXT = "Photos aren't read here yet. Type the amount, like <code>84.50 dinner</code>."
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

_NOT_SGD_HINT = "<i>Not SGD? Send it again with the currency, like 1200 yen ramen.</i>"
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
    if bill.state == "count":
        return _render_count(bill)
    if bill.state == "names":
        return _render_names(bill)
    if bill.state == "payer":
        return f"{_title(bill)}\n\nWho paid?", payer_keyboard(bill)
    if bill.state == "method":
        return f"{_title(bill)}\n\nHow should it be split?", method_keyboard(bill.bill_id)
    if bill.state == "amounts":
        return _render_amounts(bill)
    if bill.state == "preview":
        return _render_preview(bill)
    raise ValueError(f"no card for bill state {bill.state}")


def _render_count(bill: OneOffBill) -> tuple[str, InlineKeyboardMarkup]:
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


def _render_preview(bill: OneOffBill) -> tuple[str, InlineKeyboardMarkup]:
    assert bill.payer is not None
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
    how = f"split {bill.count} ways" if bill.method == "equal" else "split by amounts"
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
