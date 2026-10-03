"""`/balances`, `/mine`, `/settle`. Spec references: pinned-decisions §12, ADR 0001,
sprint 5 pins.

All three are in the home currency only, from confirmed expenses in this chat. Each
command reads Expenses, Splits and Users once; the arithmetic is
`app.services.settlements`, which is pure.
"""

from __future__ import annotations

import logging
from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.deps import get_deps
from app.bot.formatting import fmt_expense_bullet, fmt_money
from app.bot.handlers.join import require_trip
from app.models.expense import ExpenseRecord, SplitRecord
from app.models.trip import Trip
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable
from app.services.settlements import Balance, compute_balances, settle, unbalanced_by

logger = logging.getLogger(__name__)

NO_BALANCES_TEXT = "No expenses yet."
ALL_SQUARE_TEXT = "All square 🎉"
UNBALANCED_TEXT = (
    "⚠️ The Sheet's shares don't add up to its expenses (off by {amount}). "
    "Was a Splits or Expenses row edited by hand? Fix it, or /undo and re-send the expense."
)
SETTLE_HINT = "Settle up with /settle"


class _Ledger:
    """One chat's confirmed expenses, their splits, and a name for every user ID."""

    def __init__(
        self, expenses: list[ExpenseRecord], splits: list[SplitRecord], names: dict[int, str]
    ) -> None:
        self.expenses = expenses
        self.splits = splits
        self.names = names

    def name(self, user_id: int) -> str:
        return self.names.get(user_id, f"User {user_id}")


async def _load(context: ContextTypes.DEFAULT_TYPE, trip: Trip, chat_id: int) -> _Ledger:
    """Raises `LedgerUnavailable` straight through, like the other read commands."""
    deps = get_deps(context)
    expenses = [
        e
        for e in await deps.ledger.list_expenses(trip)
        if e.chat_id == chat_id and e.status == "confirmed"
    ]
    ids = {e.expense_id for e in expenses}
    splits = [s for s in await deps.ledger.list_splits(trip) if s.expense_id in ids]
    # Names: the Splits and Expenses rows first, then the Users tab, which is the
    # freshest (a renamed user shows their current name).
    names: dict[int, str] = {}
    for split in splits:
        names[split.user_id] = split.display_name
    for expense in expenses:
        names[expense.payer_user_id] = expense.payer_display_name
    for user in await deps.ledger.list_users(trip):
        names[user.user_id] = user.display_name
    return _Ledger(expenses, splits, names)


def _standing(name: str, balance: Balance, currency: str) -> str:
    """ "Ada is owed S$31.20" / "Amir owes S$31.20" / "Cy is all square"."""
    net = balance.net_minor
    if net > 0:
        return f"{escape(name)} is owed <b>{fmt_money(net, currency)}</b>"
    if net < 0:
        return f"{escape(name)} owes <b>{fmt_money(-net, currency)}</b>"
    return f"{escape(name)} is all square"


def _balance_line(name: str, balance: Balance, currency: str) -> str:
    return (
        f"• {_standing(name, balance, currency)}\n"
        f"   (paid {fmt_money(balance.paid_minor, currency)}, "
        f"share {fmt_money(balance.share_minor, currency)})"
    )


async def _loaded(update: Update, context: ContextTypes.DEFAULT_TYPE, command: str):
    trip = await require_trip(update, context)
    if trip is None:
        return None
    chat = update.effective_chat
    message = update.effective_message
    assert chat is not None and message is not None
    try:
        ledger = await _load(context, trip, chat.id)
    except LedgerUnavailable:
        logger.exception("%s: could not read the ledger", command)
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return None
    return trip, ledger


async def balances_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Who is owed and who owes, per user with any activity, most owed first, each
    with paid and share underneath (sprint 8 wording).
    """
    loaded = await _loaded(update, context, "balances")
    if loaded is None:
        return
    trip, ledger = loaded
    message = update.effective_message
    assert message is not None
    balances = compute_balances(ledger.expenses, ledger.splits)
    if not balances:
        await message.reply_text(NO_BALANCES_TEXT, parse_mode="HTML")
        return
    ordered = sorted(
        balances.values(), key=lambda b: (-b.net_minor, ledger.name(b.user_id).casefold())
    )
    lines = [f"<b>Who owes what</b> ({trip.home_currency})"]
    lines += [_balance_line(ledger.name(b.user_id), b, trip.home_currency) for b in ordered]
    off_by = unbalanced_by(balances)
    if off_by:
        lines.append(UNBALANCED_TEXT.format(amount=fmt_money(abs(off_by), trip.home_currency)))
    lines.append(SETTLE_HINT)
    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def mine_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The caller's paid, share and bottom line in plain sentences (sprint 8), then
    their last 3 expenses (paid by them or shared with them) with their own share.
    """
    loaded = await _loaded(update, context, "mine")
    if loaded is None:
        return
    trip, ledger = loaded
    message = update.effective_message
    user = update.effective_user
    assert message is not None and user is not None
    home = trip.home_currency
    balance = compute_balances(ledger.expenses, ledger.splits).get(user.id, Balance(user.id, 0, 0))
    my_shares = {s.expense_id: s.share_home_minor for s in ledger.splits if s.user_id == user.id}
    mine = sorted(
        (e for e in ledger.expenses if e.payer_user_id == user.id or e.expense_id in my_shares),
        key=lambda e: e.created_at,
    )[-3:]
    net = balance.net_minor
    if net > 0:
        bottom = f"➡️ You're owed <b>{fmt_money(net, home)}</b> overall."
    elif net < 0:
        bottom = f"➡️ You owe <b>{fmt_money(-net, home)}</b> overall."
    else:
        bottom = "➡️ You're all square."
    lines = [
        f"<b>{escape(ledger.name(user.id))}</b>",
        f"You paid {fmt_money(balance.paid_minor, home)} for the group.",
        f"Your share of everything is {fmt_money(balance.share_minor, home)}.",
        bottom,
    ]
    if mine:
        lines += ["", "Recent:"]
        for expense in mine:
            if expense.expense_id in my_shares:
                detail = f"your share {fmt_money(my_shares[expense.expense_id], home)}"
            else:
                detail = "you're not in this one"
            lines.append(fmt_expense_bullet(expense, trip, detail))
    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def settle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ "To settle up:" then one bullet per transfer, `Amir pays Ada S$31.20`
    (sprint 8), or "All square 🎉".
    """
    loaded = await _loaded(update, context, "settle")
    if loaded is None:
        return
    trip, ledger = loaded
    message = update.effective_message
    assert message is not None
    balances = compute_balances(ledger.expenses, ledger.splits)
    off_by = unbalanced_by(balances)
    if off_by:
        logger.warning("settle: nets off by %s minor units", off_by)
        await message.reply_text(
            UNBALANCED_TEXT.format(amount=fmt_money(abs(off_by), trip.home_currency)),
            parse_mode="HTML",
        )
        return
    transfers = settle({uid: b.net_minor for uid, b in balances.items()})
    if not transfers:
        await message.reply_text(ALL_SQUARE_TEXT, parse_mode="HTML")
        return
    lines = ["<b>To settle up:</b>"] + [
        f"• {escape(ledger.name(t.from_user_id))} pays {escape(ledger.name(t.to_user_id))} "
        f"<b>{fmt_money(t.amount_minor, trip.home_currency)}</b>"
        for t in transfers
    ]
    await message.reply_text("\n".join(lines), parse_mode="HTML")
