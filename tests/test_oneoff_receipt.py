"""Receipt photos on a one-off bill, through the real handlers and the built application
(spec §23, sprint 18). The harness is Sprint 17's: a fake bot records every message and
edit. Readers are fakes: no network call is made.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, Message, PhotoSize, Update, User
from telegram.error import NetworkError

from app.bot.deps import Deps
from app.bot.handlers.oneoff import oneoff_photo_handler
from app.bot.handlers.photo_expense import photo_expense_handler
from app.bot.render import oneoff as render
from app.services.oneoff import ItemsSplit
from app.services.photo_allowance import Allowed, AlreadyRead, OutOfTries, PhotoAllowance
from app.services.receipt_budget import ReceiptReadBudget
from app.services.receipt_parser import ReceiptServiceUnavailable, ReceiptUnreadable
from tests import receipt_fixtures as fixtures
from tests import test_photo_expense_handler as group_tests
from tests.helpers import make_deps
from tests.test_oneoff import (
    OWNER_ID,
    START_PROMPT,
    Chatter,
    Exploding,
    _application,
    _matching,
)

NOTICE = "I read receipts with an AI model. The photo isn't stored."
READING = "Reading the receipt…"
TITLE = "🧾 <b>Ramen House</b> · S$84.50"
COUNT_CARD = (
    f"{TITLE}\n3 items on the receipt\n\nHow many people, including you?\n"
    "<i>Wrong amount or currency? Type the bill instead, like 1200 yen ramen.</i>"
)
ALREADY_READ = (
    "You can read 1 receipt photo a day here. Type the bill instead, like "
    "<code>84.50 dinner</code>. For more, add me to a group for a trip."
)
THREE_TRIES = "That's 3 tries today. Type the bill instead, like <code>84.50 dinner</code>."
GLOBAL_CAP = (
    "Receipt photos are used up for today across the whole bot. Type the bill instead, "
    "like <code>84.50 dinner</code>."
)
ONE_AT_A_TIME = "One receipt at a time. Still reading the first."
COULDNT_READ = "🧾 <b>Receipt</b>\n\nCouldn't read that receipt"
TOO_LARGE = "That photo's too large (over 10 MB), please send a smaller one."


# ---- harness ------------------------------------------------------------------


class ScriptedReader:
    """Answers each call with the next outcome (an extraction, or an exception to
    raise); the last one repeats. Records the currency codes it was given.
    """

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str]] = []

    async def read(self, image, *, trip_currency, home_currency):
        await asyncio.sleep(0)  # a real read lets other tasks run
        self.calls.append((trip_currency, home_currency))
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class GatedReader(ScriptedReader):
    """Holds every read until `gate` is set, so a test can act while the model is busy."""

    def __init__(self, *outcomes) -> None:
        super().__init__(*outcomes)
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def wait_started(self) -> None:
        """Until the model call has begun; a handler that never got there fails the test
        instead of hanging it.
        """
        await asyncio.wait_for(self.started.wait(), timeout=5)

    async def read(self, image, *, trip_currency, home_currency):
        self.started.set()
        await self.gate.wait()
        return await super().read(image, trip_currency=trip_currency, home_currency=home_currency)


class YieldingAllowance(PhotoAllowance):
    """An allowance that, like one backed by a database, yields on every call."""

    async def may_try(self, user_id, now, *, is_owner=False):
        await asyncio.sleep(0)
        return await super().may_try(user_id, now, is_owner=is_owner)

    async def record_success(self, user_id, now):
        await asyncio.sleep(0)
        await super().record_success(user_id, now)

    async def record_failure(self, user_id, now):
        await asyncio.sleep(0)
        await super().record_failure(user_id, now)


class Realistic(Chatter):
    """A chat whose bot yields to the event loop on every send, edit, answer, download
    and `get_file`, as a real one does: tests that never yield cannot see a race.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.bot.yield_on_send = True


def bounded(awaitable):
    """Fails a test that would block (a handler that never reached the gated reader)."""
    return asyncio.wait_for(awaitable, timeout=5)


def make_chat(
    monkeypatch,
    reader=None,
    *,
    user_id: int = 1,
    first_name: str = "Sufyan",
    owner_id: int | None = OWNER_ID,
    budget: ReceiptReadBudget | None = None,
    deps: Deps | None = None,
) -> Chatter:
    reader = reader if reader is not None else ScriptedReader(fixtures.dinner())
    deps = deps or replace(
        make_deps(receipt_reader=reader, owner_user_id=owner_id, receipt_budget=budget),
        photo_allowance=YieldingAllowance(),
    )
    return Realistic(monkeypatch, deps=deps, user_id=user_id, first_name=first_name)


async def start_with_photo(chat: Chatter) -> None:
    await chat.command()
    assert await chat.photo()


async def press_toggle(chat: Chatter, name: str) -> None:
    label = name if name in chat.labels() else f"✓ {name}"
    await chat.press(label)


async def to_walk(chat: Chatter, *, count: int = 3, names: str | None = "Ali, Mei") -> None:
    await start_with_photo(chat)
    if count <= 6:
        await chat.press(str(count))
    else:
        await chat.say(str(count))
    if names:
        await chat.say(names)
    await chat.press("Next")
    await chat.press("Me")
    await chat.press("🍽 Go item by item ▶")


async def claim(chat: Chatter, *names: str) -> None:
    for name in names:
        await press_toggle(chat, name)
    await chat.press("Next ▶")


def answers(query) -> list:
    return [call.args[0] if call.args else None for call in query.answer.await_args_list]


# ---- the whole path -----------------------------------------------------------


async def test_a_photo_at_step_one_reads_notices_and_reaches_the_how_many_card(monkeypatch):
    reader = ScriptedReader(fixtures.dinner())
    chat = make_chat(monkeypatch, reader)
    await chat.command()
    assert chat.bot.texts() == [START_PROMPT]
    assert await chat.photo()
    # the prompt, the notice, then the card: the `Reading the receipt…` message edited
    assert chat.bot.texts() == [START_PROMPT, NOTICE, COUNT_CARD]
    assert chat.bot.messages[chat.bill.message_id]["keyboard"] == [
        [(str(n), f"oneoff:{chat.bill.bill_id}:count:{n}") for n in (2, 3, 4, 5, 6)],
        [("Cancel", f"oneoff:{chat.bill.bill_id}:cancel")],
    ]
    assert reader.calls == [("SGD", "SGD")]
    assert chat.bill.photo_file_id == "photo-1"


async def test_the_whole_path_item_by_item_to_a_summary_that_adds_up(monkeypatch):
    chat = make_chat(monkeypatch)
    await to_walk(chat)
    card_id = chat.bill.message_id
    assert chat.card() == (
        f"{TITLE}\nSufyan (you) paid\n\nWho had this?  (1 of 3)\n<b>Ramen</b> · S$20.00"
    )
    await claim(chat, "Me")
    assert "(2 of 3)\n<b>Gyoza</b> · S$12.00" in chat.card()
    await claim(chat, "Ali")
    assert "(3 of 3)\n<b>Beer</b> · S$30.00" in chat.card()
    await claim(chat, "Me", "Ali", "Mei")
    assert chat.card() == (
        f"{TITLE}\nSufyan (you) paid\n\nCheck before sending\nEach person's share:\n"
        "• Sufyan (you) S$40.89\n   items S$30.00 + tax S$2.42 + service S$8.47\n"
        "• Ali S$29.98\n   items S$22.00 + tax S$1.77 + service S$6.21\n"
        "• Mei S$13.63\n   items S$10.00 + tax S$0.81 + service S$2.82\n\n"
        "Shared in proportion: GST S$5.00 · Service S$17.50"
    )
    assert chat.labels() == ["Done", "Fix an item", "Edit", "Cancel"]
    assert [len(row) for row in chat.keyboard()] == [2, 2]
    assert sum(chat.bill.shares.values()) == 8450
    await chat.press("Done")
    assert chat.bot.messages[card_id]["text"] == (
        f"{TITLE}\nSufyan paid · split by items\n\n"
        "• Ali owes Sufyan S$29.98\n• Mei owes Sufyan S$13.63\n\n"
        "Split with @whopaidbot"
    )
    assert chat.bot.messages[card_id]["keyboard"] is None
    assert chat.bill is None


async def test_the_how_many_card_for_a_receipt_is_the_pinned_text(monkeypatch):
    chat = make_chat(monkeypatch)
    await start_with_photo(chat)
    assert chat.card() == COUNT_CARD
    # the hint is shown even for a non-SGD receipt
    yen = fixtures.extraction(
        [fixtures.line("Ramen", 800), fixtures.line("Tea", 300)],
        [fixtures.total(1100)],
        currency="JPY",
    )
    other = make_chat(monkeypatch, ScriptedReader(yen), user_id=7)
    await start_with_photo(other)
    assert other.card() == (
        "🧾 <b>Ramen House</b> · ¥1,100\n2 items on the receipt\n\n"
        "How many people, including you?\n"
        "<i>Wrong amount or currency? Type the bill instead, like 1200 yen ramen.</i>"
    )


