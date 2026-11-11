"""Tests for app.services.photo_allowance (spec §23, sprint 18): the per-user daily
allowance for receipt photos in a private chat, and the once-per-user notice memory.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.photo_allowance import (
    Allowed,
    AlreadyRead,
    OutOfTries,
    PhotoAllowance,
    PhotoNotices,
)

USER = 111
OTHER = 222
NOON = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)  # 12:00 in Singapore


async def test_a_new_user_may_try() -> None:
    assert await PhotoAllowance().may_try(USER, NOON) is Allowed


async def test_a_success_ends_the_days_allowance() -> None:
    allowance = PhotoAllowance()
    await allowance.record_success(USER, NOON)
    assert await allowance.may_try(USER, NOON) is AlreadyRead


async def test_a_failed_read_can_be_retried_twice_and_the_fourth_attempt_is_refused() -> None:
    allowance = PhotoAllowance()
    for _ in range(3):
        assert await allowance.may_try(USER, NOON) is Allowed
        await allowance.record_failure(USER, NOON)
    assert await allowance.may_try(USER, NOON) is OutOfTries


async def test_a_success_after_a_failure_ends_the_day_with_the_one_a_day_verdict() -> None:
    allowance = PhotoAllowance()
    await allowance.record_failure(USER, NOON)
    assert await allowance.may_try(USER, NOON) is Allowed
    await allowance.record_success(USER, NOON)
    assert await allowance.may_try(USER, NOON) is AlreadyRead


async def test_a_success_on_the_third_try_still_ends_the_day_as_a_read_not_as_tries() -> None:
    allowance = PhotoAllowance()
    await allowance.record_failure(USER, NOON)
    await allowance.record_failure(USER, NOON)
    await allowance.record_success(USER, NOON)
    assert await allowance.may_try(USER, NOON) is AlreadyRead


async def test_asking_counts_nothing_so_a_refused_attempt_counts_nowhere() -> None:
    allowance = PhotoAllowance()
    for _ in range(10):
        assert await allowance.may_try(USER, NOON) is Allowed  # no record: nothing is used up
    await allowance.record_success(USER, NOON)
    for _ in range(10):
        assert await allowance.may_try(USER, NOON) is AlreadyRead
    # still just one success, so the next day is allowed again
    assert await allowance.may_try(USER, datetime(2026, 11, 13, 4, 0, tzinfo=UTC)) is Allowed


async def test_the_next_singapore_day_allows_again() -> None:
    allowance = PhotoAllowance()
    await allowance.record_success(USER, NOON)
    tomorrow = datetime(2026, 11, 13, 4, 0, tzinfo=UTC)
    assert await allowance.may_try(USER, tomorrow) is Allowed
    await allowance.record_failure(USER, tomorrow)
    await allowance.record_failure(USER, tomorrow)
    await allowance.record_failure(USER, tomorrow)
    assert await allowance.may_try(USER, tomorrow) is OutOfTries
    assert await allowance.may_try(USER, datetime(2026, 11, 14, 4, 0, tzinfo=UTC)) is Allowed


async def test_the_day_turns_at_midnight_in_singapore_not_utc() -> None:
    allowance = PhotoAllowance()
    before = datetime(2026, 11, 12, 15, 59, tzinfo=UTC)  # 23:59 in Singapore
    after = datetime(2026, 11, 12, 16, 1, tzinfo=UTC)  # 00:01 the next Singapore day
    await allowance.record_success(USER, before)
    assert await allowance.may_try(USER, before) is AlreadyRead
    assert await allowance.may_try(USER, after) is Allowed
    # and 00:01 UTC (08:01 Singapore) is the same Singapore day as 23:59 UTC the day before
    await allowance.record_success(USER, after)
    assert await allowance.may_try(USER, datetime(2026, 11, 12, 23, 59, tzinfo=UTC)) is AlreadyRead


async def test_another_user_is_unaffected() -> None:
    allowance = PhotoAllowance()
    await allowance.record_success(USER, NOON)
    await allowance.record_failure(OTHER, NOON)
    assert await allowance.may_try(OTHER, NOON) is Allowed
    assert await allowance.may_try(USER, NOON) is AlreadyRead


async def test_the_owner_is_never_refused() -> None:
    allowance = PhotoAllowance()
    await allowance.record_success(USER, NOON)
    for _ in range(5):
        await allowance.record_failure(USER, NOON)
    assert await allowance.may_try(USER, NOON) is AlreadyRead  # as an ordinary user
    assert await allowance.may_try(USER, NOON, is_owner=True) is Allowed


async def test_the_notice_is_taken_once_per_user() -> None:
    notices = PhotoNotices()
    assert notices.take(USER) is True
    assert notices.take(USER) is False
    assert notices.take(OTHER) is True
