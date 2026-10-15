"""`SendFn`: the seam a `run_<name>` coroutine replies through.

Sprint 10 (docs/sprints/10-start-menu.md, pinned-decisions §19) splits every
command's logic into a `run_<name>(deps, chat, user, send)` coroutine so both the
typed `/command` and a future menu-button press can call it with no duplicated
logic. `send` is how `run_<name>` replies, independent of whether the caller is a
`CommandHandler` (which builds it from `update.effective_message.reply_text`) or a
button press (which, from Phase 2, posts a new plain message in the chat instead).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Message

SendFn = Callable[..., Awaitable[Message]]
"""`async def send(text: str, *, reply_markup=None) -> Message`. Always HTML parse
mode; the caller decides where the reply lands."""

SendDocumentFn = Callable[..., Awaitable[Message]]
"""`async def send_document(document, *, caption=None, read_timeout=None,
write_timeout=None) -> Message`. Only `run_export` needs this: it's the one
command whose reply is a file, not text, so `send` alone isn't enough (sprint 10
pin)."""


def build_send_document(message: Any) -> SendDocumentFn:
    """`run_export`'s sibling to `build_send`: a `send_document` that calls
    `message.reply_document`, forwarding only the kwargs actually given, so a call
    with (say) no `caption` is byte-identical to what `export_command` sent before
    this refactor.
    """

    async def send_document(document: Any, *, caption: str | None = None, **kwargs: Any) -> Message:
        if caption is not None:
            kwargs["caption"] = caption
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
        kwargs: dict[str, Any] = {"parse_mode": "HTML"}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        return await message.reply_text(text, **kwargs)

    return send