async def test_a_typed_bill_line_at_the_how_many_step_replaces_a_receipt_bill(monkeypatch):
    chat = make_chat(monkeypatch)
    await start_with_photo(chat)
    old = chat.bill
    await chat.say("1200 yen ramen")
    assert chat.bot.messages[old.message_id]["text"] == "Replaced by the bill below."
    assert chat.bill.bill_id != old.bill_id and chat.bill.state == "count"
    assert not chat.bill.from_receipt
    assert chat.card().startswith("🧾 <b>Ramen</b> · ¥1,200")


# ---- the split-method card ----------------------------------------------------


async def test_the_split_method_card_for_a_receipt_offers_item_by_item_first(monkeypatch):
    chat = make_chat(monkeypatch)
    await start_with_photo(chat)
    await chat.press("3")
    await chat.press("Next")
    await chat.press("Me")
    assert chat.card() == f"{TITLE}\n\nWho had what?"
    assert chat.labels() == [
        "🍽 Go item by item ▶",
        "Split equally",
        "Type amounts instead",
        "Cancel",
    ]
    assert [len(row) for row in chat.keyboard()] == [1, 1, 1, 1]


async def test_over_forty_items_and_zero_items_offer_no_item_by_item_button(monkeypatch):
    forty_one = fixtures.extraction(
        [fixtures.line(f"Dish {n}", 100) for n in range(41)], [fixtures.total(4100)]
    )
    chat = make_chat(monkeypatch, ScriptedReader(forty_one))
    await start_with_photo(chat)
    await chat.press("2")
    await chat.press("Next")
    await chat.press("Me")
    assert chat.card() == (
        "🧾 <b>Ramen House</b> · S$41.00\n\nHow should it be split?\n"
        "Too many items to split individually"
    )
    assert chat.labels() == ["Split equally", "Type amounts instead", "Cancel"]

    none = fixtures.extraction([], [fixtures.total(4000)])
    other = make_chat(monkeypatch, ScriptedReader(none), user_id=7)
    await start_with_photo(other)
    await other.press("2")
    await other.press("Next")
    await other.press("Me")
    assert other.card() == "🧾 <b>Ramen House</b> · S$40.00\n\nHow should it be split?"
    assert other.labels() == ["Split equally", "Type amounts instead", "Cancel"]
    assert other.card().count("Too many") == 0
    # and a stale `Go item by item` press changes nothing
    query = await other.press_data(f"oneoff:{other.bill.bill_id}:items", other.bill.message_id)
    assert other.bill.state == "method" and answers(query) == [None]


async def test_exactly_forty_items_still_offers_it(monkeypatch):
    forty = fixtures.extraction(
        [fixtures.line(f"Dish {n}", 100) for n in range(40)], [fixtures.total(4000)]
    )
    chat = make_chat(monkeypatch, ScriptedReader(forty))
    await start_with_photo(chat)
    await chat.press("2")
    await chat.press("Next")
    await chat.press("Me")
    assert "🍽 Go item by item ▶" in chat.labels()
    await chat.press("🍽 Go item by item ▶")
    assert "(1 of 40)" in chat.card()


async def test_split_equally_and_typed_amounts_still_work_on_a_receipt(monkeypatch):
    chat = make_chat(monkeypatch)
    await start_with_photo(chat)
    await chat.press("2")
    await chat.press("Next")
    await chat.press("Me")
    await chat.press("Split equally")
    assert chat.bill.shares == {1: 4225, 2: 4225}
    await chat.press("Done")
    assert "split 2 ways" in chat.bot.texts()[-2]


# ---- which total, other amount, doesn't add up --------------------------------


def _two_totals():
    return fixtures.extraction(
        [fixtures.line("Ramen", 2000), fixtures.line("Gyoza", 1200)],
        [
            fixtures.total(3200, guess=False, label="Subtotal"),
            fixtures.total(3520, guess=False, label="Total"),
        ],
    )


async def test_which_total_card_two_candidates_and_picking_one(monkeypatch):
    chat = make_chat(monkeypatch, ScriptedReader(_two_totals()))
    await start_with_photo(chat)
    assert chat.card() == "🧾 <b>Ramen House</b>\n\nWhich is the final total?"
    bill_id = chat.bill.bill_id
    assert chat.keyboard() == [
        [("S$35.20", f"oneoff:{bill_id}:total:0"), ("S$32.00", f"oneoff:{bill_id}:total:1")],
        [("Other amount", f"oneoff:{bill_id}:manual"), ("Cancel", f"oneoff:{bill_id}:cancel")],
    ]
    await chat.press("S$32.00")
    assert chat.bill.state == "count" and chat.bill.amount_minor == 3200
    assert chat.card().startswith("🧾 <b>Ramen House</b> · S$32.00\n2 items on the receipt")


async def test_other_amount_asks_then_a_bad_reply_then_a_good_one(monkeypatch):
    chat = make_chat(monkeypatch, ScriptedReader(_two_totals()))
    await start_with_photo(chat)
    card_id = chat.bill.message_id
    await chat.press("Other amount")
    assert chat.card() == "🧾 <b>Ramen House</b>\n\nSend the amount."
    assert chat.bot.messages[card_id]["keyboard"] is None
    await chat.say("about thirty")
    assert chat.bot.texts()[-1] == "Send just the amount, like 84.50."
    assert chat.bill.state == "other_amount"
    await chat.say("32.00")
    assert chat.bill.state == "count" and chat.bill.amount_minor == 3200
    assert chat.card().startswith("🧾 <b>Ramen House</b> · S$32.00")


async def test_a_typed_total_far_from_the_lines_gets_the_doesnt_add_up_card(monkeypatch):
    chat = make_chat(monkeypatch, ScriptedReader(_two_totals()))
    await start_with_photo(chat)
    await chat.press("Other amount")
    await chat.say("50")
    assert chat.bill.state == "reconcile"


async def test_the_doesnt_add_up_card_continue_and_the_unlisted_line(monkeypatch):
    short = fixtures.extraction(
        [fixtures.line("Ramen", 2000), fixtures.line("Gyoza", 1200)], [fixtures.total(4000)]
    )
    chat = make_chat(monkeypatch, ScriptedReader(short))
    await start_with_photo(chat)
    assert chat.card() == (
        "🧾 <b>Ramen House</b>\n"
        "Items S$32.00 + adjustments S$0.00 = S$32.00\n"
        "Receipt total S$40.00 (difference S$8.00)\n"
        "(S$8.00 isn't on any line; it'll be shared like tax)"
    )
    assert "Paid for part" not in chat.card()
    bill_id = chat.bill.bill_id
    assert chat.keyboard() == [
        [("Continue with S$40.00", f"oneoff:{bill_id}:continue")],
        [("Cancel", f"oneoff:{bill_id}:cancel")],
    ]
    await chat.press("Continue with S$40.00")
    assert chat.card().startswith("🧾 <b>Ramen House</b> · S$40.00\n2 items on the receipt")
    await chat.press("2")
    await chat.press("Next")
    await chat.press("Me")
    await chat.press("🍽 Go item by item ▶")
    await claim(chat, "Me")
    await claim(chat, "Person 2")
    assert chat.card() == (
        "🧾 <b>Ramen House</b> · S$40.00\nSufyan (you) paid\n\nCheck before sending\n"
        "Each person's share:\n"
        "• Sufyan (you) S$25.00\n   items S$20.00 + unlisted S$5.00\n"
        "• Person 2 S$15.00\n   items S$12.00 + unlisted S$3.00\n\n"
        "Shared in proportion: Unlisted / rounding S$8.00"
    )


async def test_a_total_below_the_lines_shows_the_gap_as_an_unlisted_discount(monkeypatch):
    over = fixtures.extraction(
        [fixtures.line("Ramen", 2000), fixtures.line("Gyoza", 1200)], [fixtures.total(2800)]
    )
    chat = make_chat(monkeypatch, ScriptedReader(over))
    await start_with_photo(chat)
    assert "Receipt total S$28.00 (difference S$4.00)" in chat.card()
    await chat.press("Continue with S$28.00")
    await chat.press("2")
    await chat.press("Next")
    await chat.press("Me")
    await chat.press("🍽 Go item by item ▶")
    await claim(chat, "Me")
    await claim(chat, "Person 2")
    assert "items S$20.00 − unlisted S$2.50" in chat.card()
    assert "Shared in proportion: Unlisted / rounding −S$4.00" in chat.card()
    assert sum(chat.bill.shares.values()) == 2800


# ---- the item walk through the buttons ----------------------------------------


