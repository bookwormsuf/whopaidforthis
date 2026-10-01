"""Tests for app.bot.keyboards.items: initials (sprint 6 pin)."""

from __future__ import annotations

from app.bot.keyboards.items import initials_for
from app.services.names import Person


def test_initials_unique_first_letters() -> None:
    people = [Person(1, "Ada", None), Person(2, "Bo Tan", None)]
    assert initials_for(people) == {1: "A", 2: "B"}


def test_initials_collisions_get_a_second_letter() -> None:
    people = [Person(1, "Amir", None), Person(2, "Ada", None), Person(3, "Bo", None)]
    assert initials_for(people) == {1: "Am", 2: "Ad", 3: "B"}


def test_initials_still_equal_get_a_number() -> None:
    people = [Person(1, "Jo Tan", None), Person(2, "Jo Lim", None)]
    assert initials_for(people) == {1: "Jo", 2: "Jo2"}
