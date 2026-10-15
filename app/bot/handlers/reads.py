"""Read commands: `/recent`, `/today`, `/summary`, `/undo`. Spec §12; sprint 2 pins.

Every reply filters by chat id and `confirmed` status, and fetches the `Expenses`
tab once per command (no background caching yet, per the sprint pin).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from html import escape
from zoneinfo import ZoneInfo

from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.ext import ContextTypes

from app.bot.deps import Deps, get_deps
from app.bot.formatting import fmt_expense_bullet, fmt_money, format_expense_amount
from app.bot.handlers.join import require_trip_send
from app.bot.send import SendFn, build_send
from app.models.expense import ExpenseRecord
from app.models.trip import Trip
from app.models.user import display_name_for
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable

logger = logging.getLogger(__name__)

NOTHING_TO_UNDO_TEXT = "Nothing to undo."
NO_RECENT_EXPENSES_TEXT = "No expenses yet."
NO_EXPENSES_TODAY_TEXT = "No expenses today."


async def _confirmed(deps: Deps, trip: Trip) -> list[ExpenseRecord]:
    """Fetches Expenses once, confirmed only. No `chat_id` filter: a trip
    belongs to exactly one chat, so the store's `confirmed_only` already scopes
    this correctly (sprint 9 review item 8). Raises `LedgerUnavailable` straight
    through; every caller wraps its own call in a try/except and replies
    `LEDGER_UNAVAILABLE_TEXT` on that.
    """
    return await deps.ledger.list_expenses(trip, confirmed_only=True)


async def run_recent(deps: Deps, chat: Chat, user: User, send: SendFn) -> None:
    trip = await require_trip_send(deps, chat.id, send)
    if trip is None:
        return
    try:
        expenses = await _confirmed(deps, trip)
    except LedgerUnavailable:
        logger.exception("recent: could not read Expenses")
        await send(LEDGER_UNAVAILABLE_TEXT)
        return

    expenses.sort(key=lambda e: e.created_at)
    last_five = expenses[-5:]
    if not last_five:
        await send(NO_RECENT_EXPENSES_TEXT)
        return
    text = "<b>Recent expenses</b>\n" + "\n".join(fmt_expense_bullet(e, trip) for e in last_five)
    await send(text)


async def recent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, message = update.effective_chat, update.effective_message
    assert chat is not None and message is not None
    await run_recent(get_deps(context), chat, update.effective_user, build_send(message))


async def run_today(deps: Deps, chat: Chat, user: User, send: SendFn) -> None:
    trip = await require_trip_send(deps, chat.id, send)
    if trip is None:
        return
    try:
        expenses = await _confirmed(deps, trip)
    except LedgerUnavailable:
        logger.exception("today: could not read Expenses")
        await send(LEDGER_UNAVAILABLE_TEXT)
        return

    today = datetime.now(UTC).astimezone(ZoneInfo(trip.timezone)).date()
    todays = sorted(
        (e for e in expenses if e.transaction_date == today), key=lambda e: e.created_at
    )
    if not todays:
        await send(NO_EXPENSES_TODAY_TEXT)
        return
    total_trip = sum(e.trip_amount_minor for e in todays)
    total_home = sum(e.home_amount_minor for e in todays)
    lines = ["<b>Today</b>"] + [fmt_expense_bullet(e, trip) for e in todays]
    # Totals show home currency first (spec §12), unlike the per-expense lines above.
    lines.append(
        f"Spent today: <b>{fmt_money(total_home, trip.home_currency)}</b> "
        f"({fmt_money(total_trip, trip.trip_currency)})"
    )
    await send("\n".join(lines))


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, message = update.effective_chat, update.effective_message
    assert chat is not None and message is not None
    await run_today(get_deps(context), chat, update.effective_user, build_send(message))


async def run_summary(deps: Deps, chat: Chat, user: User, send: SendFn) -> None:
    trip = await require_trip_send(deps, chat.id, send)
    if trip is None:
        return
    try:
        expenses = await _confirmed(deps, trip)
    except LedgerUnavailable:
        logger.exception("summary: could not read Expenses")
        await send(LEDGER_UNAVAILABLE_TEXT)
        return

    count = len(expenses)
    total_trip = sum(e.trip_amount_minor for e in expenses)
    total_home = sum(e.home_amount_minor for e in expenses)
    # Home currency first (spec §12: totals and balances show home currency first).
    text = (
        f"{count} expense{'s' if count != 1 else ''} · "
        f"{fmt_money(total_home, trip.home_currency)} · {fmt_money(total_trip, trip.trip_currency)}"
    )
    await send(text)


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, message = update.effective_chat, update.effective_message
    assert chat is not None and message is not None
    await run_summary(get_deps(context), chat, update.effective_user, build_send(message))


def _undo_keyboard(expense_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Yes, cancel it", callback_data=f"undo:{expense_id}:yes"),
                InlineKeyboardButton("Keep", callback_data=f"undo:{expense_id}:keep"),
            ]
        ]
    )


async def run_undo(deps: Deps, chat: Chat, user: User, send: SendFn) -> None:
    trip = await require_trip_send(deps, chat.id, send)
    if trip is None:
        return

    try:
        confirmed = await _confirmed(deps, trip)
    except LedgerUnavailable:
        logger.exception("undo: could not read Expenses")
        await send(LEDGER_UNAVAILABLE_TEXT)
        return

    candidates = [e for e in confirmed if e.created_by_user_id == user.id]
    if not candidates:
        await send(NOTHING_TO_UNDO_TEXT)
        return
    latest = max(candidates, key=lambda e: e.created_at)
    amount = format_expense_amount(
        trip, latest.original_currency, latest.trip_amount_minor, latest.home_amount_minor
    )
    text = f"Cancel this? {escape(latest.description)} {amount}"
    await send(text, reply_markup=_undo_keyboard(latest.expense_id))


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    assert chat is not None and user is not None and message is not None
    await run_undo(get_deps(context), chat, user, build_send(message))


async def _creator_name(deps, trip: Trip, expense: ExpenseRecord) -> str:
    """The creator's name. The Expenses tab keeps only the payer's name, and since
    "paid by" (sprint 5) the creator need not be the payer, so look them up in Users.
    """
    if expense.created_by_user_id == expense.payer_user_id:
        return expense.payer_display_name
    try:
        users = await deps.ledger.list_users(trip)
    except LedgerUnavailable:
        logger.exception("undo_callback: could not read Users")
        users = []
    for user in users:
        if user.user_id == expense.created_by_user_id:
            return user.display_name
    return "the person who added it"


async def undo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`undo:{expense_id}:{yes|keep}`. Initiator-only: only the person who created
    the expense may press (checked against the expense's Created By, since only
    that person's own expenses are ever offered by `/undo` in the first place).
    """
    query = update.callback_query
    assert query is not None
    data = query.data or ""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "undo":
        await query.answer()
        return
    _, expense_id, choice = parts

    deps = get_deps(context)
    chat = update.effective_chat
    presser = query.from_user
    if chat is None or presser is None:
        await query.answer()
        return

    try:
        trip = await deps.ledger.get_binding(chat.id)
    except LedgerUnavailable:
        logger.exception("undo_callback: could not read the current binding")
        await query.answer(LEDGER_UNAVAILABLE_TEXT)
        return
    if trip is None:
        await query.answer()
        return

    try:
        expenses = await deps.ledger.list_expenses(trip)
    except LedgerUnavailable:
        logger.exception("undo_callback: could not read Expenses")
        await query.answer(LEDGER_UNAVAILABLE_TEXT)
        return
    expense = next((e for e in expenses if e.expense_id == expense_id), None)
    if expense is None:
        await query.answer()
        return

    if presser.id != expense.created_by_user_id:
        await query.answer(f"Only {await _creator_name(deps, trip, expense)} can undo this")
        return

    if expense.status == "cancelled":
        await query.answer("Already cancelled")
        return

    if choice == "keep":
        await query.answer()
        await query.edit_message_text("Kept.", parse_mode="HTML")
        return

    presser_name = display_name_for(presser)
    try:
        await deps.ledger.cancel_expense(
            trip, expense_id, datetime.now(UTC), f"Cancelled via /undo by {presser_name}"
        )
    except LedgerUnavailable:
        logger.exception("undo_callback: could not cancel expense")
        await query.answer(LEDGER_UNAVAILABLE_TEXT)
        return

    await query.answer()
    amount = format_expense_amount(
        trip, expense.original_currency, expense.trip_amount_minor, expense.home_amount_minor
    )
    text = f"Cancelled ✅ {escape(expense.description)} {amount}"
    await query.edit_message_text(text, parse_mode="HTML")
