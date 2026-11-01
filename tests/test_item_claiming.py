"""Item by item through the real handlers (sprint 13, spec §9): the initiator answers
"Who had this?" for each receipt item, then checks the summary. Replaces the sprint 6
item-card tests; the sprint 6 maths (shared items, proportional adjustments, units)
is unchanged and still covered here end to end.

Rate in every test: 1 JPY = 0.0081 SGD (`tests.helpers.make_deps`).
"""

from __future__ import annotations

from app.bot.handlers.balances import balances_command
from app.services.drafts import PICK_FIRST_TEXT
from app.services.llm_schemas import ReceiptExtraction
from tests.group_sim import ADA, BO, CY, Card, FakeGroup


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


async def _walk(group: FakeGroup, receipt=RESTAURANT) -> Card:
    """Ada sends `receipt`, picks Everyone, and opens the first "Who had this?"."""
    card = await group.send_receipt(ADA, receipt)
    assert card is not None
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Go item by item ▶")
    return card


async def _draft(group: FakeGroup, card: Card):
    return await group.deps.draft_service._drafts.get(card.draft_id)


def _fix_buttons(card: Card) -> list[str]:
    """The `Fix an item` list, without the 🍽 marker or the Back button."""
    return [b.removeprefix("🍽 ") for b in card.buttons if b.startswith("🍽 ")]


# -- The split-method screen ----------------------------------------------------


async def test_split_screen_puts_item_by_item_first_and_typing_last() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send_receipt(ADA, RESTAURANT)
    await group.press(ADA, card, "Everyone")
    assert "Who had what?" in card.text
    assert card.buttons == [
        "🍽 Go item by item ▶",
        "Split equally",
        "Type amounts instead",
        "Cancel",
    ]
    assert [len(row) for row in card.markup.inline_keyboard] == [1, 1, 1, 1]


async def test_no_items_offers_equally_and_typing_only() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "1200 yen ramen")
    await group.press(ADA, card, "Everyone")
    assert "How should it be split?" in card.text
    assert card.buttons == ["Split equally", "Type amounts instead", "Cancel"]


async def test_more_than_forty_items_hides_item_by_item() -> None:
    group = await FakeGroup.create(ADA, BO)
    receipt = _receipt([(f"Item {n}", 10, "item") for n in range(41)], 410)
    card = await group.send_receipt(ADA, receipt)
    await group.press(ADA, card, "Everyone")
    assert card.buttons == ["Split equally", "Type amounts instead", "Cancel"]
    assert "Too many items to split individually" in card.text


async def test_type_amounts_instead_opens_the_custom_card() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send_receipt(ADA, RESTAURANT)
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Type amounts instead")
    assert "Reply to this message with one line per person" in card.text


# -- Walking the items ------------------------------------------------------------


async def test_walk_is_in_receipt_order_with_one_screen_per_item() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    assert "Who had this?  (1 of 3)\n<b>Ramen</b> · ¥1,000" in card.text
    assert card.buttons == ["Ada", "Bo", "Everyone", "Next ▶", "Cancel"]  # no Back on 1
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    assert "(2 of 3)\n<b>Udon</b> · ¥800" in card.text
    assert card.buttons == ["Ada", "Bo", "Everyone", "◀ Back", "Next ▶", "Cancel"]


async def test_names_toggle_and_two_ticks_share_the_item() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    await group.press(ADA, card, "Ada")
    assert card.buttons[:2] == ["✓ Ada", "Bo"]
    await group.press(ADA, card, "✓ Ada")
    assert card.buttons[:2] == ["Ada", "Bo"]

    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")  # Ramen: Ada
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next ▶")  # Udon: Bo
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Bo")
    assert card.buttons[:2] == ["✓ Ada", "✓ Bo"]
    await group.press(ADA, card, "Next ▶")  # Gyoza: shared

    # Subtotals 1,300 / 1,100; tax 240 in proportion -> 130 / 110.
    assert card.text.startswith("<b>Check before saving</b>")
    assert "• Ada ¥1,430 (S$11.58)\n   items ¥1,300 + tax ¥130" in card.text
    assert "• Bo ¥1,210 (S$9.80)\n   items ¥1,100 + tax ¥110" in card.text
    assert "Shared in proportion: Tax 10% ¥240" in card.text
    assert card.buttons == ["Confirm", "Fix an item", "Edit", "Cancel"]

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


