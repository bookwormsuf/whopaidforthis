"""Tests for app.services.expense_parser.

The six worked examples come from the sprint 2 checklist and pins (no brief.md is
checked into this repo to draw the canonical six from, so the sprint doc's own
examples are used, plus one alias-generalisation example -- see the sprint report).
"""

from __future__ import annotations

import pytest

from app.models.category import Category
from app.services.expense_parser import (
    CustomLine,
    parse_custom_line,
    parse_expense,
    parse_item_command,
)

BOT = "whopaidforthisbot"


@pytest.mark.parametrize(
    ("text", "amount_minor", "currency", "description", "category"),
    [
        ("1200 yen ramen", 1200, "JPY", "Ramen", Category.FOOD),
        ("I paid ¥4,850 for dinner", 4850, "JPY", "Dinner", Category.FOOD),
        ("taxi 3200", 3200, "JPY", "Taxi", Category.TRANSPORT),
        ("750 coffee", 750, "JPY", "Coffee", Category.DRINKS),
        ("S$12 grab", 1200, "SGD", "Grab", Category.TRANSPORT),
        ("12 sgd taxi", 1200, "SGD", "Taxi", Category.TRANSPORT),
    ],
)
def test_parses_the_six_examples(text, amount_minor, currency, description, category) -> None:
    result = parse_expense(text, "JPY", "SGD", BOT)
    assert result is not None
    assert result.error is None
    assert result.amount_minor == amount_minor
    assert result.currency == currency
    assert result.description == description
    assert result.category == category


@pytest.mark.parametrize(
    "text",
    [
        "lol that ramen was so good",
        "who's paying for dinner",
    ],
)
def test_non_expense_text_returns_none(text: str) -> None:
    assert parse_expense(text, "JPY", "SGD", BOT) is None


def test_thirteen_word_message_returns_none_even_with_an_amount() -> None:
    text = "I paid 1200 yen for the amazing ramen dinner we all had together"
    assert len(text.split()) == 13
    assert parse_expense(text, "JPY", "SGD", BOT) is None


def test_twelve_word_message_with_amount_still_parses() -> None:
    text = "I paid 1200 yen for the amazing ramen dinner we all had"
    assert len(text.split()) == 12
    result = parse_expense(text, "JPY", "SGD", BOT)
    assert result is not None
    assert result.amount_minor == 1200


def test_bare_number_defaults_to_trip_currency() -> None:
    result = parse_expense("500 snack", "JPY", "SGD", BOT)
    assert result is not None
    assert result.currency == "JPY"


def test_unsupported_currency_token_sets_error() -> None:
    result = parse_expense("500 baht massage", "JPY", "SGD", BOT)
    assert result is not None
    assert result.error == "unsupported_currency"


def test_empty_description_defaults_to_expense() -> None:
    result = parse_expense("1200 yen", "JPY", "SGD", BOT)
    assert result is not None
    assert result.description == "Expense"
    assert result.category == Category.OTHER


def test_strips_bot_mention() -> None:
    a = parse_expense("1200 yen ramen", "JPY", "SGD", BOT)
    b = parse_expense(f"@{BOT} 1200 yen ramen", "JPY", "SGD", BOT)
    assert a == b


def test_no_amount_returns_none() -> None:
    assert parse_expense("dinner was great", "JPY", "SGD", BOT) is None


def test_trailing_paid_by_and_split_clauses_captured_not_resolved() -> None:
    result = parse_expense("1200 yen ramen split between me Amir paid by Amir", "JPY", "SGD", BOT)
    assert result is not None
    assert result.payer_text == "Amir"
    assert result.participants_text == "me Amir"


# The six worked examples from the master spec §6 (pinned into the spec from the
# original brief, since brief.md is not in this repo -- examples 5 and 6 exercise
# the "split between <names>" / "split everyone" trailing clause).
def test_brief_example_five_split_between_names() -> None:
    result = parse_expense("¥6000 dinner split between me Amir and John", "JPY", "SGD", BOT)
    assert result is not None
    assert result.error is None
    assert result.amount_minor == 6000
    assert result.currency == "JPY"
    assert result.description == "Dinner"
    assert result.category == Category.FOOD
    assert result.participants_text == "me Amir and John"
    assert result.payer_text is None


def test_brief_example_six_split_everyone() -> None:
    result = parse_expense("¥2400 taxi split everyone", "JPY", "SGD", BOT)
    assert result is not None
    assert result.error is None
    assert result.amount_minor == 2400
    assert result.currency == "JPY"
    assert result.description == "Taxi"
    assert result.category == Category.TRANSPORT
    assert result.participants_text == "everyone"
    assert result.payer_text is None


