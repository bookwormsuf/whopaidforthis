"""One-off bill: `/split` and `Split a bill now` in a private chat. Spec §22,
docs/sprints/17-one-off-bill.md.

A *one-off bill* is a single bill split among *persons* (numbered labels, not trip
users). It lives in `Deps.oneoff_bills` (memory only) and nothing here touches the
ledger, the draft service or any other store. One card (one bot message) is edited in
place from the how-many step to the preview, then becomes the summary.

Sprint 18 (spec §23): a photo at the bill-line step is read like a group receipt, under
the per-user `PhotoAllowance` and the bot-wide read budget. The read is slow, so the photo
handler and `Try again` are registered with `block=False`, and the bill is marked
`reading` before the first `await`; after the read returns the bill is fetched again
and the result is dropped if the bill is gone or no longer reading. The image goes
Telegram -> memory -> the model; it is never logged and never written to disk.

The user's own typed messages are left in the chat, never deleted. Log lines carry the
event name only (written by `OneOffStore`), never an amount, a description or a name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from telegram import Message, Update, User
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes
from telegram.ext.filters import MessageFilter

from app.bot.deps import Deps, get_deps
from app.bot.handlers.photo_expense import MAX_PHOTO_BYTES, PHOTO_TOO_LARGE_TEXT
from app.bot.keyboards.oneoff import CALLBACK_PREFIX, START_CALLBACK, drop_keyboard, start_keyboard
from app.bot.oneoff_receipt_gate import ReceiptGate, Refusal
from app.bot.render.oneoff import (
    ALREADY_READ_TEXT,
    BAD_AMOUNT_TEXT,
    BAD_COUNT_TEXT,
    CANCELLED_TEXT,
    DROP_QUESTION_TEXT,
    DUPLICATE_NAME_TEXT,
    FORWARD_HINT_TEXT,
    GLOBAL_CAP_TEXT,
    KEEP_TEXT,
    NAME_TOO_LONG_TEXT,
    NO_AMOUNT_TEXT,
    NOTICE_TEXT,
    ONE_AT_A_TIME_TEXT,
    OUT_OF_TRIES_TEXT,
    READING_TEXT,
    REPLACED_TEXT,
    SPLIT_IN_GROUP_TEXT,
    STALE_TEXT,
    START_PROMPT_TEXT,
    render_card,
    render_summary,
    too_many_names_text,
)
from app.models.receipt import Receipt
from app.services import oneoff
from app.services.images import ImageTooLarge, PreparedImage, prepare_image
from app.services.oneoff import InputError, OneOffBill, OneOffStore, Refused, StepError, Toast
from app.services.receipt_parser import (
    ReceiptServiceUnavailable,
    ReceiptUnreadable,
    to_receipt,
)

logger = logging.getLogger(__name__)

ONEOFF_CALLBACK_PATTERN = rf"^{CALLBACK_PREFIX}"
# `Try again` re-reads the photo, which is slow: it gets its own non-blocking handler.
ONEOFF_RETRY_PATTERN = rf"^{CALLBACK_PREFIX}[^:]+:retry$"

# A private chat has no trip: the reader is told SGD for both currencies (spec §23).
_PRIVATE_CURRENCY = "SGD"

_GROUP_CHAT_TYPES = ("group", "supergroup")

# What each refusal from a text reader says. A kind with no entry is a KeyError, not a
# silent skip.
_REFUSALS = {
    "no_amount": NO_AMOUNT_TEXT,
    "bad_count": BAD_COUNT_TEXT,
    "bad_amount": BAD_AMOUNT_TEXT,
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
    """True while the sender's bill is at the bill-line step, or reading a receipt (a
    second photo is then told to wait). Any other step: false, so no reply.
    """

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
    query = update.callback_query
    assert query is not None
    answered = False

    async def answer(text: str | None = None) -> None:
        # A callback query can be answered once: `Try again` answers before its slow
        # read, a refused `Next` answers with a toast, every other press at the end.
        nonlocal answered
        if not answered:
            answered = True
            await query.answer(text)

    try:
        await _handle_callback(update, context, answer)
    finally:
        await answer()


async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, answer) -> None:
    query, chat = update.callback_query, update.effective_chat
    assert query is not None
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
        await _dispatch(update, context, deps, bill, action, arg, now, answer)
    except StepError:
        # A button that doesn't fit the bill's step (a double tap, an old keyboard).
        return
    except Toast as toast:
        await answer(toast.text)


def _int(text: str | None) -> int:
    """A button argument that must be a whole number; anything else is a stale or
    mangled press, ignored like any other that does not fit.
    """
    try:
        return int(text or "")
    except ValueError as error:
        raise StepError("bad argument") from error


async def _dispatch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: Deps,
    bill: OneOffBill,
    action: str,
    arg: str | None,
    now: datetime,
    answer,
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

    if action == "retry":
        if bill.state not in ("couldnt_read", "reading"):
            raise StepError("not at the couldn't-read card")
        # The gate marks the user before anything is awaited; the callback is answered
        # once the attempt is accepted (the read can take far longer than a callback
        # query lives). A second tap meets the mark and is told to wait.
        await _attempt_read(
            context, deps, bill.bill_id, bill.user_id, bill.chat_id, retry=True, accepted=answer
        )
        return

    if action == "count":
        try:
            changed = oneoff.set_count(bill, _int(arg))
        except InputError as error:
            raise StepError("bad count") from error
    elif action == "next":
        changed = oneoff.to_payer(bill)
    elif action == "payer":
        changed = oneoff.set_payer(bill, _int(arg))
    elif action == "equal":
        changed = oneoff.choose_equal(bill)
    elif action == "custom":
        changed = oneoff.choose_custom(bill)
    elif action == "confirm":
        changed = oneoff.confirm_amounts(bill)
    elif action == "edit":
        changed = oneoff.edit(bill)
    elif action == "type":
        changed = oneoff.type_instead(bill)
    elif action == "total":
        changed = oneoff.choose_total(bill, _int(arg))
    elif action == "manual":
        changed = oneoff.ask_other_amount(bill)
    elif action == "continue":
        changed = oneoff.continue_total(bill)
    elif action == "items":
        changed = oneoff.start_walk(bill)
    elif action == "wtoggle":
        position, _, person = (arg or "").partition(",")
        changed = oneoff.walk_toggle(bill, _int(position), _int(person))
    elif action == "weveryone":
        changed = oneoff.walk_everyone(bill, _int(arg))
    elif action == "wnext":
        changed = oneoff.walk_next(bill, _int(arg))
    elif action == "wback":
        changed = oneoff.walk_back(bill, _int(arg))
    elif action == "fixlist":
        changed = oneoff.fix_list(bill)
    elif action == "fixpick":
        changed = oneoff.fix_pick(bill, _int(arg))
    elif action == "fixback":
        changed = oneoff.fix_back(bill)
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


@dataclass(frozen=True)
class _Read:
    """The result of one model call: `ok` (with `receipt`) or `unreadable` (with
    `problem` when the reader service failed, not the photo).
    """

    status: Literal["ok", "unreadable"]
    receipt: Receipt | None = None
    problem: str | None = None


async def _download(bot, file_id: str) -> PreparedImage | Literal["too_large", "failed"]:
    """Downloads the photo and prepares it for the model. Never raises; counts nothing.
    The bytes live only in this call. Nothing is logged from here, since an error could
    carry text from the image.
    """
    try:
        file = await bot.get_file(file_id)
        if file.file_size and file.file_size > MAX_PHOTO_BYTES:
            return "too_large"
        return prepare_image(bytes(await file.download_as_bytearray()))
    except ImageTooLarge:
        return "too_large"
    except Exception:  # noqa: BLE001 - Telegram or Pillow failure: offer the retry buttons
        return "failed"


async def _read_model(deps: Deps, image: PreparedImage) -> _Read:
    """The model call and validation into a `Receipt`. Never raises."""
    try:
        extraction = await deps.receipt_reader.read(
            image, trip_currency=_PRIVATE_CURRENCY, home_currency=_PRIVATE_CURRENCY
        )
    except ReceiptServiceUnavailable as exc:
        return _Read("unreadable", problem=exc.reason)
    except ReceiptUnreadable:
        return _Read("unreadable")
    except Exception:  # noqa: BLE001 - the reader should only raise ReceiptUnreadable
        # Fixed text only: no exc_info, since an error could carry the model's answer.
        logger.warning("oneoff photo: reader failed unexpectedly")
        return _Read("unreadable")
    try:
        receipt = to_receipt(extraction, oneoff.resolve_bill_currency(extraction.currency))
    except ReceiptUnreadable:
        return _Read("unreadable")
    return _Read("ok", receipt)


_REFUSAL_TEXTS = {
    "busy": ONE_AT_A_TIME_TEXT,
    "user": ALREADY_READ_TEXT,
    "tries": OUT_OF_TRIES_TEXT,
    "global": GLOBAL_CAP_TEXT,
}
_REFUSAL_EVENTS = {
    "user": "oneoff photo refused: user",
    "tries": "oneoff photo refused: tries",
    "global": "oneoff photo refused: global",
}


async def _refuse(context: ContextTypes.DEFAULT_TYPE, chat_id: int, refusal: Refusal) -> None:
    """A refusal's text as a new message; a card is left as it was."""
    if refusal.kind in _REFUSAL_EVENTS:
        logger.warning(_REFUSAL_EVENTS[refusal.kind])
    await _send(context, chat_id, _REFUSAL_TEXTS[refusal.kind])


