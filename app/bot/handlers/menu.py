"""The `/start` welcome menu: a state-aware button menu under one pinned message.

Spec references: docs/sprints/10-start-menu.md, pinned-decisions §19. Every
`run_<name>` coroutine already applies its own trip gate (join.py's
`require_trip_send`, or its own lookup for `/settrip` and `/deletetrip`), so
`menu_callback` dispatches straight to them with no second gate of its own --
state is re-read at press time inside each coroutine, never cached from when
the menu was rendered.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from html import escape
from typing import Literal

from telegram import Bot, Chat, InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from app.bot.conversations.settrip import run_settrip
from app.bot.deps import Deps, get_deps
from app.bot.handlers.balances import run_balances, run_mine, run_settle
from app.bot.handlers.deletetrip import run_deletetrip
from app.bot.handlers.export import run_export
from app.bot.handlers.join import resolve_trip_status, run_join
from app.bot.handlers.reads import run_recent, run_summary, run_today, run_undo
from app.bot.send import SendDocumentFn, SendFn
from app.models.trip import Trip
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable

logger = logging.getLogger(__name__)

MenuState = Literal["no_trip", "open", "closed"]

NO_TRIP_LINE = "No trip yet."
CLOSED_TRIP_LINE = "The last trip is closed. Start a new one or export it."
PIN_HINT = "\n\nPin this message so everyone can find the buttons."

WELCOME_HEADER = (
    "<b>whopaid</b> records this group's trip expenses.\n"
    "Type what you spent, like <code>1200 yen ramen</code>, or send a photo of a receipt. "
    "I convert it to your home currency at the day's rate, split it between whoever was "
    "there, and track who owes whom."
)
WELCOME_FOOTER = "Tap a button below. Typed commands work too, see /help."

HOWTO_TEXT = (
    "Type an amount and what it was for:\n"
    "<code>1200 yen ramen</code>\n"
    "<code>45 sgd taxi split everyone</code>\n"
    "<code>3200 yen dinner paid by Amir split between me Amir</code>\n"
    "Or send a photo of the receipt and tap the items you had."
)

_MENU_PAGE_PATTERN = re.compile(r"^menu:page:([12])$")

# One coroutine per action, all sharing the `run_<name>(deps, chat, user, send)`
# shape (sprint 10 pin). `export` and `howto` are handled separately in
# `menu_callback`: `export` needs a `send_document` too, `howto` has no typed
# command of its own to call.
_ACTIONS: dict[str, Callable[[Deps, Chat, User, SendFn], Awaitable[None]]] = {
    "join": run_join,
    "balances": run_balances,
    "settle": run_settle,
    "recent": run_recent,
    "today": run_today,
    "summary": run_summary,
    "mine": run_mine,
    "undo": run_undo,
    "deletetrip": run_deletetrip,
    "settrip": run_settrip,
}


def _state_for(trip: Trip | None, latest_is_closed: bool) -> MenuState:
    if trip is not None:
        return "open"
    return "closed" if latest_is_closed else "no_trip"


def welcome_text(trip: Trip | None, latest_is_closed: bool) -> str:
    """The pinned welcome HTML, with the state line appended: the open trip's
    name and currencies, the closed-trip line, or "No trip yet."
    """
    if trip is not None:
        state_line = (
            f"Trip: {escape(trip.name)} "
            f"({escape(trip.trip_currency)} → {escape(trip.home_currency)})"
        )
    elif latest_is_closed:
        state_line = CLOSED_TRIP_LINE
    else:
        state_line = NO_TRIP_LINE
    return f"{WELCOME_HEADER}\n\n{WELCOME_FOOTER}\n\n{state_line}"


def _button(label: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=f"menu:{action}")


def menu_keyboard(state: MenuState, page: int = 1) -> InlineKeyboardMarkup:
    """The pinned inline keyboard for `state`. `page` only matters when the trip
    is open: page 1 is the everyday row, page 2 (`More ▸`) the rest.
    """
    if state == "no_trip":
        return InlineKeyboardMarkup([[_button("Start a trip", "settrip")]])
    if state == "closed":
        return InlineKeyboardMarkup(
            [
                [_button("Start a new trip", "settrip")],
                [_button("Export", "export")],
                [_button("Delete trip", "deletetrip")],
            ]
        )
    if page == 2:
        return InlineKeyboardMarkup(
            [
                [_button("Mine", "mine"), _button("Undo last", "undo")],
                [_button("Export", "export")],
                [_button("How to add an expense", "howto")],
                [_button("Delete trip", "deletetrip")],
                [InlineKeyboardButton("◂ Back", callback_data="menu:page:1")],
            ]
        )
    return InlineKeyboardMarkup(
        [
            [_button("Join", "join")],
            [_button("Balances", "balances"), _button("Settle", "settle")],
            [_button("Recent", "recent"), _button("Today", "today"), _button("Summary", "summary")],
            [InlineKeyboardButton("More ▸", callback_data="menu:page:2")],
        ]
    )


async def post_welcome(deps: Deps, bot: Bot, chat: Chat) -> None:
    """Resolves this chat's state, sends the welcome with its menu, then tries to
    pin it. On `BadRequest`/`Forbidden` (no pin right) logs at INFO and edits the
    message to append the "Pin this message..." line, keeping the same keyboard.
    On a store error, sends `LEDGER_UNAVAILABLE_TEXT` instead.
    """
    try:
        trip, latest_is_closed = await resolve_trip_status(deps, chat.id)
    except LedgerUnavailable:
        logger.exception("post_welcome: could not read the current binding")
        await bot.send_message(chat.id, LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return

    state = _state_for(trip, latest_is_closed)
    text = welcome_text(trip, latest_is_closed)
    keyboard = menu_keyboard(state, page=1)
    message = await bot.send_message(chat.id, text, parse_mode="HTML", reply_markup=keyboard)

    try:
        await bot.pin_chat_message(chat.id, message.message_id, disable_notification=True)
    except (BadRequest, Forbidden):
        logger.info("post_welcome: no right to pin in chat %s", chat.id)
        await bot.edit_message_text(
            text + PIN_HINT,
            chat_id=chat.id,
            message_id=message.message_id,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


def _build_send(bot: Bot, chat_id: int) -> SendFn:
    """A `SendFn` that posts a new plain message in the chat (not a reply),
    for a menu button press (sprint 10 pin).
    """

    async def send(text: str, *, reply_markup: object | None = None):
        kwargs: dict[str, object] = {"parse_mode": "HTML"}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        return await bot.send_message(chat_id, text, **kwargs)

    return send


def _build_send_document(bot: Bot, chat_id: int) -> SendDocumentFn:
    """`run_export`'s sibling to `_build_send`, mirroring `build_send_document`."""

    async def send_document(document: object, *, caption: str | None = None, **kwargs: object):
        if caption is not None:
            kwargs["caption"] = caption
        return await bot.send_document(chat_id, document=document, **kwargs)

    return send_document


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`menu:<action>` dispatches to the same `run_<name>` coroutine the typed
    command uses; `menu:page:<n>` only edits this message's keyboard.
    """
    query = update.callback_query
    assert query is not None
    await query.answer()
    data = query.data or ""

    page_match = _MENU_PAGE_PATTERN.match(data)
    if page_match is not None:
        await query.edit_message_reply_markup(
            reply_markup=menu_keyboard("open", page=int(page_match.group(1)))
        )
        return

    chat = update.effective_chat
    presser = query.from_user
    if chat is None or presser is None:
        return

    action = data.removeprefix("menu:")
    deps = get_deps(context)
    send = _build_send(context.bot, chat.id)

    if action == "howto":
        await send(HOWTO_TEXT)
        return
    if action == "export":
        await run_export(deps, chat, presser, send, _build_send_document(context.bot, chat.id))
        return

    run = _ACTIONS.get(action)
    if run is None:
        return
    await run(deps, chat, presser, send)


async def bot_added_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Posts the welcome only when the bot itself is among the new members; other
    joins are ignored. Posting failures (bot removed again, no send permission)
    are logged and swallowed.
    """
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.new_chat_members:
        return
    bot_id = context.bot.id
    if not any(member.id == bot_id for member in message.new_chat_members):
        return

    deps = get_deps(context)
    try:
        await post_welcome(deps, context.bot, chat)
    except Exception:  # noqa: BLE001 - a failed group-add welcome must not crash the handler
        logger.exception("bot_added_to_group: could not post the welcome message")


def private_start_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    """One URL button, `Add me to a group`, for private-chat `/start`/`/help`."""
    url = f"https://t.me/{bot_username}?startgroup=true"
    return InlineKeyboardMarkup([[InlineKeyboardButton("Add me to a group", url=url)]])