async def test_the_who_had_this_screen_rows_and_toggles(monkeypatch):
    chat = make_chat(monkeypatch)
    await to_walk(chat, count=7, names="A, B, C, D, E, F")
    # 7 toggles, three per row, `Me` first, then Everyone, Next (no Back on item 1), Cancel
    labels = [[label for label, _ in row] for row in chat.keyboard()]
    assert labels == [
        ["Me", "A", "B"],
        ["C", "D", "E"],
        ["F"],
        ["Everyone"],
        ["Next ▶"],
        ["Cancel"],
    ]
    await chat.press("A")
    assert "✓ A" in chat.labels() and "A" not in chat.labels()
    await chat.press("✓ A")
    assert "A" in chat.labels()


async def test_everyone_ticks_all_and_moves_on(monkeypatch):
    chat = make_chat(monkeypatch)
    await to_walk(chat)
    await chat.press("Everyone")
    assert "(2 of 3)" in chat.card()
    assert chat.bill.everyone == frozenset({0})
    # back shows the item with all ticked
    await chat.press("◀ Back")
    assert [label for label in chat.labels() if label.startswith("✓")] == [
        "✓ Me",
        "✓ Ali",
        "✓ Mei",
    ]


async def test_next_with_nobody_ticked_is_a_toast_and_stays(monkeypatch):
    chat = make_chat(monkeypatch)
    await to_walk(chat)
    text = chat.card()
    query = await chat.press("Next ▶")
    assert answers(query) == ["Pick who had this first"]
    assert chat.card() == text


async def test_back_keeps_picks_and_is_absent_on_the_first_item(monkeypatch):
    chat = make_chat(monkeypatch)
    await to_walk(chat)
    assert "◀ Back" not in chat.labels()
    await claim(chat, "Ali")
    assert "◀ Back" in chat.labels()
    await chat.press("◀ Back")
    assert "(1 of 3)" in chat.card() and "✓ Ali" in chat.labels()


async def test_stale_item_buttons_change_nothing(monkeypatch):
    chat = make_chat(monkeypatch)
    await to_walk(chat)
    stale = chat.keyboard()
    await claim(chat, "Me")  # now on item 2
    before = (chat.card(), chat.bill.split)
    message_id = chat.bill.message_id
    for label in ("Me", "Everyone", "Next ▶"):
        data = next(d for row in stale for text, d in row if text.endswith(label))
        query = await chat.press_data(data, message_id)
        assert answers(query) == [None]
        assert (chat.card(), chat.bill.split) == before
    # a mangled argument is ignored too
    await chat.press_data(f"oneoff:{chat.bill.bill_id}:wnext:x", message_id)
    await chat.press_data(f"oneoff:{chat.bill.bill_id}:wtoggle:1", message_id)
    await chat.press_data(f"oneoff:{chat.bill.bill_id}:wtoggle:1,99", message_id)
    assert (chat.card(), chat.bill.split) == before


async def test_fix_an_item_changes_one_claim_and_returns_to_the_summary(monkeypatch):
    chat = make_chat(monkeypatch)
    await to_walk(chat)
    await claim(chat, "Me")
    await claim(chat, "Ali")
    await claim(chat, "Me", "Ali", "Mei")
    await chat.press("Fix an item")
    assert chat.card() == (f"{TITLE}\nSufyan (you) paid\n\nWhich item do you want to change?")
    bill_id = chat.bill.bill_id
    assert chat.keyboard() == [
        [("🍽 1 Ramen S$20.00 · S", f"oneoff:{bill_id}:fixpick:0")],
        [("🍽 2 Gyoza S$12.00 · A", f"oneoff:{bill_id}:fixpick:1")],
        [("🍽 3 Beer S$30.00 · all", f"oneoff:{bill_id}:fixpick:2")],
        [("◀ Back to summary", f"oneoff:{bill_id}:fixback")],
    ]
    await chat.press("🍽 2 Gyoza S$12.00 · A")
    assert "(2 of 3)\n<b>Gyoza</b> · S$12.00" in chat.card()
    assert "◀ Back" in chat.labels()  # opened from Fix: Back is there on any item
    await chat.press("✓ Ali")
    await chat.press("Mei")
    await chat.press("Next ▶")
    # straight back to the summary, not on to item 3
    assert chat.card().startswith(f"{TITLE}\nSufyan (you) paid\n\nCheck before sending")
    assert chat.bill.claims[1] == (3,)
    assert sum(chat.bill.shares.values()) == 8450


async def test_back_to_summary_from_the_fix_list_and_back_from_a_fixed_item(monkeypatch):
    chat = make_chat(monkeypatch)
    await to_walk(chat)
    await claim(chat, "Me")
    await claim(chat, "Ali")
    await claim(chat, "Mei")
    await chat.press("Fix an item")
    await chat.press("◀ Back to summary")
    assert chat.bill.state == "preview"
    await chat.press("Fix an item")
    await chat.press("🍽 1 Ramen S$20.00 · S")
    await chat.press("◀ Back")  # opened from Fix: back goes to the list
    assert chat.bill.state == "fix_list"


async def test_edit_on_an_item_summary_goes_back_to_names(monkeypatch):
    chat = make_chat(monkeypatch)
    await to_walk(chat)
    await claim(chat, "Me")
    await claim(chat, "Ali")
    await claim(chat, "Mei")
    await chat.press("Edit")
    assert chat.bill.state == "names" and chat.bill.split is None
    await chat.press("Next")
    await chat.press("Ali")
    await chat.press("🍽 Go item by item ▶")
    assert "(1 of 3)" in chat.card()


async def test_a_quantity_line_is_one_screen_per_unit_in_the_walk(monkeypatch):
    teas = fixtures.extraction(
        [fixtures.line("Tea", 900, quantity=3), fixtures.line("Rice", 500)],
        [fixtures.total(1400)],
    )
    chat = make_chat(monkeypatch, ScriptedReader(teas))
    await start_with_photo(chat)
    assert "2 items on the receipt" in chat.card()  # lines, as printed
    await chat.press("2")
    await chat.press("Next")
    await chat.press("Me")
    await chat.press("🍽 Go item by item ▶")
    assert "(1 of 4)\n<b>Tea (1/3)</b> · S$3.00" in chat.card()


# ---- the allowance ------------------------------------------------------------


async def test_a_second_photo_the_same_day_is_refused_with_the_pinned_text(monkeypatch):
    chat = make_chat(monkeypatch)
    await start_with_photo(chat)  # one successful read
    await chat.press("Cancel")
    await chat.command()
    await chat.photo()
    assert chat.bot.texts()[-1] == ALREADY_READ
    assert chat.bill.state == "awaiting_bill"  # still waiting for a typed bill line
    assert len(chat.deps.receipt_reader.calls) == 1
    await chat.say("84.50 dinner")
    assert chat.bill.state == "count"


async def test_a_failed_read_can_be_retried_twice_and_the_fourth_attempt_is_refused(monkeypatch):
    reader = ScriptedReader(ReceiptUnreadable())
    chat = make_chat(monkeypatch, reader)
    await start_with_photo(chat)  # attempt 1
    assert chat.card() == COULDNT_READ
    await chat.press("Try again")  # attempt 2
    await chat.press("Try again")  # attempt 3
    assert len(reader.calls) == 3
    card_before = chat.card()
    await chat.press("Try again")  # attempt 4: refused
    assert len(reader.calls) == 3
    assert chat.bot.texts()[-1] == THREE_TRIES
    assert chat.card() == card_before  # the card is left as it was
    assert chat.bill.state == "couldnt_read"
    # and the same on a new bill: the user still has no tries left
    await chat.press("Cancel")
    await chat.command()
    await chat.photo()
    assert chat.bot.texts()[-1] == THREE_TRIES
    assert chat.bill.state == "awaiting_bill" and len(reader.calls) == 3


async def test_a_success_after_a_failure_ends_the_day(monkeypatch):
    reader = ScriptedReader(ReceiptUnreadable(), fixtures.dinner())
    chat = make_chat(monkeypatch, reader)
    await start_with_photo(chat)
    assert chat.bill.state == "couldnt_read"
    await chat.press("Try again")
    assert chat.bill.state == "count"
    await chat.press("Cancel")
    await chat.command()
    await chat.photo()
    assert chat.bot.texts()[-1] == ALREADY_READ  # one read a day, not "3 tries"


async def test_the_next_singapore_day_allows_again(monkeypatch):
    chat = make_chat(monkeypatch)
    await start_with_photo(chat)
    await chat.press("Cancel")
    chat.now = datetime(2026, 11, 12, 15, 59, tzinfo=UTC)  # 23:59 in Singapore, same day
    await chat.command()
    await chat.photo()
    assert chat.bot.texts()[-1] == ALREADY_READ
    await chat.say("84.50 dinner")  # drop that bill, start a new one next day
    await chat.press("Cancel")
    chat.now = datetime(2026, 11, 12, 16, 1, tzinfo=UTC)  # 00:01, the next day
    await chat.command()
    await chat.photo()
    assert chat.bill.state == "count"
    assert len(chat.deps.receipt_reader.calls) == 2


