"""Tags mean the payer, and the sender gives items to others (added 2026-10-02 at
go-live, at Sufyan's request).
"""

from __future__ import annotations

from app.services.expense_parser import parse_expense, parse_item_assignment
from tests.group_sim import ADA, BO, CY, FakeGroup, Member
from tests.test_item_claiming import RESTAURANT, _item_buttons, _items_card

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


# -- The sender gives items to others ------------------------------------------


def test_item_assignment_parse() -> None:
    assert parse_item_assignment("amir 1,3") == ("amir", [1, 3])
    assert parse_item_assignment("Jo Tan 2 4") == ("Jo Tan", [2, 4])
    assert parse_item_assignment("@bo 2") == ("@bo", [2])
    assert parse_item_assignment("mine 1") is None
    assert parse_item_assignment("1,3") is None


async def test_tapping_for_someone_else() -> None:
    group = await FakeGroup.create(ADA, BO, CY)
    card = await group.send_receipt(ADA, RESTAURANT)
    await group.press(ADA, card, "Choose people")
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next")
    await group.press(ADA, card, "By items")
    assert "👤 Tapping for: Me ▸" in card.buttons

    await group.press(ADA, card, "Tapping for: Me ▸")
    assert "👤 Tapping for: Bo ▸" in card.buttons
    await group.press(ADA, card, "Tapping for: Bo ▸")  # Ada now taps for Cy
    assert "👤 Tapping for: Cy ▸" in card.buttons
    assert "taps go to <b>Cy</b>" in card.text
    await group.press(ADA, card, "1 Ramen ¥1,000")
    assert _item_buttons(card)[0] == "1 Ramen ¥1,000 · C"
    draft = await group.deps.draft_service._drafts.get(card.draft_id)
    assert CY.id in draft.participant_ids  # Cy didn't tap anything

    await group.press(ADA, card, "Tapping for: Cy ▸")  # back round to Ada
    assert "👤 Tapping for: Me ▸" in card.buttons
    await group.press(ADA, card, "2 Udon ¥800")
    assert _item_buttons(card)[1] == "2 Udon ¥800 · A"
    # Others' taps are always their own.
    await group.press(BO, card, "3 Gyoza ¥600")
    assert _item_buttons(card)[2] == "3 Gyoza ¥600 · B"


async def test_only_the_sender_picks_who_they_tap_for() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _items_card(group)
    toast = await group.press(BO, card, "Tapping for: Me ▸")
    assert toast == "Only Ada can edit this"
    assert "👤 Tapping for: Me ▸" in card.buttons


async def test_reply_gives_items_to_someone_else() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _items_card(group)
    await group.send(ADA, "bo 1,3", reply_to=card)
    card = group.cards[card.message_id]
    assert _item_buttons(card) == ["1 Ramen ¥1,000 · B", "2 Udon ¥800", "3 Gyoza ¥600 · B"]

    # Only the sender can give items to others.
    await group.send(BO, "ada 2", reply_to=card)
    card = group.cards[card.message_id]
    assert "Only Ada can give items to others" in card.text
    assert _item_buttons(card)[1] == "2 Udon ¥800"


async def test_tapping_for_names_someone_who_has_left_the_people() -> None:
    from dataclasses import replace

    from app.bot.keyboards.items import tapping_for_rows

    group = await FakeGroup.create(ADA, BO, CY)
    card = await _items_card(group)
    await group.press(ADA, card, "Tapping for: Me ▸")
    await group.press(ADA, card, "Tapping for: Bo ▸")  # now Cy
    draft = await group.deps.draft_service._drafts.get(card.draft_id)
    gone = replace(draft, people=tuple(p for p in draft.people if p.user_id != CY.id))
    (row,) = tapping_for_rows(gone)
    # Not "Me": the sender's taps still go to them, as the card text says.
    assert row[0].text != "👤 Tapping for: Me ▸"