async def test_everyone_assigns_all_and_moves_on() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group, SUPERMARKET)
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")  # Milk: Ada
    await group.press(ADA, card, "Everyone")  # Bread: everyone
    assert "(3 of 3)\n<b>Eggs</b>" in card.text
    await group.press(ADA, card, "Everyone")  # Eggs: everyone, last -> summary
    assert card.text.startswith("<b>Check before saving</b>")

    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    # Subtotals: Ada 200 + 150 + 125 = 475, Bo 150 + 125 = 275 (750); tax 60 -> 38 / 22.
    assert {s.user_id: s.share_trip_minor for s in expense.splits} == {ADA.id: 513, BO.id: 297}
    assert group.ledger.assignments_for(draft_id)[1] == ([ADA.id, BO.id], "everyone")


async def test_unticking_one_name_on_an_everyone_item_keeps_the_others() -> None:
    group = await FakeGroup.create(ADA, BO, CY)
    card = await _walk(group)
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "◀ Back")
    assert card.buttons[:3] == ["✓ Ada", "✓ Bo", "✓ Cy"]
    await group.press(ADA, card, "✓ Cy")
    assert card.buttons[:3] == ["✓ Ada", "✓ Bo", "Cy"]


async def test_next_with_nobody_ticked_is_a_toast() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    toast = await group.press(ADA, card, "Next ▶")
    assert toast == PICK_FIRST_TEXT == "Pick who had this first"
    assert "(1 of 3)" in card.text
    assert (await _draft(group, card)).walk_index == 0


async def test_back_keeps_picks() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, "◀ Back")
    assert "(1 of 3)" in card.text
    assert card.buttons[:2] == ["Ada", "✓ Bo"]


async def test_non_initiator_presses_are_refused_everywhere() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    for label in ("Ada", "Everyone", "Next ▶"):
        assert await group.press(BO, card, label) == "Only Ada can edit this"
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    assert await group.press(BO, card, "◀ Back") == "Only Ada can edit this"
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Everyone")
    for label in ("Confirm", "Fix an item", "Edit"):
        assert await group.press(BO, card, label) == "Only Ada can edit this"
    await group.press(ADA, card, "Fix an item")
    assert await group.press(BO, card, "1 Ramen ¥1,000 · A") == "Only Ada can edit this"
    assert await group.press(BO, card, "◀ Back to summary") == "Only Ada can edit this"
    draft = await _draft(group, card)
    assert draft.state == "fix_list"
    assert draft.item_claims[0] == (ADA.id,)


async def test_a_press_from_an_older_card_changes_nothing() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    await group.press(ADA, card, "Ada")
    stale = Card(card.message_id, card.text, card.markup)  # the screen for item 1
    await group.press(ADA, card, "Next ▶")
    assert "(2 of 3)" in card.text

    for label in ("Next ▶", "Everyone", "Bo"):  # a double tap on item 1's screen
        assert await group.press(ADA, Card(stale.message_id, "", stale.markup), label) is None
    draft = await _draft(group, card)
    assert draft.walk_index == 1
    assert draft.item_claims == {0: (ADA.id,)}
    assert draft.everyone_items == frozenset()


async def test_reply_to_an_item_screen_is_ignored() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    before = (card.text, card.buttons)
    assert await group.send(ADA, "mine 1", reply_to=card) is None  # no new expense
    assert await group.send(ADA, "1200 yen ramen", reply_to=card) is None
    latest = group.cards[card.message_id]
    assert (latest.text, latest.buttons) == before
    assert (await _draft(group, card)).item_claims == {}


# -- Summary and Fix an item ------------------------------------------------------


