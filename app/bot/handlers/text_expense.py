"""The text-expense handler: `1200 yen ramen` -> a draft card.

Spec references: pinned-decisions §6, §8, §10, §13; sprint 2 pins. Registered as a
`MessageHandler` in group 0, AFTER the settrip awaiting-text handler, so a session
mid-`/settrip` still wins.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes, filters

from app.bot.deps import get_deps
from app.bot.formatting import unsupported_currency_text
from app.bot.handlers.photo_expense import handle_manual_amount_reply, read_receipt_photo
from app.bot.handlers.split import handle_custom_split_reply, handle_item_reply
from app.bot.handlers.tags import join_tagged, tags_as_text
from app.bot.render.draft import render_draft
from app.models.user import TripUser
from app.services.drafts import (
    ALREADY_CANCELLED_TEXT,
    ALREADY_SAVED_TEXT,
    EXPIRED_TEXT,
    AlreadyCancelled,
    AlreadySaved,
    DraftError,
    DraftExpired,
    DraftToast,
    DuplicateMessage,
    NotInitiator,
)
from app.services.expense_parser import parse_expense
from app.services.ledger_store import (
    LEDGER_UNAVAILABLE_TEXT,
    LedgerUnavailable,
    TripSheetInaccessible,
)

__all__ = [
    "DRAFT_CALLBACK_PATTERN",
    "TEXT_EXPENSE_FILTER",
    "draft_callback",
    "text_expense_handler",
    "unsupported_currency_text",
]

# ULID alphabet (Crockford base32, no I/L/O/U); cannot collide with "st:" or "join:".
DRAFT_CALLBACK_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}:"

logger = logging.getLogger(__name__)

# A candidate expense message: group chat, plain text, not a command, has a digit
# somewhere (the final "an amount was found and <=12 words" gate is enforced inside
# parse_expense itself; this filter just avoids calling it on obviously-irrelevant
# messages).
TEXT_EXPENSE_FILTER = (
    filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND & filters.Regex(r"\d")
)


async def text_expense_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or not message.text:
        return

    deps = get_deps(context)

    # Replayed-update guard (spec §10): checked before anything else, including the
    # unbound-chat check, since a replay could otherwise double-join or double-reply.
    if update.update_id is not None:
        if deps.recent_updates.seen(update.update_id):
            return
        deps.recent_updates.add(update.update_id)

    # A reply carrying the amount for a receipt's "Enter amount manually" card is
    # that draft's amount, not a new expense (sprint 4). Any other reply falls
    # through and parses as usual.
    if await handle_manual_amount_reply(update, context):
        return
    # Likewise a reply to a custom-split card carries `<name> <amount>` lines, and a
    # reply to an item card carries `mine 1,4`-style shortcuts.
    if await handle_custom_split_reply(update, context):
        return
    if await handle_item_reply(update, context):
        return

    try:
        trip = await deps.ledger.get_binding(chat.id)
    except TripSheetInaccessible:
        # Stay silent for plain text (spec §13); the commands (/join, /settrip, the
        # unbound gate) are where a user finds out and gets a fix to run.
        logger.info("text_expense: trip Sheet inaccessible, chat_id=%s", chat.id)
        return
    except LedgerUnavailable:
        logger.exception("text_expense: could not read the current binding")
        return  # unbound/unavailable: stay silent for plain text (spec §13)
    if trip is None:
        return  # unbound group: stay silent for plain text, unlike a command

    bot_username = context.bot.username or ""
    text, tagged_users = tags_as_text(message)
    parsed = parse_expense(text, trip.trip_currency, trip.home_currency, bot_username)
    if parsed is None:
        return  # not an expense candidate: stay silent (spec §6)
    if parsed.error == "unsupported_currency":
        await message.reply_text(
            unsupported_currency_text(trip.trip_currency, trip.home_currency),
            parse_mode="HTML",
        )
        return

    now = datetime.now(UTC)
    tg_user = TripUser.from_telegram_user(user, now)
    try:
        # Sending an expense joins the sender (spec §3, added this sprint).
        joined_user = await deps.ledger.upsert_user(trip, tg_user, joined=True)
        await join_tagged(deps, trip, tagged_users, now)
    except LedgerUnavailable:
        logger.exception("text_expense: could not join sender")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return

    message_time = message.date or now
    transaction_date = message_time.astimezone(ZoneInfo(trip.timezone)).date()

    try:
        draft = await deps.draft_service.start(
            chat_id=chat.id,
            message_id=message.message_id,
            initiator_id=user.id,
            initiator_name=joined_user.display_name,
            initiator_username=joined_user.username,
            parsed=parsed,
            trip=trip,
            transaction_date=transaction_date,
        )
    except DuplicateMessage:
        logger.info("text_expense: duplicate (chat, message) skipped")
        return
    except LedgerUnavailable:
        logger.exception("text_expense: could not start draft")
        await message.reply_text(LEDGER_UNAVAILABLE_TEXT, parse_mode="HTML")
        return

    text, keyboard = render_draft(draft, trip)
    sent = await message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    await deps.draft_service.set_bot_message_id(draft.draft_id, sent.message_id)


async def draft_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Every draft button: `"{draft_id}:{action}:{arg}"` (spec §8)."""
    query = update.callback_query
    assert query is not None
    answered = False

    async def answer(text: str | None = None) -> None:
        # A callback query can be answered only once; the `retry` action answers
        # early, before its slow read, so every later answer becomes a no-op.
        nonlocal answered
        if not answered:
            answered = True
            await query.answer(text)

    data = query.data or ""
    parts = data.split(":", 2)
    if len(parts) != 3:
        await answer()
        return
    draft_id, action, _arg = parts

    deps = get_deps(context)
    presser = query.from_user
    chat = update.effective_chat
    if presser is None or chat is None:
        await answer()
        return

    try:
        trip = await deps.ledger.get_binding(chat.id)
    except TripSheetInaccessible:
        logger.info("draft %s: trip Sheet inaccessible", draft_id)
        await answer()
        return
    except LedgerUnavailable:
        logger.exception("draft %s: could not read the current binding", draft_id)
        await answer(LEDGER_UNAVAILABLE_TEXT)
        return
    if trip is None:
        await answer()
        return

    try:
        if action == "justme":
            draft = await deps.draft_service.choose_just_me(draft_id, presser.id)
        elif action == "confirm":
            draft = await deps.draft_service.confirm(draft_id, presser.id)
        elif action == "cancel":
            draft = await deps.draft_service.cancel(draft_id, presser.id)
        elif action == "retry_rate":
            draft = await deps.draft_service.retry_rate(draft_id, presser.id)
        elif action == "total":
            try:
                index = int(_arg)
            except ValueError:
                await answer()
                return
            draft = await deps.draft_service.choose_total(draft_id, presser.id, index)
        elif action == "usetotal":
            draft = await deps.draft_service.use_total_anyway(draft_id, presser.id)
        elif action == "retry":
            pending = await deps.draft_service.begin_retry(draft_id, presser.id)
            # Answer now: the read can take far longer than a callback query lives.
            await answer("Reading the receipt again…")
            outcome = await read_receipt_photo(
                deps, trip, context.bot, pending.photo_file_id, datetime.now(UTC)
            )
            if outcome.status == "ok":
                assert outcome.receipt is not None and outcome.transaction_date is not None
                assert outcome.date_source is not None
                draft = await deps.draft_service.retry_receipt(
                    draft_id,
                    presser.id,
                    trip=trip,
                    receipt=outcome.receipt,
                    transaction_date=outcome.transaction_date,
                    date_source=outcome.date_source,
                )
            else:
                draft = await deps.draft_service.retry_unreadable(
                    draft_id, presser.id, read_problem=outcome.problem
                )
        elif action == "manual":
            draft = await deps.draft_service.request_manual_amount(draft_id, presser.id)
        elif action == "everyone":
            draft = await deps.draft_service.choose_everyone(draft_id, presser.id)
        elif action == "choose":
            draft = await deps.draft_service.choose_people(draft_id, presser.id)
        elif action in ("toggle", "pick"):
            try:
                user_id = int(_arg)
            except ValueError:
                await answer()
                return
            if action == "toggle":
                draft = await deps.draft_service.toggle_participant(draft_id, presser.id, user_id)
            else:
                draft = await deps.draft_service.pick_name(draft_id, presser.id, user_id)
        elif action == "unsure":
            draft = await deps.draft_service.name_unsure(draft_id, presser.id)
        elif action == "next":
            draft = await deps.draft_service.participants_next(draft_id, presser.id)
        elif action == "equal":
            draft = await deps.draft_service.choose_equal(draft_id, presser.id)
        elif action == "custom":
            draft = await deps.draft_service.choose_custom(draft_id, presser.id)
        elif action == "customok":
            draft = await deps.draft_service.finish_custom(draft_id, presser.id)
        elif action == "edit":
            draft = await deps.draft_service.edit(draft_id, presser.id)
        elif action == "byitems":
            draft = await deps.draft_service.choose_by_items(draft_id, presser.id)
        elif action == "item":
            try:
                index = int(_arg)
            except ValueError:
                await answer()
                return
            draft = await deps.draft_service.toggle_item(draft_id, presser.id, index)
        elif action == "payer":
            try:
                user_id = int(_arg)
            except ValueError:
                await answer()
                return
            draft = await deps.draft_service.set_payer(draft_id, presser.id, user_id)
        elif action == "for":
            try:
                user_id = int(_arg)
            except ValueError:
                await answer()
                return
            draft = await deps.draft_service.set_claiming_for(draft_id, presser.id, user_id)
        elif action == "rest":
            draft = await deps.draft_service.rest_to_everyone(draft_id, presser.id)
        elif action == "done":
            draft = await deps.draft_service.items_done(draft_id, presser.id)
        elif action == "fix":
            draft = await deps.draft_service.fix_items(draft_id, presser.id)
        else:
            await answer()
            return
    except DraftExpired:
        await answer()
        try:
            await query.edit_message_text(EXPIRED_TEXT, parse_mode="HTML")
        except Exception:  # noqa: BLE001 - editing an already-changed message is not fatal
            logger.debug("draft %s: could not edit expired draft message", draft_id)
        return
    except NotInitiator as exc:
        await answer(f"Only {exc.initiator_name} can edit this")
        return
    except AlreadySaved:
        await answer(ALREADY_SAVED_TEXT)
        return
    except AlreadyCancelled:
        await answer(ALREADY_CANCELLED_TEXT)
        return
    except DraftToast as exc:
        await answer(exc.text)
        return
    except LedgerUnavailable:
        logger.exception("draft %s: ledger unavailable", draft_id)
        await answer(LEDGER_UNAVAILABLE_TEXT)
        return
    except DraftError:
        logger.exception("draft %s: draft error", draft_id)
        await answer()
        return

    await answer()
    text, keyboard = render_draft(draft, trip)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
