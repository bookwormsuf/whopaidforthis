"""One-off bill: `/split` and `Split a bill now` in a private chat. Spec §22,
docs/sprints/17-one-off-bill.md.

A *one-off bill* is a single bill split among *persons* (numbered labels, not trip
users). It lives in `Deps.oneoff_bills` (memory only) and nothing here touches the
ledger, the draft service or any other store. One card (one bot message) is edited in
place from the how-many step to the preview, then becomes the summary.

The user's own typed messages are left in the chat, never deleted. Log lines carry the
event name only (written by `OneOffStore`), never an amount, a description or a name.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from telegram import Message, Update, User
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes
from telegram.ext.filters import MessageFilter

from app.bot.deps import Deps, get_deps
from app.bot.keyboards.oneoff import CALLBACK_PREFIX, START_CALLBACK, drop_keyboard, start_keyboard
from app.bot.render.oneoff import (
    BAD_COUNT_TEXT,
    CANCELLED_TEXT,
    DROP_QUESTION_TEXT,
    DUPLICATE_NAME_TEXT,
    FORWARD_HINT_TEXT,
    KEEP_TEXT,
    NAME_TOO_LONG_TEXT,
    NO_AMOUNT_TEXT,
    PHOTO_TEXT,
    REPLACED_TEXT,
    SPLIT_IN_GROUP_TEXT,
    STALE_TEXT,
    START_PROMPT_TEXT,
    render_card,
    render_summary,
    too_many_names_text,
)
from app.services import oneoff
from app.services.oneoff import InputError, OneOffBill, OneOffStore, Refused, StepError

ONEOFF_CALLBACK_PATTERN = rf"^{CALLBACK_PREFIX}"

_GROUP_CHAT_TYPES = ("group", "supergroup")

# What each refusal from a text reader says. A kind with no entry is a KeyError, not a
# silent skip.
_REFUSALS = {
    "no_amount": NO_AMOUNT_TEXT,
    "bad_count": BAD_COUNT_TEXT,
    "name_too_long": NAME_TOO_LONG_TEXT,
    "duplicate_name": DUPLICATE_NAME_TEXT,
}


def _now() -> datetime:
    """The clock, read here and passed down (the service never reads it). Tests patch it."""
    return datetime.now(UTC)


class BillWaitingForText(MessageFilter):
    """True while the sender's bill waits for typed text, or owes them the one expiry
    notice. Holds the store directly: a `MessageFilter` only gets the `Message`.
    """

    def __init__(self, store: OneOffStore) -> None:
        super().__init__(name="BillWaitingForText")
        self.store = store

    def filter(self, message: Message) -> bool:
        user = message.from_user
        return user is not None and self.store.wants_text(user.id, _now())


class BillWaitingForPhoto(MessageFilter):
    """True only while the sender's bill is at the bill-line step."""

    def __init__(self, store: OneOffStore) -> None:
        super().__init__(name="BillWaitingForPhoto")
        self.store = store

    def filter(self, message: Message) -> bool:
        user = message.from_user
        return user is not None and self.store.wants_photo(user.id, _now())


class HasOneOffBill(MessageFilter):
    """True while the sender has a live bill (for `/cancel`)."""

    def __init__(self, store: OneOffStore) -> None:
        super().__init__(name="HasOneOffBill")
        self.store = store

    def filter(self, message: Message) -> bool:
        user = message.from_user
        return user is not None and self.store.has_bill(user.id, _now())


# ---- sending and editing ------------------------------------------------------


async def _send(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, markup=None
) -> Message:
    return await context.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


async def _edit(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, text: str, markup=None
) -> bool:
    """Edits a message. Returns False when the message can no longer be edited (the user
    deleted it), True otherwise. Telegram refuses an edit that changes nothing
    ("Message is not modified"), which is not an error here (e.g. names sent that
    rename no one).
    """
    try:
        await context.bot.edit_message_text(
            text, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=markup
        )
    except BadRequest as error:
        return "not modified" in str(error).lower()
    return True


async def _show(
    context: ContextTypes.DEFAULT_TYPE, store: OneOffStore, bill: OneOffBill, now: datetime
) -> None:
    """Edits the bill's card to its current step. If the card is gone, the card goes out
    as a new message and its ID is kept on the bill, so the flow carries on. The bill's
    state was saved before this.
    """
    assert bill.message_id is not None
    text, markup = render_card(bill)
    if await _edit(context, bill.chat_id, bill.message_id, text, markup):
        return
    sent = await _send(context, bill.chat_id, text, markup)
    store.put(replace(bill, message_id=sent.message_id), now, touch=False)