async def test_fix_an_item_returns_to_the_summary() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    for _ in range(3):
        await group.press(ADA, card, "Ada")
        await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, "Fix an item")
    assert "Which item do you want to change?" in card.text
    assert _fix_buttons(card) == ["1 Ramen ¥1,000 · A", "2 Udon ¥800 · A", "3 Gyoza ¥600 · A"]
    assert card.buttons[-1] == "◀ Back to summary"

    await group.press(ADA, card, "2 Udon ¥800 · A")
    assert "(2 of 3)\n<b>Udon</b>" in card.text
    await group.press(ADA, card, "✓ Ada")
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next ▶")
    # Straight back to the summary, not on to Gyoza.
    assert card.text.startswith("<b>Check before saving</b>")
    assert "• Bo ¥880" in card.text


async def test_back_from_a_fixed_item_and_back_to_summary() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    for _ in range(3):
        await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Fix an item")
    await group.press(ADA, card, "1 Ramen ¥1,000 · all")
    assert "◀ Back" in card.buttons  # even on item 1: back to the list
    await group.press(ADA, card, "◀ Back")
    assert "Which item do you want to change?" in card.text
    await group.press(ADA, card, "◀ Back to summary")
    assert card.text.startswith("<b>Check before saving</b>")


async def test_participant_with_no_items_is_saved_at_zero() -> None:
    group = await FakeGroup.create(ADA, BO, CY)
    card = await _walk(group)
    for name in ("Ada", "Bo", "Ada"):
        await group.press(ADA, card, name)
        await group.press(ADA, card, "Next ▶")
    assert "• Cy ¥0 (S$0.00)" in card.text
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    assert {s.user_id: s.share_trip_minor for s in expense.splits}[CY.id] == 0
    assert sum(s.share_trip_minor for s in expense.splits) == 2640


async def test_edit_drops_removed_people_and_resumes_at_the_first_empty_item() -> None:
    group = await FakeGroup.create(ADA, BO, CY)
    card = await _walk(group)
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")  # Ramen: Ada
    await group.press(ADA, card, "Cy")
    await group.press(ADA, card, "Next ▶")  # Udon: Cy
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next ▶")  # Gyoza: Bo
    await group.press(ADA, card, "Edit")
    assert "Who shares this?" in card.text
    await group.press(ADA, card, "✓ Cy")
    await group.press(ADA, card, "Next")
    await group.press(ADA, card, "Go item by item ▶")
    # Cy's Udon now has nobody: the walk opens there, with the others' picks kept.
    assert "(2 of 3)\n<b>Udon</b>" in card.text
    assert card.buttons[:2] == ["Ada", "Bo"]
    draft = await _draft(group, card)
    assert draft.item_claims[0] == (ADA.id,) and draft.item_claims[2] == (BO.id,)
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next ▶")
    assert card.text.startswith("<b>Check before saving</b>")


async def test_edit_with_every_item_still_assigned_goes_to_the_summary() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    for _ in range(3):
        await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Edit")
    await group.press(ADA, card, "Next")
    await group.press(ADA, card, "Go item by item ▶")
    assert card.text.startswith("<b>Check before saving</b>")


async def test_one_participant_needs_no_walk() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send_receipt(ADA, RESTAURANT)
    await group.press(ADA, card, "Choose people")
    await group.press(ADA, card, "Next")  # only Ada ticked
    assert card.text.startswith("<b>Check before saving</b>")
    assert "• Ada ¥2,640" in card.text


async def test_balances_reflect_an_itemised_expense() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Confirm")
    # Ada paid ¥2,640 = S$21.38; Bo's share 800 + 300 = 1,100 + tax 110 = ¥1,210 = S$9.80.
    balances = await group.command(BO, balances_command)
    assert "• Bo owes <b>S$9.80</b>\n   (paid S$0.00, share S$9.80)" in balances


# -- Lines with a quantity: one screen per unit (decided 2026-10-04) --------------


