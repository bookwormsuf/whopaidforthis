"""Tags in an expense message or receipt caption: a tagged person paid (decided
2026-10-02 at go-live). The parser reads `@amir` itself; this module turns a tag
of someone without a username into a form the parser can read, and joins them.
"""

from __future__ import annotations

from datetime import datetime

from telegram import Message, MessageEntity

from app.models.user import TripUser


def tags_as_text(message: Message, caption: bool = False) -> tuple[str, list]:
    """The message text (or caption) with each tag of a user who has no username
    (a "text mention", shown as their name) rewritten as `@id<user ID>`, so the
    parser sees one tag it can resolve (`app.services.names`). Also returns those
    users, so the caller can join them. A tag with a username (`@amir`) is left
    as written.
    """
    text = (message.caption if caption else message.text) or ""
    parse = message.parse_caption_entities if caption else message.parse_entities
    try:
        entities = parse([MessageEntity.TEXT_MENTION])
    except Exception:  # noqa: BLE001 - no entities (or a test double): nothing to rewrite
        return text, []
    if not isinstance(entities, dict):
        return text, []
    users = []
    for entity, shown in entities.items():
        if entity.user is None:
            continue
        text = text.replace(shown, f"@id{entity.user.id}", 1)
        users.append(entity.user)
    return text, users


async def join_tagged(deps, trip, users: list, now: datetime) -> None:
    """A tagged person is in the group: join them, so they can be the payer
    (decided 2026-10-02), as `/join` sent as a reply joins someone else.
    """
    for tagged in users:
        if getattr(tagged, "is_bot", False):
            continue
        await deps.ledger.upsert_user(trip, TripUser.from_telegram_user(tagged, now), joined=True)
