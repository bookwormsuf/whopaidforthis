"""Tags mean the payer (added 2026-10-02 at go-live, at Sufyan's request). Giving
items to others by reply was retired in Sprint 13: the sender assigns every item on
the "Who had this?" screens.
"""

from __future__ import annotations

from app.services.expense_parser import parse_expense
from tests.group_sim import ADA, BO, FakeGroup, Member
from tests.test_item_claiming import RESTAURANT

BOT = "whopaidforthisbot"


# -- Tags --------------------------------------------------------------------


def test_a_tag_is_the_payer_and_not_the_description() -> None:
    for text in ("1200 ramen @amir", "@amir 1200 ramen", "1200 ramen paid by @amir"):
        result = parse_expense(text, "JPY", "SGD", BOT)
        assert (result.description, result.payer_text) == ("Ramen", "@amir"), text
    # An explicit "paid by" wins over a tag.
    assert parse_expense("1200 ramen @amir paid by me", "JPY", "SGD", BOT).payer_text == "me"
    # The bot's own mention is not a tag.
    assert parse_expense(f"@{BOT} 1200 ramen", "JPY", "SGD", BOT).payer_text is None


async def test_tagging_someone_makes_them_the_payer() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "1200 yen ramen @bo")
    assert "Bo paid" in card.text
    assert "<b>Ramen</b>" in card.text


async def test_tagging_someone_without_a_username_joins_them_and_makes_them_payer() -> None:
    group = await FakeGroup.create(ADA)
    amir = Member(7, "Amir", None, "Rahman")  # no username: shown by name in the tag
    card = await group.send(ADA, "1200 yen ramen Amir Rahman", mentions={"Amir Rahman": amir})
    assert "Amir Rahman paid" in card.text
    assert "<b>Ramen</b>" in card.text
    joined = await group.ledger.list_users(group.trip, joined_only=True)
    assert {u.user_id for u in joined} == {ADA.id, amir.id}


async def test_a_tag_on_a_receipt_caption_makes_them_the_payer() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send_receipt(ADA, RESTAURANT, caption="@bo")
    assert "Bo paid" in card.text