async def test_another_user_is_unaffected(monkeypatch):
    chat = make_chat(monkeypatch)
    await start_with_photo(chat)
    other = Realistic(monkeypatch, deps=chat.deps, user_id=2, first_name="Ali")
    await start_with_photo(other)
    assert other.bill.state == "count"
    assert other.bot.texts()[:2] == [START_PROMPT, NOTICE]  # their own notice, too


async def test_the_owner_is_never_refused(monkeypatch):
    reader = ScriptedReader(ReceiptUnreadable())
    chat = make_chat(monkeypatch, reader, owner_id=1)
    await start_with_photo(chat)
    for _ in range(5):
        await chat.press("Try again")
    assert len(reader.calls) == 6
    assert chat.bill.state == "couldnt_read"
    # nor after a success
    chat.deps.receipt_reader.outcomes[:] = [fixtures.dinner()]
    await chat.press("Try again")
    await chat.press("Cancel")
    await chat.command()
    await chat.photo()
    assert chat.bill.state == "count"
    assert await chat.deps.photo_allowance.may_try(1, chat.now) is AlreadyRead  # as anyone else
    assert not any(t in (ALREADY_READ, THREE_TRIES) for t in chat.bot.texts())


async def test_with_no_owner_set_nobody_is_exempt(monkeypatch):
    chat = make_chat(monkeypatch, owner_id=None)
    await start_with_photo(chat)
    await chat.press("Cancel")
    await chat.command()
    await chat.photo()
    assert chat.bot.texts()[-1] == ALREADY_READ


# ---- the bot-wide cap ---------------------------------------------------------


async def test_bot_wide_cap_refuses_with_the_pinned_text_and_counts_nowhere(monkeypatch):
    budget = ReceiptReadBudget(chat_cap=40, global_cap=1)
    chat = make_chat(monkeypatch, budget=budget)
    await start_with_photo(chat)
    other = Realistic(monkeypatch, deps=chat.deps, user_id=2, first_name="Ali")
    await start_with_photo(other)
    assert other.bot.texts()[-1] == GLOBAL_CAP
    assert other.bill.state == "awaiting_bill"
    assert other.card() == START_PROMPT  # back to step 1: the card is the prompt again
    assert budget._global_count == 1  # the refused attempt took nothing
    # ...and it did not use up the other user's allowance
    assert await chat.deps.photo_allowance.may_try(2, chat.now) is Allowed
    # the owner is refused by the bot-wide cap too
    owner = Realistic(monkeypatch, deps=chat.deps, user_id=OWNER_ID, first_name="Boss")
    await start_with_photo(owner)
    assert owner.bot.texts()[-1] == GLOBAL_CAP
    assert await chat.deps.photo_allowance.may_try(2, chat.now) is Allowed
    assert chat.deps.photo_allowance._failures == {}  # nothing was recorded for anyone


async def test_every_allowed_attempt_takes_one_and_a_refused_one_takes_none(monkeypatch):
    budget = ReceiptReadBudget(chat_cap=40, global_cap=100)
    reader = ScriptedReader(ReceiptUnreadable(), ReceiptUnreadable(), fixtures.dinner())
    chat = make_chat(monkeypatch, reader, budget=budget)
    await start_with_photo(chat)
    assert budget._global_count == 1
    await chat.press("Try again")
    assert budget._global_count == 2
    await chat.press("Try again")  # succeeds: the third attempt
    assert budget._global_count == 3
    await chat.press("Cancel")
    await chat.command()
    await chat.photo()  # refused by the allowance: takes nothing
    assert chat.bot.texts()[-1] == ALREADY_READ
    assert budget._global_count == 3


async def test_the_owners_attempts_take_from_the_bot_wide_cap(monkeypatch):
    budget = ReceiptReadBudget(chat_cap=40, global_cap=100)
    chat = make_chat(monkeypatch, ScriptedReader(ReceiptUnreadable()), owner_id=1, budget=budget)
    await start_with_photo(chat)
    await chat.press("Try again")
    await chat.press("Try again")
    await chat.press("Try again")
    assert budget._global_count == 4


async def test_the_per_chat_cap_does_not_apply_in_a_private_chat(monkeypatch):
    budget = ReceiptReadBudget(chat_cap=0, global_cap=400)  # a chat cap of 0 refuses a group
    chat = make_chat(monkeypatch, budget=budget)
    await start_with_photo(chat)
    assert chat.bill.state == "count"
    assert budget.remaining(chat.chat.id, chat.now) == 0  # no chat count was kept
    assert budget._chat_counts == {}


async def test_group_reads_still_take_from_the_chat_and_global_counts(monkeypatch):
    class Spy(ReceiptReadBudget):
        def __init__(self) -> None:
            super().__init__()
            self.taken: list[str] = []

        def try_take(self, chat_id, now):
            self.taken.append("try_take")
            return super().try_take(chat_id, now)

        def try_take_global(self, now):
            self.taken.append("try_take_global")
            return super().try_take_global(now)

    ledger, _trip = await group_tests._bound_ledger()
    spy = Spy()
    deps = make_deps(
        ledger,
        receipt_reader=ScriptedReader(group_tests._confident_extraction()),
        receipt_budget=spy,
    )
    await photo_expense_handler(group_tests._update(), group_tests._context(deps))
    assert spy.taken == ["try_take"]
    assert spy._chat_counts and spy._global_count == 1


# ---- the notice ---------------------------------------------------------------


async def test_the_notice_goes_once_per_user(monkeypatch):
    reader = ScriptedReader(ReceiptUnreadable(), ReceiptUnreadable(), fixtures.dinner())
    chat = make_chat(monkeypatch, reader)
    await start_with_photo(chat)
    await chat.press("Try again")
    await chat.press("Try again")
    assert chat.bot.texts().count(NOTICE) == 1
    other = Realistic(monkeypatch, deps=chat.deps, user_id=2, first_name="Ali")
    await start_with_photo(other)
    assert other.bot.texts().count(NOTICE) == 1


async def test_a_refused_attempt_gets_no_notice_and_does_not_use_it_up(monkeypatch):
    chat = make_chat(monkeypatch)
    await start_with_photo(chat)
    await chat.press("Cancel")
    notices_before = chat.bot.texts().count(NOTICE)
    await chat.command()
    await chat.photo()  # refused: already read
    assert chat.bot.texts().count(NOTICE) == notices_before
    # the bot-wide cap is taken just before the model call, after the notice: that user
    # has been told once, and is not told again when a later attempt reads
    budget = ReceiptReadBudget(global_cap=0)
    capped = make_chat(monkeypatch, budget=budget, user_id=5)
    await start_with_photo(capped)
    assert capped.bot.texts()[-1] == GLOBAL_CAP and capped.bot.texts().count(NOTICE) == 1
    budget.global_cap = 10
    await capped.photo()
    assert capped.bot.texts().count(NOTICE) == 1
    assert capped.bill.state == "count"


# ---- couldn't read ------------------------------------------------------------


async def test_couldnt_read_card_and_keyboard(monkeypatch):
    chat = make_chat(monkeypatch, ScriptedReader(ReceiptUnreadable()))
    await start_with_photo(chat)
    assert chat.card() == "🧾 <b>Receipt</b>\n\nCouldn't read that receipt"
    bill_id = chat.bill.bill_id
    assert chat.keyboard() == [
        [
            ("Try again", f"oneoff:{bill_id}:retry"),
            ("Type the bill instead", f"oneoff:{bill_id}:type"),
        ],
        [("Cancel", f"oneoff:{bill_id}:cancel")],
    ]


async def test_couldnt_read_card_when_the_reader_service_is_down(monkeypatch):
    chat = make_chat(monkeypatch, ScriptedReader(ReceiptServiceUnavailable("temporarily down")))
    await start_with_photo(chat)
    assert chat.card() == (
        "🧾 <b>Receipt</b>\n\nCouldn't read that receipt: the receipt reader is "
        "temporarily down, not a problem with your photo. Try again in a minute, "
        "or type the bill."
    )
    assert chat.labels() == ["Try again", "Type the bill instead", "Cancel"]


async def test_a_download_failure_is_a_couldnt_read_card(monkeypatch):
    chat = make_chat(monkeypatch)
    chat.bot.get_file = AsyncMock(side_effect=RuntimeError("telegram is down"))
    await start_with_photo(chat)
    assert chat.card() == COULDNT_READ
    assert chat.deps.receipt_reader.calls == []


async def test_a_reading_with_no_usable_totals_is_a_couldnt_read_card(monkeypatch):
    nothing = fixtures.extraction([fixtures.line("Blur", 100)], [])
    chat = make_chat(monkeypatch, ScriptedReader(nothing))
    await start_with_photo(chat)
    assert chat.card() == COULDNT_READ


