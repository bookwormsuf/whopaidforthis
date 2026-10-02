"""Name resolution for "split between me Amir and John", "paid by Amir" and custom
split lines. Pure, no I/O. Spec references: pinned-decisions §3, sprint 5 pins.

A name matches a joined trip user, case-insensitively, by their username (with or
without the leading `@`), their first name, or their full display name. "me" is the
sender. One match resolves; zero or several matches are reported back so the draft
can ask with buttons -- this module never guesses.

`TripUser` has no separate first-name field: the first name is the first word of
the display name, which is first + last name when Telegram gives one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.models.user import TripUser

_ME_WORDS = frozenset({"me", "myself", "i"})
# A tag for a Telegram user without a username, written by the bot layer as
# `@id<user ID>` (see `app.bot.handlers.text_expense.tags_as_text`).
_ID_TAG_RE = re.compile(r"^id(\d+)$")
_JOINER_WORDS = frozenset({"and", "&"})
# Longest run of words tried as one full display name ("Amir Rahman").
_MAX_NAME_WORDS = 3


@dataclass(frozen=True)
class Person:
    """A joined trip user as the draft flow sees them: enough to resolve, render
    and record them in Splits.
    """

    user_id: int
    display_name: str
    username: str | None

    @classmethod
    def from_trip_user(cls, user: TripUser) -> Person:
        return cls(user.user_id, user.display_name, user.username)

    @property
    def first_name(self) -> str:
        words = self.display_name.split()
        return words[0] if words else self.display_name


@dataclass(frozen=True)
class NameMatch:
    """`status` is `exact` (one match, in `user_ids`), `ambiguous` (several) or
    `unknown` (none). `token` is the name as written, for "Who did you mean by …?".
    """

    token: str
    status: Literal["exact", "ambiguous", "unknown"]
    user_ids: tuple[int, ...]


def _normalise(text: str) -> str:
    return text.strip().strip(".,;:!?").lstrip("@").casefold()


def _matches(person: Person, key: str) -> bool:
    return key in {
        (person.username or "").casefold(),
        person.first_name.casefold(),
        person.display_name.casefold(),
    }


def resolve_name(token: str, people: Iterable[Person], sender_id: int) -> NameMatch:
    """Resolves one name. "me" is always the sender, even if the sender has not
    joined (they have: sending an expense joins them, spec §3).
    """
    key = _normalise(token)
    if key in _ME_WORDS:
        return NameMatch(token, "exact", (sender_id,))
    if not key:
        return NameMatch(token, "unknown", ())
    id_tag = _ID_TAG_RE.match(key)
    if id_tag and token.strip().startswith("@"):
        wanted = int(id_tag.group(1))
        if any(p.user_id == wanted for p in people):
            return NameMatch(token, "exact", (wanted,))
    ids = tuple(dict.fromkeys(p.user_id for p in people if _matches(p, key)))
    if len(ids) == 1:
        return NameMatch(token, "exact", ids)
    return NameMatch(token, "ambiguous" if ids else "unknown", ids)


def split_names(text: str, people: Iterable[Person]) -> list[str]:
    """Splits a names clause (`me Amir and John`, `me, Amir & John Tan`) into name
    tokens. Commas, `and` and `&` separate names; a run of up to three words is kept
    together when it is someone's full display name, so "John Tan" is one name.
    """
    people = list(people)
    display_names = {p.display_name.casefold() for p in people}
    words = [
        word
        for word in re.split(r"[\s,]+", text.strip())
        if word and word.casefold() not in _JOINER_WORDS
    ]
    tokens: list[str] = []
    index = 0
    while index < len(words):
        taken = 1
        for length in range(min(_MAX_NAME_WORDS, len(words) - index), 1, -1):
            candidate = " ".join(words[index : index + length])
            if _normalise(candidate) in display_names:
                taken = length
                break
        tokens.append(" ".join(words[index : index + taken]))
        index += taken
    return tokens


def resolve_names(text: str, people: Iterable[Person], sender_id: int) -> list[NameMatch]:
    """Every name in a names clause, in order, each resolved with `resolve_name`."""
    people = list(people)
    return [resolve_name(token, people, sender_id) for token in split_names(text, people)]


def is_everyone(text: str) -> bool:
    """`split everyone` / `split all` (spec §6)."""
    return _normalise(text) in {"everyone", "all"}
