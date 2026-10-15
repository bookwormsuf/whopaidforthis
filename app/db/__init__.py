"""Postgres pool creation and migrations. No ORM, no Alembic.

Spec references: docs/sprints/09-neon-store-and-export.md ("Store" pins).
Migrations are numbered SQL files in `app/db/migrations/NNN_*.sql`, applied in order
inside one transaction, and recorded in `schema_migrations(version, applied_at)` so a
restart never re-applies one.
"""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_SCHEMA_MIGRATIONS_TABLE = """
create table if not exists schema_migrations (
    version int primary key,
    applied_at timestamptz not null default now()
)
"""


def _migration_files() -> list[tuple[int, Path]]:
    """Every `NNN_*.sql` file in `app/db/migrations`, sorted by its numeric prefix."""
    files = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        version_str = path.stem.split("_", 1)[0]
        files.append((int(version_str), path))
    return sorted(files, key=lambda pair: pair[0])


async def create_pool(url: str) -> asyncpg.Pool:
    """A small connection pool (min 1, max 5) for `DATABASE_URL`."""
    return await asyncpg.create_pool(url, min_size=1, max_size=5)


async def migrate(pool: asyncpg.Pool) -> None:
    """Apply every migration not yet recorded in `schema_migrations`, in order,
    each inside its own transaction.
    """
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA_MIGRATIONS_TABLE)
        applied = {
            row["version"] for row in await conn.fetch("select version from schema_migrations")
        }
        for version, path in _migration_files():
            if version in applied:
                continue
            sql = path.read_text()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("insert into schema_migrations (version) values ($1)", version)
            logger.info("applied migration %s (%s)", version, path.name)