async def test_try_again_success_goes_on_to_the_how_many_card(monkeypatch):
    chat = make_chat(monkeypatch, ScriptedReader(ReceiptUnreadable(), fixtures.dinner()))
    await start_with_photo(chat)
    card_id = chat.bill.message_id
    await chat.press("Try again")
    assert chat.bill.message_id == card_id  # the card is edited in place
    assert chat.card() == COUNT_CARD
    assert chat.bill.state == "count"


async def test_try_again_failure_stays_on_the_couldnt_read_card(monkeypatch):
    chat = make_chat(monkeypatch, ScriptedReader(ReceiptUnreadable()))
    await start_with_photo(chat)
    await chat.press("Try again")
    assert chat.card() == COULDNT_READ and chat.bill.state == "couldnt_read"
    assert chat.labels() == ["Try again", "Type the bill instead", "Cancel"]


async def test_try_again_shows_reading_while_it_reads(monkeypatch):
    reader = GatedReader(ReceiptUnreadable(), fixtures.dinner())
    chat = make_chat(monkeypatch, reader)
    reader.gate.set()
    await start_with_photo(chat)
    reader.gate.clear()
    reader.started.clear()
    task = asyncio.create_task(chat.press("Try again"))
    await reader.wait_started()
    assert chat.card() == READING and chat.keyboard() is None
    assert chat.bill.state == "reading"
    reader.gate.set()
    query = await bounded(task)
    assert chat.card() == COUNT_CARD
    assert answers(query) == [None]  # answered once, before the slow read


async def test_type_the_bill_instead_returns_to_the_step_one_prompt(monkeypatch):
    chat = make_chat(monkeypatch, ScriptedReader(ReceiptUnreadable()))
    await start_with_photo(chat)
    card_id = chat.bill.message_id
    await chat.press("Type the bill instead")
    assert chat.bot.messages[card_id]["text"] == START_PROMPT
    assert chat.bot.messages[card_id]["keyboard"] is None
    assert chat.bill.state == "awaiting_bill" and not chat.bill.from_receipt
    await chat.say("84.50 dinner")
    assert chat.card().startswith("🧾 <b>Dinner</b> · S$84.50")
    assert "items on the receipt" not in chat.card()


async def test_cancel_on_the_couldnt_read_card(monkeypatch):
    chat = make_chat(monkeypatch, ScriptedReader(ReceiptUnreadable()))
    await start_with_photo(chat)
    card_id = chat.bill.message_id
    await chat.press("Cancel")
    assert chat.bot.messages[card_id]["text"] == "Cancelled." and chat.bill is None


async def test_a_deleted_reading_card_is_replaced_not_stranded(monkeypatch):
    reader = GatedReader(fixtures.dinner())
    chat = make_chat(monkeypatch, reader)
    await chat.command()
    task = asyncio.create_task(chat.photo())
    await reader.wait_started()
    chat.bot.deleted.add(chat.bill.message_id)  # the user deletes `Reading the receipt…`
    reader.gate.set()
    await bounded(task)
    assert chat.bill.state == "count"
    assert chat.card() == COUNT_CARD  # the card went out again as a new message


# ---- a photo elsewhere --------------------------------------------------------


def _photo_message_update(user_id: int = 1) -> Update:
    message = Message(
        message_id=2,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, first_name="Ada", is_bot=False),
        photo=(PhotoSize("f", "u", 100, 100),),
    )
    return Update(update_id=2, message=message)


async def test_a_photo_at_any_other_step_or_with_no_bill_gets_no_reply(monkeypatch):
    chat = make_chat(monkeypatch)
    update = SimpleNamespace(
        effective_chat=chat.chat,
        effective_user=chat.user,
        effective_message=SimpleNamespace(
            photo=[SimpleNamespace(file_id="x", file_size=10)], caption=None
        ),
    )
    # no bill
    await oneoff_photo_handler(update, chat.context)
    assert chat.bot.sent == []
    await start_with_photo(chat)
    await chat.press("Cancel")
    assert chat.bill is None
    await chat.command()
    await chat.say("84.50 dinner")  # at the how-many step now
    sent = len(chat.bot.sent)
    reads = len(chat.deps.receipt_reader.calls)
    for state_press in (None, "3", "Next", "Me"):
        if state_press:
            await chat.press(state_press)
        await oneoff_photo_handler(update, chat.context)  # even if it got through the filter
        assert len(chat.bot.sent) == sent
    assert len(chat.deps.receipt_reader.calls) == reads


def test_the_application_routes_a_private_photo_only_at_step_one_or_while_reading():
    deps = make_deps()
    application = _application(deps)
    handler = next(
        h for h in application.handlers[0] if getattr(h, "callback", None) is oneoff_photo_handler
    )
    assert handler.block is False  # non-blocking, like the group's photo handler
    update = _photo_message_update()
    assert oneoff_photo_handler not in _matching(application, update)  # no bill
    from app.services import oneoff

    now = datetime.now(UTC)
    bill = deps.oneoff_bills.create(user_id=1, chat_id=1, first_name="Ada", now=now)
    assert oneoff_photo_handler in _matching(application, update)  # step 1
    reading = oneoff.begin_read(bill, "f")
    deps.oneoff_bills.put(reading, now)
    assert oneoff_photo_handler in _matching(application, update)  # reading
    failed = oneoff.read_failed(reading)
    deps.oneoff_bills.put(failed, now)
    assert oneoff_photo_handler not in _matching(application, update)  # couldn't read
    deps.oneoff_bills.put(oneoff.type_instead(failed), now)
    assert oneoff_photo_handler in _matching(application, update)
    count = oneoff.set_bill_line(
        deps.oneoff_bills.for_user(1, now),  # type: ignore[arg-type]
        oneoff.BillLine("Dinner", 100, "SGD"),
    )
    deps.oneoff_bills.put(count, now)
    assert oneoff_photo_handler not in _matching(application, update)
    # an edited message with a photo is not read
    edited = Update(update_id=3, edited_message=update.message)
    assert oneoff_photo_handler not in _matching(application, edited)
    # a photo sent as a file (document) is not a photo
    document = Message(
        message_id=3,
        date=now,
        chat=Chat(id=1, type="private"),
        from_user=User(id=1, first_name="Ada", is_bot=False),
    )
    assert oneoff_photo_handler not in _matching(application, Update(update_id=4, message=document))


async def test_a_photo_over_ten_megabytes_gets_the_group_text_and_counts_nowhere(monkeypatch):
    budget = ReceiptReadBudget()
    chat = make_chat(monkeypatch, budget=budget)
    await chat.command()
    await chat.photo(file_size=11 * 1024 * 1024)
    assert chat.bot.texts()[-1] == TOO_LARGE
    assert chat.bot.get_file.await_count == 0  # not even downloaded
    assert chat.bill.state == "awaiting_bill"
    assert budget._global_count == 0
    assert await chat.deps.photo_allowance.may_try(1, chat.now) is Allowed
    assert NOTICE not in chat.bot.texts()
    await chat.photo()  # a smaller one still reads
    assert chat.bill.state == "count"


async def test_a_photo_found_too_large_on_download_is_not_counted_as_a_try(monkeypatch):
    chat = make_chat(monkeypatch)
    big = SimpleNamespace(file_size=11 * 1024 * 1024, download_as_bytearray=AsyncMock())
    chat.bot.get_file = AsyncMock(return_value=big)
    await chat.command()
    await chat.photo(file_size=None)  # Telegram did not say how big it was
    assert chat.bot.texts()[-1] == TOO_LARGE
    assert chat.deps.receipt_reader.calls == []
    # back to step 1, the card is the prompt, and there is no `Try again` for it
    assert chat.bill.state == "awaiting_bill" and chat.card() == START_PROMPT
    assert chat.keyboard() is None
    assert chat.deps.receipt_budget._global_count == 0  # the cap is taken just before the model
    assert await chat.deps.photo_allowance.may_try(1, chat.now) is Allowed
    big.download_as_bytearray.assert_not_awaited()
    # no failure was recorded: three more real attempts are still there
    for _ in range(3):
        assert await chat.deps.photo_allowance.may_try(1, chat.now) is Allowed
        await chat.deps.photo_allowance.record_failure(1, chat.now)
    assert await chat.deps.photo_allowance.may_try(1, chat.now) is OutOfTries


async def test_the_caption_is_ignored(monkeypatch):
    chat = make_chat(monkeypatch)
    await chat.command()
    await chat.photo(caption="dinner for 5 paid by Ali")
    assert chat.bill.description == "Ramen House"
    assert chat.card() == COUNT_CARD


# ---- a read in progress -------------------------------------------------------


