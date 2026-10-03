"""Multi-user draft flows through the real handlers (sprint 5). These stand in for
the sprint 5 checklist steps that need a second person (decision 2026-10-01);
each test names the checklist step it covers.

Rate in every test: 1 JPY = 0.0081 SGD (`tests.helpers.make_deps`).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.handlers.balances import balances_command, mine_command, settle_command
from app.bot.handlers.reads import undo_callback, undo_command
from app.services.drafts import CUSTOM_NOT_BALANCED_TEXT, NOBODY_SELECTED_TEXT
from tests.group_sim import ADA, BO, CY, FakeGroup, Member


def _shares(group: FakeGroup, draft_id: str) -> dict[int, tuple[int, int]]:
    """The saved expense's shares, by user ID, as (trip, home) minor units."""
    expense = group.ledger.expense_for(draft_id)
    assert expense is not None
    return {s.user_id: (s.share_trip_minor, s.share_home_minor) for s in expense.splits}


def _undo_press(group: FakeGroup, member: Member, expense_id: str) -> MagicMock:
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=group.trip.chat_id, type="supergroup")
    update.callback_query.data = f"undo:{expense_id}:yes"
    update.callback_query.from_user = SimpleNamespace(
        id=member.id, first_name=member.first_name, last_name=None, username=member.username
    )
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def _confirm(group: FakeGroup, member: Member, card) -> str:
    draft_id = card.draft_id
    await group.press(member, card, "Confirm")
    assert card.text.startswith("Saved ✅"), card.text
    card.markup = None
    return draft_id


# -- Checklist step 1: split everyone ---------------------------------------


async def test_step1_split_everyone_two_people() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "6000 yen dinner split everyone")
    assert card is not None
    # Everyone selected straight from the message: the split-method card.
    assert card.buttons == ["Equally", "Custom", "Cancel"]
    assert "Shared by Ada, Bo" in card.text

    await group.press(ADA, card, "Equally")
    assert "• Ada ¥3,000 (S$24.30)" in card.text
    assert "• Bo ¥3,000 (S$24.30)" in card.text
    assert card.buttons == ["Confirm", "Edit", "Cancel"]

    draft_id = await _confirm(group, ADA, card)
    expense = group.ledger.expense_for(draft_id)
    assert expense.split_method == "equal"
    assert {s.display_name for s in expense.splits} == {"Ada", "Bo"}

    balances = await group.command(ADA, balances_command)
    assert balances.splitlines() == [
        "<b>Who owes what</b> (SGD)",
        "• Ada is owed <b>S$24.30</b>",
        "   (paid S$48.60, share S$24.30)",
        "• Bo owes <b>S$24.30</b>",
        "   (paid S$0.00, share S$24.30)",
        "Settle up with /settle",
    ]


# -- Checklist step 2: Everyone button, remainder to a non-payer ------------


async def test_step2_everyone_button_remainder_skips_payer() -> None:
    group = await FakeGroup.create(ADA, BO, CY)
    card = await group.send(ADA, "4850 yen dinner")
    assert card.buttons == ["Just me", "Everyone", "Choose people", "Cancel"]

    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Equally")
    draft_id = card.draft_id
    await _confirm(group, ADA, card)

    expense = group.ledger.expense_for(draft_id)
    trip_shares = {s.user_id: s.share_trip_minor for s in expense.splits}
    # 4850 / 3 = 1616 r 2: the two spare yen go to Bo and Cy, never the payer.
    assert trip_shares == {ADA.id: 1616, BO.id: 1617, CY.id: 1617}
    assert sum(s.share_home_minor for s in expense.splits) == expense.home_amount_minor


async def test_step2_two_people_even_split() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "4850 yen dinner")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Equally")
    assert "• Ada ¥2,425" in card.text and "• Bo ¥2,425" in card.text


# -- Checklist step 3: choose people, payer not a participant ----------------


