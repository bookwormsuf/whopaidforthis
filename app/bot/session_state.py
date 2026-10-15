"""In-memory, per-process state shared via `Deps` rather than module globals.

Kept separate from `app.bot.deps` and the handlers that use it so nothing here needs
to import `Deps` (which would create an import cycle): `Deps` imports these classes,
handlers get instances of them off `Deps`.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from ulid import ULID

_SESSION_TTL = timedelta(minutes=30)
_SEEN_USER_REFRESH_INTERVAL = timedelta(hours=1)
_RECENT_UPDATES_MAX_SIZE = 1000


@dataclass(frozen=True)
class SettripSession:
    session_id: str
    chat_id: int
    initiator_id: int
    initiator_name: str
    trip_name: str
    step: str  # "rebind" | "trip_ccy" | "home_ccy" | "tz" | "confirm"
    trip_currency: str | None = None
    home_currency: str | None = None
    timezone: str | None = None
    message_id: int | None = None
    awaiting_text_for: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SettripSessionStore:
    """In-memory sessions for `/settrip`, TTL 30 minutes. One store per running bot."""

    def __init__(self, ttl: timedelta = _SESSION_TTL) -> None:
        self._ttl = ttl
        self._sessions: dict[str, SettripSession] = {}

    def create(self, **kwargs: object) -> SettripSession:
        session_id = str(ULID())
        session = SettripSession(session_id=session_id, **kwargs)  # type: ignore[arg-type]
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> SettripSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if datetime.now(UTC) - session.created_at > self._ttl:
            del self._sessions[session_id]
            return None
        return session

    def update(self, session_id: str, **changes: object) -> SettripSession | None:
        session = self.get(session_id)
        if session is None:
            return None
        updated = replace(session, **changes)  # type: ignore[arg-type]
        self._sessions[session_id] = updated
        return updated

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def find_pending_for(self, chat_id: int, user_id: int) -> SettripSession | None:
        """Any live session for this chat and initiator, expired ones evicted."""
        for session in list(self._sessions.values()):
            if session.chat_id == chat_id and session.initiator_id == user_id:
                if datetime.now(UTC) - session.created_at > self._ttl:
                    del self._sessions[session.session_id]
                    continue
                return session
        return None

    def find_awaiting_text(self, chat_id: int, user_id: int) -> SettripSession | None:
        """Like `find_pending_for`, but only when that session is waiting on free text."""
        session = self.find_pending_for(chat_id, user_id)
        if session is not None and session.awaiting_text_for is not None:
            return session
        return None


class SeenUsersThrottle:
    """At most one "seen but not joined" upsert per user per chat per hour."""

    def __init__(self, refresh_interval: timedelta = _SEEN_USER_REFRESH_INTERVAL) -> None:
        self._refresh_interval = refresh_interval
        self._last_upsert: dict[int, dict[int, datetime]] = {}

    def should_upsert(self, chat_id: int, user_id: int, now: datetime) -> bool:
        last = self._last_upsert.get(chat_id, {}).get(user_id)
        return last is None or now - last >= self._refresh_interval

    def mark(self, chat_id: int, user_id: int, now: datetime) -> None:
        self._last_upsert.setdefault(chat_id, {})[user_id] = now


class RecentUpdates:
    """Bounded LRU of `update.update_id`, guarding the text-expense handler against a
    replayed Telegram update (spec §10, "Also guard the PTB update handler...").
    """

    def __init__(self, max_size: int = _RECENT_UPDATES_MAX_SIZE) -> None:
        self._max_size = max_size
        self._seen: OrderedDict[int, None] = OrderedDict()

    def seen(self, update_id: int) -> bool:
        return update_id in self._seen

    def add(self, update_id: int) -> None:
        if update_id in self._seen:
            self._seen.move_to_end(update_id)
            return
        self._seen[update_id] = None
        if len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
