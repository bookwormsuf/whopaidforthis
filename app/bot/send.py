"""`SendFn`: the seam a `run_<name>` coroutine replies through.

Sprint 10 (docs/sprints/10-start-menu.md, pinned-decisions §19) splits every
command's logic into a `run_<name>(deps, chat, user, send)` coroutine so both the
typed `/command` and a menu-button press can call it with no duplicated
logic. `send` is how `run_<name>` replies, independent of whether the caller is a
`CommandHandler` (which builds it from `update.effective_message.reply_text`) or a
button press (which posts a new plain message in the chat instead).

`build_send`/`build_send_document` wire a `CommandHandler`'s reply through
`message.reply_text`/`message.reply_document`; `build_chat_send`/
`build_chat_send_document` (review fix, moved here from menu.py) wire a menu
button press's reply through `bot.send_message`/`bot.send_document` on the
chat instead, a new plain message rather than a reply to the button press.
Both pairs share the same kwargs handling below.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Bot, Message

SendFn = Callable[..., Awaitable[Message]]
"""`async def send(text: str, *, reply_markup=None) -> Message`. Always HTML parse
mode; the caller decides where the reply lands."""

SendDocumentFn = Callable[..., Awaitable[Message]]
"""`async def send_document(document, *, caption=None, read_timeout=None,
write_timeout=None) -> Message`. Only `run_export` needs this: it's the one
command whose reply is a file, not text, so `send` alone isn't enough (sprint 10
pin)."""


def _text_kwargs(reply_markup: Any | None) -> dict[str, Any]:
    """Shared by every `SendFn` builder: HTML parse mode always, `reply_markup`
    omitted entirely when `None` rather than passed as `reply_markup=None`, so a
    reply with no keyboard is byte-identical to what each handler sent before
    this refactor (existing tests assert on the exact call).
    """
    kwargs: dict[str, Any] = {"parse_mode": "HTML"}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    return kwargs


def _document_kwargs(caption: str | None, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Shared by every `SendDocumentFn` builder: forwards only the kwargs
    actually given, folding `caption` in when it's not `None`.
    """
    if caption is not None:
        kwargs["caption"] = caption
    return kwargs


def build_send_document(message: Any) -> SendDocumentFn:
    """`run_export`'s sibling to `build_send`: a `send_document` that calls
    `message.reply_document`, forwarding only the kwargs actually given, so a call
    with (say) no `caption` is byte-identical to what `export_command` sent before
    this refactor.
    """

    async def send_document(document: Any, *, caption: str | None = None, **kwargs: Any) -> Message:
        kwargs = _document_kwargs(caption, kwargs)
        return await message.reply_document(document=document, **kwargs)

    return send_document


def build_send(message: Any) -> SendFn:
    """The thin-wrapper adapter every typed `<name>_command` uses: a `send` that
    calls `message.reply_text`, parse mode HTML always. Omits `reply_markup`
    entirely when none is given, rather than passing `reply_markup=None`, so a
    reply with no keyboard is byte-identical to what the handler sent before this
    refactor (existing tests assert on the exact call).
    """

    async def send(text: str, *, reply_markup: Any | None = None) -> Message:
        return await message.reply_text(text, **_text_kwargs(reply_markup))

    return send


def build_chat_send(bot: Bot, chat_id: int) -> SendFn:
    """A `SendFn` that posts a new plain message in the chat (not a reply),
    for a menu button press (sprint 10 pin; moved here from menu.py on review).
    """

    async def send(text: str, *, reply_markup: Any | None = None) -> Message:
        return await bot.send_message(chat_id, text, **_text_kwargs(reply_markup))

    return send


def build_chat_send_document(bot: Bot, chat_id: int) -> SendDocumentFn:
    """`run_export`'s sibling to `build_chat_send`, mirroring `build_send_document`
    (moved here from menu.py on review)."""

    async def send_document(document: Any, *, caption: str | None = None, **kwargs: Any) -> Message:
        kwargs = _document_kwargs(caption, kwargs)
        return await bot.send_document(chat_id, document=document, **kwargs)

    return send_document