def _receipt_with_quantities(
    items: list[tuple[str, int, int]], total: int, *, currency: str = "JPY"
) -> ReceiptExtraction:
    """Line items as (description, quantity, amount)."""
    return ReceiptExtraction(
        merchant="Moon Coffee",
        currency=currency,
        transaction_datetime=None,
        line_items=[
            {"description": d, "quantity": q, "amount_minor": a, "kind": "item"}
            for d, q, a in items
        ],
        candidate_totals=[
            {"label_as_printed": "合計", "amount_minor": total, "is_grand_total_guess": True}
        ],
        description="Tea",
        category="food",
        confidence=0.9,
    )


async def test_units_of_a_line_are_separate_screens() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group, _receipt_with_quantities([("Tea", 3, 2835), ("Cake", 1, 500)], 3335))
    assert "(1 of 4)\n<b>Tea (1/3)</b> · ¥945" in card.text
    for name in ("Ada", "Ada", "Bo", "Bo"):
        await group.press(ADA, card, name)
        await group.press(ADA, card, "Next ▶")
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    assert {s.user_id: s.share_trip_minor for s in expense.splits} == {ADA.id: 1890, BO.id: 1445}
    assert group.ledger.assignments_for(draft_id)[2] == ([BO.id], "claimed")


async def test_zero_amount_items_get_no_screen() -> None:
    group = await FakeGroup.create(ADA, BO)
    receipt = _receipt([("Set", 1000, "item"), ("Salad (in set)", 0, "item")], 1000)
    card = await _walk(group, receipt)
    assert "(1 of 1)" in card.text
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    assert "• Ada ¥1,000" in card.text


# -- The receipt total gap: shared like tax (sprint 13) ---------------------------


async def test_picked_total_above_the_lines_gets_continue_and_an_unlisted_line() -> None:
    group = await FakeGroup.create(ADA, BO)
    receipt = _receipt([("Ramen", 1000, "item"), ("Udon", 800, "item")], 1800, 2000)
    card = await group.send_receipt(ADA, receipt)
    await group.press(ADA, card, "¥2,000")
    assert "(¥200 isn't on any line; it'll be shared like tax)" in card.text
    assert card.buttons == ["Continue with ¥2,000", "Cancel"]
    await group.press(ADA, card, "Continue with ¥2,000")
    await group.press(ADA, card, "Me")  # Who paid?
    await group.press(ADA, card, "Everyone")
    assert "🍽 Go item by item ▶" in card.buttons
    await group.press(ADA, card, "Go item by item ▶")
    assert "(1 of 2)" in card.text  # the gap gets no screen
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next ▶")
    # Ada 1,000 + 111, Bo 800 + 89 (200 in proportion 1,000 : 800).
    assert "• Ada ¥1,111 (S$9.00)\n   items ¥1,000 + unlisted ¥111" in card.text
    assert "• Bo ¥889 (S$7.20)\n   items ¥800 + unlisted ¥89" in card.text
    assert "Shared in proportion: Unlisted / rounding ¥200" in card.text
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    assert expense.trip_amount_minor == 2000
    assert sum(s.share_trip_minor for s in expense.splits) == 2000
    assert sum(s.share_home_minor for s in expense.splits) == expense.home_amount_minor


async def test_picked_total_below_the_lines_is_an_unlisted_discount() -> None:
    group = await FakeGroup.create(ADA, BO)
    receipt = _receipt([("Wagyu", 3300, "item")], 3300, 3000)
    card = await group.send_receipt(ADA, receipt)
    await group.press(ADA, card, "¥3,000")
    await group.press(ADA, card, "Continue with ¥3,000")
    await group.press(ADA, card, "Me")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Go item by item ▶")
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    assert "items ¥3,300 − unlisted ¥300" in card.text
    assert "Shared in proportion: Unlisted / rounding −¥300" in card.text
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    assert {s.user_id: s.share_trip_minor for s in expense.splits} == {ADA.id: 3000, BO.id: 0}


async def test_auto_picked_total_within_five_gets_an_unlisted_line_not_a_screen() -> None:
    group = await FakeGroup.create(ADA, BO)
    receipt = _receipt([("Ramen", 1000, "item"), ("Udon", 798, "item")], 1800)
    card = await _walk(group, receipt)
    assert "(1 of 2)" in card.text
    draft = await _draft(group, card)
    assert draft.line_items[-1].description == "Unlisted / rounding"
    assert draft.line_items[-1].kind == "service_charge"
    assert draft.line_items[-1].amount_minor == 2