async def _close_card(context: ContextTypes.DEFAULT_TYPE, bill: OneOffBill, text: str) -> None:
    """Turns the bill's card into a one-line outcome with no buttons. Best effort: the
    bill is already gone, so a card that cannot be edited is not worth failing over.
    """
    if bill.message_id is None:
        return
    try:
        await _edit(context, bill.chat_id, bill.message_id, text)
    except TelegramError:
        pass


async def _send_stale(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    await _send(context, chat_id, STALE_TEXT, start_keyboard())


async def _send_card(
    context: ContextTypes.DEFAULT_TYPE, store: OneOffStore, bill: OneOffBill, now: datetime
) -> None:
    """Sends the card as a new message, remembers its ID, and saves the bill."""
    text, markup = render_card(bill)
    sent = await _send(context, bill.chat_id, text, markup)
    store.put(replace(bill, message_id=sent.message_id), now)


# ---- starting -----------------------------------------------------------------


async def _start_new(
    context: ContextTypes.DEFAULT_TYPE,
    deps: Deps,
    chat_id: int,
    user: User,
    now: datetime,
    *,
    reuse_message_id: int | None = None,
) -> None:
    """Step 1: asks for the bill line. `reuse_message_id` turns an existing message
    (the answered `Drop the bill you started?` question) into the prompt.
    """
    bill = deps.oneoff_bills.create(
        user_id=user.id, chat_id=chat_id, first_name=user.first_name, now=now
    )
    message_id = reuse_message_id
    if message_id is None or not await _edit(context, chat_id, message_id, START_PROMPT_TEXT):
        # No message to reuse, or the user deleted it: send the prompt.
        message_id = (await _send(context, chat_id, START_PROMPT_TEXT)).message_id
    deps.oneoff_bills.put(replace(bill, message_id=message_id), now)


async def _begin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user: User) -> None:
    """`/split` or `Split a bill now`: the prompt, or the drop question when this user
    already has a bill.
    """
    deps = get_deps(context)
    store = deps.oneoff_bills
    now = _now()
    store.sweep(now)
    existing = store.for_user(user.id, now)
    if existing is not None:
        await _send(context, chat_id, DROP_QUESTION_TEXT, drop_keyboard(existing.bill_id))
        return
    await _start_new(context, deps, chat_id, user, now)


async def split_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if chat is None or user is None:
        return
    if chat.type == "private":
        await _begin(context, chat.id, user)
    elif chat.type in _GROUP_CHAT_TYPES:
        # No trip is needed and nothing else happens; a one-off bill is private.
        await _send(context, chat.id, SPLIT_IN_GROUP_TEXT)


# ---- /cancel ------------------------------------------------------------------


