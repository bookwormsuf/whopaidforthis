"""The `Trip` domain model: one Telegram group's stretch of shared spending.

Spec references: pinned-decisions §2, §18.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, field_validator

_CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$")


class Trip(BaseModel):
    """One Telegram group's trip: its two currencies, timezone, and open/closed state.

    `id` is the Postgres row id; `None` only for a `Trip` built before it has been
    saved (e.g. mid-`/settrip`, before `save_binding` assigns one). `closed_at` is
    `None` for the one open trip of a chat; set once `/settrip` starts a new trip or
    `Close this trip` is pressed (Phase 2).
    """

    model_config = ConfigDict(frozen=True)

    chat_id: int
    name: str
    trip_currency: str
    home_currency: str
    timezone: str
    created_at: datetime
    id: int | None = None
    created_by: int | None = None
    closed_at: datetime | None = None

    @field_validator("trip_currency", "home_currency")
    @classmethod
    def _validate_currency_code(cls, value: str) -> str:
        if not _CURRENCY_CODE_RE.match(value):
            raise ValueError("currency code must be 3 upper-case letters")
        return value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        if value not in available_timezones():
            raise ValueError("timezone must be a valid IANA timezone name")
        return value