async def test_a_second_photo_during_a_read_is_told_to_wait(monkeypatch):
    reader = GatedReader(fixtures.dinner())
    chat = make_chat(monkeypatch, reader)
    await chat.command()
    first = asyncio.create_task(chat.photo(file_id="first"))
    await reader.wait_started()
    assert chat.bill.state == "reading"
    assert await bounded(chat.photo(file_id="second"))
    assert chat.bot.texts()[-1] == ONE_AT_A_TIME
    assert len(reader.calls) == 0  # the first is still in the model; no second call
    reader.gate.set()
    await bounded(first)
    assert chat.bill.state == "count" and chat.bill.photo_file_id == "first"
    assert len(reader.calls) == 1
    assert chat.bot.texts().count(ONE_AT_A_TIME) == 1


async def _unfiltered_photo(chat: Chatter, file_id: str) -> None:
    """The handler, called as PTB does for a photo whose filter already matched."""
    message = SimpleNamespace(
        photo=[SimpleNamespace(file_id=file_id, file_size=1000)], caption=None
    )
    update = SimpleNamespace(
        effective_chat=chat.chat, effective_user=chat.user, effective_message=message
    )
    await oneoff_photo_handler(update, chat.context)


async def test_two_photos_that_overlap_before_either_awaits_read_once(monkeypatch):
    """With `block=False` two photos can both be matched before either runs. The
    reading flag is set before the first await, so the second one waits.
    """
    reader = GatedReader(fixtures.dinner())
    chat = make_chat(monkeypatch, reader)
    chat.bot.yield_on_send = True  # every send lets the other task run
    await chat.command()
    first = asyncio.create_task(_unfiltered_photo(chat, "first"))
    second = asyncio.create_task(_unfiltered_photo(chat, "second"))
    await asyncio.sleep(0.01)
    reader.gate.set()
    await bounded(asyncio.gather(first, second))
    assert len(reader.calls) == 1
    assert chat.bot.texts().count(ONE_AT_A_TIME) == 1
    assert chat.bot.texts().count(NOTICE) == 1
    assert chat.bill.state == "count" and chat.bill.photo_file_id == "first"
    assert chat.deps.receipt_budget._global_count == 1


async def test_cancelling_during_the_read_leaves_no_bill_and_no_new_card(monkeypatch):
    reader = GatedReader(fixtures.dinner())
    chat = make_chat(monkeypatch, reader)
    await chat.command()
    task = asyncio.create_task(chat.photo())
    await reader.wait_started()
    card_id = chat.bill.message_id
    assert chat.bot.messages[card_id]["text"] == READING
    await chat.command("cancel")
    assert chat.bill is None
    assert chat.bot.messages[card_id]["text"] == "Cancelled."
    sent = len(chat.bot.sent)
    reader.gate.set()
    await bounded(task)
    # the late result did not bring the bill back or write over the cancelled card
    assert chat.bill is None
    assert chat.store._bills == {}
    assert chat.bot.messages[card_id]["text"] == "Cancelled."
    assert len(chat.bot.sent) == sent
    # the read did happen: it counts against the allowance, as any read that reached the model
    assert await chat.deps.photo_allowance.may_try(1, chat.now) is AlreadyRead


async def test_start_again_during_the_read_keeps_the_new_bill_clean(monkeypatch):
    reader = GatedReader(fixtures.dinner())
    chat = make_chat(monkeypatch, reader)
    await chat.command()
    old = chat.bill
    task = asyncio.create_task(chat.photo())
    await reader.wait_started()
    reading_id = chat.bill.message_id
    await chat.press_data("oneoff:start", old.message_id)  # `Split a bill now`
    await chat.press("Yes, start again")
    new = chat.bill
    assert new.bill_id != old.bill_id and new.state == "awaiting_bill"
    assert chat.bot.messages[reading_id]["text"] == "Cancelled."
    sent = len(chat.bot.sent)
    reader.gate.set()
    await bounded(task)
    # the old bill's result was dropped: the new bill is untouched
    assert chat.bill == new
    assert chat.bill.line_items == () and not chat.bill.from_receipt
    assert len(chat.bot.sent) == sent
    assert COUNT_CARD not in chat.bot.texts()


async def test_the_result_is_dropped_if_the_bill_is_no_longer_reading(monkeypatch):
    """A bill that left `reading` another way (here: expired) takes no result."""
    reader = GatedReader(fixtures.dinner())
    chat = make_chat(monkeypatch, reader)
    await chat.command()
    task = asyncio.create_task(chat.photo())
    await reader.wait_started()
    chat.advance(minutes=31)
    reader.gate.set()
    await bounded(task)
    assert chat.bill is None
    assert COUNT_CARD not in chat.bot.texts()


# ---- no ledger or store, no log, no disk --------------------------------------


def _exploding_deps(reader) -> Deps:
    return Deps(
        ledger=Exploding(),
        currencies=Exploding(),
        bot_version="test",
        draft_service=Exploding(),
        receipt_reader=reader,
        settrip_sessions=Exploding(),
        feedback_limiter=Exploding(),
        feedback_waits=Exploding(),
    )


async def _receipt_flow(chat: Chatter) -> None:
    await to_walk(chat)
    await claim(chat, "Me")
    await claim(chat, "Ali")
    await claim(chat, "Me", "Ali", "Mei")
    await chat.press("Fix an item")
    await chat.press("◀ Back to summary")
    await chat.press("Done")


async def test_the_whole_receipt_path_touches_no_ledger_store_or_draft_service(monkeypatch):
    chat = Realistic(monkeypatch, deps=_exploding_deps(ScriptedReader(fixtures.dinner())))
    await _receipt_flow(chat)
    assert chat.bill is None
    assert chat.bot.texts()[-1] == (
        "Forward the message above to the people on the bill. I haven't saved it."
    )


