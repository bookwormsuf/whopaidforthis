"""`/balances`, `/mine`, `/settle`. Spec references: pinned-decisions §12, ADR 0001,
sprint 5 pins.

All three are in the home currency only, from confirmed expenses in this chat. Each
command reads Expenses, Splits and Users once; the arithmetic is
`app.services.settlements`, which is pure.
"""

from __future__ import annotations

import logging
from html import escape

from telegram import Chat, Update, User
from telegram.ext import ContextTypes

from app.bot.deps import Deps, get_deps
from app.bot.formatting import fmt_expense_bullet, fmt_money
from app.bot.handlers.join import require_trip_send
from app.bot.keyboards.settrip import close_trip_keyboard
from app.bot.send import SendFn, build_send
from app.models.expense import ExpenseRecord, SplitRecord
from app.models.trip import Trip
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable
from app.services.settlements import Balance, compute_balances, settle, unbalanced_by

logger = logging.getLogger(__name__)

NO_BALANCES_TEXT = "No expenses yet."
ALL_SQUARE_TEXT = "All square 🎉"
UNBALANCED_TEXT = (
    "⚠️ The recorded shares don't add up to the expenses (off by {amount}). "
    "Was a row edited by hand? Fix it, or /undo and re-send the expense."
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


async def _load(deps: Deps, trip: Trip) -> _Ledger:
    """Raises `LedgerUnavailable` straight through, like the other read commands.

    No `chat_id` filter: a trip belongs to exactly one chat, so the store's
    `confirmed_only` already scopes this correctly (sprint 9 review item 8).
    """
    expenses = await deps.ledger.list_expenses(trip, confirmed_only=True)
    splits = await deps.ledger.list_splits(trip, confirmed_only=True)
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


async def _loaded(deps: Deps, chat: Chat, send: SendFn, command: str):
    trip = await require_trip_send(deps, chat.id, send)
    if trip is None:
        return None
    try:
        ledger = await _load(deps, trip)
    except LedgerUnavailable:
        logger.exception("%s: could not read the ledger", command)
        await send(LEDGER_UNAVAILABLE_TEXT)
        return None
    return trip, ledger


async def run_balances(deps: Deps, chat: Chat, user: User, send: SendFn) -> None:
    """Who is owed and who owes, per user with any activity, most owed first, each
    with paid and share underneath (sprint 8 wording).
    """
    loaded = await _loaded(deps, chat, send, "balances")
    if loaded is None:
        return
    trip, ledger = loaded
    balances = compute_balances(ledger.expenses, ledger.splits)
    if not balances:
        await send(NO_BALANCES_TEXT)
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
    await send("\n".join(lines))


async def balances_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, message = update.effective_chat, update.effective_message
    assert chat is not None and message is not None
    await run_balances(get_deps(context), chat, update.effective_user, build_send(message))


async def run_mine(deps: Deps, chat: Chat, user: User, send: SendFn) -> None:
    """The caller's paid, share and bottom line in plain sentences (sprint 8), then
    their last 3 expenses (paid by them or shared with them) with their own share.
    """
    loaded = await _loaded(deps, chat, send, "mine")
    if loaded is None:
        return
    trip, ledger = loaded
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
    await send("\n".join(lines))


async def mine_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    assert chat is not None and user is not None and message is not None
    await run_mine(get_deps(context), chat, user, build_send(message))


async def run_settle(deps: Deps, chat: Chat, user: User, send: SendFn) -> None:
    """ "To settle up:" then one bullet per transfer, `Amir pays Ada S$31.20`
    (sprint 8), or "All square 🎉". Ends with a `Close this trip` button (sprint 9
    item 2), usable by the trip starter or whoever ran /settle.
    """
    loaded = await _loaded(deps, chat, send, "settle")
    if loaded is None:
        return
    trip, ledger = loaded
    assert trip.id is not None
    close_markup = close_trip_keyboard(trip.id, user.id)
    balances = compute_balances(ledger.expenses, ledger.splits)
    off_by = unbalanced_by(balances)
    if off_by:
        logger.warning("settle: nets off by %s minor units", off_by)
        await send(UNBALANCED_TEXT.format(amount=fmt_money(abs(off_by), trip.home_currency)))
        return
    transfers = settle({uid: b.net_minor for uid, b in balances.items()})
    if not transfers:
        await send(ALL_SQUARE_TEXT, reply_markup=close_markup)
        return
    lines = ["<b>To settle up:</b>"] + [
        f"• {escape(ledger.name(t.from_user_id))} pays {escape(ledger.name(t.to_user_id))} "
        f"<b>{fmt_money(t.amount_minor, trip.home_currency)}</b>"
        for t in transfers
    ]
    await send("\n".join(lines), reply_markup=close_markup)


async def settle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    assert chat is not None and user is not None and message is not None
    await run_settle(get_deps(context), chat, user, build_send(message))


CLOSE_TRIP_NOT_ALLOWED_TOAST = "Only the person who started the trip or ran /settle can close it"
TRIP_CLOSED_SUFFIX = "\n\nTrip closed. Run /settrip to start a new one."


async def close_trip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`closetrip:{trip_id}:{settle_presser_id}` from the `Close this trip` button
    on /settle's reply (sprint 9 item 2). Allowed for the trip's `created_by` or
    the user who ran /settle; anyone else gets a toast.
    """
    query = update.callback_query
    assert query is not None
    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "closetrip":
        await query.answer()
        return
    try:
        trip_id, settle_presser_id = int(parts[1]), int(parts[2])
    except ValueError:
        await query.answer()
        return

    deps = get_deps(context)
    presser = query.from_user
    chat = update.effective_chat
    if presser is None or chat is None:
        await query.answer()
        return

    try:
        trip = await deps.ledger.get_trip(chat.id, trip_id)
    except LedgerUnavailable:
        logger.exception("close_trip_callback: could not read the trip")
        await query.answer(LEDGER_UNAVAILABLE_TEXT)
        return
    if trip is None:
        await query.answer()
        return

    allowed = {settle_presser_id}
    if trip.created_by is not None:
        allowed.add(trip.created_by)
    if presser.id not in allowed:
        await query.answer(CLOSE_TRIP_NOT_ALLOWED_TOAST)
        return

    if trip.closed_at is not None:
        await query.answer()
        return

    try:
        await deps.ledger.close_trip(trip)
    except LedgerUnavailable:
        logger.exception("close_trip_callback: could not close the trip")
        await query.answer(LEDGER_UNAVAILABLE_TEXT)
        return

    await query.answer()
    message = query.message
    current_text = message.text_html if message is not None else None
    if current_text is not None:
        try:
            await query.edit_message_text(
                current_text + TRIP_CLOSED_SUFFIX, parse_mode="HTML", reply_markup=None
            )
        except Exception:  # noqa: BLE001 - editing an already-changed message is not fatal
            logger.debug("close_trip_callback: could not edit message")