async def test_step3_choose_people_deselect_self() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "6000 taxi")
    await group.press(ADA, card, "Choose people")
    # Initiator pre-selected.
    assert card.buttons == ["✓ Ada", "Bo", "Next", "Cancel"]

    await group.press(ADA, card, "Bo")
    await group.press(ADA, card, "✓ Ada")
    assert card.buttons == ["Ada", "✓ Bo", "Next", "Cancel"]

    # One participant: straight to the preview, no split-method card.
    await group.press(ADA, card, "Next")
    assert "• Bo ¥6,000" in card.text
    assert "Ada 💳" not in card.text  # the payer has no share line

    draft_id = await _confirm(group, ADA, card)
    assert _shares(group, draft_id) == {BO.id: (6000, 4860)}
    expense = group.ledger.expense_for(draft_id)
    assert expense.payer_user_id == ADA.id


async def test_next_with_nobody_selected_is_a_toast() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "6000 taxi")
    await group.press(ADA, card, "Choose people")
    await group.press(ADA, card, "✓ Ada")
    toast = await group.press(ADA, card, "Next")
    assert toast == NOBODY_SELECTED_TEXT
    assert card.buttons == ["Ada", "Bo", "Next", "Cancel"]  # still the same card


# -- Checklist step 4: tester B, paid by and split between -------------------


async def test_step4_paid_by_and_split_between_from_tester_b() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(BO, "3000 yen lunch paid by Ada split between me and Ada")
    # Both names resolved: participants card skipped, split method asked.
    assert card.buttons == ["Equally", "Custom", "Cancel"]
    assert "Ada paid" in card.text

    await group.press(BO, card, "Equally")
    assert "• Ada ¥1,500" in card.text
    assert "• Bo ¥1,500" in card.text

    draft_id = card.draft_id
    await _confirm(group, BO, card)
    expense = group.ledger.expense_for(draft_id)
    assert expense.payer_user_id == ADA.id
    assert expense.created_by_user_id == BO.id
    assert {s.user_id: s.share_trip_minor for s in expense.splits} == {
        ADA.id: 1500,
        BO.id: 1500,
    }


# -- Checklist step 5: the other person's buttons do nothing -----------------


@pytest.mark.parametrize("label", ["Confirm", "Edit", "Cancel"])
async def test_step5_non_initiator_gets_toast_and_no_change(label: str) -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "6000 yen dinner split everyone")
    await group.press(ADA, card, "Equally")
    before = (card.text, card.buttons)

    toast = await group.press(BO, card, label)

    assert toast == "Only Ada can edit this"
    assert (card.text, card.buttons) == before
    draft = await group.deps.draft_service._drafts.get(card.draft_id)
    assert draft.state == "preview"
    assert group.ledger.expense_for(card.draft_id) is None


async def test_non_initiator_cannot_toggle_participants() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "6000 taxi")
    await group.press(ADA, card, "Choose people")
    toast = await group.press(BO, card, "Bo")
    assert toast == "Only Ada can edit this"
    assert card.buttons == ["✓ Ada", "Bo", "Next", "Cancel"]


# -- Checklist step 6: custom amounts by reply -------------------------------


async def test_step6_custom_split_by_replies() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "9000 yen izakaya")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Custom")
    assert "Reply to this message with one line per person" in card.text
    assert "Remaining: ¥9,000 ❌" in card.text
    assert card.buttons == ["Cancel"]

    assert await group.send(ADA, "Me 4000", reply_to=card) is None  # no new draft
    card = group.cards[card.message_id]
    assert "• Ada ¥4,000" in card.text and "• Bo –" in card.text
    assert "Remaining: ¥5,000 ❌" in card.text
    assert "Confirm" not in card.buttons

    await group.send(ADA, "Bo 5000", reply_to=card)
    card = group.cards[card.message_id]
    assert "Remaining: ¥0 ✅" in card.text
    assert card.buttons == ["Confirm", "Cancel"]

    await group.press(ADA, card, "Confirm")  # custom card -> preview
    assert "• Ada ¥4,000" in card.text and "• Bo ¥5,000" in card.text
    draft_id = card.draft_id
    await _confirm(group, ADA, card)
    expense = group.ledger.expense_for(draft_id)
    assert expense.split_method == "custom"
    assert {s.user_id: s.share_trip_minor for s in expense.splits} == {ADA.id: 4000, BO.id: 5000}
    assert sum(s.share_home_minor for s in expense.splits) == expense.home_amount_minor


