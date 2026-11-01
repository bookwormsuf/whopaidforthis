"""`python -m app.announce`: one message to every group with an open trip."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import pytest
from telegram.error import BadRequest, Forbidden, NetworkError

from app.announce import announce, message_from
from app.models.trip import Trip
from app.services.ledger_store import InMemoryLedgerStore


async def _no_pause() -> None:
    return None


async def test_sends_to_each_chat_and_counts() -> None:
    sent: list[tuple[int, str]] = []

    async def send(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    result = await announce([-1, -2, -3], "hi", send, pause=_no_pause)
    assert sent == [(-1, "hi"), (-2, "hi"), (-3, "hi")]
    assert (result.groups, result.sent, result.bot_removed, result.failed) == (3, 3, 0, 0)


async def test_a_group_that_removed_the_bot_or_fails_does_not_stop_the_rest() -> None:
    sent: list[int] = []

    async def send(chat_id: int, text: str) -> None:
        if chat_id == -1:
            raise Forbidden("Forbidden: bot was kicked from the group chat")
        if chat_id == -2:
            raise NetworkError("timed out")
        sent.append(chat_id)

    result = await announce([-1, -2, -3], "hi", send, pause=_no_pause)
    assert sent == [-3]
    assert (result.sent, result.bot_removed, result.failed) == (1, 1, 1)


async def test_malformed_html_stops_before_the_next_group() -> None:
    sent: list[int] = []

    async def send(chat_id: int, text: str) -> None:
        sent.append(chat_id)
        raise BadRequest("Can't parse entities: unclosed start tag")

    with pytest.raises(BadRequest):
        await announce([-1, -2], "<b>hi", send, pause=_no_pause)
    assert sent == [-1]


async def test_pauses_between_sends_only() -> None:
    pauses = 0

    async def pause() -> None:
        nonlocal pauses
        pauses += 1

    async def send(chat_id: int, text: str) -> None:
        return None

    await announce([-1, -2, -3], "hi", send, pause=pause)
    assert pauses == 2


def test_text_turns_backslash_n_into_new_lines(tmp_path) -> None:
    args = argparse.Namespace(file=None, text="<b>New</b>\\n• one\\n• two")
    assert message_from(args) == "<b>New</b>\n• one\n• two"
    path = tmp_path / "msg.txt"
    path.write_text("  line one\nline two\n\n", encoding="utf-8")
    assert message_from(argparse.Namespace(file=str(path), text=None)) == "line one\nline two"
    with pytest.raises(SystemExit):
        message_from(argparse.Namespace(file=None, text="   "))


async def test_in_memory_store_lists_only_open_trips() -> None:
    store = InMemoryLedgerStore()
    now = datetime.now(UTC)

    def trip(chat_id: int) -> Trip:
        return Trip(
            chat_id=chat_id,
            name="T",
            trip_currency="THB",
            home_currency="SGD",
            timezone="Asia/Bangkok",
            created_at=now,
        )

    await store.save_binding(trip(-2))
    closed = await store.save_binding(trip(-1))
    await store.close_trip(closed)
    await store.save_binding(trip(-3))
    assert await store.list_open_trip_chat_ids() == [-3, -2]
