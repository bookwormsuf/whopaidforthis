"""Tests for app.models.trip and app.models.user."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models.trip import Trip
from app.models.user import TripUser, display_name_for


def _trip(**overrides: object) -> Trip:
    defaults = dict(
        chat_id=-100123,
        sheet_id="sheet-id-1234567890",
        name="Japan Trip",
        trip_currency="JPY",
        home_currency="SGD",
        timezone="Asia/Tokyo",
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Trip(**defaults)


def test_trip_accepts_valid_currency_and_timezone() -> None:
    trip = _trip()
    assert trip.trip_currency == "JPY"
    assert trip.timezone == "Asia/Tokyo"


def test_trip_rejects_lowercase_currency_code() -> None:
    with pytest.raises(ValidationError):
        _trip(trip_currency="jpy")


def test_trip_rejects_bad_length_currency_code() -> None:
    with pytest.raises(ValidationError):
        _trip(home_currency="SG")


def test_trip_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        _trip(timezone="Not/AZone")


def test_trip_is_frozen() -> None:
    trip = _trip()
    with pytest.raises(ValidationError):
        trip.name = "Other"  # type: ignore[misc]


def test_display_name_prefers_first_and_last_name() -> None:
    user = SimpleNamespace(id=1, first_name="Ada", last_name="Lovelace", username="ada")
    assert display_name_for(user) == "Ada Lovelace"


def test_display_name_falls_back_to_username() -> None:
    user = SimpleNamespace(id=1, first_name=None, last_name=None, username="ada")
    assert display_name_for(user) == "ada"


def test_display_name_falls_back_to_user_id() -> None:
    user = SimpleNamespace(id=42, first_name=None, last_name=None, username=None)
    assert display_name_for(user) == "User 42"


def test_trip_user_from_telegram_user_starts_not_joined() -> None:
    user = SimpleNamespace(id=1, first_name="Ada", last_name=None, username="ada")
    now = datetime.now(UTC)
    tg_user = TripUser.from_telegram_user(user, now)
    assert tg_user.joined is False
    assert tg_user.first_seen == now
    assert tg_user.last_seen == now