async def test_custom_over_total_shows_negative_remaining_and_no_confirm() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "9000 yen izakaya")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Custom")
    await group.send(ADA, "me 4000\nbo 5500", reply_to=card)
    card = group.cards[card.message_id]
    assert "Remaining: −¥500 ❌" in card.text
    assert "Confirm" not in card.buttons


async def test_custom_unmentioned_person_dropped_and_problems_listed() -> None:
    group = await FakeGroup.create(ADA, BO, CY)
    card = await group.send(ADA, "9000 yen izakaya")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Custom")
    await group.send(ADA, "ada 9000\nzed 100", reply_to=card)
    card = group.cards[card.message_id]
    assert "⚠️ Who is 'zed'?" in card.text
    assert card.buttons == ["Confirm", "Cancel"]
    await group.press(ADA, card, "Confirm")
    draft_id = card.draft_id
    await _confirm(group, ADA, card)
    assert [s.user_id for s in group.ledger.expense_for(draft_id).splits] == [ADA.id]


async def test_custom_reply_from_someone_else_is_ignored() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "9000 yen izakaya")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Custom")
    before = card.text
    # Bo's reply neither changes the card nor starts a ¥5,000 expense.
    assert await group.send(BO, "Bo 5000", reply_to=card) is None
    assert group.cards[card.message_id].text == before
    assert group.replies == [group.replies[0]]  # only Ada's original card


async def test_custom_confirm_pressed_while_unbalanced_is_a_toast() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "9000 yen izakaya")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Custom")
    await group.send(ADA, "me 9000", reply_to=card)
    card = group.cards[card.message_id]
    stale = card.callback_for("Confirm")
    await group.send(ADA, "me 4000", reply_to=card)  # Confirm disappears ...
    card = group.cards[card.message_id]
    assert "Confirm" not in card.buttons
    # ... but a press already in flight still carries the old callback.
    card.markup = InlineKeyboardMarkup([[InlineKeyboardButton("Confirm", callback_data=stale)]])
    toast = await group.press(ADA, card, "Confirm")
    assert toast == CUSTOM_NOT_BALANCED_TEXT


# -- Edit --------------------------------------------------------------------


async def test_edit_returns_to_participants_keeping_amount_and_payer() -> None:
    group = await FakeGroup.create(ADA, BO, CY)
    card = await group.send(BO, "6000 yen dinner paid by Ada split between me and Ada")
    await group.press(BO, card, "Equally")
    await group.press(BO, card, "Edit")
    assert card.buttons == ["✓ Ada", "✓ Bo", "Cy", "Next", "Cancel"]
    assert "¥6,000" in card.text and "Ada paid" in card.text

    await group.press(BO, card, "Cy")
    await group.press(BO, card, "Next")
    await group.press(BO, card, "Equally")
    assert "¥6,000" in card.text
    assert "• Ada ¥2,000" in card.text
    assert "• Bo ¥2,000" in card.text and "• Cy ¥2,000" in card.text


# -- Name questions ------------------------------------------------------------


async def test_ambiguous_name_asks_with_candidates() -> None:
    jo_tan = Member(4, "Jo", "jotan", "Tan")
    jo_lim = Member(5, "Jo", "jolim", "Lim")
    group = await FakeGroup.create(ADA, jo_tan, jo_lim)
    card = await group.send(ADA, "6000 yen dinner split between me and jo")
    assert "Who did you mean by 'jo'?" in card.text
    assert card.buttons == ["Jo Lim", "Jo Tan", "Not sure, choose people", "Cancel"]

    await group.press(ADA, card, "Jo Tan")
    assert card.buttons == ["Equally", "Custom", "Cancel"]
    assert "Shared by Ada, Jo Tan" in card.text


async def test_full_display_name_resolves_without_asking() -> None:
    jo_tan = Member(4, "Jo", "jotan", "Tan")
    jo_lim = Member(5, "Jo", "jolim", "Lim")
    group = await FakeGroup.create(ADA, jo_tan, jo_lim)
    card = await group.send(ADA, "6000 yen dinner split between me and Jo Lim")
    assert "Shared by Ada, Jo Lim" in card.text


async def test_unknown_name_not_sure_goes_to_choose_people() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "6000 yen dinner split between me and Zed")
    assert "Who did you mean by 'Zed'?" in card.text
    assert card.buttons == ["Ada", "Bo", "Not sure, choose people", "Cancel"]

    await group.press(ADA, card, "Not sure, choose people")
    assert card.buttons == ["✓ Ada", "Bo", "Next", "Cancel"]


