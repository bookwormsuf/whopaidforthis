"""Sprint 14: `Not on this bill` on "Who had this?". On a group order where each
person paid their own part, the payer leaves out the dishes someone else paid for;
the adjustments are scaled to what is kept and the rounding line is worked out
again, so the shares still add up to the amount paid.

Rate in every test: 1 JPY = 0.0081 SGD (`tests.helpers.make_deps`).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.models.receipt import ReceiptLineItem
from app.services.conversion import both_amounts
from app.services.drafts import (
    KEEP_ONE_TEXT,
    UNLISTED_DESCRIPTION,
    Draft,
    DraftToast,
    billed,
    items_spec,
    with_unlisted_line,
)
from app.services.llm_schemas import ReceiptExtraction
from app.services.receipt_parser import SYSTEM_PROMPT_TEMPLATE, line_item_sums
from app.services.splitting import Items, shares_in_both_currencies, split_items
from tests.group_sim import ADA, BO, CY, Card, FakeGroup
from tests.test_item_claiming import RESTAURANT, _draft, _fix_buttons, _receipt, _walk

LEAVE_OUT = "🚫 Not on this bill"


# -- The button on "Who had this?" ------------------------------------------------


async def test_not_on_this_bill_clears_names_and_moves_on() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, LEAVE_OUT)
    assert "(2 of 3)" in card.text
    draft = await _draft(group, card)
    assert draft.left_out == frozenset({0})
    assert draft.item_claims.get(0, ()) == ()


async def test_not_on_this_bill_on_the_last_item_goes_to_the_summary() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, LEAVE_OUT)  # Gyoza ¥600
    assert "Check before saving" in card.text
    assert "Not on this bill: Gyoza" in card.text
    # Kept items ¥1,800 of ¥2,400: tax scaled to ¥180, amount ¥2,640 → rounding ¥660.
    assert "Shared in proportion: Tax 10% ¥180 · Unlisted / rounding ¥660" in card.text


async def test_back_on_a_left_out_item_shows_it_and_next_moves_on() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    await group.press(ADA, card, LEAVE_OUT)
    await group.press(ADA, card, "◀ Back")
    assert "<b>Ramen</b> · ¥1,000\nNot on this bill" in card.text
    assert card.buttons[:2] == ["Ada", "Bo"]
    await group.press(ADA, card, "Next ▶")  # answered: left out
    assert "(2 of 3)" in card.text


async def test_ticking_a_name_puts_a_left_out_item_back() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    await group.press(ADA, card, LEAVE_OUT)
    await group.press(ADA, card, "◀ Back")
    await group.press(ADA, card, "Bo")
    assert "Not on this bill" not in card.text.split("\n")[-1]
    draft = await _draft(group, card)
    assert draft.left_out == frozenset()
    assert draft.item_claims[0] == (BO.id,)


async def test_everyone_puts_a_left_out_item_back() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    await group.press(ADA, card, LEAVE_OUT)
    await group.press(ADA, card, "◀ Back")
    await group.press(ADA, card, "Everyone")
    draft = await _draft(group, card)
    assert draft.left_out == frozenset()
    assert 0 in draft.everyone_items


async def test_leaving_out_every_item_is_refused() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    await group.press(ADA, card, LEAVE_OUT)
    await group.press(ADA, card, LEAVE_OUT)
    assert "(3 of 3)" in card.text
    assert LEAVE_OUT not in card.buttons  # the only item still on the bill
    with pytest.raises(DraftToast) as refused:
        await group.deps.draft_service.walk_left_out(card.draft_id, ADA.id, 2)
    assert str(refused.value) == KEEP_ONE_TEXT == "Keep at least one item, or Cancel"
    assert (await _draft(group, card)).left_out == frozenset({0, 1})


async def test_a_one_item_receipt_has_no_not_on_this_bill() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group, _receipt([("Wagyu", 3000, "item")], 3000))
    assert LEAVE_OUT not in card.buttons


async def test_a_stale_not_on_this_bill_changes_nothing() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    stale = Card(card.message_id, card.text, card.markup)
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    assert await group.press(ADA, Card(stale.message_id, "", stale.markup), LEAVE_OUT) is None
    draft = await _draft(group, card)
    assert draft.left_out == frozenset()
    assert draft.walk_index == 1


# -- Summary, Fix an item, other split methods -------------------------------------


async def _leave_out_gyoza(group: FakeGroup) -> Card:
    card = await _walk(group)
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, LEAVE_OUT)
    return card


async def test_fix_an_item_shows_a_dash_and_can_bring_it_back() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _leave_out_gyoza(group)
    await group.press(ADA, card, "Fix an item")
    assert _fix_buttons(card) == ["1 Ramen ¥1,000 · A", "2 Udon ¥800 · B", "3 Gyoza ¥600 · —"]
    await group.press(ADA, card, "🍽 3 Gyoza ¥600 · —")
    assert "Not on this bill" in card.text
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    assert "Check before saving" in card.text
    assert "Not on this bill:" not in card.text
    assert "• Ada ¥1,760" in card.text  # Ramen + Gyoza + tax, as in sprint 13


async def test_fix_an_item_can_leave_one_out_and_return_to_the_summary() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group)
    for _ in range(3):
        await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Fix an item")
    await group.press(ADA, card, "🍽 1 Ramen ¥1,000 · all")
    await group.press(ADA, card, LEAVE_OUT)
    assert "Check before saving" in card.text
    assert "Not on this bill: Ramen" in card.text


async def test_saved_expense_holds_the_kept_lines_only() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _leave_out_gyoza(group)
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    assert expense.trip_amount_minor == 2640
    assert sum(s.share_trip_minor for s in expense.splits) == 2640
    assert sum(s.share_home_minor for s in expense.splits) == expense.home_amount_minor
    lines = group.ledger.line_items_for(draft_id)
    assert [line.description for line in lines] == [
        "Ramen",
        "Udon",
        "Tax 10%",
        UNLISTED_DESCRIPTION,
    ]
    assert sum(line_item_sums(lines)) == 2640


async def test_split_equally_ignores_left_out_items() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _leave_out_gyoza(group)
    await group.press(ADA, card, "Edit")
    await group.press(ADA, card, "Next")
    await group.press(ADA, card, "Split equally")
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    assert {s.user_id: s.share_trip_minor for s in expense.splits} == {ADA.id: 1320, BO.id: 1320}
    assert len(group.ledger.line_items_for(draft_id)) == 4  # the receipt as read


async def test_nothing_left_out_is_unchanged() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await _walk(group, RESTAURANT)
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, "Everyone")
    draft = await _draft(group, card)
    assert billed(draft) is draft
    assert "Not on this bill" not in card.text


# -- The reconciliation prompt -----------------------------------------------------


async def test_total_below_the_lines_suggests_not_on_this_bill() -> None:
    group = await FakeGroup.create(ADA, BO)
    receipt = _receipt([("Ramen", 1000, "item"), ("Udon", 800, "item")], 1800, 1000)
    card = await group.send_receipt(ADA, receipt)
    await group.press(ADA, card, "¥1,000")
    assert "Paid for part of the order? Go item by item and mark the rest Not on this bill." in (
        card.text
    )
    assert "(¥800 isn't on any line; it'll be shared like tax)" in card.text
    assert card.buttons == ["Continue with ¥1,000", "Cancel"]


async def test_total_above_the_lines_keeps_the_sprint_13_line() -> None:
    group = await FakeGroup.create(ADA, BO)
    receipt = _receipt([("Ramen", 1000, "item"), ("Udon", 800, "item")], 1800, 2000)
    card = await group.send_receipt(ADA, receipt)
    await group.press(ADA, card, "¥2,000")
    assert "(¥200 isn't on any line; it'll be shared like tax)" in card.text
    assert "Paid for part" not in card.text


# -- The group order end to end ----------------------------------------------------


def _grab_order() -> ReceiptExtraction:
    """A delivery group order: everyone pays their own part. Order total ฿1,087,
    this person paid ฿764. Amounts in satang.
    """
    items = [
        ("Plastic Cutlery", 3, 900, "item"),
        ("Stir Fried Cabbage", 1, 10000, "item"),
        ("Bael Juice", 2, 6000, "item"),
        ("Water Mimosa", 1, 10000, "item"),
        ("Crab Roe Fried Rice", 1, 20500, "item"),
        ("Super Crab Fried Rice", 1, 32000, "item"),
        ("Roselle Juice", 1, 3000, "item"),
        ("Ocean Fried Rice", 1, 16500, "item"),
        ("Morning Glory", 1, 8000, "item"),
        ("Delivery fee", 1, 1600, "service_charge"),
        ("Special order fee", 1, 1800, "service_charge"),
        ("Promo code", 1, 1500, "discount"),
        ("Group order discount", 1, 3200, "discount"),
    ]
    return ReceiptExtraction(
        merchant="Grab",
        currency="THB",
        transaction_datetime=None,
        line_items=[
            {"description": d, "quantity": q, "amount_minor": a, "kind": k} for d, q, a, k in items
        ],
        candidate_totals=[
            {"label_as_printed": "ทั้งหมด", "amount_minor": 108700, "is_grand_total_guess": False},
            {"label_as_printed": "คุณจ่าย", "amount_minor": 76400, "is_grand_total_guess": True},
        ],
        description="Grab food delivery",
        category="food",
        confidence=0.9,
    )


async def test_group_order_paid_in_part_end_to_end() -> None:
    group = await FakeGroup.create(ADA, BO, trip_currency="THB")
    card = await group.send_receipt(ADA, _grab_order())
    # The paid figure is the grand-total guess, so it is the first button.
    assert card.buttons[:2] == ["฿764.00", "฿1,087.00"]
    await group.press(ADA, card, "฿764.00")
    assert "Paid for part of the order?" in card.text
    await group.press(ADA, card, "Continue with ฿764.00")
    await group.press(ADA, card, "Me")  # Who paid?
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Go item by item ▶")
    assert "(1 of 12)" in card.text

    left_out = {"Super Crab Fried Rice"}
    bo_only = {"Crab Roe Fried Rice", "Roselle Juice"}
    for _ in range(12):
        if "Check before saving" in card.text:
            break
        if any(f"<b>{name}</b>" in card.text for name in left_out):
            await group.press(ADA, card, LEAVE_OUT)
        elif any(f"<b>{name}</b>" in card.text for name in bo_only):
            await group.press(ADA, card, "Bo")
            await group.press(ADA, card, "Next ▶")
        else:
            await group.press(ADA, card, "Everyone")
    assert "Check before saving" in card.text
    assert "Not on this bill: Super Crab Fried Rice" in card.text

    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    assert expense.trip_amount_minor == 76400
    shares = {s.user_id: s.share_trip_minor for s in expense.splits}
    assert sum(shares.values()) == 76400
    assert sum(s.share_home_minor for s in expense.splits) == expense.home_amount_minor
    assert shares[BO.id] > shares[ADA.id]  # Bo had the crab roe rice on top
    lines = group.ledger.line_items_for(draft_id)
    assert not any(line.description in left_out for line in lines)
    assert sum(line_item_sums(lines)) == 76400
    # Kept items ฿749 of ฿1,069, fees and discounts scaled to match: what is left
    # over is Grab's own rounding of its split, a few baht.
    rounding = [line for line in lines if line.description == UNLISTED_DESCRIPTION]
    assert len(rounding) == 1 and rounding[0].amount_minor < 3000
    assert "⚠️" not in card.text


def test_receipt_instruction_prefers_what_this_person_paid() -> None:
    assert "you paid" in SYSTEM_PROMPT_TEMPLATE
    assert "คุณจ่าย" in SYSTEM_PROMPT_TEMPLATE
    # Not cash handed over on an ordinary receipt.
    assert "お預り" in SYSTEM_PROMPT_TEMPLATE.split("Group orders:")[1]


async def test_leaving_out_against_the_whole_order_total_warns() -> None:
    """The full order total was kept: the left-out dish comes back as rounding."""
    group = await FakeGroup.create(ADA, BO)
    card = await _leave_out_gyoza(group)  # ¥600 of ¥2,400 left out, amount still ¥2,640
    assert "⚠️ ¥660 of ¥2,640 isn't on any kept dish and is shared like tax." in card.text


async def test_units_of_one_left_out_dish_are_named_once() -> None:
    group = await FakeGroup.create(ADA, BO, trip_currency="THB")
    card = await group.send_receipt(ADA, _grab_order())
    await group.press(ADA, card, "฿764.00")
    await group.press(ADA, card, "Continue with ฿764.00")
    await group.press(ADA, card, "Me")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Go item by item ▶")
    for _ in range(12):
        if "Check before saving" in card.text:
            break
        if "Bael Juice" in card.text or "Super Crab" in card.text:
            await group.press(ADA, card, LEAVE_OUT)
        else:
            await group.press(ADA, card, "Everyone")
    assert "Not on this bill: Bael Juice, Super Crab Fried Rice\n" in card.text + "\n"


async def test_three_people_one_left_out_is_not_on_the_expense_items() -> None:
    """Cy paid for their own dish separately and is not a participant here."""
    group = await FakeGroup.create(ADA, BO, CY)
    card = await group.send_receipt(ADA, RESTAURANT)
    await group.press(ADA, card, "Choose people")
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next")
    await group.press(ADA, card, "Go item by item ▶")
    assert card.buttons[:2] == ["Ada", "Bo"]
    await group.press(ADA, card, "Ada")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "Next ▶")
    await group.press(ADA, card, LEAVE_OUT)
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    expense = group.ledger.expense_for(draft_id)
    assert {s.user_id for s in expense.splits} == {ADA.id, BO.id}
    assert sum(s.share_trip_minor for s in expense.splits) == 2640


# -- The maths, over random receipts -----------------------------------------------


def _draft_for(lines: list[ReceiptLineItem], amount: int, **fields) -> Draft:
    return Draft(
        draft_id="d",
        chat_id=-1,
        message_id=1,
        bot_message_id=None,
        initiator_id=1,
        initiator_name="Ada",
        state="preview",
        amount_minor=amount,
        currency="THB",
        description="Order",
        category="food",
        merchant=None,
        source_type="receipt",
        payer_id=1,
        payer_name="Ada",
        payer_username=None,
        participant_ids=[1, 2, 3],
        **fields,
    )


@settings(max_examples=300, deadline=None)
@given(
    items=st.lists(st.integers(1, 50_000), min_size=1, max_size=12),
    minus=st.lists(st.integers(1, 5_000), max_size=2),
    adjustments=st.lists(
        st.tuples(st.sampled_from(["tax", "service_charge", "discount"]), st.integers(1, 5_000)),
        max_size=4,
    ),
    amount=st.integers(1, 1_000_000),
    data=st.data(),
)
def test_shares_and_saved_lines_add_up_to_the_amount(
    items, minus, adjustments, amount, data
) -> None:
    lines = [
        ReceiptLineItem(description=f"I{n}", quantity=1, amount_minor=a, kind="item")
        for n, a in enumerate(items)
    ]
    lines += [
        ReceiptLineItem(description=k, quantity=1, amount_minor=a, kind=k) for k, a in adjustments
    ]
    # An order-wide voucher read as a minus item: scaled like a discount. Kept small
    # enough that the kept items still cost something to spread adjustments over.
    assume(2 * sum(minus) <= min(items))
    lines += [
        ReceiptLineItem(description=f"V{n}", quantity=1, amount_minor=-a, kind="item")
        for n, a in enumerate(minus)
    ]
    lines = with_unlisted_line(lines, amount)
    count = len(items)
    left_out = data.draw(st.frozensets(st.integers(0, count - 1), max_size=count - 1))
    claims = {
        i: tuple(data.draw(st.lists(st.sampled_from([1, 2, 3]), min_size=1, unique=True)))
        for i in range(count)
        if i not in left_out
    }
    draft = _draft_for(lines, amount, line_items=lines, item_claims=claims, left_out=left_out)
    draft = replace(draft, split=items_spec(draft))
    bill = billed(draft)
    assert isinstance(bill.split, Items)
    assert sum(line_item_sums(bill.line_items)) == amount
    assert not any(
        bill.line_items[i].description in {f"I{n}" for n in left_out}
        for i in range(len(bill.line_items))
    )
    split = split_items(bill.line_items, bill.split, [1, 2, 3], 1, amount)
    assert sum(split.shares.values()) == amount
    # And in both currencies (CLAUDE.md): a THB receipt settled in SGD.
    rate = Decimal("0.0379")
    trip_amount, home_amount = both_amounts(amount, "THB", "THB", "SGD", rate)
    trip_shares, home_shares = shares_in_both_currencies(
        split.shares,
        original_currency="THB",
        trip_currency="THB",
        home_currency="SGD",
        trip_to_home_rate=rate,
        trip_amount_minor=trip_amount,
        home_amount_minor=home_amount,
        payer_id=1,
    )
    assert sum(trip_shares.values()) == trip_amount == amount
    assert sum(home_shares.values()) == home_amount