async def test_no_log_record_carries_an_item_a_merchant_a_name_or_an_amount(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    secret = fixtures.extraction(
        [
            fixtures.line("Truffle Fries", 123456),
            fixtures.line("Zebra Wine", 654321),
            fixtures.line("GST", 777, kind="tax"),
        ],
        [fixtures.total(778554)],
        description="Zebra Bistro",
        merchant="Zebra Bistro",
    )
    # a reader that crashes with the model's text in its message, then an unreadable one,
    # then a good one; and after Done a refusal
    reader = ScriptedReader(RuntimeError("Truffle Fries 123456"), ReceiptUnreadable(), secret)
    chat = make_chat(monkeypatch, reader, first_name="Quincy")
    await start_with_photo(chat)
    await chat.press("Try again")
    await chat.press("Try again")
    await chat.press("2")
    await chat.say("Zorro")
    await chat.press("Next")
    await chat.press("Me")
    await chat.press("🍽 Go item by item ▶")
    await claim(chat, "Me")
    await claim(chat, "Zorro")
    await chat.press("Done")
    await chat.command()
    await chat.photo()  # refused: already read today
    assert chat.bot.texts()[-1] == ALREADY_READ
    text = " ".join(
        " ".join(
            [
                record.getMessage(),
                str(record.__dict__.get("args", "")),
                record.exc_text or "",
                str(record.exc_info or ""),
            ]
        )
        for record in caplog.records
    )
    assert all(r.exc_info is None and r.exc_text is None for r in caplog.records)
    for needle in (
        "Truffle",
        "Zebra",
        "Quincy",
        "Zorro",
        "123456",
        "654321",
        "7785",
        "1234.56",
        "6543.21",
        "photo-1",
        "S$",
    ):
        assert needle not in text, needle
    events = [r.getMessage() for r in caplog.records if r.name.startswith("app.")]
    assert set(events) <= {
        "oneoff started",
        "oneoff finished",
        "oneoff cancelled",
        "oneoff expired",
        "oneoff photo read",
        "oneoff photo failed",
        "oneoff photo: reader failed unexpectedly",
        "oneoff photo refused: user",
        "oneoff photo refused: tries",
        "oneoff photo refused: global",
    }
    assert "oneoff photo failed" in events and "oneoff photo read" in events
    assert "oneoff photo: reader failed unexpectedly" in events
    assert "oneoff photo refused: user" in events


async def test_each_refusal_logs_its_event_name_only(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    chat = make_chat(monkeypatch)
    await start_with_photo(chat)
    await chat.press("Cancel")
    await chat.command()
    await chat.photo()  # user
    failing = make_chat(monkeypatch, ScriptedReader(ReceiptUnreadable()), user_id=3)
    await start_with_photo(failing)
    for _ in range(3):
        await failing.press("Try again")  # the third of these is refused: tries
    capped = make_chat(monkeypatch, budget=ReceiptReadBudget(global_cap=0), user_id=4)
    await start_with_photo(capped)
    events = [r.getMessage() for r in caplog.records if "photo" in r.getMessage()]
    assert "oneoff photo refused: user" in events
    assert "oneoff photo refused: tries" in events
    assert "oneoff photo refused: global" in events
    assert "oneoff photo read" in events and "oneoff photo failed" in events


async def test_nothing_is_written_to_disk(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    real_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            raise AssertionError(f"wrote to disk: {file!r}")
        return real_open(file, mode, *args, **kwargs)

    chat = make_chat(monkeypatch, ScriptedReader(ReceiptUnreadable(), fixtures.dinner()))
    monkeypatch.setattr(builtins, "open", guarded_open)
    await start_with_photo(chat)
    await chat.press("Try again")
    await chat.press("3")
    await chat.say("Ali, Mei")
    await chat.press("Next")
    await chat.press("Me")
    await chat.press("🍽 Go item by item ▶")
    await claim(chat, "Me")
    await claim(chat, "Ali")
    await claim(chat, "Mei")
    await chat.press("Done")
    monkeypatch.setattr(builtins, "open", real_open)
    assert list(tmp_path.iterdir()) == []


async def test_the_image_never_reaches_the_bill_only_its_file_id(monkeypatch):
    chat = make_chat(monkeypatch)
    await start_with_photo(chat)
    bill = chat.bill
    assert bill.photo_file_id == "photo-1"
    assert "/9j/" not in repr(bill)  # no base64 of a JPEG anywhere on the bill


def test_the_item_split_is_a_third_split_kind_beside_equal_and_typed():
    from app.services.oneoff import EqualSplit, Split, TypedSplit

    assert set(Split.__args__) == {EqualSplit, TypedSplit, ItemsSplit}  # type: ignore[attr-defined]


# ---- rendering ----------------------------------------------------------------


def test_the_refusal_and_notice_texts_are_the_pinned_ones():
    assert render.NOTICE_TEXT == NOTICE
    assert render.READING_TEXT == READING
    assert render.ONE_AT_A_TIME_TEXT == ONE_AT_A_TIME
    assert render.ALREADY_READ_TEXT == ALREADY_READ
    assert render.OUT_OF_TRIES_TEXT == THREE_TRIES
    assert render.GLOBAL_CAP_TEXT == GLOBAL_CAP
    assert render.START_PROMPT_TEXT == START_PROMPT
    assert render.BAD_AMOUNT_TEXT == "Send just the amount, like 84.50."
    assert render.SEND_AMOUNT_TEXT == "Send the amount."


def test_an_item_preview_with_twenty_long_names_fits_in_one_message():
    from dataclasses import replace

    from app.models.receipt import ReceiptLineItem
    from app.services import oneoff

    names = tuple("N" * 30 + str(n) for n in range(2, 21))
    lines = tuple(
        ReceiptLineItem(
            description=f"Dish number {n:02d} with a long name",
            quantity=1,
            amount_minor=99_999_99,
            kind="item",
        )
        for n in range(40)
    ) + (
        ReceiptLineItem(description="GST", quantity=1, amount_minor=9_999_99, kind="tax"),
        ReceiptLineItem(
            description="Service", quantity=1, amount_minor=9_999_99, kind="service_charge"
        ),
        ReceiptLineItem(description="Voucher", quantity=1, amount_minor=9_999_99, kind="discount"),
    )
    total = sum(i.amount_minor for i in lines[:40]) + 9_999_99
    bill = oneoff.OneOffBill(
        bill_id="abcdefgh",
        user_id=1,
        chat_id=1,
        first_name="Sufyan",
        state="preview",
        message_id=1,
        description="D" * 80,
        amount_minor=total,
        count=20,
        names=("Sufyan", *names),
        payer=1,
        photo_file_id="f",
        line_items=lines,
        claims={i: tuple(range(1, 21)) for i in range(40)},
        split=ItemsSplit(),
    )
    bill = replace(bill, split=replace(bill.split, shares=dict(oneoff.item_breakdown(bill).shares)))
    text, _markup = render.render_card(bill)
    assert len(text) < 4096


async def test_a_second_try_again_press_during_the_read_is_ignored(monkeypatch):
    reader = GatedReader(ReceiptUnreadable())
    chat = make_chat(monkeypatch, reader)
    reader.gate.set()
    await start_with_photo(chat)  # attempt 1 fails
    reader.gate.clear()
    reader.started.clear()
    message_id, data = chat.find_button("Try again")
    first = asyncio.create_task(chat.press_data(data, message_id))
    await reader.wait_started()
    second = await bounded(chat.press_data(data, message_id))  # double tap while reading
    assert answers(second) == [None]
    reader.gate.set()
    await bounded(first)
    assert len(reader.calls) == 2  # the start and one retry: the double tap read nothing
    assert chat.deps.receipt_budget._global_count == 2


# ---- review: the daily limit cannot be bypassed by timing ---------------------


async def test_a_double_tap_on_try_again_makes_one_model_call(monkeypatch):
    """Two failures, then two taps together: one read, one recorded failure, and the
    second tap is told to wait (it used to give 4 model calls in all).
    """
    reader = ScriptedReader(ReceiptUnreadable())
    chat = make_chat(monkeypatch, reader)
    await bounded(start_with_photo(chat))  # attempt 1
    await bounded(chat.press("Try again"))  # attempt 2
    assert len(reader.calls) == 2
    message_id, data = chat.find_button("Try again")
    first = asyncio.create_task(chat.press_data(data, message_id))
    second = asyncio.create_task(chat.press_data(data, message_id))
    await bounded(asyncio.gather(first, second))
    assert len(reader.calls) == 3  # not 4
    assert chat.bot.texts().count(ONE_AT_A_TIME) == 1
    assert chat.deps.receipt_budget._global_count == 3
    assert chat.deps.photo_allowance._failures == {1: 3}


async def test_a_photo_on_a_new_bill_during_a_read_is_told_to_wait(monkeypatch):
    """Photo, /cancel, /split, photo again while the first read is still running: the
    guard is per user, not per bill (a non-owner used to get 3 reads this way).
    """
    reader = GatedReader(fixtures.dinner())
    chat = make_chat(monkeypatch, reader)
    await chat.command()
    first = asyncio.create_task(chat.photo(file_id="first"))
    try:
        await reader.wait_started()
        await chat.command("cancel")
        await chat.command()  # a new bill at step 1
        assert chat.bill.state == "awaiting_bill"
        assert await bounded(chat.photo(file_id="second"))
        assert chat.bot.texts()[-1] == ONE_AT_A_TIME
        assert chat.bill.state == "awaiting_bill"
    finally:
        reader.gate.set()
    await bounded(first)
    assert len(reader.calls) == 1
    assert chat.deps.receipt_budget._global_count == 1


# ---- review: what counts, in every ending ---------------------------------------


def counts(chat: Chatter) -> tuple[int, int, int]:
    """(bot-wide count, failed tries, successes) for user 1."""
    allowance = chat.deps.photo_allowance
    return (
        chat.deps.receipt_budget._global_count,
        allowance._failures.get(1, 0),
        allowance._successes.get(1, 0),
    )


async def _undecodable(chat: Chatter) -> None:
    async def download():
        return bytearray(b"this is not an image")

    chat.bot.get_file = AsyncMock(
        return_value=SimpleNamespace(file_size=20, download_as_bytearray=download)
    )


async def _download_fails(chat: Chatter) -> None:
    chat.bot.get_file = AsyncMock(side_effect=RuntimeError("telegram is down"))


async def _too_large(chat: Chatter) -> None:
    big = SimpleNamespace(file_size=11 * 1024 * 1024, download_as_bytearray=AsyncMock())
    chat.bot.get_file = AsyncMock(return_value=big)


async def _nothing(chat: Chatter) -> None:
    return None


@pytest.mark.parametrize(
    ("reader", "setup", "photo_kw", "expected_counts", "expected_state"),
    [
        (ScriptedReader(fixtures.dinner()), _download_fails, {}, (0, 0, 0), "couldnt_read"),
        (ScriptedReader(fixtures.dinner()), _undecodable, {}, (0, 0, 0), "couldnt_read"),
        (
            ScriptedReader(fixtures.dinner()),
            _too_large,
            {"file_size": None},
            (0, 0, 0),
            "awaiting_bill",
        ),
        (ScriptedReader(ReceiptUnreadable()), _nothing, {}, (1, 1, 0), "couldnt_read"),
        (
            ScriptedReader(ReceiptServiceUnavailable("busy")),
            _nothing,
            {},
            (1, 1, 0),
            "couldnt_read",
        ),
        (ScriptedReader(RuntimeError("boom")), _nothing, {}, (1, 1, 0), "couldnt_read"),
        (ScriptedReader(fixtures.dinner()), _nothing, {}, (1, 0, 1), "count"),
    ],
    ids=[
        "download failure",
        "undecodable image",
        "too large at download",
        "unreadable receipt",
        "reader outage",
        "reader crash",
        "success",
    ],
)
async def test_what_counts_on_the_cap_and_the_allowance_in_every_ending(
    monkeypatch, reader, setup, photo_kw, expected_counts, expected_state
):
    chat = make_chat(monkeypatch, reader)
    await setup(chat)
    await bounded(chat.command())
    await bounded(chat.photo(**photo_kw))
    assert counts(chat) == expected_counts
    assert chat.bill.state == expected_state
    assert 1 not in chat.deps.photo_in_flight  # the in-flight mark is gone, whatever happened
    # and the user can try again at once (not stuck behind a mark)
    if expected_state == "couldnt_read":
        assert chat.labels() == ["Try again", "Type the bill instead", "Cancel"]


async def test_refused_by_the_allowance_counts_nowhere(monkeypatch):
    chat = make_chat(monkeypatch)
    await bounded(start_with_photo(chat))
    assert counts(chat) == (1, 0, 1)
    await chat.press("Cancel")
    await chat.command()
    await bounded(chat.photo())
    assert chat.bot.texts()[-1] == ALREADY_READ
    assert counts(chat) == (1, 0, 1)  # nothing moved
    assert 1 not in chat.deps.photo_in_flight


async def test_refused_by_the_bot_wide_cap_counts_nowhere(monkeypatch):
    chat = make_chat(monkeypatch, budget=ReceiptReadBudget(global_cap=0))
    await bounded(start_with_photo(chat))
    assert chat.bot.texts()[-1] == GLOBAL_CAP
    assert counts(chat) == (0, 0, 0)
    assert chat.deps.receipt_reader.calls == []  # the model was never called
    assert chat.bill.state == "awaiting_bill" and chat.card() == START_PROMPT
    assert 1 not in chat.deps.photo_in_flight


async def test_the_owner_can_read_twice_in_a_row_after_a_read_ends(monkeypatch):
    chat = make_chat(monkeypatch, owner_id=1)
    await bounded(start_with_photo(chat))
    assert 1 not in chat.deps.photo_in_flight
    await chat.press("Cancel")
    await chat.command()
    await bounded(chat.photo())
    assert chat.bill.state == "count"
    assert chat.deps.receipt_budget._global_count == 2
    assert ONE_AT_A_TIME not in chat.bot.texts()


async def test_a_later_attempt_works_once_the_first_read_has_ended(monkeypatch):
    reader = GatedReader(ReceiptUnreadable(), fixtures.dinner())
    chat = make_chat(monkeypatch, reader)
    await chat.command()
    first = asyncio.create_task(chat.photo())
    await reader.wait_started()
    assert 1 in chat.deps.photo_in_flight
    reader.gate.set()
    await bounded(first)
    assert 1 not in chat.deps.photo_in_flight
    await bounded(chat.press("Try again"))  # a second attempt is not told to wait
    assert chat.bill.state == "count" and ONE_AT_A_TIME not in chat.bot.texts()


# ---- review: a Telegram failure must not strand the user ---------------------------


async def test_a_telegram_failure_sending_reading_leaves_no_mark_and_no_stuck_bill(monkeypatch):
    chat = make_chat(monkeypatch)
    await chat.command()
    real_send = chat.bot.send_message
    chat.bot.send_message = AsyncMock(side_effect=NetworkError("send failed"))
    with pytest.raises(NetworkError):
        await bounded(chat.photo())
    assert 1 not in chat.deps.photo_in_flight
    assert chat.bill.state == "awaiting_bill" and chat.bill.photo_file_id is None
    assert counts(chat) == (0, 0, 0)  # the model was never called
    chat.bot.send_message = real_send
    await bounded(chat.photo())  # not stuck: the next photo reads
    assert chat.bill.state == "count"


async def test_a_telegram_failure_on_try_again_puts_the_card_back(monkeypatch):
    chat = make_chat(monkeypatch, ScriptedReader(ReceiptServiceUnavailable("busy")))
    await bounded(start_with_photo(chat))
    chat.bot.edit_message_text = AsyncMock(side_effect=NetworkError("edit failed"))
    with pytest.raises(NetworkError):
        await bounded(chat.press("Try again"))
    assert 1 not in chat.deps.photo_in_flight
    assert chat.bill.state == "couldnt_read" and chat.bill.read_problem == "busy"
    assert counts(chat) == (1, 1, 0)  # only the first attempt reached the model


async def test_a_failure_while_recording_does_not_strand_the_bill(monkeypatch):
    chat = make_chat(monkeypatch)
    await chat.command()

    async def broken(*_args, **_kwargs):
        raise RuntimeError("database is down")

    chat.deps.photo_allowance.record_success = broken  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await bounded(chat.photo())
    assert 1 not in chat.deps.photo_in_flight
    assert chat.bill.state == "awaiting_bill"


# ---- review: claims belong to the receipt ------------------------------------------


async def test_edit_keeps_the_ticks_and_the_shares_are_the_same(monkeypatch):
    chat = make_chat(monkeypatch)
    await to_walk(chat)
    await claim(chat, "Me")
    await claim(chat, "Ali")
    await claim(chat, "Me", "Ali", "Mei")
    before = dict(chat.bill.shares)
    await chat.press("Edit")
    await chat.say("Zed")  # rename person 2
    await chat.press("Next")
    await chat.press("Me")  # who paid
    await chat.press("🍽 Go item by item ▶")
    assert "(1 of 3)\n<b>Ramen</b> · S$20.00" in chat.card()
    assert "✓ Me" in chat.labels() and "Zed" in chat.labels()  # the earlier tick is there
    await chat.press("Next ▶")
    assert "✓ Zed" in chat.labels()  # item 2 was Ali's, now Zed's
    await chat.press("Next ▶")
    await chat.press("Next ▶")
    assert chat.bill.state == "preview"
    assert chat.bill.shares == before


async def test_a_new_read_clears_the_claims(monkeypatch):
    reader = ScriptedReader(fixtures.dinner())
    chat = make_chat(monkeypatch, reader)
    await to_walk(chat)
    await claim(chat, "Me")
    assert chat.bill.claims
    bill_id = chat.bill.bill_id
    # a typed bill line at a later step replaces the bill: a new bill, no receipt, no claims
    await chat.press("Cancel")
    await chat.command()
    await chat.say("84.50 dinner")
    assert chat.bill.bill_id != bill_id and chat.bill.claims == {} and not chat.bill.from_receipt


# ---- review: the items line --------------------------------------------------------


async def test_the_items_line_says_item_for_one_and_items_otherwise(monkeypatch):
    one = fixtures.extraction([fixtures.line("Ramen", 800)], [fixtures.total(800)])
    chat = make_chat(monkeypatch, ScriptedReader(one))
    await start_with_photo(chat)
    assert chat.card().splitlines()[1] == "1 item on the receipt"
    taxes = fixtures.extraction([fixtures.line("GST", 300, kind="tax")], [fixtures.total(300)])
    other = make_chat(monkeypatch, ScriptedReader(taxes), user_id=2)
    await start_with_photo(other)
    assert other.card().splitlines()[1] == "0 items on the receipt"  # as the group card
    bare = fixtures.extraction([], [fixtures.total(500)])
    third = make_chat(monkeypatch, ScriptedReader(bare), user_id=3)
    await start_with_photo(third)
    assert third.card().splitlines()[1] == ""  # no lines at all: the group card has no line
    assert "items on the receipt" not in third.card()


class LaggyAllowance(PhotoAllowance):
    """An allowance whose answer arrives late, as a database's can: the verdict is worked
    out when asked and delivered after `lags` event-loop turns (one entry per call).
    """

    def __init__(self, lags: list[int]) -> None:
        super().__init__()
        self.lags = lags

    async def may_try(self, user_id, now, *, is_owner=False):
        verdict = await super().may_try(user_id, now, is_owner=is_owner)
        for _ in range(self.lags.pop(0) if self.lags else 0):
            await asyncio.sleep(0)
        return verdict


async def test_the_mark_is_set_before_a_slow_allowance_answers(monkeypatch):
    """A double tap where the second tap's allowance answer arrives only after the first
    read has finished. A guard marked after the allowance would let it through with a
    stale "allowed" (a user got a fourth model call this way).
    """
    reader = ScriptedReader(ReceiptUnreadable())
    deps = replace(
        make_deps(receipt_reader=reader, owner_user_id=OWNER_ID),
        photo_allowance=LaggyAllowance([0, 0, 0, 400]),
    )
    chat = make_chat(monkeypatch, deps=deps)
    await bounded(start_with_photo(chat))  # attempt 1
    await bounded(chat.press("Try again"))  # attempt 2
    message_id, data = chat.find_button("Try again")
    first = asyncio.create_task(chat.press_data(data, message_id))  # call 3: no lag
    second = asyncio.create_task(chat.press_data(data, message_id))  # call 4: very late
    await bounded(asyncio.gather(first, second))
    assert len(reader.calls) == 3
    assert chat.deps.photo_allowance._failures == {1: 3}
    assert chat.bot.texts().count(ONE_AT_A_TIME) == 1
