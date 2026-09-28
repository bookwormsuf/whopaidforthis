"""The `LedgerStore` interface and an in-memory implementation for tests.

Spec references: pinned-decisions §10.
"""

from __future__ import annotations

from typing import Protocol

from app.models.trip import Trip
from app.models.user import TripUser


class LedgerUnavailable(RuntimeError):
    """The ledger backend (Google Sheets) could not be reached or refused a call.

    Message must never include a Sheet URL, Sheet ID, or Sheet contents. `status`
    carries the HTTP status code when one is known (e.g. 429, 500), else None.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# The one user-facing line for a LedgerUnavailable, shared by every caller (handlers
# that catch it locally, and the PTB-wide error handler as the last resort).
LEDGER_UNAVAILABLE_TEXT = "Couldn't reach Google Sheets, please try again."


class LedgerStore(Protocol):
    """Persistence for trip bindings, trip facts, and trip users."""

    async def get_binding(self, chat_id: int) -> Trip | None: ...

    async def save_binding(self, trip: Trip) -> None: ...

    async def can_open(self, sheet_id: str) -> bool: ...

    async def initialise_trip_sheet(self, trip: Trip) -> None: ...

    async def upsert_user(self, trip: Trip, user: TripUser, joined: bool | None) -> TripUser: ...

    async def list_users(self, trip: Trip, joined_only: bool = False) -> list[TripUser]: ...


def merge_user(existing: TripUser | None, incoming: TripUser, joined: bool | None) -> TripUser:
    """Pure merge logic shared by every LedgerStore implementation's `upsert_user`.

    `incoming` carries the freshly observed username/display name and a `last_seen`
    timestamp (built via `TripUser.from_telegram_user`). `joined=None` means "keep
    whatever joined flag the user already has"; a brand-new user with joined=None
    starts out not joined.
    """
    if existing is None:
        return TripUser(
            user_id=incoming.user_id,
            username=incoming.username,
            display_name=incoming.display_name,
            first_seen=incoming.last_seen,
            last_seen=incoming.last_seen,
            joined=bool(joined) if joined is not None else False,
        )
    return TripUser(
        user_id=existing.user_id,
        username=incoming.username,
        display_name=incoming.display_name,
        first_seen=existing.first_seen,
        last_seen=incoming.last_seen,
        joined=existing.joined if joined is None else joined,
    )


class InMemoryLedgerStore:
    """In-memory LedgerStore for tests and for use before Sheets wiring exists.

    `unopenable_sheet_ids` simulates Sheets that exist but are not shared with the
    service account, for testing the `/settrip` access-check path.
    """

    def __init__(self, unopenable_sheet_ids: frozenset[str] = frozenset()) -> None:
        self._unopenable_sheet_ids = unopenable_sheet_ids
        self._bindings: dict[int, Trip] = {}
        self._users: dict[str, dict[int, TripUser]] = {}
        self.initialised_sheet_ids: set[str] = set()

    async def get_binding(self, chat_id: int) -> Trip | None:
        return self._bindings.get(chat_id)

    async def save_binding(self, trip: Trip) -> None:
        self._bindings[trip.chat_id] = trip

    async def can_open(self, sheet_id: str) -> bool:
        return sheet_id not in self._unopenable_sheet_ids

    async def initialise_trip_sheet(self, trip: Trip) -> None:
        self.initialised_sheet_ids.add(trip.sheet_id)
        self._users.setdefault(trip.sheet_id, {})

    async def upsert_user(self, trip: Trip, user: TripUser, joined: bool | None) -> TripUser:
        users = self._users.setdefault(trip.sheet_id, {})
        existing = users.get(user.user_id)
        merged = merge_user(existing, user, joined)
        users[user.user_id] = merged
        return merged

    async def list_users(self, trip: Trip, joined_only: bool = False) -> list[TripUser]:
        users = self._users.get(trip.sheet_id, {}).values()
        if joined_only:
            return [user for user in users if user.joined]
        return list(users)
