"""Shared test fixtures.

`postgres_pool`/`postgres_store` back the `@pytest.mark.postgres` tests (real
Postgres, spec: docs/sprints/09-neon-store-and-export.md). They are skipped
automatically when no database is reachable at `TEST_DATABASE_URL`, so the rest of
the suite (handler tests, against `InMemoryLedgerStore`) runs with no Docker needed.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio

from app.db import migrate
from app.services.postgres_store import PostgresLedgerStore

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/whopaid_test"
)

_TABLES = (
    "line_items",
    "splits",
    "expenses",
    "trip_users",
    "trips",
    "exchange_rates",
)


def _database_reachable() -> bool:
    async def _check() -> bool:
        try:
            conn = await asyncio.wait_for(asyncpg.connect(TEST_DATABASE_URL), timeout=2.0)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_check())


_REACHABLE = _database_reachable()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _REACHABLE:
        return
    skip = pytest.mark.skip(reason=f"no Postgres reachable at {TEST_DATABASE_URL}")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)


@pytest_asyncio.fixture(scope="session")
async def postgres_pool() -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=5)
    await migrate(pool)
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def postgres_store(postgres_pool: asyncpg.Pool) -> AsyncIterator[PostgresLedgerStore]:
    async with postgres_pool.acquire() as conn:
        for table in _TABLES:
            await conn.execute(f"truncate table {table} restart identity cascade")
    yield PostgresLedgerStore.from_pool(postgres_pool)