def _leave_reading(store: OneOffStore, bill_id: str, back_to, problem: str | None) -> None:
    """After a failure mid-attempt: a bill still `reading` goes back to where it was, so
    the user is not stuck for 30 minutes.
    """
    now = _now()
    current = store.get(bill_id, now)
    if current is not None and current.state == "reading":
        store.put(oneoff.abort_read(current, back_to, problem), now, touch=False)


async def _attempt_read(
    context: ContextTypes.DEFAULT_TYPE,
    deps: Deps,
    bill_id: str,
    user_id: int,
    chat_id: int,
    *,
    retry: bool,
    file_id: str | None = None,
    accepted=None,
) -> None:
    """One receipt read for this user's bill: a new photo (`file_id`) or `Try again`. The
    sequence is `ReceiptGate`'s (see `app/bot/oneoff_receipt_gate.py`); this function shows
    the cards. The bill is fetched again by ID, never trusted from the caller, and again
    after every slow step. `accepted` is awaited once the attempt is accepted (the
    callback is answered then, not before: a second tap must meet the in-flight mark).
    """
    store = deps.oneoff_bills
    gate = ReceiptGate(
        allowance=deps.photo_allowance,
        budget=deps.receipt_budget,
        in_flight=deps.photo_in_flight,
        owner_user_id=deps.owner_user_id,
    )
    ticket = await gate.begin(user_id, _now())  # marks the user in flight before any await
    if isinstance(ticket, Refusal):
        await _refuse(context, chat_id, ticket)
        return
    back_to: Literal["awaiting_bill", "couldnt_read"] = "couldnt_read" if retry else "awaiting_bill"
    problem: str | None = None
    marked = False
    try:
        if accepted is not None:
            await accepted()
        now = _now()
        bill = store.get(bill_id, now)
        if bill is None or bill.state != back_to:
            return
        photo = bill.photo_file_id if retry else file_id
        assert photo is not None
        problem = bill.read_problem
        started = oneoff.begin_retry(bill) if retry else oneoff.begin_read(bill, photo)
        if not store.put(started, now):
            return
        marked = True

        if deps.photo_notices.take(user_id):
            await _send(context, chat_id, NOTICE_TEXT)
        # The card showing `Reading the receipt…`: a new message for a photo, the couldn't-
        # read card edited in place for `Try again`.
        if (
            retry
            and started.message_id is not None
            and await _edit(context, chat_id, started.message_id, READING_TEXT)
        ):
            message_id = started.message_id
        else:
            message_id = (await _send(context, chat_id, READING_TEXT)).message_id
        current = store.get(bill_id, _now())
        if current is None or current.state != "reading":
            # Cancelled or replaced in the meantime: no model call, and a `Reading…`
            # message that nothing will update is closed.
            if message_id != started.message_id:
                await _close_card(context, replace(bill, message_id=message_id), CANCELLED_TEXT)
            return
        if not store.put(replace(current, message_id=message_id), _now()):
            return

        image = await _download(context.bot, photo)  # counts nowhere
        fresh = store.get(bill_id, _now())
        if fresh is None or fresh.state != "reading":
            return
        if image == "failed":
            await _finish(context, store, oneoff.read_failed(fresh, None))
            marked = False
            return
        if image == "too_large":
            await _finish(context, store, oneoff.back_to_step_one(fresh))
            marked = False
            await _send(context, chat_id, PHOTO_TOO_LARGE_TEXT)
            return

        capped = gate.take_cap(ticket, _now())  # immediately before the model call
        if capped is not None:
            await _finish(context, store, oneoff.back_to_step_one(fresh))
            marked = False
            await _refuse(context, chat_id, capped)
            return

        result = await _read_model(deps, image)
        after = _now()
        await gate.record(ticket, after, success=result.status == "ok")
        logger.info("oneoff photo read" if result.status == "ok" else "oneoff photo failed")
        # The read was slow: the bill may have been cancelled or replaced meanwhile. Fetch
        # it again and drop the result unless it is still waiting for this read.
        fresh = store.get(bill_id, after)
        if fresh is None or fresh.state != "reading":
            return
        if result.status == "ok":
            assert result.receipt is not None
            changed = oneoff.apply_receipt(fresh, result.receipt)
        else:
            changed = oneoff.read_failed(fresh, result.problem)
        await _finish(context, store, changed)
        marked = False
    except BaseException:
        if marked:
            _leave_reading(store, bill_id, back_to, problem)
        raise
    finally:
        gate.release(ticket)


