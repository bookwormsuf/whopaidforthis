"""Tests for the `SendFn`/`SendDocumentFn` builders in app/bot/send.py.

`build_chat_send`/`build_chat_send_document` moved here from menu.py on review
(item 5): no behaviour change, just a shared home with `build_send`/
`build_send_document`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.send import build_chat_send, build_chat_send_document


async def test_build_chat_send_omits_reply_markup_when_none() -> None:
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    send = build_chat_send(bot, -100)

    await send("hello")

    bot.send_message.assert_awaited_once_with(-100, "hello", parse_mode="HTML")


async def test_build_chat_send_includes_reply_markup_when_given() -> None:
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    send = build_chat_send(bot, -100)
    markup = object()

    await send("hello", reply_markup=markup)

    bot.send_message.assert_awaited_once_with(-100, "hello", parse_mode="HTML", reply_markup=markup)


async def test_build_chat_send_document_omits_caption_when_none() -> None:
    bot = SimpleNamespace(send_document=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    send_document = build_chat_send_document(bot, -100)
    document = object()

    await send_document(document)

    bot.send_document.assert_awaited_once_with(-100, document=document)


async def test_build_chat_send_document_includes_caption_when_given() -> None:
    bot = SimpleNamespace(send_document=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    send_document = build_chat_send_document(bot, -100)
    document = object()

    await send_document(document, caption="a caption")

    bot.send_document.assert_awaited_once_with(-100, document=document, caption="a caption")
