"""Item claiming through the real handlers (sprint 6, spec §9). Multi-user tests
stand in for the sprint 6 checklist's two-person steps (decision 2026-10-01); each
test names the checklist step it covers.

Rate in every test: 1 JPY = 0.0081 SGD (`tests.helpers.make_deps`).
"""

from __future__ import annotations

from app.bot.handlers.balances import balances_command
from app.services.drafts import NOT_JOINED_TEXT
from app.services.llm_schemas import ReceiptExtraction
from tests.group_sim import ADA, BO, CY, FakeGroup, Member


def _receipt(items: list[tuple[str, int, str]], *totals: int) -> ReceiptExtraction:
    """Line items as (description, amount, kind). One total is a confident grand
    total; with several, all are guesses, so the bot asks which one.
    """
    return ReceiptExtraction(
        merchant="Izakaya",
        currency="JPY",
        transaction_datetime=None,
        line_items=[
            {"description": d, "quantity": 1, "amount_minor": a, "kind": k} for d, a, k in items
        ],
        candidate_totals=[
            {"label_as_printed": "合計", "amount_minor": t, "is_grand_total_guess": True}
            for t in totals
        ],
        description="Dinner",
        category="food",
        confidence=0.9,
    )


RESTAURANT = _receipt(
    [
        ("Ramen", 1000, "item"),
        ("Udon", 800, "item"),
        ("Gyoza", 600, "item"),
        ("Tax 10%", 240, "tax"),
    ],
    2640,
)

SUPERMARKET = _receipt(
    [
        ("Milk", 200, "item"),
        ("Bread", 300, "item"),
        ("Eggs", 250, "item"),
        ("Tax 8%", 60, "tax"),
    ],
    810,
)


async def _items_card(group: FakeGroup, receipt=RESTAURANT, sender: Member = ADA):
    card = await group.send_receipt(sender, receipt)
    assert card is not None
    await group.press(sender, card, "Everyone")
    assert "By items" in card.buttons
    await group.press(sender, card, "By items")
    return card


def _item_buttons(card) -> list[str]:
    return [b for b in card.buttons if b[:1].isdigit()]


# -- Checklist step 1: restaurant receipt, own dishes plus a shared one ---------


async def test_step1_claim_own_and_shared_dishes() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _items_card(group)
    assert "Unclaimed: ¥2,400" in card.text
    assert "Tax: Tax 10% ¥240 (shared in proportion)" in card.text
    assert _item_buttons(card) == ["1 Ramen ¥1,000", "2 Udon ¥800", "3 Gyoza ¥600"]
    assert card.buttons[-2:] == ["Rest → everyone", "Done (unclaimed: ¥2,400)"]

    await group.press(ADA, card, "1 Ramen ¥1,000")
    await group.press(BO, card, "2 Udon ¥800")
    await group.press(ADA, card, "3 Gyoza ¥600")
    await group.press(BO, card, "3 Gyoza ¥600 · A")
    assert _item_buttons(card) == ["1 Ramen ¥1,000 · A", "2 Udon ¥800 · B", "3 Gyoza ¥600 · AB"]
    assert "Unclaimed: ¥0" in card.text
    assert card.buttons[-1] == "Done"

    await group.press(ADA, card, "Done")
    # Subtotals 1,300 / 1,100; tax 240 in proportion -> 130 / 110.
    assert "Ada 💳: items ¥1,300 + tax ¥130 = ¥1,430 · S$11.58" in card.text
    assert "Bo: items ¥1,100 + tax ¥110 = ¥1,210 · S$9.80" in card.text
    assert card.buttons == ["Confirm", "Edit", "Cancel"]

    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    assert card.text.startswith("Saved ✅")
    expense = group.ledger.expense_for(draft_id)
    assert expense.split_method == "items"
    assert {s.user_id: s.share_trip_minor for s in expense.splits} == {ADA.id: 1430, BO.id: 1210}
    assert sum(s.share_home_minor for s in expense.splits) == expense.home_amount_minor
    assert group.ledger.assignments_for(draft_id) == {
        0: ([ADA.id], "claimed"),
        1: ([BO.id], "claimed"),
        2: ([ADA.id, BO.id], "claimed"),
        3: ([ADA.id, BO.id], "proportional"),
    }


# -- Checklist step 2: claim some, Rest -> everyone ---------------------------