def test_paid_by_clause_lands_in_payer_text_not_description() -> None:
    result = parse_expense("1200 yen ramen paid by Amir", "JPY", "SGD", BOT)
    assert result is not None
    assert result.payer_text == "Amir"
    assert result.description == "Ramen"
    assert "paid" not in result.description.lower()
    assert "amir" not in result.description.lower()


def test_leading_bot_mention_is_ignored() -> None:
    with_mention = parse_expense(f"@{BOT} 1200 yen ramen", "JPY", "SGD", BOT)
    without_mention = parse_expense("1200 yen ramen", "JPY", "SGD", BOT)
    assert with_mention is not None
    assert with_mention == without_mention
    assert with_mention.description == "Ramen"
    assert BOT not in with_mention.description


def test_dollar_sign_resolves_to_home_currency_when_home_is_sgd() -> None:
    result = parse_expense("$12 grab", "JPY", "SGD", BOT)
    assert result is not None
    assert result.error is None
    assert result.amount_minor == 1200
    assert result.currency == "SGD"


def test_dollar_sign_resolves_to_home_currency_when_home_is_usd() -> None:
    result = parse_expense("$12 grab", "JPY", "USD", BOT)
    assert result is not None
    assert result.error is None
    assert result.currency == "USD"


def test_dollar_sign_falls_back_to_usd_when_home_is_neither_sgd_nor_usd() -> None:
    # USD isn't this trip's currency either, so it's unsupported.
    result = parse_expense("$12 grab", "JPY", "EUR", BOT)
    assert result is not None
    assert result.error == "unsupported_currency"


# The six trailing clauses (sprint 5): three "split <word> <names>", two "split
# everyone/all", and "paid by", plus the two clauses in either order.
@pytest.mark.parametrize(
    ("text", "participants", "payer"),
    [
        ("6000 yen dinner split between me and Amir", "me and Amir", None),
        ("6000 yen dinner split among me, Amir", "me, Amir", None),
        ("6000 yen dinner split with Amir", "Amir", None),
        ("6000 yen dinner split everyone", "everyone", None),
        ("6000 yen dinner split all", "all", None),
        ("6000 yen dinner paid by Amir", None, "Amir"),
        ("6000 yen dinner paid by Amir split between me and Amir", "me and Amir", "Amir"),
        ("6000 yen dinner split everyone paid by Amir", "everyone", "Amir"),
    ],
)
def test_trailing_clauses(text: str, participants: str | None, payer: str | None) -> None:
    result = parse_expense(text, "JPY", "SGD", BOT)
    assert result is not None
    assert (result.amount_minor, result.description) == (6000, "Dinner")
    assert result.participants_text == participants
    assert result.payer_text == payer


def test_custom_line() -> None:
    def parse(line: str, expense_currency: str = "JPY") -> CustomLine | None:
        return parse_custom_line(line, "JPY", "SGD", expense_currency)

    assert parse("Amir 2000") == CustomLine("Amir", 2000, "JPY")
    assert parse("me ¥1,500") == CustomLine("me", 1500, "JPY")
    assert parse("Jo Tan: S$12.50") == CustomLine("Jo Tan", 1250, "SGD")
    # A bare number is in the expense's own currency.
    assert parse("Me 6", expense_currency="SGD") == CustomLine("Me", 600, "SGD")
    assert parse("2000") is None  # no name
    assert parse("Amir") is None  # no amount
    assert parse("Amir 20 baht") is None  # not this trip's currency


def test_item_commands() -> None:
    assert parse_item_command("mine 1,4") == ("mine", [1, 4])
    assert parse_item_command("Mine 1, 4 5") == ("mine", [1, 4, 5])
    assert parse_item_command("unclaim 2") == ("unclaim", [2])
    assert parse_item_command("split 5 everyone") == ("everyone", [5])
    assert parse_item_command("split 5") is None
    assert parse_item_command("mine 1 everyone") is None
    assert parse_item_command("mine") is None
    assert parse_item_command("1200 yen ramen") is None


def test_bare_split_is_dropped_and_selects_nobody() -> None:
    for text in ("6000 split dinner", "6000 dinner split", "split 6000 dinner"):
        result = parse_expense(text, "JPY", "SGD", BOT)
        assert result is not None, text
        assert (result.description, result.participants_text) == ("Dinner", None), text
    # The clauses still work.
    assert parse_expense("6000 dinner split all", "JPY", "SGD", BOT).participants_text == "all"
    assert parse_expense("6000 split", "JPY", "SGD", BOT).description == "Expense"
