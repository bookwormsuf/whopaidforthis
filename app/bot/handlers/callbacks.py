"""Callback query handlers that are not part of the draft flow.

`join_button_callback` handles both join-row buttons (`^join:`, spec §3): `I'm in
too` (`join:<chat_id>:tap`) and `Everyone's in` (`join:<chat_id>:done`). The draft
callback (a ULID prefix) lives in `app.bot.handlers.text_expense`; the `/undo`
confirmation callback lives in `app.bot.handlers.reads`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.deps import get_deps
from app.bot.keyboards.settrip import joined_keyboard
from app.models.user import TripUser
from app.services.ledger_store import LedgerUnavailable

logger = logging.getLogger(__name__)

_JOINED_SO_FAR_PREFIX = "Joined so far: "
_EVERYONE_IN_LINE = "Everyone's in. Latecomers can type /join."
_CLOSED_TEXT = "Joining is closed here, type /join."
_NOT_JOINED_TEXT = "Only trip users can do this"


def add_joined_so_far(text: str, name: str) -> tuple[str, int]:
    """Append `name` to the message's "Joined so far: A, B, C" line, no duplicates.

    If the line does not exist yet, it is added as a new last line. Returns the new
    text and the resulting count of names on that line. Pure function, unit-tested
    directly.
    """
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.startswith(_JOINED_SO_FAR_PREFIX):
            names = [n.strip() for n in line[len(_JOINED_SO_FAR_PREFIX) :].split(",") if n.strip()]
            if name not in names:
                names.append(name)
            lines[index] = _JOINED_SO_FAR_PREFIX + ", ".join(names)
            return "\n".join(lines), len(names)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + f"{_JOINED_SO_FAR_PREFIX}{name}", 1


def is_closed(text: str) -> bool:
    """True once `Everyone's in` has been pressed on this message."""
    return _EVERYONE_IN_LINE in text


def close_for_joining(text: str) -> str:
    """Appends the "Everyone's in" line. Pure function, unit-tested directly."""
    if text and not text.endswith("\n"):
        text += "\n"
    return text + _EVERYONE_IN_LINE


async def join_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query is not None
    data = query.data or ""
    parts = data.split(":", 2)
    if len(parts) < 2:
        await query.answer()
        return
    raw_chat_id = parts[1]
    # Route on the third segment; a bare "join:<chat_id>" (pre-this-change) is
    # treated as "tap" so an already-sent message's button keeps working.
    action = parts[2] if len(parts) == 3 else "tap"
    try:
        chat_id = int(raw_chat_id)
    except ValueError:
        await query.answer()
        return

    deps = get_deps(context)
    presser = query.from_user
    if presser is None:
        await query.answer()
        return

    try:
        trip = await deps.ledger.get_binding(chat_id)
    except LedgerUnavailable:
        logger.exception("join button: could not read the current binding")
        await query.answer("Couldn't reach Google Sheets, try again.")
        return
    if trip is None:
        await query.answer("This trip is no longer connected.")
        return

    message = query.message
    current_text = message.text_html if message is not None else None

    if action == "done":
        await _handle_done(query, deps, trip, presser, current_text)
        return

    if current_text is not None and is_closed(current_text):
        await query.answer(_CLOSED_TEXT)
        return

    tg_user = TripUser.from_telegram_user(presser, datetime.now(UTC))
    try:
        joined_user = await deps.ledger.upsert_user(trip, tg_user, joined=True)
    except LedgerUnavailable:
        logger.exception("join button: could not upsert user")
        await query.answer("Couldn't reach Google Sheets, try again.")
        return

    await query.answer(f"Joined ✅ {joined_user.display_name}")

    # `.text_html` (not `.text`), so existing HTML markup in the message (e.g. the
    # "Connected ✅ <b>...</b>" title) survives the edit, and a joiner's own name is
    # escaped by us going in -- never re-derived from already-escaped HTML.
    if current_text is not None:
        new_text, joined_count = add_joined_so_far(current_text, escape(joined_user.display_name))
        try:
            await query.edit_message_text(
                new_text, parse_mode="HTML", reply_markup=joined_keyboard(chat_id, joined_count)
            )
        except Exception:  # noqa: BLE001 - editing an already-changed message is not fatal
            logger.debug("join button: could not edit message")


async def _handle_done(query, deps, trip, presser, current_text: str | None) -> None:
    """`Everyone's in`: any joined user may press it; it closes joining on this
    message (removes the keyboard, appends the closing line).
    """
    if current_text is not None and is_closed(current_text):
        await query.answer()  # already closed; nothing to do
        return

    joined_users = await deps.ledger.list_users(trip, joined_only=True)
    if not any(user.user_id == presser.id for user in joined_users):
        await query.answer(_NOT_JOINED_TEXT)
        return

    await query.answer()
    if current_text is not None:
        new_text = close_for_joining(current_text)
        try:
            await query.edit_message_text(new_text, parse_mode="HTML", reply_markup=None)
        except Exception:  # noqa: BLE001 - editing an already-changed message is not fatal
            logger.debug("join button: could not edit message (done)")