async def test_unknown_payer_not_sure_keeps_sender_and_chooses_people() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "6000 yen dinner paid by Zed split between me and bo")
    assert "(paid by)" in card.text
    assert "Not sure, choose people" in card.buttons
    await group.press(ADA, card, "Not sure, choose people")
    assert card.buttons == ["✓ Ada", "✓ Bo", "Next", "Cancel"]
    assert "Ada paid" in card.text


async def test_ambiguous_payer_pick() -> None:
    jo_tan = Member(4, "Jo", "jotan", "Tan")
    jo_lim = Member(5, "Jo", "jolim", "Lim")
    group = await FakeGroup.create(ADA, jo_tan, jo_lim)
    card = await group.send(ADA, "6000 yen dinner paid by jo")
    await group.press(ADA, card, "Jo Lim")
    assert "Jo Lim paid" in card.text
    assert card.buttons == ["Just me", "Everyone", "Choose people", "Cancel"]


# -- Checklist steps 7 and 8: balances, settle, undo --------------------------


async def test_steps7_and_8_balances_settle_and_undo() -> None:
    group = await FakeGroup.create(ADA, BO, CY)
    # Ada pays ¥9,000 for everyone; Bo pays ¥3,000 for Bo and Cy.
    card = await group.send(ADA, "9000 yen dinner split everyone")
    await group.press(ADA, card, "Equally")
    await _confirm(group, ADA, card)
    card = await group.send(BO, "3000 yen taxi split between me and cy")
    await group.press(BO, card, "Equally")
    await _confirm(group, BO, card)

    # Home: dinner S$72.90 = 24.30 each; taxi S$24.30 = 12.15 each.
    # Ada +48.60; Bo 24.30 - 24.30 - 12.15 = -12.15; Cy -24.30 - 12.15 = -36.45.
    balances = await group.command(CY, balances_command)
    assert balances.splitlines()[1:7] == [
        "• Ada is owed <b>S$48.60</b>",
        "   (paid S$72.90, share S$24.30)",
        "• Bo owes <b>S$12.15</b>",
        "   (paid S$24.30, share S$36.45)",
        "• Cy owes <b>S$36.45</b>",
        "   (paid S$0.00, share S$36.45)",
    ]
    settle = await group.command(CY, settle_command)
    assert settle.splitlines() == [
        "<b>To settle up:</b>",
        "• Cy pays Ada <b>S$36.45</b>",
        "• Bo pays Ada <b>S$12.15</b>",
    ]

    mine = await group.command(BO, mine_command)
    assert "You paid S$24.30 for the group." in mine
    assert "Your share of everything is S$36.45." in mine
    assert "➡️ You owe <b>S$12.15</b> overall." in mine
    assert "Dinner" in mine and "your share S$24.30" in mine
    assert "Taxi" in mine and "your share S$12.15" in mine

    # Step 8: Bo undoes the taxi; balances move back to the dinner alone.
    await group.command(BO, undo_command)
    expenses = await group.ledger.list_expenses(group.trip)
    taxi_id = next(e.expense_id for e in expenses if e.description == "Taxi")
    await undo_callback(_undo_press(group, BO, taxi_id), group._context())

    balances = await group.command(CY, balances_command)
    assert balances.splitlines()[1:7] == [
        "• Ada is owed <b>S$48.60</b>",
        "   (paid S$72.90, share S$24.30)",
        "• Bo owes <b>S$24.30</b>",
        "   (paid S$0.00, share S$24.30)",
        "• Cy owes <b>S$24.30</b>",
        "   (paid S$0.00, share S$24.30)",
    ]


async def test_settle_all_square() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "1200 yen ramen")
    await group.press(ADA, card, "Just me")
    await _confirm(group, ADA, card)
    assert await group.command(BO, settle_command) == "All square 🎉"


async def test_balances_with_no_expenses() -> None:
    group = await FakeGroup.create(ADA)
    assert await group.command(ADA, balances_command) == "No expenses yet."