async def test_step2_rest_to_everyone() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _items_card(group, SUPERMARKET)
    await group.press(ADA, card, "1 Milk ¥200")
    await group.press(BO, card, "2 Bread ¥300")
    await group.press(ADA, card, "Rest → everyone")
    assert _item_buttons(card)[2] == "3 Eggs ¥250 · all"
    assert card.buttons[-1] == "Done"

    await group.press(ADA, card, "Done")
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    # Subtotals: Ada 200 + 125 = 325, Bo 300 + 125 = 425 (750); tax 60 -> 26 / 34.
    assert {s.user_id: s.share_trip_minor for s in expense.splits} == {ADA.id: 351, BO.id: 459}
    assert group.ledger.assignments_for(draft_id)[2] == ([ADA.id, BO.id], "everyone")


async def test_rest_to_everyone_is_reversible_per_item() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _items_card(group, SUPERMARKET)
    await group.press(ADA, card, "Rest → everyone")
    await group.press(BO, card, "3 Eggs ¥250 · all")
    assert _item_buttons(card)[2] == "3 Eggs ¥250 · B"


# -- Checklist step 3: text shortcuts -----------------------------------------


async def test_step3_mine_reply_equals_two_taps() -> None:
    by_text = await FakeGroup.create(ADA, BO)
    card = await _items_card(by_text)
    assert await by_text.send(ADA, "mine 1,3", reply_to=card) is None  # no new expense
    text_card = by_text.cards[card.message_id]

    by_taps = await FakeGroup.create(ADA, BO)
    tapped = await _items_card(by_taps)
    await by_taps.press(ADA, tapped, "1 Ramen ¥1,000")
    await by_taps.press(ADA, tapped, "3 Gyoza ¥600")

    assert _item_buttons(text_card) == _item_buttons(tapped)
    assert _item_buttons(text_card) == ["1 Ramen ¥1,000 · A", "2 Udon ¥800", "3 Gyoza ¥600 · A"]


async def test_split_everyone_and_unclaim_replies() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _items_card(group)
    await group.send(BO, "mine 2\nsplit 3 everyone", reply_to=card)
    card = group.cards[card.message_id]
    assert _item_buttons(card)[1:] == ["2 Udon ¥800 · B", "3 Gyoza ¥600 · all"]
    await group.send(BO, "unclaim 2", reply_to=card)
    card = group.cards[card.message_id]
    assert _item_buttons(card)[1] == "2 Udon ¥800"


async def test_unknown_item_number_is_reported_on_the_card() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _items_card(group)
    await group.send(ADA, "mine 1, 9", reply_to=card)
    card = group.cards[card.message_id]
    assert "⚠️ No item 9" in card.text
    assert _item_buttons(card)[0] == "1 Ramen ¥1,000"  # the whole line is refused


# -- Checklist step 4: Done too early -----------------------------------------


async def test_step4_done_before_everything_claimed_is_a_toast() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _items_card(group)
    await group.press(ADA, card, "1 Ramen ¥1,000")
    toast = await group.press(ADA, card, "Done (unclaimed: ¥1,400)")
    assert toast == "Still unclaimed: ¥1,400 (items 2, 3)"
    draft = await group.deps.draft_service._drafts.get(card.draft_id)
    assert draft.state == "items"


# -- Checklist step 5: tester B presses Done ----------------------------------


async def test_step5_non_initiator_done_is_refused() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _items_card(group)
    await group.press(ADA, card, "Rest → everyone")
    toast = await group.press(BO, card, "Done")
    assert toast == "Only Ada can edit this"
    draft = await group.deps.draft_service._drafts.get(card.draft_id)
    assert draft.state == "items"


# -- Checklist step 6: Fix items ---------------------------------------------


async def test_step6_fix_items_adds_unlisted_and_reconciles() -> None:
    group = await FakeGroup.create(ADA, BO)
    # Items + tax = 2,640 but the receipt also prints 3,000; the user picks 3,000.
    receipt = _receipt(
        [
            ("Ramen", 1000, "item"),
            ("Udon", 800, "item"),
            ("Gyoza", 600, "item"),
            ("Tax", 240, "tax"),
        ],
        2640,
        3000,
    )
    card = await group.send_receipt(ADA, receipt)
    await group.press(ADA, card, "¥3,000")
    assert "difference ¥360" in card.text
    await group.press(ADA, card, "Fix items")
    assert _item_buttons(card)[-1] == "4 Unlisted ¥360"
    assert "Unclaimed: ¥2,760" in card.text

    await group.press(ADA, card, "1 Ramen ¥1,000")
    await group.press(BO, card, "2 Udon ¥800")
    await group.press(ADA, card, "4 Unlisted ¥360")
    await group.press(ADA, card, "Rest → everyone")
    await group.press(ADA, card, "Done")
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    assert expense.trip_amount_minor == 3000
    assert sum(s.share_trip_minor for s in expense.splits) == 3000


