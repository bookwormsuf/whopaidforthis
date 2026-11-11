"""Unit tests for the one-off bill service: the line parsers, every transition and the
in-memory store (spec §22, sprint 17). No Telegram anywhere.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.services.oneoff import (
    TEXT_READERS,
    TEXT_STATES,
    AmountError,
    BillLine,
    InputError,
    OneOffBill,
    OneOffStore,
    StepError,
    apply_amount_lines,
    choose_custom,
    choose_equal,
    confirm_amounts,
    edit,
    is_balanced,
    parse_bill_line,
    read_count_text,
    read_text,
    remaining,
    rename,
    set_bill_line,
    set_count,
    set_payer,
    to_payer,
)

T0 = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def _new(first_name: str = "Sufyan") -> OneOffBill:
    return OneOffStore().create(user_id=1, chat_id=1, first_name=first_name, now=T0)


def _at_names(total: int = 8450, count: int = 3, currency: str = "SGD") -> OneOffBill:
    bill = set_bill_line(_new(), BillLine("Dinner", total, currency))
    return set_count(bill, count)


def _at_method(total: int = 8450, count: int = 3, payer: int = 1) -> OneOffBill:
    return set_payer(to_payer(_at_names(total, count)), payer)


def _at_amounts(total: int = 8450, count: int = 3, currency: str = "SGD") -> OneOffBill:
    bill = to_payer(_at_names(total, count, currency))
    return choose_custom(set_payer(bill, 1))


# ---- parse_bill_line ---------------------------------------------------------


def test_bill_line_in_sgd_by_default() -> None:
    assert parse_bill_line("84.50 dinner", "bot") == BillLine("Dinner", 8450, "SGD")


def test_bill_line_keeps_the_typed_currency() -> None:
    assert parse_bill_line("1200 yen ramen", "bot") == BillLine("Ramen", 1200, "JPY")


@pytest.mark.parametrize("text", ["dinner", "0 dinner", "0.00", "hello there", "", "   "])
def test_bill_line_without_a_usable_amount_is_none(text: str) -> None:
    assert parse_bill_line(text, "bot") is None


def test_bill_line_ignores_paid_by_split_and_tags() -> None:
    expected = BillLine("Dinner", 8450, "SGD")
    assert parse_bill_line("84.50 dinner paid by Ali", "bot") == expected
    assert parse_bill_line("84.50 dinner split everyone", "bot") == expected
    assert parse_bill_line("84.50 dinner split between me Ali paid by Mei", "bot") == expected
    assert parse_bill_line("84.50 dinner @ali", "bot") == expected


def test_bill_line_with_no_description_is_bill() -> None:
    assert parse_bill_line("84.50", "bot").description == "Bill"  # type: ignore[union-attr]


def test_a_very_long_description_is_cut() -> None:
    line = parse_bill_line("10 " + "a" * 500, "bot")
    assert line is not None and len(line.description) <= 81


# ---- read_count_text ---------------------------------------------------------


@pytest.mark.parametrize("text", ["2", "7", "20", " 5 "])
def test_count_text_accepts_2_to_20(text: str) -> None:
    assert read_count_text(text, "bot") == int(text)


@pytest.mark.parametrize(
    "text", ["1", "0", "21", "100", "-3", "2.5", "1,000", "abc", "", "zzz yyy"]
)
def test_count_text_rejects_everything_else(text: str) -> None:
    assert read_count_text(text, "bot") is None


def test_count_text_that_parses_as_a_bill_is_a_bill() -> None:
    assert read_count_text("1200 yen ramen", "bot") == BillLine("Ramen", 1200, "JPY")


# ---- people and names --------------------------------------------------------


def test_set_count_numbers_people_with_default_names() -> None:
    bill = _at_names(count=4)
    assert bill.state == "names"
    assert bill.names == ("Sufyan", "Person 2", "Person 3", "Person 4")
    assert bill.person_ids == [1, 2, 3, 4]


@pytest.mark.parametrize("count", [1, 21, 0, -1])
def test_set_count_outside_range_is_refused(count: int) -> None:
    bill = set_bill_line(_new(), BillLine("Dinner", 100, "SGD"))
    with pytest.raises(InputError):
        set_count(bill, count)


def test_rename_gives_names_in_order_to_person_2_onwards() -> None:
    assert rename(_at_names(), "Ali, Mei").names == ("Sufyan", "Ali", "Mei")


def test_rename_splits_on_line_breaks_and_trims() -> None:
    assert rename(_at_names(), "  Ali \n Mei ").names == ("Sufyan", "Ali", "Mei")


def test_fewer_names_than_people_keep_the_defaults() -> None:
    assert rename(_at_names(), "Ali").names == ("Sufyan", "Ali", "Person 3")


def test_rename_twice_replaces_from_person_2_again() -> None:
    bill = rename(rename(_at_names(), "Ali, Mei"), "Zed")
    assert bill.names == ("Sufyan", "Zed", "Mei")


def test_names_with_nothing_in_them_change_nothing() -> None:
    bill = _at_names()
    assert rename(bill, " , \n ,") == bill


def test_name_whitespace_is_collapsed() -> None:
    assert rename(_at_names(), "Ali   Khan").names[1] == "Ali Khan"


@pytest.mark.parametrize(("count", "others"), [(2, 1), (3, 2), (20, 19)])
def test_more_names_than_people(count: int, others: int) -> None:
    bill = _at_names(count=count)
    with pytest.raises(InputError) as error:
        rename(bill, ", ".join(f"N{i}" for i in range(others + 1)))
    assert (error.value.kind, error.value.count) == ("too_many_names", others)


def test_a_name_over_30_characters_is_refused_and_30_is_fine() -> None:
    bill = _at_names()
    with pytest.raises(InputError) as error:
        rename(bill, "a" * 31)
    assert error.value.kind == "name_too_long"
    assert rename(bill, "a" * 30).names[1] == "a" * 30


def test_duplicate_names_ignoring_case_are_refused() -> None:
    with pytest.raises(InputError) as error:
        rename(_at_names(), "Ali, ali")
    assert error.value.kind == "duplicate_name"


def test_a_name_equal_to_the_users_first_name_is_refused() -> None:
    with pytest.raises(InputError) as error:
        rename(_at_names(), "SUFYAN")
    assert error.value.kind == "duplicate_name"


def test_a_name_equal_to_an_untouched_default_is_refused() -> None:
    with pytest.raises(InputError) as error:
        rename(_at_names(), "Person 3")
    assert error.value.kind == "duplicate_name"


def test_a_refused_rename_changes_nothing() -> None:
    bill = _at_names()
    with pytest.raises(InputError):
        rename(bill, "Ali, Mei, Zed")
    assert bill.names == ("Sufyan", "Person 2", "Person 3")


# ---- who paid, split method --------------------------------------------------


def test_set_payer_moves_to_the_split_method() -> None:
    bill = set_payer(to_payer(_at_names()), 2)
    assert (bill.state, bill.payer) == ("method", 2)


def test_set_payer_refuses_a_person_who_is_not_on_the_bill() -> None:
    with pytest.raises(StepError):
        set_payer(to_payer(_at_names()), 4)


def test_equal_split_of_the_pinned_example() -> None:
    bill = choose_equal(_at_method())
    assert bill.state == "preview" and bill.method == "equal"
    assert bill.shares == {1: 2816, 2: 2817, 3: 2817}


def test_equal_split_gives_leftover_units_to_the_lowest_numbered_payer_last() -> None:
    # 10 among 4 with person 2 paying: 2 left over go to persons 1 and 3.
    bill = choose_equal(_at_method(total=10, count=4, payer=2))
    assert bill.shares == {1: 3, 2: 2, 3: 3, 4: 2}


def test_choose_custom_starts_with_no_amounts() -> None:
    bill = _at_amounts()
    assert (bill.state, bill.method, bill.amounts) == ("amounts", "custom", {})


# ---- typed amounts -----------------------------------------------------------


def test_several_lines_in_one_message() -> None:
    bill = rename(_at_names(), "Ali, Mei")
    bill = choose_custom(set_payer(to_payer(bill), 1))
    bill = apply_amount_lines(bill, "Ali 20\nMei 30.5\nme 10")
    assert bill.amounts == {2: 2000, 3: 3050, 1: 1000}
    assert bill.amount_errors == ()


def test_a_line_replaces_that_persons_earlier_amount() -> None:
    bill = apply_amount_lines(_at_amounts(), "Person 2 20")
    bill = apply_amount_lines(bill, "Person 2 25")
    assert bill.amounts == {2: 2500}


@pytest.mark.parametrize("alias", ["me", "Me", "I", "i", "myself", "MYSELF"])
def test_me_i_and_myself_are_the_user(alias: str) -> None:
    assert apply_amount_lines(_at_amounts(), f"{alias} 20").amounts == {1: 2000}


def test_the_users_first_name_also_works() -> None:
    assert apply_amount_lines(_at_amounts(), "sufyan 20").amounts == {1: 2000}


def test_a_default_name_with_a_space_and_a_digit() -> None:
    assert apply_amount_lines(_at_amounts(), "Person 2 20").amounts == {2: 2000}


def test_unknown_name() -> None:
    bill = apply_amount_lines(_at_amounts(), "Zed 20\nme 5")
    assert bill.amount_errors == (AmountError("unknown_person", "Zed"),)
    assert bill.amounts == {1: 500}  # the good line still applies


@pytest.mark.parametrize(
    "line", ["Ali", "20", "Ali twenty", "Ali -5", "me 20 dinner", "Person 2", "Zed twenty"]
)
def test_unreadable_lines(line: str) -> None:
    bill = apply_amount_lines(_at_amounts(), line)
    assert bill.amount_errors == (AmountError("unreadable", line),)
    assert bill.amounts == {}


def test_the_wrong_currency_is_refused() -> None:
    bill = apply_amount_lines(_at_amounts(), "Person 2 5usd\nPerson 2 €5\nme $5 usd")
    assert [e.kind for e in bill.amount_errors] == [
        "wrong_currency",
        "wrong_currency",
        "unreadable",
    ]
    assert bill.amounts == {}


def test_the_bills_own_symbol_and_code_are_accepted() -> None:
    bill = apply_amount_lines(_at_amounts(), "Person 2 S$20\nme SGD20")
    assert bill.amounts == {2: 2000, 1: 2000}


def test_a_yen_bill_takes_whole_yen() -> None:
    bill = _at_amounts(total=1200, count=2, currency="JPY")
    bill = apply_amount_lines(bill, "me ¥500\nPerson 2 700")
    assert bill.amounts == {1: 500, 2: 700}
    assert is_balanced(bill)


def test_remaining_goes_negative_when_over() -> None:
    bill = apply_amount_lines(_at_amounts(), "me 100")
    assert remaining(bill) == -1550 and not is_balanced(bill)


def test_confirm_amounts_needs_an_exact_sum() -> None:
    short = apply_amount_lines(_at_amounts(), "me 20")
    with pytest.raises(StepError):
        confirm_amounts(short)
    full = apply_amount_lines(short, "Person 2 64.50")
    done = confirm_amounts(full)
    assert done.state == "preview"
    assert done.shares == {1: 2000, 2: 6450, 3: 0}  # no amount counts as zero


# ---- edit and wrong steps ----------------------------------------------------


def test_edit_returns_to_names_with_names_kept() -> None:
    bill = edit(choose_equal(_at_method()))
    assert bill.state == "names"
    assert (bill.payer, bill.method, bill.amounts, bill.shares) == (None, None, {}, {})
    assert bill.count == 3 and bill.description == "Dinner"


def test_edit_keeps_typed_names() -> None:
    bill = rename(_at_names(), "Ali, Mei")
    bill = choose_equal(set_payer(to_payer(bill), 2))
    assert edit(bill).names == ("Sufyan", "Ali", "Mei")


def test_every_transition_refuses_the_wrong_step() -> None:
    fresh = _new()
    names = _at_names()
    preview = choose_equal(_at_method())
    for call in (
        lambda: set_count(fresh, 3),
        lambda: rename(fresh, "Ali"),
        lambda: to_payer(fresh),
        lambda: set_payer(names, 1),
        lambda: choose_equal(names),
        lambda: choose_custom(names),
        lambda: apply_amount_lines(names, "me 1"),
        lambda: confirm_amounts(names),
        lambda: edit(names),
        lambda: set_bill_line(names, BillLine("x", 1, "SGD")),
        lambda: to_payer(preview),
    ):
        with pytest.raises(StepError):
            call()


# ---- shares always sum -------------------------------------------------------


@given(
    total=st.integers(min_value=1, max_value=10**9),
    count=st.integers(min_value=2, max_value=20),
    payer_pick=st.integers(min_value=0, max_value=19),
)
def test_equal_shares_always_sum_to_the_bill(total: int, count: int, payer_pick: int) -> None:
    payer = payer_pick % count + 1
    bill = choose_equal(_at_method(total=total, count=count, payer=payer))
    assert sum(bill.shares.values()) == total
    assert set(bill.shares) == set(range(1, count + 1))
    assert max(bill.shares.values()) - min(bill.shares.values()) <= 1


@given(parts=st.lists(st.integers(min_value=0, max_value=10**6), min_size=2, max_size=20))
def test_typed_shares_always_sum_to_the_bill(parts: list[int]) -> None:
    total = sum(parts) * 100  # the lines below are in whole dollars
    if total == 0:
        return
    bill = _at_amounts(total=total, count=len(parts))
    lines = "\n".join(
        f"{'me' if i == 0 else f'Person {i + 1}'} {amount}" for i, amount in enumerate(parts)
    )
    bill = confirm_amounts(apply_amount_lines(bill, lines))
    assert sum(bill.shares.values()) == total


# ---- the store ---------------------------------------------------------------


def _store_with_bill() -> tuple[OneOffStore, OneOffBill]:
    store = OneOffStore()
    return store, store.create(user_id=7, chat_id=7, first_name="Ada", now=T0)


def test_one_bill_per_user_and_ids_are_short() -> None:
    store, first = _store_with_bill()
    second = store.create(user_id=7, chat_id=7, first_name="Ada", now=T0)
    assert store.get(first.bill_id, T0) is None
    assert store.for_user(7, T0) == second
    assert len(second.bill_id) <= 8 and ":" not in second.bill_id


def test_a_bill_expires_30_minutes_after_its_last_put() -> None:
    store, bill = _store_with_bill()
    assert store.get(bill.bill_id, T0 + timedelta(minutes=30)) is not None
    assert store.get(bill.bill_id, T0 + timedelta(minutes=30, seconds=1)) is None
    assert store.for_user(7, T0) is None  # evicted, stays gone


def test_the_clock_is_sliding() -> None:
    store, bill = _store_with_bill()
    store.put(bill, T0 + timedelta(minutes=25))
    assert store.get(bill.bill_id, T0 + timedelta(minutes=50)) is not None
    assert store.get(bill.bill_id, T0 + timedelta(minutes=56)) is None


def test_put_without_touch_does_not_restart_the_clock() -> None:
    store, bill = _store_with_bill()
    store.put(bill, T0 + timedelta(minutes=25), touch=False)
    assert store.get(bill.bill_id, T0 + timedelta(minutes=31)) is None


def test_an_expired_user_gets_one_notice() -> None:
    store, bill = _store_with_bill()
    late = T0 + timedelta(minutes=31)
    assert store.wants_text(7, late)  # looking expires the bill and owes the notice
    assert store.take_expired_notice(7) is True
    assert store.take_expired_notice(7) is False
    assert not store.wants_text(7, late)
    assert store.get(bill.bill_id, late) is None


def test_sweep_expires_other_users_bills_and_owes_them_a_notice() -> None:
    store, _bill = _store_with_bill()
    store.sweep(T0 + timedelta(minutes=31))
    assert store.for_user(7, T0) is None
    assert store.has_expired_notice(7)


def test_starting_a_new_bill_clears_the_notice() -> None:
    store, _bill = _store_with_bill()
    store.sweep(T0 + timedelta(minutes=31))
    store.create(user_id=7, chat_id=7, first_name="Ada", now=T0 + timedelta(minutes=32))
    assert not store.has_expired_notice(7)


def test_remove_forgets_the_bill_without_a_notice() -> None:
    store, bill = _store_with_bill()
    store.remove(bill.bill_id, "cancelled")
    assert store.get(bill.bill_id, T0) is None
    assert not store.has_expired_notice(7)


def test_wants_text_only_in_text_steps_and_photo_only_at_the_bill_line() -> None:
    store, bill = _store_with_bill()
    assert store.wants_text(7, T0) and store.wants_photo(7, T0)
    counted = set_bill_line(bill, BillLine("Dinner", 100, "SGD"))
    store.put(counted, T0)
    assert store.wants_text(7, T0) and not store.wants_photo(7, T0)
    store.put(set_payer(to_payer(set_count(counted, 2)), 1), T0)
    assert not store.wants_text(7, T0)  # waiting for a button, not text
    assert not store.wants_text(8, T0)  # someone else


# ---- review A: the longest known name starts the line -------------------------


def _named_bill(*names: str, total: int = 8450, currency: str = "SGD") -> OneOffBill:
    bill = to_payer(_at_names(total, len(names) + 1, currency))
    bill = set_payer(bill, 1)
    bill = rename(replace(bill, state="names"), ", ".join(names)) if names else bill
    return choose_custom(replace(bill, state="method"))


@pytest.mark.parametrize(
    "line", ["Ali 20", "Ali S$20", "Ali 20 sgd", "Ali SGD 20", "ali 20", "Ali  20", "Ali $20"]
)
def test_name_then_amount_in_every_sgd_form(line: str) -> None:
    bill = apply_amount_lines(_named_bill("Ali", "Mei"), line)
    assert bill.amount_errors == ()
    assert bill.amounts == {2: 2000}


@pytest.mark.parametrize("line", ["Person 2 20", "Person 2 S$20", "person 2 20 sgd"])
def test_default_names_with_a_digit_read(line: str) -> None:
    assert apply_amount_lines(_at_amounts(), line).amounts == {2: 2000}


def test_me_reads_with_the_amount_after_it() -> None:
    assert apply_amount_lines(_at_amounts(), "me 20").amounts == {1: 2000}
    assert apply_amount_lines(_at_amounts(), "myself S$20").amounts == {1: 2000}


def test_a_known_name_then_another_currency_is_the_wrong_currency() -> None:
    bill = apply_amount_lines(_named_bill("Ali", "Mei"), "Ali 500 yen")
    assert bill.amount_errors == (AmountError("wrong_currency"),)
    assert bill.amounts == {}


def test_a_yen_bill_reads_amounts_with_the_word_yen() -> None:
    bill = _at_amounts(total=1200, count=2, currency="JPY")
    bill = apply_amount_lines(bill, "me 500 yen\nPerson 2 700 yen")
    assert bill.amount_errors == ()
    assert bill.amounts == {1: 500, 2: 700}


def test_the_longest_name_wins() -> None:
    bill = _named_bill("Ali", "Ali 2")
    assert apply_amount_lines(bill, "Ali 2 30").amounts == {3: 3000}
    assert apply_amount_lines(bill, "Ali 30").amounts == {2: 3000}


def test_an_unknown_name_is_named_in_the_error() -> None:
    bill = apply_amount_lines(_named_bill("Ali", "Mei"), "Bob 20")
    assert bill.amount_errors == (AmountError("unknown_person", "Bob"),)


def test_words_after_the_amount_make_the_line_unreadable() -> None:
    bill = apply_amount_lines(_named_bill("Ali", "Mei"), "Ali 20 for dinner")
    assert bill.amount_errors == (AmountError("unreadable", "Ali 20 for dinner"),)
    assert bill.amounts == {}


# ---- review B: "3 people" is the count ---------------------------------------


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("3 people", 3),
        ("4 pax", 4),
        ("2 PPL", 2),
        ("5 persons", 5),
        ("6 Person", 6),
        ("20 people", 20),
    ],
)
def test_a_count_with_a_people_word_is_the_count(text: str, count: int) -> None:
    assert read_count_text(text, "bot") == count


@pytest.mark.parametrize("text", ["21 people", "1 person", "0 pax"])
def test_a_people_word_with_a_bad_count_is_not_a_bill(text: str) -> None:
    assert read_count_text(text, "bot") is None


def test_a_number_and_a_description_is_still_a_new_bill() -> None:
    assert read_count_text("12 lunch", "bot") == BillLine("Lunch", 1200, "SGD")


# ---- review F: a bill that is gone cannot be put back ---------------------------


def test_put_returns_true_for_a_live_bill() -> None:
    store, bill = _store_with_bill()
    assert store.put(bill, T0 + timedelta(minutes=1)) is True


def test_put_after_remove_is_refused() -> None:
    store, bill = _store_with_bill()
    store.remove(bill.bill_id, "cancelled")
    assert store.put(bill, T0) is False
    assert store.get(bill.bill_id, T0) is None and store.for_user(7, T0) is None


def test_put_after_the_bill_was_replaced_is_refused() -> None:
    store, old = _store_with_bill()
    new = store.create(user_id=7, chat_id=7, first_name="Ada", now=T0)
    assert store.put(old, T0) is False
    assert store.for_user(7, T0) == new


def test_put_after_expiry_is_refused() -> None:
    store, bill = _store_with_bill()
    assert store.put(bill, T0 + timedelta(minutes=31)) is False
    assert store.for_user(7, T0 + timedelta(minutes=31)) is None


# ---- review G: one table of text readers --------------------------------------


def test_every_text_state_has_a_reader_and_no_other_state_does() -> None:
    assert TEXT_STATES == frozenset(TEXT_READERS)
    assert TEXT_STATES == {"awaiting_bill", "count", "names", "amounts"}


def test_read_text_refuses_a_state_with_no_reader() -> None:
    with pytest.raises(StepError):
        read_text(_at_method(), "hello", "bot")


# ---- review H: the split lives in one field ------------------------------------


def test_going_back_resets_the_split_to_none() -> None:
    equal = choose_equal(_at_method())
    assert equal.split is not None
    assert edit(equal).split is None
    typed = confirm_amounts(apply_amount_lines(_at_amounts(total=100, count=2), "me 1"))
    assert typed.split is not None
    assert edit(typed).split is None
    assert choose_custom(_at_method()).split is not None
    assert _at_method().split is None
