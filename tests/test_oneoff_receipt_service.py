"""Unit tests for the one-off bill's receipt states and the item-by-item split (spec §23,
sprint 18). No Telegram anywhere. Parity tests feed the same receipts to this module's
copies of two rules and to the group draft engine's.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.models.receipt import ReceiptLineItem
from app.services import drafts, oneoff
from app.services.oneoff import (
    BillLine,
    OneOffBill,
    OneOffStore,
    Refused,
    StepError,
    Toast,
    Updated,
)
from app.services.receipt_parser import to_receipt
from app.services.splitting import split_items
from tests.receipt_fixtures import dinner, extraction, line, total

T0 = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


# ---- building bills at each step ----------------------------------------------


def _reading(first_name: str = "Sufyan") -> OneOffBill:
    bill = OneOffStore().create(user_id=1, chat_id=1, first_name=first_name, now=T0)
    return oneoff.begin_read(bill, "file-1")


def _read(ext=None, currency: str = "SGD") -> OneOffBill:
    receipt = to_receipt(ext or dinner(), currency)
    return oneoff.apply_receipt(_reading(), receipt)


def _at_method(ext=None, count: int = 2, payer: int = 1) -> OneOffBill:
    bill = _read(ext)
    if bill.state == "reconcile":
        bill = oneoff.continue_total(bill)
    assert bill.state == "count"
    bill = oneoff.set_count(bill, count)
    return oneoff.set_payer(oneoff.to_payer(bill), payer)


def _walk_bill(ext=None, count: int = 2, payer: int = 1) -> OneOffBill:
    return oneoff.start_walk(_at_method(ext, count, payer))


def _claim(bill: OneOffBill, *persons: int) -> OneOffBill:
    """On the item on screen: tick `persons`, then Next."""
    position = bill.split.walk_index  # type: ignore[union-attr]
    for person in persons:
        bill = oneoff.walk_toggle(bill, position, person)
    return oneoff.walk_next(bill, position)


def _finish(bill: OneOffBill, *claims: tuple[int, ...]) -> OneOffBill:
    for persons in claims:
        bill = _claim(bill, *persons)
    return bill


# ---- reading states -----------------------------------------------------------


def test_a_confident_receipt_goes_straight_to_how_many_with_the_receipts_own_words() -> None:
    bill = _read()
    assert bill.state == "count"
    assert (bill.description, bill.amount_minor, bill.currency) == ("Ramen House", 8450, "SGD")
    assert bill.photo_file_id == "file-1" and bill.from_receipt
    assert [i.description for i in bill.line_items] == ["Ramen", "Gyoza", "Beer", "GST", "Service"]
    assert len(oneoff.claimable_indices(bill)) == 3


def test_no_description_is_called_receipt() -> None:
    ext = extraction([line("Tea", 500)], [total(500)], description="", merchant=None)
    assert _read(ext).description == "Receipt"


def test_the_currency_is_the_readers_if_iso_else_sgd() -> None:
    assert oneoff.resolve_bill_currency("jpy") == "JPY"
    assert oneoff.resolve_bill_currency(" thb ") == "THB"
    assert oneoff.resolve_bill_currency("RMB") == "SGD"  # not an ISO code
    assert oneoff.resolve_bill_currency("S$") == "SGD"
    assert oneoff.resolve_bill_currency(None) == "SGD"
    assert oneoff.resolve_bill_currency("") == "SGD"


def test_two_plausible_totals_ask_which() -> None:
    ext = extraction(
        [line("Ramen", 2000), line("Gyoza", 1200)],
        [total(3200, guess=False, label="Subtotal"), total(3520, guess=False, label="Total")],
    )
    bill = _read(ext)
    assert bill.state == "choose_total"
    assert [c.amount_minor for c in bill.candidates] == [3520, 3200]  # largest first
    picked = oneoff.choose_total(bill, 1)
    # 3200 is the lines' own sum: nothing to reconcile, straight to how many.
    assert picked.state == "count" and picked.amount_minor == 3200
    far = oneoff.choose_total(bill, 0)  # 3520 is 320 off the lines
    assert far.state == "reconcile" and far.amount_minor == 3520


def test_choose_total_refuses_a_candidate_that_is_not_there_and_the_wrong_step() -> None:
    bill = _read(
        extraction([line("A", 100)], [total(100, guess=False), total(200, guess=False, label="x")])
    )
    with pytest.raises(StepError):
        oneoff.choose_total(bill, 2)
    with pytest.raises(StepError):
        oneoff.choose_total(bill, -1)
    with pytest.raises(StepError):
        oneoff.choose_total(_read(), 0)


def _asking() -> OneOffBill:
    ext = extraction(
        [line("Ramen", 2000), line("Gyoza", 1200)],
        [total(3200, guess=False), total(3520, guess=False, label="Total")],
    )
    return oneoff.ask_other_amount(_read(ext))


def test_other_amount_reads_a_good_reply_as_the_total_in_the_receipts_currency() -> None:
    bill = _asking()
    assert bill.state == "other_amount"
    reading = oneoff.read_text(bill, "32.00", "bot")
    assert isinstance(reading, Updated)
    assert reading.bill.state == "count" and reading.bill.amount_minor == 3200


def test_other_amount_far_from_the_lines_asks_to_reconcile() -> None:
    reading = oneoff.read_text(_asking(), "S$50", "bot")
    assert isinstance(reading, Updated)
    assert reading.bill.state == "reconcile" and reading.bill.amount_minor == 5000


@pytest.mark.parametrize("reply", ["abc", "", "dinner 32", "0", "-5", "32 for dinner", "500 yen"])
def test_other_amount_refuses_anything_that_is_not_just_an_amount(reply: str) -> None:
    reading = oneoff.read_text(_asking(), reply, "bot")
    assert reading == Refused("bad_amount")


def test_other_amount_in_a_yen_receipt_takes_whole_yen() -> None:
    ext = extraction(
        [line("Ramen", 800), line("Tea", 300)],
        [total(1100, guess=False), total(1210, guess=False, label="Total")],
        currency="JPY",
    )
    bill = oneoff.ask_other_amount(_read(ext, "JPY"))
    reading = oneoff.read_text(bill, "1,100", "bot")
    assert isinstance(reading, Updated) and reading.bill.amount_minor == 1100


def test_a_total_more_than_five_off_the_lines_is_reconciled_and_five_is_not() -> None:
    near = extraction([line("Ramen", 3200)], [total(3205)])
    assert _read(near).state == "count"  # 5 minor units off: no card
    far = extraction([line("Ramen", 3200)], [total(3206)])
    assert _read(far).state == "reconcile"


def test_continue_adds_the_gap_as_an_unlisted_service_charge() -> None:
    ext = extraction([line("Ramen", 2000), line("Gyoza", 1200)], [total(4000)])
    bill = _read(ext)
    assert bill.state == "reconcile" and bill.amount_minor == 4000
    assert oneoff.reconciliation_sums(bill) == (3200, 0)
    bill = oneoff.continue_total(bill)
    assert bill.state == "count"
    last = bill.line_items[-1]
    assert (last.description, last.kind, last.amount_minor) == (
        "Unlisted / rounding",
        "service_charge",
        800,
    )
    assert oneoff.claimable_indices(bill) == [0, 1]  # never claimable


def test_continue_with_a_total_below_the_lines_adds_an_unlisted_discount() -> None:
    ext = extraction([line("Ramen", 2000), line("Gyoza", 1200)], [total(2000)])
    bill = oneoff.continue_total(_read(ext))
    last = bill.line_items[-1]
    assert (last.kind, last.amount_minor) == ("discount", 1200)


def test_a_gap_within_five_becomes_the_unlisted_line_with_no_card() -> None:
    ext = extraction([line("Ramen", 3200)], [total(3203)])
    bill = _read(ext)
    assert bill.state == "count"
    assert bill.line_items[-1].amount_minor == 3 and bill.line_items[-1].kind == "service_charge"


def test_a_receipt_with_no_lines_has_no_unlisted_line_and_no_items() -> None:
    bill = _read(extraction([], [total(4000)]))
    assert bill.state == "count" and bill.line_items == ()
    assert not oneoff.by_items_available(bill)


def test_the_unlisted_gap_is_shared_like_tax() -> None:
    """Lines 20.00 (person 1) and 12.00 (person 2), the receipt says 40.00: the 8.00
    that is on no line is spread in proportion to what each had, as tax is.
    """
    ext = extraction([line("Ramen", 2000), line("Gyoza", 1200)], [total(4000)])
    bill = _finish(_walk_bill(ext), (1,), (2,))
    assert bill.state == "preview"
    # 800 * 2000/3200 = 500, 800 * 1200/3200 = 300
    assert bill.shares == {1: 2500, 2: 1500}
    breakdown = oneoff.item_breakdown(bill)
    unlisted_at = len(bill.line_items) - 1
    assert breakdown.line_allocations[unlisted_at] == {1: 500, 2: 300}


# ---- couldn't read ------------------------------------------------------------


def test_read_failed_then_retry_then_type_instead() -> None:
    failed = oneoff.read_failed(_reading(), "busy")
    assert failed.state == "couldnt_read" and failed.read_problem == "busy"
    again = oneoff.begin_retry(failed)
    assert again.state == "reading" and again.read_problem is None
    assert again.photo_file_id == "file-1"
    typed = oneoff.type_instead(oneoff.read_failed(again))
    assert typed.state == "awaiting_bill" and not typed.from_receipt
    assert typed.line_items == () and typed.candidates == ()


def test_the_receipt_steps_refuse_the_wrong_state() -> None:
    fresh = OneOffStore().create(user_id=1, chat_id=1, first_name="A", now=T0)
    with pytest.raises(StepError):
        oneoff.begin_retry(fresh)
    with pytest.raises(StepError):
        oneoff.read_failed(fresh)
    with pytest.raises(StepError):
        oneoff.apply_receipt(fresh, to_receipt(dinner(), "SGD"))
    with pytest.raises(StepError):
        oneoff.begin_read(_reading(), "second")  # already reading


def test_only_the_bill_line_step_and_a_running_read_hear_a_photo() -> None:
    store = OneOffStore()
    bill = store.create(user_id=1, chat_id=1, first_name="A", now=T0)
    assert store.wants_photo(1, T0)
    store.put(oneoff.begin_read(bill, "f"), T0)
    assert store.wants_photo(1, T0)  # a second photo is told to wait
    failed = oneoff.read_failed(store.for_user(1, T0))  # type: ignore[arg-type]
    store.put(failed, T0)
    assert not store.wants_photo(1, T0)


def test_typed_text_is_heard_at_other_amount_and_nowhere_else_new() -> None:
    store = OneOffStore()
    bill = store.create(user_id=1, chat_id=1, first_name="A", now=T0)
    for state, heard in [
        ("reading", False),
        ("couldnt_read", False),
        ("choose_total", False),
        ("other_amount", True),
        ("reconcile", False),
        ("item_walk", False),
        ("fix_list", False),
    ]:
        store.put(OneOffBill(**{**bill.__dict__, "state": state}), T0)
        assert store.wants_text(1, T0) is heard, state


# ---- the item split -----------------------------------------------------------


def test_a_shared_item_tax_and_service_in_proportion() -> None:
    """Ramen 20.00 to person 1, Gyoza 12.00 to person 2, Beer 30.00 shared. Tax 5.00 and
    service 17.50 are spread by item subtotals 35.00 and 27.00 (largest remainder).
    """
    bill = _finish(_walk_bill(), (1,), (2,), (1, 2))
    assert bill.state == "preview"
    breakdown = oneoff.item_breakdown(bill)
    assert breakdown.subtotals == {1: 3500, 2: 2700}
    # tax: 282.26 / 217.74 -> 282 + 218; service: 987.90 / 762.10 -> 988 + 762
    assert breakdown.adjustments["tax"] == {1: 282, 2: 218}
    assert breakdown.adjustments["service_charge"] == {1: 988, 2: 762}
    assert bill.shares == {1: 4770, 2: 3680}
    assert sum(bill.shares.values()) == bill.amount_minor == 8450


def test_an_odd_amount_shared_by_two_goes_to_the_non_payer_first() -> None:
    ext = extraction([line("Cake", 1001)], [total(1001)])
    bill = _finish(_walk_bill(ext, payer=1), (1, 2))
    assert bill.shares == {1: 500, 2: 501}
    bill = _finish(_walk_bill(ext, payer=2), (1, 2))
    assert bill.shares == {1: 501, 2: 500}


def test_a_discount_comes_off_in_proportion() -> None:
    ext = extraction(
        [line("Ramen", 1000), line("Gyoza", 1000), line("Coupon", 200, kind="discount")],
        [total(1800)],
    )
    bill = _finish(_walk_bill(ext), (1,), (2,))
    assert bill.shares == {1: 900, 2: 900}


def test_an_everyone_item_is_everyones_and_moves_on() -> None:
    bill = _walk_bill()
    bill = oneoff.walk_everyone(bill, 0)
    assert bill.split.walk_index == 1  # type: ignore[union-attr]
    assert oneoff.claimants_on_screen(bill, 0) == (1, 2)


def test_everyone_on_the_last_item_goes_to_the_summary() -> None:
    bill = _finish(_walk_bill(), (1,), (2,))  # on the third and last item
    assert bill.split.walk_index == 2  # type: ignore[union-attr]
    bill = oneoff.walk_everyone(bill, 2)
    assert bill.state == "preview"
    assert sum(bill.shares.values()) == 8450


def test_next_with_nobody_ticked_is_a_toast_and_changes_nothing() -> None:
    bill = _walk_bill()
    with pytest.raises(Toast) as raised:
        oneoff.walk_next(bill, 0)
    assert raised.value.text == "Pick who had this first"
    assert bill.split.walk_index == 0  # type: ignore[union-attr]


def test_unticking_the_only_person_leaves_the_item_unanswered() -> None:
    bill = oneoff.walk_toggle(_walk_bill(), 0, 1)
    bill = oneoff.walk_toggle(bill, 0, 1)
    with pytest.raises(Toast):
        oneoff.walk_next(bill, 0)


def test_unticking_one_person_on_an_everyone_item_leaves_the_rest() -> None:
    bill = oneoff.walk_everyone(_walk_bill(count=3), 0)
    bill = oneoff.walk_back(bill, 1)
    bill = oneoff.walk_toggle(bill, 0, 2)
    assert oneoff.claimants_on_screen(bill, 0) == (1, 3)


def test_back_keeps_picks_and_is_refused_on_the_first_item() -> None:
    bill = _claim(_walk_bill(), 2)
    bill = oneoff.walk_back(bill, 1)
    assert bill.split.walk_index == 0  # type: ignore[union-attr]
    assert oneoff.claimants_on_screen(bill, 0) == (2,)
    with pytest.raises(StepError):
        oneoff.walk_back(bill, 0)


def test_a_press_for_another_position_changes_nothing() -> None:
    bill = _claim(_walk_bill(), 1)  # now on item 2 (position 1)
    for press in (
        lambda: oneoff.walk_toggle(bill, 0, 1),
        lambda: oneoff.walk_everyone(bill, 0),
        lambda: oneoff.walk_next(bill, 0),
        lambda: oneoff.walk_back(bill, 0),
        lambda: oneoff.walk_next(bill, 2),
    ):
        with pytest.raises(StepError):
            press()


def test_a_toggle_for_someone_who_is_not_on_the_bill_is_refused() -> None:
    with pytest.raises(StepError):
        oneoff.walk_toggle(_walk_bill(count=2), 0, 3)


def test_after_the_last_item_the_walk_returns_to_the_first_one_still_empty() -> None:
    """The buttons cannot leave an earlier item empty and reach the last, but the rule
    is the group's, so it is held here: state built directly.
    """
    bill = _walk_bill()
    split = replace(bill.split, walk_index=2)  # type: ignore[type-var]
    # the last item (position 2) is on screen; item 0 is empty
    bill = replace(bill, split=split, claims={1: (1,), 2: (2,)})
    bill = _claim(bill, 1)
    assert bill.state == "item_walk" and bill.split.walk_index == 0  # type: ignore[union-attr]


def test_fix_an_item_changes_one_claim_and_returns_to_the_summary() -> None:
    bill = _finish(_walk_bill(), (1,), (2,), (1, 2))
    before = dict(bill.shares)
    bill = oneoff.fix_list(bill)
    assert bill.state == "fix_list"
    bill = oneoff.fix_pick(bill, 0)  # Ramen
    assert bill.state == "item_walk" and bill.split.walk_index == 0  # type: ignore[union-attr]
    assert oneoff.claimants_on_screen(bill, 0) == (1,)
    bill = oneoff.walk_toggle(bill, 0, 1)
    bill = oneoff.walk_toggle(bill, 0, 2)  # Ramen now person 2's
    bill = oneoff.walk_next(bill, 0)
    assert bill.state == "preview"  # not the following item
    assert bill.shares != before and sum(bill.shares.values()) == 8450
    # Ramen (line 0) moved to person 2; Gyoza and Beer are as they were.
    claims = bill.claims
    assert claims[0] == (2,) and claims[1] == (2,) and claims[2] == (1, 2)


def test_fix_back_returns_to_the_summary_and_back_from_a_fixed_item_to_the_list() -> None:
    bill = _finish(_walk_bill(), (1,), (2,), (1, 2))
    listing = oneoff.fix_list(bill)
    assert oneoff.fix_back(listing).state == "preview"
    opened = oneoff.fix_pick(listing, 1)
    assert opened.split.fixing_one  # type: ignore[union-attr]
    # Back from a fixed item goes to the list, not the previous item
    assert oneoff.walk_back(opened, 1).state == "fix_list"
    # and not with nobody ticked
    cleared = oneoff.walk_toggle(opened, 1, 2)
    with pytest.raises(Toast):
        oneoff.walk_back(cleared, 1)


def test_fix_list_and_pick_refuse_the_wrong_state_and_a_missing_item() -> None:
    bill = _walk_bill()
    with pytest.raises(StepError):
        oneoff.fix_list(bill)  # not at the summary
    done = _finish(bill, (1,), (2,), (1, 2))
    listing = oneoff.fix_list(done)
    with pytest.raises(StepError):
        oneoff.fix_pick(listing, 3)
    with pytest.raises(StepError):
        oneoff.fix_pick(done, 0)  # not on the list


def test_edit_from_an_item_summary_goes_back_to_names_and_keeps_the_picks() -> None:
    bill = _finish(_walk_bill(), (1,), (2,), (1, 2))
    back = oneoff.edit(bill)
    assert back.state == "names" and back.split is None and back.payer is None
    assert back.names == bill.names
    assert back.claims == bill.claims and back.everyone == bill.everyone  # on the receipt
    # and the walk opens with the earlier ticks in place, to the same shares
    again = oneoff.start_walk(oneoff.set_payer(oneoff.to_payer(back), 1))
    assert oneoff.claimants_on_screen(again, 0) == (1,)
    for position in range(3):  # Next on each item, ticks untouched
        again = oneoff.walk_next(again, position)
    assert again.state == "preview" and again.shares == bill.shares


def test_the_claims_are_on_the_bill_not_the_split() -> None:
    bill = _finish(_walk_bill(), (1,), (2,), (1, 2))
    assert bill.claims == {0: (1,), 1: (2,), 2: (1, 2)}
    assert not hasattr(bill.split, "claims") and not hasattr(bill.split, "everyone")


def test_a_new_read_type_instead_and_back_to_step_one_clear_the_claims() -> None:
    bill = _finish(_walk_bill(), (1,), (2,), (1, 2))
    assert bill.claims
    assert oneoff.back_to_step_one(bill).claims == {}
    failed = oneoff.read_failed(replace(bill, state="reading"))
    assert oneoff.type_instead(failed).claims == {}
    reading = oneoff.begin_read(
        replace(bill, state="awaiting_bill", photo_file_id=None), "another-photo"
    )
    assert reading.claims == {} and reading.everyone == frozenset()
    read = oneoff.apply_receipt(reading, to_receipt(dinner(), "SGD"))
    assert read.claims == {}


def test_abort_read_puts_a_reading_bill_back_where_it_was() -> None:
    reading = _reading()
    assert oneoff.abort_read(reading, "awaiting_bill", None).state == "awaiting_bill"
    back = oneoff.abort_read(reading, "couldnt_read", "busy")
    assert back.state == "couldnt_read" and back.read_problem == "busy"
    with pytest.raises(StepError):
        oneoff.abort_read(_walk_bill(), "awaiting_bill", None)


def test_a_quantity_line_becomes_one_screen_per_unit() -> None:
    ext = extraction(
        [line("Tea", 900, quantity=3), line("Rice", 500)],
        [total(1400)],
    )
    bill = _walk_bill(ext)
    assert [i.description for i in bill.line_items] == [
        "Tea (1/3)",
        "Tea (2/3)",
        "Tea (3/3)",
        "Rice",
    ]
    assert len(oneoff.claimable_indices(bill)) == 4
    bill = _finish(bill, (1,), (2,), (2,), (1, 2))
    assert sum(bill.shares.values()) == 1400


def test_units_that_would_pass_forty_items_stay_one_line() -> None:
    lines = [line(f"Dish {n}", 100, quantity=1) for n in range(39)] + [line("Tea", 300, quantity=3)]
    bill = _at_method(extraction(lines, [total(4200)]))
    walked = oneoff.start_walk(bill)
    assert len(oneoff.claimable_indices(walked)) == 40  # Tea stays one screen
    assert walked.line_items[39].description == "Tea"


def test_item_by_item_is_offered_for_one_to_forty_items_only() -> None:
    forty = [line(f"Dish {n}", 100) for n in range(40)]
    assert oneoff.by_items_available(_at_method(extraction(forty, [total(4000)])))
    forty_one = [line(f"Dish {n}", 100) for n in range(41)]
    over = _at_method(extraction(forty_one, [total(4100)]))
    assert not oneoff.by_items_available(over) and oneoff.too_many_items(over)
    with pytest.raises(StepError):
        oneoff.start_walk(over)
    none = _at_method(extraction([], [total(4000)]))
    assert not oneoff.by_items_available(none) and not oneoff.too_many_items(none)
    with pytest.raises(StepError):
        oneoff.start_walk(none)
    only_tax = _at_method(extraction([line("GST", 300, kind="tax")], [total(300)]))
    assert not oneoff.by_items_available(only_tax)


def test_a_typed_bill_never_offers_item_by_item() -> None:
    from app.services.oneoff import set_bill_line

    bill = OneOffStore().create(user_id=1, chat_id=1, first_name="A", now=T0)
    bill = set_bill_line(bill, BillLine("Dinner", 8450, "SGD"))
    assert not oneoff.by_items_available(bill)


def test_the_summary_says_split_by_items_through_the_method_property() -> None:
    bill = _finish(_walk_bill(), (1,), (2,), (1, 2))
    assert bill.method == "items"


# ---- parity with the group engine ---------------------------------------------


def _item(
    description: str,
    amount: int,
    *,
    quantity: int = 1,
    unit: int | None = None,
    kind: str = "item",
) -> ReceiptLineItem:
    return ReceiptLineItem(
        description=description,
        quantity=quantity,
        unit_amount_minor=unit,
        amount_minor=amount,
        kind=kind,  # type: ignore[arg-type]
    )


def test_the_copied_constants_match_the_group_engines() -> None:
    assert oneoff.MAX_CLAIMABLE_ITEMS == drafts.MAX_CLAIMABLE_ITEMS
    assert oneoff.MAX_UNITS_PER_LINE == drafts.MAX_UNITS_PER_LINE
    assert oneoff.UNLISTED_DESCRIPTION == drafts.UNLISTED_DESCRIPTION
    assert oneoff.PICK_FIRST_TEXT == drafts.PICK_FIRST_TEXT


_UNIT_CASES = [
    [_item("Tea", 900, quantity=3), _item("Rice", 500)],
    [_item("Tea", 1000, quantity=3)],  # 334 / 333 / 333: the remainder goes to the first
    [_item("Tea", 300, quantity=3, unit=300)],  # amount is the unit price: left as printed
    [_item("Tea", 300, quantity=3, unit=100)],
    [_item("Bulk", 1100, quantity=11)],  # over 10 units: left as printed
    [_item("Coupon", 100, quantity=2, kind="discount")],
    [_item("Free", 0, quantity=2)],
    [_item(f"Dish {n}", 100) for n in range(39)] + [_item("Tea", 300, quantity=3)],  # passes 40
    [_item(f"Dish {n}", 100) for n in range(38)] + [_item("Tea", 300, quantity=2)],  # exactly 40
    [_item("Done twice (1/2)", 100), _item("Done twice (2/2)", 100)],
    [],
]


@pytest.mark.parametrize("lines", _UNIT_CASES)
def test_one_screen_per_unit_matches_the_group_engine(lines: list[ReceiptLineItem]) -> None:
    assert oneoff.one_button_per_unit(lines) == drafts._one_button_per_unit(lines)
    # idempotent in both
    once = oneoff.one_button_per_unit(lines)
    assert oneoff.one_button_per_unit(once) == drafts._one_button_per_unit(once)


@given(
    st.lists(
        st.builds(
            _item,
            st.text(alphabet="abcdef", min_size=1, max_size=6),
            st.integers(-500, 5000),
            quantity=st.integers(1, 12),
            unit=st.one_of(st.none(), st.integers(0, 5000)),
            kind=st.sampled_from(["item", "item", "item", "tax", "service_charge", "discount"]),
        ),
        max_size=45,
    )
)
def test_one_screen_per_unit_matches_the_group_engine_on_random_receipts(lines) -> None:
    assert oneoff.one_button_per_unit(lines) == drafts._one_button_per_unit(lines)


@given(
    st.lists(
        st.builds(
            _item,
            st.text(alphabet="abcdef", min_size=1, max_size=6),
            st.integers(0, 5000),
            kind=st.sampled_from(["item", "item", "tax", "service_charge", "discount"]),
        ),
        max_size=10,
    ),
    st.integers(-1000, 60000),
)
def test_the_unlisted_line_matches_the_group_engine(lines, total_minor) -> None:
    ours = oneoff.with_unlisted_line(lines, total_minor)
    assert ours == drafts.with_unlisted_line(lines, total_minor)
    assert oneoff.with_unlisted_line(ours, total_minor) == ours  # idempotent


@pytest.mark.parametrize("total_minor", [3200, 3201, 3206, 4000, 1000, 0])
def test_the_unlisted_line_matches_the_group_engine_on_a_fixed_receipt(total_minor: int) -> None:
    lines = [_item("Ramen", 2000), _item("Gyoza", 1200), _item("GST", 100, kind="tax")]
    assert oneoff.with_unlisted_line(lines, total_minor) == drafts.with_unlisted_line(
        lines, total_minor
    )


def test_an_item_split_gives_the_same_shares_as_split_items_for_the_same_claims() -> None:
    bill = _finish(_walk_bill(count=3), (1,), (2, 3), (1, 2, 3))
    direct = split_items(
        list(bill.line_items), oneoff.items_spec(bill), [1, 2, 3], 1, bill.amount_minor
    )
    assert bill.shares == direct.shares


# ---- shares always sum (Hypothesis) -------------------------------------------


@st.composite
def _receipts(draw):
    persons = draw(st.integers(2, 20))
    amounts = draw(st.lists(st.integers(1, 100_000), min_size=1, max_size=12))
    adjustments = draw(
        st.lists(
            st.tuples(
                st.sampled_from(["tax", "service_charge", "discount"]), st.integers(1, 20_000)
            ),
            max_size=3,
        )
    )
    claims = [
        draw(st.one_of(st.just(None), st.sets(st.integers(1, persons), min_size=1)))
        for _ in amounts
    ]
    payer = draw(st.integers(1, persons))
    return persons, amounts, adjustments, claims, payer


@settings(max_examples=150, deadline=None)
@given(_receipts(), st.integers(-5000, 50_000))
def test_item_shares_always_sum_to_the_bill_amount(receipt, gap) -> None:
    persons, amounts, adjustments, claims, payer = receipt
    lines = [line(f"Dish {n}", a) for n, a in enumerate(amounts)]
    lines += [line(kind, a, kind=kind) for kind, a in adjustments]
    items_sum = sum(amounts)
    adj = sum(a if k != "discount" else -a for k, a in adjustments)
    chosen = max(items_sum + adj + gap, 1)  # a total, possibly with a gap either way
    bill = oneoff.apply_receipt(_reading(), to_receipt(extraction(lines, [total(chosen)]), "SGD"))
    if bill.state == "reconcile":
        bill = oneoff.continue_total(bill)
    assert bill.state == "count" and bill.amount_minor == chosen
    bill = oneoff.set_payer(oneoff.to_payer(oneoff.set_count(bill, persons)), payer)
    bill = oneoff.start_walk(bill)
    for who in claims:
        if who is None:
            bill = oneoff.walk_everyone(bill, bill.split.walk_index)  # type: ignore[union-attr]
        else:
            bill = _claim(bill, *sorted(who))
    assert bill.state == "preview"
    assert sum(bill.shares.values()) == chosen
    assert set(bill.shares) == set(range(1, persons + 1))


@settings(max_examples=60, deadline=None)
@given(_receipts())
def test_shares_still_sum_after_fixing_items(receipt) -> None:
    persons, amounts, adjustments, claims, payer = receipt
    lines = [line(f"Dish {n}", a) for n, a in enumerate(amounts)]
    lines += [line(kind, a, kind=kind) for kind, a in adjustments]
    chosen = max(sum(amounts) + sum(a if k != "discount" else -a for k, a in adjustments), 1)
    bill = oneoff.apply_receipt(_reading(), to_receipt(extraction(lines, [total(chosen)]), "SGD"))
    if bill.state == "reconcile":
        bill = oneoff.continue_total(bill)
    bill = oneoff.set_payer(oneoff.to_payer(oneoff.set_count(bill, persons)), payer)
    bill = oneoff.start_walk(bill)
    for who in claims:
        if who is None:
            bill = oneoff.walk_everyone(bill, bill.split.walk_index)  # type: ignore[union-attr]
        else:
            bill = _claim(bill, *sorted(who))
    for position in range(len(amounts)):
        bill = oneoff.fix_pick(oneoff.fix_list(bill), position)
        bill = oneoff.walk_everyone(bill, position)
        assert bill.state == "preview"
        assert sum(bill.shares.values()) == chosen
