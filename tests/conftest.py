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
from urllib.parse import urlsplit

import asyncpg
import pytest
import pytest_asyncio

from app.db import migrate
from app.services.postgres_store import PostgresLedgerStore

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/whopaid_test"
)


def _host_and_port() -> str:
    """Host and port only, from TEST_DATABASE_URL -- never the credentials or
    database name, even in a CI failure message (sprint 9 review item 5).
    """
    parts = urlsplit(TEST_DATABASE_URL)
    return f"{parts.hostname}:{parts.port}"


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
    has_postgres_tests = any("postgres" in item.keywords for item in items)
    if has_postgres_tests and os.environ.get("CI"):
        # In CI the Postgres service is expected to be up; a skip here would
        # silently drop store coverage from every run (sprint 9 review item 5).
        raise pytest.UsageError(f"no Postgres reachable at {_host_and_port()} (CI=1)")
    skip = pytest.mark.skip(reason=f"no Postgres reachable at {_host_and_port()}")
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
