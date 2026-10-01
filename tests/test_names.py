"""Tests for app.services.names (spec §3, sprint 5)."""

from __future__ import annotations

from app.services.names import Person, is_everyone, resolve_name, resolve_names, split_names

ADA = Person(1, "Ada Lovelace", "ada")
AMIR = Person(2, "Amir", "amir_r")
JO_TAN = Person(3, "Jo Tan", "jotan")
JO_LIM = Person(4, "Jo Lim", None)
PEOPLE = [ADA, AMIR, JO_TAN, JO_LIM]


def test_exact_first_name() -> None:
    assert resolve_name("Amir", PEOPLE, sender_id=1).user_ids == (2,)


def test_case_insensitive_first_name_username_and_full_name() -> None:
    for token in ("amir", "AMIR", "amir_r", "@Amir_R"):
        match = resolve_name(token, PEOPLE, sender_id=1)
        assert (match.status, match.user_ids) == ("exact", (2,)), token
    assert resolve_name("ada lovelace", PEOPLE, sender_id=2).user_ids == (1,)


def test_ambiguous_first_name() -> None:
    match = resolve_name("jo", PEOPLE, sender_id=1)
    assert match.status == "ambiguous"
    assert set(match.user_ids) == {3, 4}


def test_unknown_name() -> None:
    match = resolve_name("John", PEOPLE, sender_id=1)
    assert (match.status, match.user_ids, match.token) == ("unknown", (), "John")


def test_me_is_the_sender() -> None:
    assert resolve_name("me", PEOPLE, sender_id=4).user_ids == (4,)
    assert resolve_name("Me", PEOPLE, sender_id=2).user_ids == (2,)


def test_split_names_on_and_commas_and_full_names() -> None:
    assert split_names("me Amir and John", PEOPLE) == ["me", "Amir", "John"]
    assert split_names("me, amir & Jo Tan", PEOPLE) == ["me", "amir", "Jo Tan"]


def test_resolve_names_mixes_statuses_in_order() -> None:
    statuses = [m.status for m in resolve_names("me Amir and jo and John", PEOPLE, 1)]
    assert statuses == ["exact", "exact", "ambiguous", "unknown"]


def test_everyone_and_all() -> None:
    assert is_everyone("everyone") and is_everyone("All") and not is_everyone("Amir")