async def oneoff_cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/cancel` in a private chat with a bill open: the card becomes `Cancelled.` and
    nothing else is sent. Registered only behind `HasOneOffBill`, so with no bill the
    ordinary `/cancel` runs.
    """
    user = update.effective_user
    if user is None:
        return
    store = get_deps(context).oneoff_bills
    bill = store.for_user(user.id, _now())
    if bill is None:
        return
    store.remove(bill.bill_id, "cancelled")
    await _close_card(context, bill, CANCELLED_TEXT)


# ---- buttons ------------------------------------------------------------------


async def _stale_press(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A press for a bill that no longer exists: a new message with a start button, and
    the pressed card loses its buttons.
    """
    query, chat = update.callback_query, update.effective_chat
    assert query is not None and chat is not None
    get_deps(context).oneoff_bills.take_expired_notice(query.from_user.id)
    await _send_stale(context, chat.id)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except BadRequest as error:
        if "not modified" not in str(error).lower():
            raise


async def oneoff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`oneoff:start`, and `oneoff:<bill id>:<action>[:<argument>]`."""
    query, chat = update.callback_query, update.effective_chat
    assert query is not None
    await query.answer()
    user = query.from_user
    if chat is None or user is None or chat.type != "private":
        return
    data = query.data or ""
    if data == START_CALLBACK:
        await _begin(context, chat.id, user)
        return

    parts = data.split(":")
    if len(parts) < 3:
        return
    bill_id, action = parts[1], parts[2]
    arg = parts[3] if len(parts) > 3 else None

    deps = get_deps(context)
    now = _now()
    bill = deps.oneoff_bills.get(bill_id, now)
    if bill is None:
        await _stale_press(update, context)
        return
    if bill.user_id != user.id:
        return
    try:
        await _dispatch(update, context, deps, bill, action, arg, now)
    except StepError:
        # A button that doesn't fit the bill's step (a double tap, an old keyboard).
        return


async def _dispatch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: Deps,
    bill: OneOffBill,
    action: str,
    arg: str | None,
    now: datetime,
) -> None:
    store = deps.oneoff_bills
    query = update.callback_query
    assert query is not None and query.message is not None

    if action == "cancel":
        store.remove(bill.bill_id, "cancelled")
        await _close_card(context, bill, CANCELLED_TEXT)
        return
    if action == "drop":
        if arg == "no":
            try:
                await _edit(context, bill.chat_id, query.message.message_id, KEEP_TEXT)
            except TelegramError:
                pass
        elif arg == "yes":
            store.remove(bill.bill_id, "cancelled")
            await _close_card(context, bill, CANCELLED_TEXT)
            await _start_new(
                context,
                deps,
                bill.chat_id,
                query.from_user,
                now,
                reuse_message_id=query.message.message_id,
            )
        return
    if action == "done":
        if bill.state != "preview":
            raise StepError("not at the preview")
        summary = render_summary(bill, context.bot.username or "")
        # The summary must reach the user: a card they deleted gets it as a new message.
        if bill.message_id is None or not await _edit(
            context, bill.chat_id, bill.message_id, summary
        ):
            await _send(context, bill.chat_id, summary)
        store.remove(bill.bill_id, "finished")
        await _send(context, bill.chat_id, FORWARD_HINT_TEXT)
        return

    if action == "count":
        try:
            changed = oneoff.set_count(bill, int(arg or ""))
        except (ValueError, InputError) as error:
            raise StepError("bad count") from error
    elif action == "next":
        changed = oneoff.to_payer(bill)
    elif action == "payer":
        try:
            changed = oneoff.set_payer(bill, int(arg or ""))
        except ValueError as error:
            raise StepError("bad payer") from error
    elif action == "equal":
        changed = oneoff.choose_equal(bill)
    elif action == "custom":
        changed = oneoff.choose_custom(bill)
    elif action == "confirm":
        changed = oneoff.confirm_amounts(bill)
    elif action == "edit":
        changed = oneoff.edit(bill)
    else:
        return
    if not store.put(changed, now):
        return  # the bill went away meanwhile (cancelled, replaced, expired)
    await _show(context, store, changed, now)


# ---- typed text and photos ----------------------------------------------------


async def oneoff_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Typed text while a bill waits for it: the bill line, the number of people, names
    or typed amounts, by step. After an expiry, the one notice.
    """
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if message is None or chat is None or user is None or not message.text:
        return
    deps = get_deps(context)
    store = deps.oneoff_bills
    now = _now()
    bill = store.for_user(user.id, now)
    if bill is None:
        if store.take_expired_notice(user.id):
            await _send_stale(context, chat.id)
        return

    try:
        reading = oneoff.read_text(bill, message.text, context.bot.username or "")
    except StepError:
        return
    if isinstance(reading, Refused):
        await _send(context, chat.id, _refusal_text(reading))
    elif isinstance(reading, oneoff.FirstCard):
        await _send_card(context, store, reading.bill, now)
    elif isinstance(reading, oneoff.Replaced):
        # A new bill line replaces the bill: the old card is closed, a new card is sent.
        await _close_card(context, bill, REPLACED_TEXT)
        store.remove(bill.bill_id, "cancelled")
        fresh = store.create(
            user_id=bill.user_id, chat_id=bill.chat_id, first_name=bill.first_name, now=now
        )
        await _send_card(context, store, oneoff.set_bill_line(fresh, reading.line), now)
    else:
        if store.put(reading.bill, now, touch=reading.touch):
            await _show(context, store, reading.bill, now)


def _refusal_text(refused: Refused) -> str:
    if refused.kind == "too_many_names":
        return too_many_names_text(refused.count)
    return _REFUSALS[refused.kind]


async def oneoff_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A photo at the bill-line step: receipts are not read here yet."""
    chat = update.effective_chat
    if chat is not None:
        await _send(context, chat.id, PHOTO_TEXT)
