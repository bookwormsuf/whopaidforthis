"""Tests for app.services.receipt_budget.ReceiptReadBudget (spec §20, sprint 12)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.receipt_budget import Allowed, ChatCapped, GlobalCapped, ReceiptReadBudget

CHAT_A = -100
CHAT_B = -200

NOW = datetime(2026, 10, 16, 4, 0, tzinfo=UTC)  # a plain mid-day instant in UTC


def test_allows_up_to_chat_cap_then_caps() -> None:
    budget = ReceiptReadBudget(chat_cap=3, global_cap=400)
    for _ in range(3):
        assert budget.try_take(CHAT_A, NOW) is Allowed
    assert budget.try_take(CHAT_A, NOW) is ChatCapped


def test_global_cap_wins_over_chat_cap() -> None:
    budget = ReceiptReadBudget(chat_cap=40, global_cap=2)
    assert budget.try_take(CHAT_A, NOW) is Allowed
    assert budget.try_take(CHAT_B, NOW) is Allowed
    # Chat A is nowhere near its own cap, but the global cap is spent.
    assert budget.try_take(CHAT_A, NOW) is GlobalCapped


def test_refused_attempts_do_not_count() -> None:
    budget = ReceiptReadBudget(chat_cap=1, global_cap=400)
    assert budget.try_take(CHAT_A, NOW) is Allowed
    assert budget.try_take(CHAT_A, NOW) is ChatCapped
    assert budget.try_take(CHAT_A, NOW) is ChatCapped
    assert budget.remaining(CHAT_A, NOW) == 0


def test_counts_reset_on_singapore_date_rollover() -> None:
    # 23:59 Singapore time is 15:59 UTC the same day; 00:01 Singapore the next
    # calendar day is 16:01 UTC the same UTC day (Singapore is UTC+8).
    late_in_day = datetime(2026, 10, 16, 15, 59, tzinfo=UTC)
    just_after_midnight = datetime(2026, 10, 16, 16, 1, tzinfo=UTC)

    budget = ReceiptReadBudget(chat_cap=1, global_cap=400)
    assert budget.try_take(CHAT_A, late_in_day) is Allowed
    assert budget.try_take(CHAT_A, late_in_day) is ChatCapped

    # Two UTC minutes later, but a new Singapore calendar day: counters reset.
    assert budget.try_take(CHAT_A, just_after_midnight) is Allowed


def test_remaining_at_zero_midway_and_after_reset() -> None:
    budget = ReceiptReadBudget(chat_cap=3, global_cap=400)
    assert budget.remaining(CHAT_A, NOW) == 3
    budget.try_take(CHAT_A, NOW)
    assert budget.remaining(CHAT_A, NOW) == 2
    budget.try_take(CHAT_A, NOW)
    budget.try_take(CHAT_A, NOW)
    assert budget.remaining(CHAT_A, NOW) == 0
    budget.try_take(CHAT_A, NOW)  # refused, still 0
    assert budget.remaining(CHAT_A, NOW) == 0

    next_day = datetime(2026, 10, 17, 4, 0, tzinfo=UTC)
    assert budget.remaining(CHAT_A, next_day) == 3


def test_zero_caps_refuse_everything() -> None:
    assert ReceiptReadBudget(chat_cap=0, global_cap=400).try_take(CHAT_A, NOW) is ChatCapped
    assert ReceiptReadBudget(chat_cap=40, global_cap=0).try_take(CHAT_A, NOW) is GlobalCapped
    budget = ReceiptReadBudget(chat_cap=0, global_cap=0)
    assert budget.try_take(CHAT_A, NOW) is GlobalCapped
    assert budget.remaining(CHAT_A, NOW) == 0


# -- try_take_global: a private chat takes from the bot-wide count only (sprint 18) ----


def test_try_take_global_allows_up_to_the_global_cap_then_refuses() -> None:
    budget = ReceiptReadBudget(chat_cap=0, global_cap=3)  # a chat cap of 0 does not matter
    for _ in range(3):
        assert budget.try_take_global(NOW) is Allowed
    assert budget.try_take_global(NOW) is GlobalCapped


def test_try_take_global_leaves_every_chat_count_alone() -> None:
    budget = ReceiptReadBudget(chat_cap=2, global_cap=400)
    budget.try_take_global(NOW)
    budget.try_take_global(NOW)
    assert budget.remaining(CHAT_A, NOW) == 2  # no chat was charged
    assert budget.try_take(CHAT_A, NOW) is Allowed and budget.try_take(CHAT_A, NOW) is Allowed
    assert budget.try_take(CHAT_A, NOW) is ChatCapped


def test_private_and_group_reads_share_the_one_global_count() -> None:
    budget = ReceiptReadBudget(chat_cap=40, global_cap=3)
    assert budget.try_take_global(NOW) is Allowed
    assert budget.try_take(CHAT_A, NOW) is Allowed
    assert budget.try_take_global(NOW) is Allowed
    assert budget.try_take(CHAT_B, NOW) is GlobalCapped
    assert budget.try_take_global(NOW) is GlobalCapped


def test_a_refused_global_take_does_not_count() -> None:
    budget = ReceiptReadBudget(chat_cap=40, global_cap=1)
    assert budget.try_take_global(NOW) is Allowed
    for _ in range(3):
        assert budget.try_take_global(NOW) is GlobalCapped
    later = datetime(2026, 10, 17, 4, 0, tzinfo=UTC)  # the next Singapore day
    assert budget.try_take_global(later) is Allowed


def test_try_take_global_resets_on_the_singapore_date_rollover() -> None:
    budget = ReceiptReadBudget(global_cap=1)
    before = datetime(2026, 10, 16, 15, 59, tzinfo=UTC)  # 23:59 in Singapore
    after = datetime(2026, 10, 16, 16, 1, tzinfo=UTC)  # 00:01 the next day
    assert budget.try_take_global(before) is Allowed
    assert budget.try_take_global(before) is GlobalCapped
    assert budget.try_take_global(after) is Allowed


def test_a_global_cap_of_zero_refuses_every_private_read() -> None:
    assert ReceiptReadBudget(global_cap=0).try_take_global(NOW) is GlobalCapped