async def test_auto_picked_total_within_five_gets_an_unlisted_item() -> None:
    group = await FakeGroup.create(ADA, BO)
    receipt = _receipt([("Ramen", 1000, "item"), ("Udon", 798, "item")], 1800)
    card = await _items_card(group, receipt)
    assert _item_buttons(card)[-1] == "3 Unlisted ¥2"


async def test_use_total_anyway_hides_by_items() -> None:
    group = await FakeGroup.create(ADA, BO)
    receipt = _receipt([("Ramen", 1000, "item"), ("Udon", 800, "item")], 1800, 2000)
    card = await group.send_receipt(ADA, receipt)
    await group.press(ADA, card, "¥2,000")
    await group.press(ADA, card, "Use total anyway")
    await group.press(ADA, card, "Everyone")
    assert card.buttons == ["Equally", "Custom", "Cancel"]


# -- Checklist step 7: balances ----------------------------------------------


async def test_step7_balances_reflect_itemised_expense() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _items_card(group)
    await group.press(ADA, card, "1 Ramen ¥1,000")
    await group.press(BO, card, "2 Udon ¥800")
    await group.press(ADA, card, "Rest → everyone")
    await group.press(ADA, card, "Done")
    await group.press(ADA, card, "Confirm")
    # Ada paid ¥2,640 = S$21.38; Bo's share 800 + 300 = 1,100 + tax 110 = ¥1,210 = S$9.80.
    balances = await group.command(BO, balances_command)
    assert "Bo: paid S$0.00 · share S$9.80 · <b>−S$9.80</b>" in balances


# -- Who may claim --------------------------------------------------------------


async def test_tapping_adds_a_joined_non_participant() -> None:
    group = await FakeGroup.create(ADA, BO, CY)
    card = await group.send_receipt(ADA, RESTAURANT)
    await group.press(ADA, card, "Choose people")
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next")
    await group.press(ADA, card, "By items")
    await group.press(CY, card, "2 Udon ¥800")
    draft = await group.deps.draft_service._drafts.get(card.draft_id)
    assert CY.id in draft.participant_ids


async def test_tapping_without_joining_is_a_toast() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _items_card(group)
    stranger = Member(9, "Zed", "zed")
    toast = await group.press(stranger, card, "1 Ramen ¥1,000")
    assert toast == NOT_JOINED_TEXT
    assert _item_buttons(card)[0] == "1 Ramen ¥1,000"


async def test_edit_returns_to_item_card_with_claims() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _items_card(group)
    await group.press(ADA, card, "1 Ramen ¥1,000")
    await group.press(ADA, card, "Rest → everyone")
    await group.press(ADA, card, "Done")
    await group.press(ADA, card, "Edit")
    assert _item_buttons(card) == ["1 Ramen ¥1,000 · A", "2 Udon ¥800 · all", "3 Gyoza ¥600 · all"]


async def test_more_than_forty_items_hides_by_items() -> None:
    group = await FakeGroup.create(ADA, BO)
    receipt = _receipt([(f"Item {n}", 10, "item") for n in range(41)], 410)
    card = await group.send_receipt(ADA, receipt)
    await group.press(ADA, card, "Everyone")
    assert card.buttons == ["Equally", "Custom", "Cancel"]
    assert "Too many items to split individually" in card.text


# -- Receipt captions (spec §13, carried from Sprint 5) -------------------------


async def test_caption_split_everyone_selects_everyone() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send_receipt(ADA, RESTAURANT, caption="dinner split everyone")
    assert "Shared by Ada, Bo" in card.text
    assert card.buttons == ["Equally", "By items", "Custom", "Cancel"]
    assert "<b>Dinner</b>" in card.text


async def test_caption_paid_by_sets_the_payer() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send_receipt(ADA, RESTAURANT, caption="paid by Bo")
    assert "Paid by Bo" in card.text
    assert card.buttons == ["Just me", "Everyone", "Choose people", "Cancel"]