async def _finish(
    context: ContextTypes.DEFAULT_TYPE, store: OneOffStore, changed: OneOffBill
) -> None:
    """Saves the bill's new state and shows its card, unless the bill is gone."""
    now = _now()
    if store.put(changed, now):
        await _show(context, store, changed, now)


async def oneoff_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A photo at the bill-line step: read as a receipt. While a read is running, a
    second photo is told to wait. Any other step, or no bill: no reply. The caption is
    ignored. Registered with `block=False`, so a slow read holds up no other update; the
    bill is re-checked here because two photos can be matched before either runs.
    """
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if message is None or chat is None or user is None or not message.photo:
        return
    deps = get_deps(context)
    update_id = getattr(update, "update_id", None)
    if update_id is not None:
        if deps.recent_updates.seen(update_id):
            return
        deps.recent_updates.add(update_id)
    bill = deps.oneoff_bills.for_user(user.id, _now())
    if bill is None:
        return
    if bill.state == "reading":
        await _send(context, chat.id, ONE_AT_A_TIME_TEXT)
        return
    if bill.state != "awaiting_bill":
        return
    largest = message.photo[-1]
    if largest.file_size and largest.file_size > MAX_PHOTO_BYTES:
        await _send(context, chat.id, PHOTO_TOO_LARGE_TEXT)  # not counted anywhere
        return
    await _attempt_read(
        context, deps, bill.bill_id, user.id, chat.id, retry=False, file_id=largest.file_id
    )
