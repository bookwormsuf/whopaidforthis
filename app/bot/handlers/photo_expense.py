"""The photo-expense handler: a receipt photo -> a draft card.

Spec references: pinned-decisions §7, §8, §13; sprint 4 pins. Registered as a
`MessageHandler` in group 0, alongside the text-expense handler. Also exposes
`retry_receipt_read`, used by `app.bot.handlers.text_expense.draft_callback` for the
`retry` action (re-downloading the same photo by file_id, per spec §7: bytes must
not outlive the request, so a retry re-fetches rather than keeping a copy).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes, filters

from app.bot.deps import Deps, get_deps
from app.bot.formatting import unsupported_currency_text
from app.bot.render.draft import render_draft
from app.models.user import TripUser
from app.services.drafts import Draft, DraftError, DuplicateMessage
from app.services.expense_parser import parse_amount_only, parse_caption_description
from app.services.images import ImageTooLarge, PreparedImage, prepare_image
from app.services.ledger_store import (
    LEDGER_UNAVAILABLE_TEXT,
    LedgerUnavailable,
    TripSheetInaccessible,
)
from app.services.receipt_parser import (
    ReceiptUnreadable,
    resolve_receipt_currency,
    resolve_transaction_date,
)

logger = logging.getLogger(__name__)

MAX_PHOTO_BYTES = 10 * 1024 * 1024
PHOTO_TOO_LARGE_TEXT = "That photo's too large (over 10 MB), please send a smaller one."

PHOTO_EXPENSE_FILTER = filters.ChatType.GROUPS & filters.PHOTO


class _ReplyToBot(filters.MessageFilter):
    """True if this message replies to a message sent by the bot itself -- narrows
    the manual-amount reply handler so it never swallows a reply to someone else's
    message (which should still be free to parse as a normal text expense).
    """

    def filter(self, message) -> bool:
        reply = message.reply_to_message
        return bool(reply is not None and reply.from_user is not None and reply.from_user.is_bot)


MANUAL_AMOUNT_REPLY_FILTER = (
    filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND & _ReplyToBot()
)


async def _download_prepared_image(bot, file_id: str) -> PreparedImage:
    file = await bot.get_file(file_id)
    if file.file_size and file.file_size > MAX_PHOTO_BYTES:
        raise ImageTooLarge(f"file is {file.file_size} bytes")
    data = bytes(await file.download_as_bytearray())
    return prepare_image(data)


async def retry_receipt_read(deps: Deps, trip, bot, draft: Draft) -> dict:
    """Re-downloads `draft.photo_file_id` and re-runs the LLM read. Returns a dict of
    kwargs for `DraftService.retry_receipt`, or `{"unreadable": True}` on failure.
    Never raises; the caller (draft_callback) decides which DraftService method to call.
    """
    assert draft.photo_file_id is not None
    try:
        image = await _download_prepared_image(bot, draft.photo_file_id)
        extraction = await deps.receipt_reader.read(image, trip=trip)
    except (ImageTooLarge, ReceiptUnreadable):
        return {"unreadable": True}

    currency = resolve_receipt_currency(extraction.currency, trip)
    if currency is None:
        return {"unreadable": True}

    date_source, transaction_date = resolve_transaction_date(
        extraction, datetime.now(UTC), trip.timezone
    )
    return {
        "unreadable": False,
        "extraction": extraction,
        "currency": currency,
        "transaction_date": transaction_date,
        "date_source": date_source,
    }


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

    try:
        trip = await deps.ledger.get_binding(chat.id)
    except TripSheetInaccessible:
        logger.info("photo_expense: trip Sheet inaccessible, chat_id=%s", chat.id)
        return
    except LedgerUnavailable:
        logger.exception("photo_expense: could not read the current binding")
        return
    if trip is None:
        return  # unbound group: stay silent for a photo, same as plain text

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
    transaction_date_fallback = message_time.astimezone(ZoneInfo(trip.timezone)).date()

    try:
        image = await _download_prepared_image(context.bot, largest.file_id)
    except ImageTooLarge:
        await message.reply_text(PHOTO_TOO_LARGE_TEXT)
        return

    caption_parsed = None
    if message.caption:
        bot_username = context.bot.username or ""
        caption_parsed = parse_caption_description(message.caption, bot_username)

    try:
        extraction = await deps.receipt_reader.read(image, trip=trip)
    except ReceiptUnreadable:
        try:
            draft = await deps.draft_service.start_unreadable(
                chat_id=chat.id,
                message_id=message.message_id,
                photo_file_id=largest.file_id,
                initiator_id=user.id,
                initiator_name=joined_user.display_name,
                initiator_username=joined_user.username,
                trip=trip,
                transaction_date=transaction_date_fallback,
            )
        except DuplicateMessage:
            logger.info("photo_expense: duplicate (chat, message) skipped")
            return
        except LedgerUnavailable:
            logger.exception("photo_expense: could not start draft")
            await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
            return
        await _send_draft(message, deps, draft, trip)
        return

    currency = resolve_receipt_currency(extraction.currency, trip)
    if currency is None:
        await message.reply_text(
            unsupported_currency_text(trip.trip_currency, trip.home_currency), parse_mode="HTML"
        )
        return

    date_source, transaction_date = resolve_transaction_date(
        extraction, message_time, trip.timezone
    )

    try:
        draft = await deps.draft_service.start_from_receipt(
            chat_id=chat.id,
            message_id=message.message_id,
            photo_file_id=largest.file_id,
            initiator_id=user.id,
            initiator_name=joined_user.display_name,
            initiator_username=joined_user.username,
            trip=trip,
            extraction=extraction,
            currency=currency,
            transaction_date=transaction_date,
            date_source=date_source,
            caption_parsed=caption_parsed,
        )
    except DuplicateMessage:
        logger.info("photo_expense: duplicate (chat, message) skipped")
        return
    except (LedgerUnavailable, ReceiptUnreadable):
        logger.exception("photo_expense: could not start draft")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return
    except DraftError:
        logger.exception("photo_expense: draft error")
        return

    await _send_draft(message, deps, draft, trip)


async def _send_draft(message, deps: Deps, draft: Draft, trip) -> None:
    text, keyboard = render_draft(draft, trip)
    sent = await message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    await deps.draft_service.set_bot_message_id(draft.draft_id, sent.message_id)


async def manual_amount_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A reply to the bot's `manual_amount` card with a plain amount (spec §8:
    "Enter amount manually"). Silently ignored unless it is a reply to a draft
    currently awaiting a manual amount from its own initiator.
    """
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or not message.text:
        return
    if message.reply_to_message is None:
        return

    deps = get_deps(context)
    draft = await deps.draft_service.find_by_bot_message(
        chat.id, message.reply_to_message.message_id
    )
    if draft is None or draft.state != "manual_amount" or not draft.awaiting_manual_amount:
        return
    if user.id != draft.initiator_id:
        return

    try:
        trip = await deps.ledger.get_binding(chat.id)
    except (TripSheetInaccessible, LedgerUnavailable):
        return
    if trip is None:
        return

    parsed = parse_amount_only(message.text, trip.trip_currency, trip.home_currency)
    if parsed is None:
        return  # not a parseable amount: stay silent
    amount_minor, currency = parsed

    try:
        updated = await deps.draft_service.set_manual_amount(
            draft.draft_id, user.id, trip, amount_minor, currency
        )
    except DraftError:
        logger.exception("manual_amount_reply: draft error")
        return

    text, keyboard = render_draft(updated, trip)
    try:
        await context.bot.edit_message_text(
            chat_id=chat.id,
            message_id=draft.bot_message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception:  # noqa: BLE001 - editing an already-changed message is not fatal
        logger.debug("manual_amount_reply: could not edit draft message")
