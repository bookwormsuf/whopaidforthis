"""Unit tests (no real Postgres needed) for PostgresLedgerStore's error mapping.

Spec reference: sprint 9 review item 4 -- every asyncpg error, not just the
PostgresError (SQL-level) hierarchy, must map to LedgerUnavailable.
"""

from __future__ import annotations

import asyncpg
import pytest

from app.services.ledger_store import LedgerUnavailable
from app.services.postgres_store import PostgresLedgerStore


class _FakeAcquireCtx:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self) -> None:
        raise self._exc

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakePool:
    """A pool whose every `acquire()` raises a given asyncpg error, so a store
    call never retries (the error isn't a connection error) and never touches a
    real database.
    """

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def acquire(self) -> _FakeAcquireCtx:
        return _FakeAcquireCtx(self._exc)


async def test_interface_error_becomes_ledger_unavailable() -> None:
    store = PostgresLedgerStore.from_pool(_FakePool(asyncpg.InterfaceError("pool is closed")))
    with pytest.raises(LedgerUnavailable):
        await store.get_binding(-1)


async def test_internal_client_error_becomes_ledger_unavailable() -> None:
    store = PostgresLedgerStore.from_pool(_FakePool(asyncpg.InternalClientError("internal bug")))
    with pytest.raises(LedgerUnavailable):
        await store.get_binding(-1)
