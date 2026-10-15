"""The photo-expense handler: a receipt photo -> a draft card.

Spec references: pinned-decisions §7, §8, §13, §14; sprint 4 pins. Registered as a
non-blocking `MessageHandler` in group 0 (a receipt read can take tens of seconds and
must not hold up every other update). Also exposes:

- `read_receipt_photo`, which `app.bot.handlers.text_expense.draft_callback` reuses
  for the `retry` action (re-downloading the same photo by file_id, per spec §7:
  bytes must not outlive the request, so a retry re-fetches rather than keeping a
  copy);
- `handle_manual_amount_reply`, which `text_expense_handler` calls first, so a reply
  to an "Enter amount manually" card is taken as that amount and every other reply
  still parses as a normal text expense.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes, filters

from app.bot.deps import Deps, get_deps
from app.bot.formatting import unsupported_currency_text
from app.bot.handlers.join import require_trip_for_expense
from app.bot.handlers.tags import join_tagged, tags_as_text
from app.bot.render.draft import render_draft
from app.models.category import Category
from app.models.receipt import Receipt
from app.models.trip import Trip
from app.models.user import TripUser
from app.services.drafts import Draft, DraftError
from app.services.expense_parser import (
    parse_amount_only,
    parse_caption_clauses,
    parse_caption_description,
)
from app.services.images import ImageTooLarge, prepare_image
from app.services.ledger_store import LEDGER_UNAVAILABLE_TEXT, LedgerUnavailable
from app.services.receipt_parser import (
    ReceiptServiceUnavailable,
    ReceiptUnreadable,
    resolve_receipt_currency,
    resolve_transaction_date,
    to_receipt,
)

logger = logging.getLogger(__name__)

MAX_PHOTO_BYTES = 10 * 1024 * 1024
PHOTO_TOO_LARGE_TEXT = "That photo's too large (over 10 MB), please send a smaller one."

PHOTO_EXPENSE_FILTER = filters.ChatType.GROUPS & filters.PHOTO


@dataclass(frozen=True)
class ReceiptOutcome:
    """The result of downloading and reading one receipt photo. Exactly one of:
    `ok` (with `receipt`, `transaction_date`, `date_source`), `unreadable`,
    `unsupported_currency`, `too_large`.
    """

    status: Literal["ok", "unreadable", "unsupported_currency", "too_large"]
    receipt: Receipt | None = None
    transaction_date: date | None = None
    date_source: Literal["receipt", "message"] | None = None
    # Why an `unreadable` outcome happened when it was the reader service, not the
    # photo ("out of API credit", "temporarily down"); `None` for a bad photo.
    problem: str | None = None


async def read_receipt_photo(
    deps: Deps, trip: Trip, bot, file_id: str, message_time: datetime
) -> ReceiptOutcome:
    """Downloads the photo, reads it and validates it into a `Receipt`. Never raises:
    every failure (Telegram download, a bad image, the LLM call, validation) becomes
    an outcome the caller can show (spec §14). Logs without image bytes or bodies.
    """
    try:
        file = await bot.get_file(file_id)
        if file.file_size and file.file_size > MAX_PHOTO_BYTES:
            return ReceiptOutcome("too_large")
        image = prepare_image(bytes(await file.download_as_bytearray()))
    except ImageTooLarge:
        return ReceiptOutcome("too_large")
    except Exception:  # noqa: BLE001 - Telegram or Pillow failure: offer the retry buttons
        logger.exception("receipt photo: could not download or decode the image")
        return ReceiptOutcome("unreadable")

    try:
        extraction = await deps.receipt_reader.read(image, trip=trip)
    except ReceiptServiceUnavailable as exc:
        return ReceiptOutcome("unreadable", problem=exc.reason)
    except ReceiptUnreadable:
        return ReceiptOutcome("unreadable")
    except Exception:  # noqa: BLE001 - the reader should only raise ReceiptUnreadable
        logger.exception("receipt photo: reader failed unexpectedly")
        return ReceiptOutcome("unreadable")

    currency = resolve_receipt_currency(extraction.currency, trip)
    if currency is None:
        return ReceiptOutcome("unsupported_currency")
    try:
        receipt = to_receipt(extraction, currency)
    except ReceiptUnreadable:
        return ReceiptOutcome("unreadable")

    date_source, transaction_date = resolve_transaction_date(receipt, message_time, trip.timezone)
    return ReceiptOutcome("ok", receipt, transaction_date, date_source)


async def photo_expense_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or not message.photo:
        return

    deps = get_deps(context)

    if update.update_id is not None:
        if deps.recent_updates.seen(update.update_id):
            return
        deps.recent_updates.add(update.update_id)

    trip = await require_trip_for_expense(update, context)
    if trip is None:
        # Unbound group: stay silent for a photo, same as plain text (spec §13). A
        # closed trip gets the "This trip is closed..." reply (sprint 9 item 3).
        return

    largest = message.photo[-1]
    if largest.file_size and largest.file_size > MAX_PHOTO_BYTES:
        await message.reply_text(PHOTO_TOO_LARGE_TEXT)
        return

    now = datetime.now(UTC)
    tg_user = TripUser.from_telegram_user(user, now)
    try:
        joined_user = await deps.ledger.upsert_user(trip, tg_user, joined=True)
    except LedgerUnavailable:
        logger.exception("photo_expense: could not join sender")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return

    message_time = message.date or now

    caption_override: tuple[str, Category] | None = None
    participants_text = payer_text = None
    if message.caption:
        bot_username = context.bot.username or ""
        caption, tagged_users = tags_as_text(message, caption=True)
        caption_override = parse_caption_description(
            caption, trip.trip_currency, trip.home_currency, bot_username
        )
        participants_text, payer_text = parse_caption_clauses(caption, bot_username)
        try:
            await join_tagged(deps, trip, tagged_users, now)
        except LedgerUnavailable:
            logger.exception("photo_expense: could not join a tagged user")

    outcome = await read_receipt_photo(deps, trip, context.bot, largest.file_id, message_time)
    if outcome.status == "too_large":
        await message.reply_text(PHOTO_TOO_LARGE_TEXT)
        return
    if outcome.status == "unsupported_currency":
        await message.reply_text(
            unsupported_currency_text(trip.trip_currency, trip.home_currency), parse_mode="HTML"
        )
        return

    common = dict(
        chat_id=chat.id,
        message_id=message.message_id,
        photo_file_id=largest.file_id,
        initiator_id=user.id,
        initiator_name=joined_user.display_name,
        initiator_username=joined_user.username,
        trip=trip,
        caption_override=caption_override,
        participants_text=participants_text,
        payer_text=payer_text,
    )
    try:
        if outcome.status == "unreadable":
            draft = await deps.draft_service.start_unreadable(
                transaction_date=message_time.astimezone(ZoneInfo(trip.timezone)).date(),
                read_problem=outcome.problem,
                **common,
            )
        else:
            assert outcome.receipt is not None and outcome.transaction_date is not None
            assert outcome.date_source is not None
            draft = await deps.draft_service.start_from_receipt(
                receipt=outcome.receipt,
                transaction_date=outcome.transaction_date,
                date_source=outcome.date_source,
                **common,
            )
    except LedgerUnavailable:
        logger.exception("photo_expense: could not start draft")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return

    text, keyboard = render_draft(draft, trip)
    sent = await message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    await deps.draft_service.set_bot_message_id(draft.draft_id, sent.message_id)


async def handle_manual_amount_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """A reply to a receipt's "Reply to this message with the amount" card (spec §8,
    "Enter amount manually"). Returns `True` if the message was such a reply and has
    been handled (including staying silent on an unparseable amount), `False` if it
    is not a reply to a draft awaiting a manual amount from this sender, so the
    caller should treat it as an ordinary message.
    """
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or not message.text:
        return False
    if message.reply_to_message is None:
        return False

    deps = get_deps(context)
    draft = await deps.draft_service.find_by_bot_message(
        chat.id, message.reply_to_message.message_id
    )
    if draft is None or draft.state != "manual_amount" or not draft.awaiting_manual_amount:
        return False
    if user.id != draft.initiator_id:
        return False

    try:
        trip = await deps.ledger.get_binding(chat.id)
    except LedgerUnavailable:
        logger.exception("manual_amount_reply: could not read the current binding")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return True
    if trip is None:
        return True

    parsed = parse_amount_only(message.text, trip.trip_currency, trip.home_currency)
    if parsed is None:
        return True  # not a parseable amount: stay silent, the card still waits
    amount_minor, currency = parsed

    try:
        updated = await deps.draft_service.set_manual_amount(
            draft.draft_id, user.id, trip, amount_minor, currency
        )
    except DraftError:
        logger.exception("manual_amount_reply: draft %s error", draft.draft_id)
        return True

    await edit_draft_card(context, chat.id, updated, trip)
    return True


async def edit_draft_card(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, draft: Draft, trip
) -> None:
    text, keyboard = render_draft(draft, trip)
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=draft.bot_message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception:  # noqa: BLE001 - editing an already-changed message is not fatal
        logger.debug("draft %s: could not edit draft message", draft.draft_id)
