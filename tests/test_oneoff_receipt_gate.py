"""The one-off receipt gate (spec §23, sprint 18, amended at review): the order of its
steps and what counts at each early exit. No Telegram objects anywhere.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.bot.oneoff_receipt_gate import ReceiptGate, Refusal, Ticket
from app.services.photo_allowance import InFlightReads, PhotoAllowance
from app.services.receipt_budget import ReceiptReadBudget

NOW = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)
USER = 1
OWNER = 99


def make_gate(*, global_cap: int = 400):
    allowance, budget, in_flight = (
        PhotoAllowance(),
        ReceiptReadBudget(global_cap=global_cap),
        InFlightReads(),
    )
    gate = ReceiptGate(allowance=allowance, budget=budget, in_flight=in_flight, owner_user_id=OWNER)
    return gate, allowance, budget, in_flight


def totals(allowance: PhotoAllowance, budget: ReceiptReadBudget) -> tuple[int, int, int]:
    return budget._global_count, allowance._failures.get(USER, 0), allowance._successes.get(USER, 0)


async def test_the_full_order_for_a_successful_read():
    gate, allowance, budget, in_flight = make_gate()
    ticket = await gate.begin(USER, NOW)
    assert isinstance(ticket, Ticket) and USER in in_flight  # marked
    assert totals(allowance, budget) == (0, 0, 0)  # nothing counts yet
    assert gate.take_cap(ticket, NOW) is None  # just before the model call
    assert totals(allowance, budget) == (1, 0, 0)
    await gate.record(ticket, NOW, success=True)
    assert totals(allowance, budget) == (1, 0, 1)
    assert USER in in_flight  # still marked until released
    gate.release(ticket)
    assert USER not in in_flight


async def test_a_failure_is_one_try():
    gate, allowance, budget, in_flight = make_gate()
    ticket = await gate.begin(USER, NOW)
    gate.take_cap(ticket, NOW)
    await gate.record(ticket, NOW, success=False)
    gate.release(ticket)
    assert totals(allowance, budget) == (1, 1, 0)


async def test_the_user_is_marked_before_the_first_await():
    """A second `begin` made before the first has yielded still meets the mark."""
    seen: list[bool] = []

    class Slow(PhotoAllowance):
        async def may_try(self, user_id, now, *, is_owner=False):
            seen.append(user_id in in_flight)  # marked already when the allowance runs
            await asyncio.sleep(0)
            return await super().may_try(user_id, now, is_owner=is_owner)

    in_flight = InFlightReads()
    gate = ReceiptGate(
        allowance=Slow(), budget=ReceiptReadBudget(), in_flight=in_flight, owner_user_id=None
    )
    first, second = await asyncio.gather(gate.begin(USER, NOW), gate.begin(USER, NOW))
    assert isinstance(first, Ticket) and second == Refusal("busy")
    assert seen == [True]  # the allowance was asked once


async def test_busy_counts_nowhere_and_leaves_the_first_attempts_mark():
    gate, allowance, budget, in_flight = make_gate()
    first = await gate.begin(USER, NOW)
    assert await gate.begin(USER, NOW) == Refusal("busy")
    assert totals(allowance, budget) == (0, 0, 0)
    assert USER in in_flight  # the busy refusal did not clear the first one's mark
    gate.release(first)
    assert USER not in in_flight


async def test_already_read_counts_nowhere_and_leaves_no_mark():
    gate, allowance, budget, in_flight = make_gate()
    await allowance.record_success(USER, NOW)
    assert await gate.begin(USER, NOW) == Refusal("user")
    assert totals(allowance, budget) == (0, 0, 1) and USER not in in_flight


async def test_out_of_tries_counts_nowhere_and_leaves_no_mark():
    gate, allowance, budget, in_flight = make_gate()
    for _ in range(3):
        await allowance.record_failure(USER, NOW)
    assert await gate.begin(USER, NOW) == Refusal("tries")
    assert totals(allowance, budget) == (0, 3, 0) and USER not in in_flight


async def test_a_capped_attempt_takes_and_records_nothing():
    gate, allowance, budget, in_flight = make_gate(global_cap=0)
    ticket = await gate.begin(USER, NOW)
    assert isinstance(ticket, Ticket)
    assert gate.take_cap(ticket, NOW) == Refusal("global")
    gate.release(ticket)
    assert totals(allowance, budget) == (0, 0, 0) and USER not in in_flight


async def test_the_owner_is_never_refused_by_the_allowance_but_is_by_the_cap():
    gate, allowance, budget, in_flight = make_gate(global_cap=1)
    await allowance.record_success(OWNER, NOW)
    for _ in range(5):
        await allowance.record_failure(OWNER, NOW)
    ticket = await gate.begin(OWNER, NOW)
    assert isinstance(ticket, Ticket) and ticket.is_owner
    assert gate.take_cap(ticket, NOW) is None
    gate.release(ticket)
    again = await gate.begin(OWNER, NOW)
    assert isinstance(again, Ticket)
    assert gate.take_cap(again, NOW) == Refusal("global")  # the bot-wide cap binds the owner too


async def test_with_no_owner_nobody_is_exempt():
    allowance = PhotoAllowance()
    await allowance.record_success(USER, NOW)
    gate = ReceiptGate(
        allowance=allowance,
        budget=ReceiptReadBudget(),
        in_flight=InFlightReads(),
        owner_user_id=None,
    )
    assert await gate.begin(USER, NOW) == Refusal("user")


async def test_release_is_safe_twice_and_does_not_clear_a_later_attempts_mark():
    gate, _allowance, _budget, in_flight = make_gate()
    first = await gate.begin(USER, NOW)
    gate.release(first)
    second = await gate.begin(USER, NOW)
    assert isinstance(second, Ticket)
    gate.release(first)  # a stale second release
    assert USER in in_flight  # the second attempt's mark is still there
    gate.release(second)
    assert USER not in in_flight


async def test_an_allowance_that_raises_leaves_no_mark():
    class Broken(PhotoAllowance):
        async def may_try(self, user_id, now, *, is_owner=False):
            raise RuntimeError("database is down")

    in_flight = InFlightReads()
    gate = ReceiptGate(
        allowance=Broken(), budget=ReceiptReadBudget(), in_flight=in_flight, owner_user_id=None
    )
    with pytest.raises(RuntimeError):
        await gate.begin(USER, NOW)
    assert USER not in in_flight


async def test_another_user_is_not_blocked_by_a_running_read():
    gate, _allowance, _budget, in_flight = make_gate()
    first = await gate.begin(USER, NOW)
    other = await gate.begin(2, NOW)
    assert isinstance(other, Ticket)
    gate.release(first)
    gate.release(other)
    assert not in_flight._users
