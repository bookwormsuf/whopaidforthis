"""`/feedback`: a text message a user sends to the owner through the bot. Spec §21,
docs/sprints/16-feedback.md.

Works in groups and private chats and needs no trip, so it never calls the trip gate.
The text is delivered once and never stored; it must never reach a log call (log lines
here carry the event name only, never the text, a name or the owner's ID).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from telegram import Message, Update
from telegram.ext import ContextTypes
from telegram.ext.filters import MessageFilter

from app.bot.deps import Deps, get_deps
from app.bot.session_state import FeedbackWaitStore
from app.services.feedback import format_owner_message, is_empty, is_too_long

logger = logging.getLogger(__name__)

FEEDBACK_OFF_TEXT = "Feedback isn't set up on this bot."
FEEDBACK_ASK_TEXT = "What's your feedback? Send it as one message."
FEEDBACK_GROUP_HINT_TEXT = "Send it in one line, like <code>/feedback your message</code>."
FEEDBACK_TOO_LONG_TEXT = "Keep it under 1,000 characters."
FEEDBACK_LIMIT_TEXT = "There's a limit of 5 feedback messages a day. Try again tomorrow."
FEEDBACK_SENT_TEXT = "Sent. Thank you."
FEEDBACK_FAILED_TEXT = "Couldn't send that. Try again later."
FEEDBACK_TEXT_ONLY_TEXT = "Text only for now."
FEEDBACK_PROMPT_TEXT = (
    "How was Who Paid For This on this trip? Send <code>/feedback</code> and your message. "
    "It goes straight to the person who built it."
)

_GROUP_CHAT_TYPES = ("group", "supergroup")


class AwaitingFeedback(MessageFilter):
    """True only while the sender is waiting to give feedback. Holds the store directly
    because a `MessageFilter` only receives the `Message`, not the handler `context`.
    """

    def __init__(self, store: FeedbackWaitStore) -> None:
        super().__init__(name="AwaitingFeedback")
        self.store = store

    def filter(self, message: Message) -> bool:
        user = message.from_user
        return user is not None and self.store.is_waiting(user.id)


async def _deliver(
    update: Update, context: ContextTypes.DEFAULT_TYPE, deps: Deps, text: str
) -> bool:
    """Validates and sends `text` to the owner, replying to the sender. Returns `True`
    when the feedback is finished with (sent, refused over the limit, or failed to
    send) and `False` when only the text was refused for length, so a private wait can
    carry on.
    """
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    assert message is not None and chat is not None and user is not None
    assert deps.owner_user_id is not None

    if is_empty(text):
        # Only reachable from a private wait (the command path never passes blank text).
        await message.reply_text(FEEDBACK_ASK_TEXT, parse_mode="HTML")
        return False

    if is_too_long(text):
        await message.reply_text(FEEDBACK_TOO_LONG_TEXT, parse_mode="HTML")
        return False

    now = datetime.now(UTC)
    if not deps.feedback_limiter.is_allowed(user.id, now):
        logger.info("feedback refused: limit")
        await message.reply_text(FEEDBACK_LIMIT_TEXT, parse_mode="HTML")
        return True

    body = format_owner_message(
        first_name=user.first_name,
        username=user.username,
        text=text,
        from_group=chat.type in _GROUP_CHAT_TYPES,
    )
    failed = False
    try:
        await context.bot.send_message(deps.owner_user_id, body, parse_mode="HTML")
    except Exception:  # noqa: BLE001 - any send failure is the same to the user
        # No exc_info: the exception text can carry the owner's chat ID.
        logger.warning("feedback failed")
        failed = True
    if failed:
        # Outside the except block: were this reply to raise, Python would chain the
        # send error (and the owner's chat ID in it) onto it, and the app's error
        # handler logs the chain.
        await message.reply_text(FEEDBACK_FAILED_TEXT, parse_mode="HTML")
        return True

    deps.feedback_limiter.record(user.id, now)
    logger.info("feedback sent")
    await message.reply_text(FEEDBACK_SENT_TEXT, parse_mode="HTML")
    return True


async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if message is None or chat is None or user is None:
        return
    deps = get_deps(context)
    if deps.owner_user_id is None:
        await message.reply_text(FEEDBACK_OFF_TEXT, parse_mode="HTML")
        return

    parts = (message.text or "").split(maxsplit=1)
    text = parts[1].strip() if len(parts) > 1 else ""
    if text:
        await _deliver(update, context, deps, text)
        return

    if chat.type == "private":
        deps.feedback_waits.start(user.id)
        await message.reply_text(FEEDBACK_ASK_TEXT, parse_mode="HTML")
        return
    # In a group a waiting bot would read the next chat line as an expense.
    await message.reply_text(FEEDBACK_GROUP_HINT_TEXT, parse_mode="HTML")


async def feedback_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The next private text message from a user who is waiting."""
    message, user = update.effective_message, update.effective_user
    if message is None or user is None or not message.text:
        return
    deps = get_deps(context)
    if deps.owner_user_id is None or not deps.feedback_waits.is_waiting(user.id):
        return
    if await _deliver(update, context, deps, message.text.strip()):
        deps.feedback_waits.clear(user.id)


async def feedback_non_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A photo or other non-text message while waiting: the wait carries on."""
    message = update.effective_message
    if message is not None:
        await message.reply_text(FEEDBACK_TEXT_ONLY_TEXT, parse_mode="HTML")


async def drop_feedback_wait(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Any command in a private chat ends a feedback wait (`/cancel` included). Runs in
    an early handler group, before the command's own handler, so `/feedback` can start
    a fresh wait afterwards.
    """
    user = update.effective_user
    if user is not None:
        get_deps(context).feedback_waits.clear(user.id)