async def test_other_amount_typed_total_keeps_items_and_reconciles() -> None:
    group = await FakeGroup.create(ADA, BO)
    receipt = _receipt([("Ramen", 1000, "item"), ("Udon", 800, "item")], 1800, 2000)
    card = await group.send_receipt(ADA, receipt)
    assert card.buttons == ["¥2,000", "¥1,800", "Other amount", "Cancel"]
    await group.press(ADA, card, "Other amount")
    await group.send(ADA, "1900", reply_to=card)
    card = group.cards[card.message_id]
    assert "difference ¥100" in card.text
    assert card.buttons == ["Continue with ¥1,900", "Cancel"]


async def test_other_amount_matching_the_lines_goes_to_the_card() -> None:
    group = await FakeGroup.create(ADA, BO)
    receipt = _receipt([("Ramen", 1000, "item"), ("Udon", 800, "item")], 1700, 2000)
    card = await group.send_receipt(ADA, receipt)
    await group.press(ADA, card, "Other amount")
    await group.send(ADA, "1800", reply_to=card)
    card = group.cards[card.message_id]
    assert card.text.endswith("Who paid?")
    await group.press(ADA, card, "Me")
    await group.press(ADA, card, "Everyone")
    assert "🍽 Go item by item ▶" in card.buttons


# -- The receipt that started Sprint 13 ---------------------------------------------


async def test_eggtisan_one_set_each_way() -> None:
    """Two egg sando sets: one eaten by Ada, the other shared by Bo and Cy. The
    reader missed the ฿0.29 rounding line, so the lines come to ฿1,226.71.
    """
    group = await FakeGroup.create(ADA, BO, CY, trip_currency="THB")
    receipt = ReceiptExtraction(
        merchant="Eggtisan",
        currency="THB",
        transaction_datetime=None,
        line_items=[
            {
                "description": "Egg Sando Set",
                "quantity": 2,
                "unit_amount_minor": 33373,
                "amount_minor": 66746,
                "kind": "item",
            },
            {
                "description": "KhaoSoi Chicken",
                "quantity": 1,
                "amount_minor": 34000,
                "kind": "item",
            },
            {"description": "Yuzu Earl Grey", "quantity": 1, "amount_minor": 13900, "kind": "item"},
            {"description": "VAT 7%", "quantity": 1, "amount_minor": 8025, "kind": "tax"},
        ],
        candidate_totals=[
            {"label_as_printed": "Total", "amount_minor": 122700, "is_grand_total_guess": True}
        ],
        description="Eggtisan",
        category="food",
        confidence=0.9,
    )
    card = await group.send_receipt(ADA, receipt)
    # One printed total, 29 off the lines: the prompt, then Continue.
    assert card.buttons == ["Continue with ฿1,227.00", "Cancel"]
    await group.press(ADA, card, "Continue with ฿1,227.00")
    await group.press(ADA, card, "Me")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Go item by item ▶")

    assert "(1 of 4)\n<b>Egg Sando Set (1/2)</b> · ฿333.73" in card.text
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    assert "(2 of 4)\n<b>Egg Sando Set (2/2)</b> · ฿333.73" in card.text
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Cy")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next ▶")  # KhaoSoi
    await group.press(ADA, card, "Cy")
    await group.press(ADA, card, "Next ▶")  # Earl Grey
    assert "Shared in proportion: VAT 7% ฿80.25 · Unlisted / rounding ฿0.29" in card.text

    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    shares = {s.user_id: s.share_trip_minor for s in expense.splits}
    assert sum(shares.values()) == 122700
    assert sum(s.share_home_minor for s in expense.splits) == expense.home_amount_minor
    # Item subtotals: Ada 333.73; Bo 166.87 + 340.00; Cy 166.86 + 139.00, each
    # scaled up by the same ~7.02% of VAT and rounding.
    assert shares == {ADA.id: 35717, BO.id: 54248, CY.id: 32735}
