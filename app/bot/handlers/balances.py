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
from app.bot.formatting import fmt_expense_line, fmt_money
from app.bot.handlers.join import require_trip
from app.models.expense import ExpenseRecord, SplitRecord
from app.models.trip import Trip
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable
from app.services.settlements import Balance, compute_balances, settle

logger = logging.getLogger(__name__)

NO_BALANCES_TEXT = "No expenses yet."
ALL_SQUARE_TEXT = "All square 🎉"
SETTLE_HINT = "Settle with /settle"


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


def _signed(amount_minor: int, currency: str) -> str:
    if amount_minor > 0:
        return f"+{fmt_money(amount_minor, currency)}"
    if amount_minor < 0:
        return f"−{fmt_money(-amount_minor, currency)}"
    return fmt_money(0, currency)


def _balance_line(name: str, balance: Balance, currency: str) -> str:
    return (
        f"{escape(name)}: paid {fmt_money(balance.paid_minor, currency)} · "
        f"share {fmt_money(balance.share_minor, currency)} · "
        f"<b>{_signed(balance.net_minor, currency)}</b>"
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
    """Paid / Share / Net per user with any activity, net descending (sprint 5 pin)."""
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
    lines = [f"<b>Balances</b> ({trip.home_currency})"]
    lines += [_balance_line(ledger.name(b.user_id), b, trip.home_currency) for b in ordered]
    lines.append(SETTLE_HINT)
    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def mine_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The caller's paid, share and net, then their last 3 expenses (paid by them
    or shared with them), each with their own share.
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
    lines = [
        f"<b>{escape(ledger.name(user.id))}</b>",
        f"Paid {fmt_money(balance.paid_minor, home)} · Share {fmt_money(balance.share_minor, home)}"
        f" · Net <b>{_signed(balance.net_minor, home)}</b>",
    ]
    if mine:
        lines.append("")
        lines.append("Last 3:")
        for expense in mine:
            share = my_shares.get(expense.expense_id, 0)
            lines.append(f"{fmt_expense_line(expense, trip)} · you {fmt_money(share, home)}")
    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def settle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """One line per transfer, `Amir → Sufyan S$31.20`, or "All square 🎉"."""
    loaded = await _loaded(update, context, "settle")
    if loaded is None:
        return
    trip, ledger = loaded
    message = update.effective_message
    assert message is not None
    balances = compute_balances(ledger.expenses, ledger.splits)
    transfers = settle({uid: b.net_minor for uid, b in balances.items()})
    if not transfers:
        await message.reply_text(ALL_SQUARE_TEXT, parse_mode="HTML")
        return
    lines = [
        f"{escape(ledger.name(t.from_user_id))} → {escape(ledger.name(t.to_user_id))} "
        f"{fmt_money(t.amount_minor, trip.home_currency)}"
        for t in transfers
    ]
    await message.reply_text("\n".join(lines), parse_mode="HTML")
