"""Custom amounts sent the way people actually send them (fixed 2026-11-01, from a
tester's report): lines sent without replying to the card, and a reply to a card
whose draft was cleared by a deploy, must never become a new expense.
"""

from __future__ import annotations

from app.bot.handlers.split import EXPIRED_CARD_TEXT
from tests.group_sim import ADA, BO, CY, Card, FakeGroup


async def _custom_card(group: FakeGroup) -> Card:
    card = await group.send(ADA, "1200 yen ramen")
    assert card is not None
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Type amounts instead")
    assert "Reply to this message with one line per person" in card.text
    return card


async def test_lines_sent_without_replying_fill_the_open_card() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _custom_card(group)
    assert await group.send(ADA, "Ada - 500\nBo - 700") is None  # no new expense
    latest = group.cards[card.message_id]
    assert "Remaining: ¥0 ✅" in latest.text
    assert "Confirm" in latest.buttons


async def test_lines_naming_someone_unknown_are_read_as_usual() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _custom_card(group)
    before = group.cards[card.message_id].text
    new = await group.send(ADA, "Zed 500")
    assert new is not None and new.message_id != card.message_id  # a new expense
    assert group.cards[card.message_id].text == before


async def test_someone_elses_lines_do_not_touch_the_card() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _custom_card(group)
    before = group.cards[card.message_id].text
    await group.send(BO, "Ada 500\nBo 700")
    assert group.cards[card.message_id].text == before


async def test_a_reply_with_dashes_fills_the_card() -> None:
    group = await FakeGroup.create(ADA, BO, CY)
    card = await _custom_card(group)
    await group.send(ADA, "Cy - 200\nBo - 500\nAda - 500", reply_to=card)
    assert "Remaining: ¥0 ✅" in group.cards[card.message_id].text


async def test_a_reply_to_a_card_whose_draft_is_gone_says_expired() -> None:
    """The bot restarted (a deploy) while the card was open: drafts live in memory."""
    group = await FakeGroup.create(ADA, BO)
    card = await _custom_card(group)
    await group.deps.draft_service._drafts.delete(card.draft_id)
    replies = len(group.replies)
    assert await group.send(ADA, "Ada - 500\nBo - 700", reply_to=card) is None
    assert group.replies[replies:] == [EXPIRED_CARD_TEXT]


async def test_a_reply_to_a_saved_expense_is_read_as_usual() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "1200 yen ramen")
    await group.press(ADA, card, "Just me")
    await group.press(ADA, card, "Confirm")
    new = await group.send(ADA, "800 yen gyoza", reply_to=card)
    assert new is not None  # the saved card has no buttons, so it is a new expense


async def test_no_open_card_means_a_normal_expense() -> None:
    group = await FakeGroup.create(ADA, BO)
    assert await group.send(ADA, "Bo 700") is not None
