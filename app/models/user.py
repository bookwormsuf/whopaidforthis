"""The `TripUser` domain model: a person who has been seen or has joined a trip.

Spec references: pinned-decisions §3, §10.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TripUser(BaseModel):
    """A Telegram user in the context of one trip's `Users` tab."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    username: str | None
    display_name: str
    first_seen: datetime
    last_seen: datetime
    joined: bool

    @classmethod
    def from_telegram_user(cls, user: Any, now: datetime) -> TripUser:
        """Build a `TripUser` seen for the first time right now.

        `joined` is always False here; upsert logic decides the final joined value.
        Display name: first + last name if present, else username, else "User <id>".
        """
        return cls(
            user_id=user.id,
            username=getattr(user, "username", None),
            display_name=display_name_for(user),
            first_seen=now,
            last_seen=now,
            joined=False,
        )


def display_name_for(user: Any) -> str:
    """First + last name if present, else username, else "User <id>"."""
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    full = " ".join(part for part in (first, last) if part)
    if full:
        return full
    username = getattr(user, "username", None)
    if username:
        return username
    return f"User {user.id}"