async def test_undo_by_someone_else_names_the_creator_not_the_payer() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(BO, "3000 yen lunch paid by Ada")
    await group.press(BO, card, "Just me")
    draft_id = await _confirm(group, BO, card)

    update = _undo_press(group, ADA, draft_id)
    await undo_callback(update, group._context())
    update.callback_query.answer.assert_awaited_once_with("Only Bo can undo this")


async def test_home_currency_custom_split() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "S$30 grab")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Custom")
    await group.send(ADA, "me S$10\nbo S$20", reply_to=card)
    card = group.cards[card.message_id]
    assert "Remaining: S$0.00 ✅" in card.text
    await group.press(ADA, card, "Confirm")
    assert "• Bo S$20.00" in card.text  # home-currency expense: home only
    draft_id = card.draft_id
    await _confirm(group, ADA, card)
    expense = group.ledger.expense_for(draft_id)
    assert {s.user_id: s.share_home_minor for s in expense.splits} == {ADA.id: 1000, BO.id: 2000}
    assert sum(s.share_trip_minor for s in expense.splits) == expense.trip_amount_minor


async def test_home_currency_custom_split_with_bare_numbers() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "S$12 grab")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Custom")
    await group.send(ADA, "Me 6\nBo 6", reply_to=card)
    card = group.cards[card.message_id]
    assert "Remaining: S$0.00 ✅" in card.text
    assert card.buttons == ["Confirm", "Cancel"]


async def test_settle_warns_when_sheet_shares_do_not_add_up() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "6000 yen dinner split everyone")
    await group.press(ADA, card, "Equally")
    draft_id = await _confirm(group, ADA, card)
    # Simulate a hand edit: drop Bo's Splits row.
    expense = group.ledger.expense_for(draft_id)
    bo_split = next(s for s in expense.splits if s.user_id == BO.id)
    real = group.ledger.list_splits

    async def edited(trip):
        return [s for s in await real(trip) if s.user_id != bo_split.user_id]

    group.ledger.list_splits = edited
    settle = await group.command(ADA, settle_command)
    assert settle.startswith("⚠️ The Sheet's shares don't add up") and "S$24.30" in settle
    balances = await group.command(ADA, balances_command)
    assert "⚠️" in balances


async def test_custom_amount_in_the_other_currency_is_rejected() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "9000 yen izakaya")
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Custom")
    await group.send(ADA, "me S$10", reply_to=card)
    card = group.cards[card.message_id]
    assert "Use ¥ amounts for this expense" in card.text


async def test_who_paid_step_when_nobody_is_tagged() -> None:
    group = await FakeGroup.create(ADA, BO)
    card = await group.send(ADA, "6000 yen dinner", who_paid=False)
    assert card.text.endswith("Who paid?")
    assert card.buttons == ["Me", "Bo", "Cancel"]
    # Only the sender answers it.
    assert await group.press(BO, card, "Bo") == "Only Ada can edit this"
    await group.press(ADA, card, "Bo")
    assert "Bo paid" in card.text
    assert card.buttons == ["Just me", "Everyone", "Choose people", "Cancel"]
    await group.press(ADA, card, "Everyone")
    await group.press(ADA, card, "Equally")
    draft_id = card.draft_id
    await group.press(ADA, card, "Confirm")
    assert group.ledger.expense_for(draft_id).payer_user_id == BO.id


async def test_no_who_paid_step_when_tagged_or_paid_by() -> None:
    group = await FakeGroup.create(ADA, BO)
    for text in ("6000 yen dinner @bo", "6000 yen dinner paid by bo"):
        card = await group.send(ADA, text, who_paid=False)
        assert "Bo paid" in card.text and not card.text.endswith("Who paid?"), text


async def test_no_who_paid_step_when_alone() -> None:
    group = await FakeGroup.create(ADA)
    card = await group.send(ADA, "6000 yen dinner", who_paid=False)
    assert card.buttons == ["Just me", "Everyone", "Choose people", "Cancel"]


async def test_saved_line_with_a_blank_name() -> None:
    blank = Member(9, " ", None)
    group = await FakeGroup.create(blank)
    card = await group.send(blank, "1200 yen ramen")
    await group.press(blank, card, "Just me")
    await group.press(blank, card, "Confirm")
    assert card.text.startswith("Saved ✅ Ramen ¥1,200")
