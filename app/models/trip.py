"""The `Trip` domain model: a Telegram group bound to one Google Sheet.

Spec references: pinned-decisions §2, §10.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, field_validator

_CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$")


class Trip(BaseModel):
    """One Telegram group bound to one Google Sheet, with its two currencies and timezone."""

    model_config = ConfigDict(frozen=True)

    chat_id: int
    sheet_id: str
    name: str
    trip_currency: str
    home_currency: str
    timezone: str
    created_at: datetime

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
