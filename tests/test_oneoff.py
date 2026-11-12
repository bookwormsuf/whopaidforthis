"""Tests for the one-off bill through its handlers, the render and the built application
(spec §22, sprint 17). A fake bot records every message and edit and, like Telegram,
refuses an edit that changes nothing.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from telegram import Chat, Message, MessageEntity, PhotoSize, Update, User
from telegram.error import BadRequest

from app.bot import app as bot_app
from app.bot.app import build_application
from app.bot.conversations import settrip
from app.bot.deps import Deps
from app.bot.handlers import oneoff as oneoff_handlers
from app.bot.handlers.basic import (
    HELP_TEXT,
    PRIVATE_START_TEXT,
    help_command,
    private_fallback_handler,
    start_command,
)
from app.bot.handlers.feedback import drop_feedback_wait, feedback_text_handler
from app.bot.handlers.oneoff import (
    oneoff_callback,
    oneoff_cancel_command,
    oneoff_photo_handler,
    oneoff_text_handler,
    split_command,
)
from app.bot.handlers.text_expense import text_expense_handler
from app.bot.keyboards import oneoff as keyboards
from app.bot.keyboards import split as split_keyboards
from app.bot.render import oneoff as render
from app.services.oneoff import (
    BillLine,
    EqualSplit,
    ItemsSplit,
    OneOffBill,
    OneOffStore,
    TypedSplit,
    set_bill_line,
    set_count,
)
from tests import receipt_fixtures
from tests.helpers import FakeReceiptReader, make_deps

OWNER_ID = 999000111
START = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)

# Step 1's prompt, as Sprint 18 pinned it (docs/sprints/18-one-off-receipt-photos.md).
START_PROMPT = "Send the bill, like <code>84.50 dinner</code>, or a photo of the receipt."

# The pinned example, character for character (docs/sprints/17-one-off-bill.md).
TITLE = "🧾 <b>Dinner</b> · S$84.50"
COUNT_CARD = (
    f"{TITLE}\n\nHow many people, including you?\n"
    "<i>Not SGD? Send it again with the currency, like 1200 yen ramen.</i>"
)
NAMES_CARD = (
    f"{TITLE}\n\nSufyan (you), Person 2, Person 3\n\n"
    "Send names to rename them, like <code>Ali, Mei</code>, or tap Next."
)
NAMES_CARD_RENAMED = NAMES_CARD.replace("Person 2, Person 3", "Ali, Mei")
PAYER_CARD = f"{TITLE}\n\nWho paid?"
METHOD_CARD = f"{TITLE}\n\nHow should it be split?"
PREVIEW_CARD = (
    f"{TITLE}\nSufyan (you) paid\n\nCheck before sending\n"
    "• Sufyan (you) S$28.16\n• Ali S$28.17\n• Mei S$28.17"
)
SUMMARY = (
    f"{TITLE}\nSufyan paid · split 3 ways\n\n"
    "• Ali owes Sufyan S$28.17\n• Mei owes Sufyan S$28.17\n\n"
    "Split with @whopaidbot"
)
AMOUNTS_CARD_ALI_20 = (
    f"{TITLE}\n\nSend one line per person, e.g. <code>Ali 20</code>\n"
    "• Sufyan (you) –\n• Ali S$20.00\n• Mei –\n"
    "Total S$20.00 of S$84.50\nRemaining: S$64.50 ❌"
)


# ---- harness ------------------------------------------------------------------


def _jpeg_bytes() -> bytes:
    image = Image.new("RGB", (100, 100), color=(10, 20, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def _keyboard(markup) -> list[list[tuple[str, str]]] | None:
    if markup is None:
        return None
    return [[(b.text, b.callback_data or b.url) for b in row] for row in markup.inline_keyboard]


class FakeBot:
    """Records sends and edits. Refuses an edit that changes nothing, as Telegram does."""

    username = "whopaidbot"
    id = 42

    def __init__(self) -> None:
        self.messages: dict[int, dict] = {}
        self.sent: list[int] = []  # message IDs in send order
        self._next = 100
        self.deleted: set[int] = set()  # edits to these raise, as for a deleted message
        self.send_message = AsyncMock(side_effect=self._send)
        self.get_file = AsyncMock(side_effect=self._get_file)
        self.downloads = 0
        self.yield_on_send = False  # a send that lets other tasks run, as a real one does

    async def _yield(self, *_args) -> None:
        """A real Telegram call lets other tasks run; with `yield_on_send` so does this."""
        if self.yield_on_send:
            await asyncio.sleep(0)

    async def _get_file(self, file_id):
        await self._yield()

        async def download():
            await self._yield()
            self.downloads += 1
            return bytearray(_jpeg_bytes())

        return SimpleNamespace(file_size=1000, download_as_bytearray=download)

    async def _send(self, chat_id, text, parse_mode=None, reply_markup=None, **_):
        assert parse_mode == "HTML"
        await self._yield()
        self._next += 1
        self.messages[self._next] = {
            "chat_id": chat_id,
            "text": text,
            "keyboard": _keyboard(reply_markup),
        }
        self.sent.append(self._next)
        return SimpleNamespace(message_id=self._next)

    async def edit_message_text(
        self, text, chat_id=None, message_id=None, parse_mode=None, reply_markup=None, **_
    ):
        assert parse_mode == "HTML"
        await self._yield()
        if message_id in self.deleted:
            raise BadRequest("Message to edit not found")
        message = self.messages[message_id]
        keyboard = _keyboard(reply_markup)
        if text == message["text"] and keyboard == message["keyboard"]:
            raise BadRequest("Message is not modified: specified new message content is the same")
        message["text"], message["keyboard"] = text, keyboard

    async def clear_keyboard(self, message_id: int) -> None:
        if self.messages[message_id]["keyboard"] is None:
            raise BadRequest("Message is not modified")
        self.messages[message_id]["keyboard"] = None

    def texts(self) -> list[str]:
        return [self.messages[mid]["text"] for mid in self.sent]


class Chatter:
    """One user in a private chat with the bot, driving the real handlers."""

    def __init__(
        self, monkeypatch, *, deps: Deps | None = None, user_id: int = 1, first_name="Sufyan"
    ) -> None:
        self.now = START
        monkeypatch.setattr(oneoff_handlers, "_now", lambda: self.now)
        self.deps = deps or make_deps(owner_user_id=OWNER_ID)
        self.bot = FakeBot()
        self.context = SimpleNamespace(
            bot=self.bot, application=SimpleNamespace(bot_data={"deps": self.deps}), args=[]
        )
        self.user = SimpleNamespace(id=user_id, first_name=first_name, username=None)
        self.chat = SimpleNamespace(id=user_id, type="private")

    @property
    def store(self):
        return self.deps.oneoff_bills

    @property
    def bill(self) -> OneOffBill | None:
        return self.store.for_user(self.user.id, self.now)

    def card(self) -> str:
        assert self.bill is not None and self.bill.message_id is not None
        return self.bot.messages[self.bill.message_id]["text"]

    def keyboard(self) -> list[list[tuple[str, str]]] | None:
        assert self.bill is not None and self.bill.message_id is not None
        return self.bot.messages[self.bill.message_id]["keyboard"]

    def labels(self) -> list[str]:
        return [label for row in self.keyboard() or [] for label, _ in row]

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)

    async def command(self, name: str = "split") -> None:
        update = SimpleNamespace(effective_chat=self.chat, effective_user=self.user)
        handler = {"split": split_command, "cancel": oneoff_cancel_command}[name]
        await handler(update, self.context)

    async def say(self, text: str) -> bool:
        """Types `text`; like the application, only a waiting bill hears it."""
        if not self.store.wants_text(self.user.id, self.now):
            return False
        message = SimpleNamespace(text=text)
        update = SimpleNamespace(
            effective_chat=self.chat, effective_user=self.user, effective_message=message
        )
        await oneoff_text_handler(update, self.context)
        return True

    async def photo(
        self, *, file_id: str = "photo-1", file_size: int | None = 1000, caption: str | None = None
    ) -> bool:
        """Sends a compressed photo; like the application, only a bill at the bill-line
        step (or reading) hears it.
        """
        if not self.store.wants_photo(self.user.id, self.now):
            return False
        message = SimpleNamespace(
            photo=[SimpleNamespace(file_id=file_id, file_size=file_size)], caption=caption
        )
        update = SimpleNamespace(
            effective_chat=self.chat, effective_user=self.user, effective_message=message
        )
        await oneoff_photo_handler(update, self.context)
        return True

    async def press_data(self, data: str, message_id: int, from_user=None):
        async def clear(reply_markup=None):
            await self.bot.clear_keyboard(message_id)

        query = SimpleNamespace(
            data=data,
            from_user=from_user or self.user,
            message=SimpleNamespace(message_id=message_id, chat_id=self.chat.id),
            answer=AsyncMock(side_effect=self.bot._yield),
            edit_message_reply_markup=AsyncMock(side_effect=clear),
        )
        update = SimpleNamespace(
            callback_query=query, effective_chat=self.chat, effective_user=from_user or self.user
        )
        await oneoff_callback(update, self.context)
        return query

    def find_button(self, label: str) -> tuple[int, str]:
        """The newest message that has a button called `label`: (message ID, data)."""
        for message_id in reversed(list(self.bot.messages)):
            for row in self.bot.messages[message_id]["keyboard"] or []:
                for text, data in row:
                    if text == label:
                        return message_id, data
        raise AssertionError(f"no button {label!r}")

    async def press(self, label: str):
        message_id, data = self.find_button(label)
        return await self.press_data(data, message_id)

    async def to_names(self, line: str = "84.50 dinner", count: int = 3) -> None:
        await self.command()
        await self.say(line)
        await self.press(str(count))

    async def to_payer(self, names: str | None = None, **kwargs) -> None:
        await self.to_names(**kwargs)
        if names:
            await self.say(names)
        await self.press("Next")

    async def to_method(self, payer: str = "Me", names: str | None = "Ali, Mei", **kwargs) -> None:
        await self.to_payer(names, **kwargs)
        await self.press(payer)

    async def to_amounts(self, **kwargs) -> None:
        await self.to_method(**kwargs)
        await self.press("Type amounts instead")

    async def to_preview(self, **kwargs) -> None:
        await self.to_method(**kwargs)
        await self.press("Split equally")


@pytest.fixture
def chat(monkeypatch) -> Chatter:
    return Chatter(monkeypatch)


# ---- the whole path -----------------------------------------------------------


async def test_whole_path_equal_split_renders_every_pinned_text(chat: Chatter) -> None:
    await chat.command()
    assert chat.bot.texts() == [START_PROMPT]
    assert chat.bot.messages[chat.bot.sent[0]]["keyboard"] is None

    await chat.say("84.50 dinner")
    assert chat.card() == COUNT_CARD
    assert chat.keyboard() == [
        [(str(n), f"oneoff:{chat.bill.bill_id}:count:{n}") for n in (2, 3, 4, 5, 6)],
        [("Cancel", f"oneoff:{chat.bill.bill_id}:cancel")],
    ]
    card_id = chat.bill.message_id

    await chat.press("3")
    assert chat.card() == NAMES_CARD
    assert chat.labels() == ["Next", "Cancel"]

    await chat.say("Ali, Mei")
    assert chat.card() == NAMES_CARD_RENAMED

    await chat.press("Next")
    assert chat.card() == PAYER_CARD
    assert chat.labels() == ["Me", "Ali", "Mei", "Cancel"]
    assert [len(row) for row in chat.keyboard()] == [3, 1]

    await chat.press("Me")
    assert chat.card() == METHOD_CARD
    assert chat.labels() == ["Split equally", "Type amounts instead", "Cancel"]

    await chat.press("Split equally")
    assert chat.card() == PREVIEW_CARD
    assert chat.labels() == ["Done", "Edit", "Cancel"]
    assert [len(row) for row in chat.keyboard()] == [2, 1]
    assert chat.bill.shares == {1: 2816, 2: 2817, 3: 2817}

    await chat.press("Done")
    # One card, edited in place, becomes the summary with no buttons.
    assert chat.bot.messages[card_id]["text"] == SUMMARY
    assert chat.bot.messages[card_id]["keyboard"] is None
    assert chat.bot.texts()[-1] == (
        "Forward the message above to the people on the bill. I haven't saved it."
    )
    assert chat.bill is None
    # Prompt, the card, the hint: three messages in all.
    assert len(chat.bot.sent) == 3


async def test_whole_path_typed_amounts_with_another_person_paying(chat: Chatter) -> None:
    await chat.to_names("1200 yen ramen", count=2)
    assert "Not SGD?" not in chat.card()
    await chat.press("Next")
    await chat.press("Person 2")
    await chat.press("Type amounts instead")
    await chat.say("me 500\nPerson 2 700")
    assert chat.labels() == ["Confirm", "Cancel"]
    await chat.press("Confirm")
    assert chat.card() == (
        "🧾 <b>Ramen</b> · ¥1,200\nPerson 2 paid\n\nCheck before sending\n"
        "• Sufyan (you) ¥500\n• Person 2 ¥700"
    )
    card_id = chat.bill.message_id
    await chat.press("Done")
    assert chat.bot.messages[card_id]["text"] == (
        "🧾 <b>Ramen</b> · ¥1,200\nPerson 2 paid · split by amounts\n\n"
        "• Sufyan owes Person 2 ¥500\n\nSplit with @whopaidbot"
    )


async def test_the_amounts_card_matches_the_pin(chat: Chatter) -> None:
    await chat.to_amounts()
    assert chat.card() == (
        f"{TITLE}\n\nSend one line per person, e.g. <code>Ali 20</code>\n"
        "• Sufyan (you) –\n• Ali –\n• Mei –\n"
        "Total S$0.00 of S$84.50\nRemaining: S$84.50 ❌"
    )
    await chat.say("Ali 20")
    assert chat.card() == AMOUNTS_CARD_ALI_20
    assert chat.labels() == ["Cancel"]


async def test_payer_carries_everything(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("me 84.50")
    assert "Remaining: S$0.00 ✅" in chat.card()
    await chat.press("Confirm")
    await chat.press("Done")
    assert chat.bot.messages[chat.bot.sent[1]]["text"] == (
        f"{TITLE}\nSufyan paid · split by amounts\n\n"
        "Nobody owes anything.\n\nSplit with @whopaidbot"
    )


async def test_an_equal_split_among_two_where_the_other_pays(chat: Chatter) -> None:
    await chat.to_payer(count=2)
    await chat.press("Person 2")
    await chat.press("Split equally")
    await chat.press("Done")
    summary = chat.bot.messages[chat.bot.sent[1]]["text"]
    assert "Person 2 paid · split 2 ways" in summary
    assert "• Sufyan owes Person 2 S$42.25" in summary


async def test_a_person_with_a_zero_share_is_not_listed_as_owing(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("me 40\nAli 44.50")  # Mei has no amount: zero
    await chat.press("Confirm")
    await chat.press("Done")
    summary = chat.bot.messages[chat.bot.sent[1]]["text"]
    assert "• Ali owes Sufyan S$44.50" in summary
    assert "Mei" not in summary
    assert summary.count("•") == 1


# ---- the bill line ------------------------------------------------------------


@pytest.mark.parametrize("text", ["dinner", "0 dinner", "hello", "0.00"])
async def test_a_line_without_an_amount_keeps_waiting(chat: Chatter, text: str) -> None:
    await chat.command()
    await chat.say(text)
    assert chat.bot.texts()[-1] == (
        "I couldn't find an amount. Send it like <code>84.50 dinner</code>."
    )
    assert chat.bill.state == "awaiting_bill"
    await chat.say("84.50 dinner")
    assert chat.bill.state == "count"


async def test_a_yen_bill_stays_in_yen_with_no_hint_line(chat: Chatter) -> None:
    await chat.command()
    await chat.say("1200 yen ramen")
    assert chat.card() == "🧾 <b>Ramen</b> · ¥1,200\n\nHow many people, including you?"


async def test_sgd_by_default_shows_the_hint_line(chat: Chatter) -> None:
    await chat.command()
    await chat.say("84.50 dinner")
    assert chat.card().endswith(
        "<i>Not SGD? Send it again with the currency, like 1200 yen ramen.</i>"
    )


async def test_paid_by_and_split_clauses_are_ignored(chat: Chatter) -> None:
    await chat.command()
    await chat.say("84.50 dinner paid by Ali split everyone")
    assert chat.card() == COUNT_CARD
    await chat.press("3")
    assert chat.bill.names == ("Sufyan", "Person 2", "Person 3")
    assert chat.bill.payer is None


async def test_an_empty_description_shows_bill(chat: Chatter) -> None:
    await chat.command()
    await chat.say("84.50")
    assert chat.card().startswith("🧾 <b>Bill</b> · S$84.50")


async def test_the_description_is_escaped(chat: Chatter) -> None:
    await chat.command()
    await chat.say("84.50 <b>x</b>")
    assert chat.card().splitlines()[0] == "🧾 <b>&lt;B&gt;X&lt;/B&gt;</b> · S$84.50"


async def test_the_sprint_17_photo_reply_is_retired(chat: Chatter) -> None:
    """A photo at the bill-line step is read now (sprint 18): the old "Photos aren't read
    here yet" reply is gone. (The read itself is tested in tests/test_oneoff_receipt.py.)
    """
    await chat.command()
    assert await chat.photo()
    assert not any("aren't read here yet" in text for text in chat.bot.texts())


# ---- how many -----------------------------------------------------------------


@pytest.mark.parametrize("n", [2, 3, 4, 5, 6])
async def test_how_many_buttons(chat: Chatter, n: int) -> None:
    await chat.to_names(count=n)
    assert chat.bill.count == n and chat.bill.state == "names"


async def test_how_many_typed_7(chat: Chatter) -> None:
    await chat.command()
    await chat.say("84.50 dinner")
    await chat.say("7")
    assert chat.bill.count == 7
    assert chat.card().count("Person") == 6


@pytest.mark.parametrize("text", ["1", "21", "abc", "0", "2.5"])
async def test_how_many_typed_out_of_range_or_not_a_number(chat: Chatter, text: str) -> None:
    await chat.command()
    await chat.say("84.50 dinner")
    await chat.say(text)
    assert chat.bot.texts()[-1] == "Type a number from 2 to 20."
    assert chat.bill.state == "count"


async def test_a_new_bill_line_replaces_the_bill(chat: Chatter) -> None:
    await chat.command()
    await chat.say("84.50 dinner")
    old_id, old_card = chat.bill.bill_id, chat.bill.message_id
    old_data = chat.find_button("3")[1]
    await chat.say("1200 yen ramen")
    assert chat.bot.messages[old_card]["text"] == "Replaced by the bill below."
    assert chat.bot.messages[old_card]["keyboard"] is None
    assert chat.bill.bill_id != old_id and chat.bill.message_id != old_card
    assert chat.card() == "🧾 <b>Ramen</b> · ¥1,200\n\nHow many people, including you?"
    # The old card's button is dead.
    await chat.press_data(old_data, old_card)
    assert chat.bot.texts()[-1] == render.STALE_TEXT


# ---- names --------------------------------------------------------------------


async def test_fewer_names_than_people_keep_defaults(chat: Chatter) -> None:
    await chat.to_names()
    await chat.say("Ali")
    assert chat.card() == NAMES_CARD.replace("Person 2", "Ali")


async def test_names_on_separate_lines(chat: Chatter) -> None:
    await chat.to_names()
    await chat.say("Ali\nMei")
    assert chat.card() == NAMES_CARD_RENAMED


@pytest.mark.parametrize(
    ("count", "typed", "reply"),
    [
        (3, "A, B, C", "That's more names than people. There are 2 others on this bill."),
        (2, "A, B", "That's more names than people. There is 1 other on this bill."),
        (3, "x" * 31, "Keep each name under 30 characters."),
        (3, "Ali, ali", "Two people can't share a name. Change one."),
        (3, "SUFYAN", "Two people can't share a name. Change one."),
    ],
)
async def test_name_errors_rename_nothing(
    chat: Chatter, count: int, typed: str, reply: str
) -> None:
    await chat.to_names(count=count)
    before = chat.card()
    await chat.say(typed)
    assert chat.bot.texts()[-1] == reply
    assert chat.card() == before
    assert chat.bill.state == "names"


async def test_html_in_a_name_is_escaped_everywhere(chat: Chatter) -> None:
    await chat.to_names()
    await chat.say("<b>Ali</b> & co, Mei")
    assert "&lt;b&gt;Ali&lt;/b&gt; &amp; co" in chat.card()
    assert "<b>Ali" not in chat.card()
    await chat.press("Next")
    await chat.press("Me")
    await chat.press("Split equally")
    assert "&lt;b&gt;Ali&lt;/b&gt; &amp; co S$28.17" in chat.card()
    await chat.press("Done")
    summary = chat.bot.messages[chat.bot.sent[1]]["text"]
    assert "&lt;b&gt;Ali&lt;/b&gt; &amp; co owes Sufyan" in summary


async def test_the_users_first_name_is_escaped_too(monkeypatch) -> None:
    chatter = Chatter(monkeypatch, first_name="A<i>da")
    await chatter.to_names()
    assert "A&lt;i&gt;da (you)" in chatter.card()


async def test_names_that_change_nothing_do_not_crash(chat: Chatter) -> None:
    await chat.to_names()
    await chat.say("Ali, Mei")
    before = chat.card()
    await chat.say("Ali, Mei")  # the edit would be "not modified"
    await chat.say("Ali")
    assert chat.card() == before
    assert chat.bill.state == "names"


async def test_typed_amounts_that_change_nothing_do_not_crash(chat: Chatter) -> None:
    await chat.to_names()
    await chat.say("Ali, Mei")
    await chat.press("Next")
    await chat.press("Me")
    await chat.press("Type amounts instead")
    await chat.say("Ali 20")
    await chat.say("Ali 20")
    assert chat.card() == AMOUNTS_CARD_ALI_20


# ---- typed amounts ------------------------------------------------------------


async def test_several_lines_in_one_message(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("Ali 20\nMei 30\nme 34.50")
    assert "Remaining: S$0.00 ✅" in chat.card()
    assert chat.labels() == ["Confirm", "Cancel"]


async def test_lines_across_several_messages_and_replacing(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("Ali 20")
    await chat.say("Mei 30")
    await chat.say("Ali 25")
    assert "• Ali S$25.00" in chat.card() and "Total S$55.00 of S$84.50" in chat.card()


async def test_me_and_a_default_name_with_a_digit(chat: Chatter) -> None:
    await chat.to_amounts(names=None)
    await chat.say("me 20\nPerson 2 20")
    assert "• Sufyan (you) S$20.00\n• Person 2 S$20.00\n• Person 3 –" in chat.card()
    assert "e.g. <code>Person 2 20</code>" in chat.card()


async def test_an_unknown_name_is_one_warning_and_other_lines_apply(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("Zed 20\nAli 20")
    assert chat.card() == AMOUNTS_CARD_ALI_20 + "\n⚠️ Who is 'Zed'?"


async def test_an_unreadable_line(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("Ali twenty")
    assert chat.card().endswith("\n⚠️ Couldn't read 'Ali twenty'")


async def test_the_wrong_currency(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("Ali 5usd")
    assert chat.card().endswith("\n⚠️ Use S$ amounts for this bill")


async def test_errors_are_joined_on_one_line_and_cleared_by_a_clean_message(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("Zed 20\nAli 5usd\nMei")
    assert chat.card().splitlines()[-1] == (
        "⚠️ Who is 'Zed'?; Use S$ amounts for this bill; Couldn't read 'Mei'"
    )
    await chat.say("Ali 20")
    assert "⚠️" not in chat.card()


async def test_error_text_from_the_user_is_escaped(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("<b>Zed</b> 20")
    assert chat.card().endswith("⚠️ Who is '&lt;b&gt;Zed&lt;/b&gt;'?")


async def test_over_the_total_shows_a_minus_and_no_confirm(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("me 100")
    assert chat.card().endswith("Total S$100.00 of S$84.50\nRemaining: −S$15.50 ❌")
    assert chat.labels() == ["Cancel"]


async def test_confirm_appears_only_when_the_amounts_add_up(chat: Chatter) -> None:
    await chat.to_amounts()
    assert "Confirm" not in chat.labels()
    await chat.say("me 20\nAli 20")
    assert "Confirm" not in chat.labels()
    await chat.say("Mei 44.50")
    assert chat.labels() == ["Confirm", "Cancel"]
    await chat.say("Mei 44")  # no longer adds up
    assert chat.labels() == ["Cancel"]


async def test_a_stale_confirm_press_is_ignored(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("me 20\nAli 20\nMei 44.50")
    confirm = chat.find_button("Confirm")
    await chat.say("Mei 1")
    await chat.press_data(confirm[1], confirm[0])
    assert chat.bill.state == "amounts"


# ---- edit, cancel -------------------------------------------------------------


async def test_edit_returns_to_names_with_names_kept(chat: Chatter) -> None:
    await chat.to_preview()
    await chat.press("Edit")
    assert chat.card() == NAMES_CARD_RENAMED
    assert chat.labels() == ["Next", "Cancel"]
    assert chat.bill.payer is None and chat.bill.method is None
    # Payer and split are chosen again.
    await chat.press("Next")
    await chat.press("Ali")
    await chat.press("Type amounts instead")
    await chat.say("me 40\nAli 44.50")
    await chat.press("Confirm")
    assert "Ali paid" in chat.card()
    assert "Sufyan (you) S$40.00" in chat.card()


@pytest.mark.parametrize("step", ["count", "names", "payer", "method", "amounts", "preview"])
async def test_cancel_at_each_step(chat: Chatter, step: str) -> None:
    if step == "count":
        await chat.command()
        await chat.say("84.50 dinner")
    elif step == "names":
        await chat.to_names()
    elif step == "payer":
        await chat.to_payer()
    elif step == "method":
        await chat.to_method()
    elif step == "amounts":
        await chat.to_amounts()
    else:
        await chat.to_preview()
    card_id = chat.bill.message_id
    await chat.press("Cancel")
    assert chat.bot.messages[card_id]["text"] == "Cancelled."
    assert chat.bot.messages[card_id]["keyboard"] is None
    assert chat.bill is None


async def test_slash_cancel_cancels_the_bill_and_sends_nothing_else(chat: Chatter) -> None:
    await chat.to_preview()
    card_id, sent_before = chat.bill.message_id, len(chat.bot.sent)
    await chat.command("cancel")
    assert chat.bot.messages[card_id]["text"] == "Cancelled."
    assert chat.bot.messages[card_id]["keyboard"] is None
    assert len(chat.bot.sent) == sent_before
    assert chat.bill is None


async def test_slash_cancel_at_the_bill_line_step_cancels_the_prompt(chat: Chatter) -> None:
    await chat.command()
    prompt_id = chat.bot.sent[0]
    await chat.command("cancel")
    assert chat.bot.messages[prompt_id]["text"] == "Cancelled."
    assert chat.bill is None


# ---- a second Split a bill now ------------------------------------------------


async def test_a_second_start_asks_to_drop_the_bill_yes(chat: Chatter) -> None:
    await chat.to_names()
    old = chat.bill
    await chat.press_data("oneoff:start", 1)
    question = chat.bot.sent[-1]
    assert chat.bot.messages[question]["text"] == "Drop the bill you started?"
    assert chat.bot.messages[question]["keyboard"] == [
        [
            ("Yes, start again", f"oneoff:{old.bill_id}:drop:yes"),
            ("No, keep it", f"oneoff:{old.bill_id}:drop:no"),
        ]
    ]
    await chat.press("Yes, start again")
    assert chat.bot.messages[old.message_id]["text"] == "Cancelled."
    assert chat.bot.messages[old.message_id]["keyboard"] is None
    assert chat.bot.messages[question]["text"] == START_PROMPT
    assert chat.bot.messages[question]["keyboard"] is None
    assert chat.bill.bill_id != old.bill_id and chat.bill.state == "awaiting_bill"
    await chat.say("10 taxi")
    assert chat.bill.state == "count"


async def test_a_second_start_asks_to_drop_the_bill_no(chat: Chatter) -> None:
    await chat.to_names()
    old = chat.bill
    card_before = chat.card()
    await chat.command()
    question = chat.bot.sent[-1]
    await chat.press("No, keep it")
    assert chat.bot.messages[question]["text"] == "OK. Carry on with the bill above."
    assert chat.bot.messages[question]["keyboard"] is None
    assert chat.bill == old and chat.card() == card_before


async def test_a_second_start_while_waiting_for_the_bill_line_also_asks(chat: Chatter) -> None:
    await chat.command()
    await chat.command()
    assert chat.bot.texts()[-1] == "Drop the bill you started?"


# ---- expiry and stale buttons -------------------------------------------------


async def test_the_clock_is_sliding_through_the_handlers(chat: Chatter) -> None:
    await chat.to_names()
    chat.advance(minutes=29)
    await chat.say("Ali")  # accepted: restarts the 30 minutes
    chat.advance(minutes=29)
    assert chat.bill is not None  # 58 minutes in, 29 since the last accepted action
    await chat.press("Next")
    chat.advance(minutes=31)
    assert chat.bill is None


async def test_a_refused_message_does_not_restart_the_clock(chat: Chatter) -> None:
    await chat.to_names()
    chat.advance(minutes=20)
    await chat.say("x" * 31)  # refused
    chat.advance(minutes=11)
    assert chat.bill is None


async def test_a_stale_button_gets_the_message_and_loses_its_buttons(chat: Chatter) -> None:
    await chat.to_names()
    card_id, data = chat.bill.message_id, chat.find_button("Next")[1]
    chat.advance(minutes=31)
    query = await chat.press_data(data, card_id)
    query.answer.assert_awaited_once()
    assert chat.bot.texts()[-1] == "That bill expired. Tap Split a bill now to start again."
    assert chat.bot.messages[chat.bot.sent[-1]]["keyboard"] == [
        [("Split a bill now", "oneoff:start")]
    ]
    assert chat.bot.messages[card_id]["keyboard"] is None


async def test_a_text_after_expiry_gets_the_message_once(chat: Chatter) -> None:
    await chat.to_names()
    chat.advance(minutes=31)
    sent = len(chat.bot.sent)
    assert await chat.say("Ali") is True
    assert chat.bot.texts()[-1] == render.STALE_TEXT
    assert len(chat.bot.sent) == sent + 1
    assert await chat.say("Ali again") is False  # nobody is listening any more
    assert len(chat.bot.sent) == sent + 1


async def test_a_bill_swept_by_someone_elses_start_still_owes_its_owner_one_notice(
    monkeypatch,
) -> None:
    ada = Chatter(monkeypatch, user_id=1)
    bob = Chatter(monkeypatch, user_id=2, first_name="Bob", deps=ada.deps)
    ada.now = bob.now = START
    await ada.to_names()
    bob.now = ada.now = START + timedelta(minutes=31)
    await bob.command()  # sweeps Ada's expired bill
    assert ada.store.for_user(1, ada.now) is None
    assert await ada.say("hello") is True
    assert ada.bot.texts()[-1] == render.STALE_TEXT


async def test_a_press_on_a_finished_bills_button_is_stale(chat: Chatter) -> None:
    await chat.to_preview()
    done = chat.find_button("Done")
    await chat.press("Done")
    await chat.press_data(done[1], done[0])
    assert chat.bot.texts()[-1] == render.STALE_TEXT


async def test_a_press_on_a_cancelled_bills_button_is_stale(chat: Chatter) -> None:
    await chat.to_names()
    nxt = chat.find_button("Next")
    await chat.press("Cancel")
    await chat.press_data(nxt[1], nxt[0])
    assert chat.bot.texts()[-1] == render.STALE_TEXT


async def test_a_button_that_does_not_fit_the_step_is_ignored(chat: Chatter) -> None:
    await chat.to_names()
    bill_id = chat.bill.bill_id
    sent = len(chat.bot.sent)
    for action in ("payer:1", "equal", "custom", "confirm", "done", "edit", "count:99", "bogus"):
        await chat.press_data(f"oneoff:{bill_id}:{action}", chat.bill.message_id)
    assert chat.bill.state == "names" and len(chat.bot.sent) == sent


async def test_only_the_owner_can_press_a_bills_buttons(monkeypatch) -> None:
    ada = Chatter(monkeypatch, user_id=1)
    await ada.to_names()
    stranger = SimpleNamespace(id=2, first_name="Bob", username=None)
    message_id, data = ada.find_button("Next")
    await ada.press_data(data, message_id, from_user=stranger)
    assert ada.bill.state == "names"


# ---- routing through the built application ------------------------------------


def _application(deps):
    return build_application("123456:fake-token", deps)


def _matching(application, update: Update) -> list:
    found = []
    for group in sorted(application.handlers):
        for handler in application.handlers[group]:
            if handler.check_update(update):
                found.append(handler.callback)
                break
    return found


def _bot_stub():
    return SimpleNamespace(username="whopaidbot")


def _text_update(
    text: str, *, chat_type="private", user_id=1, command=False, edited=False
) -> Update:
    chat = Chat(id=user_id if chat_type == "private" else -100, type=chat_type)
    user = User(id=user_id, first_name="Ada", is_bot=False)
    entities = None
    if command:
        entities = (MessageEntity(MessageEntity.BOT_COMMAND, 0, len(text.split()[0])),)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text=text,
        entities=entities,
    )
    message.set_bot(_bot_stub())  # type: ignore[arg-type]
    if edited:
        return Update(update_id=1, edited_message=message)
    return Update(update_id=1, message=message)


def _photo_update(user_id: int = 1) -> Update:
    message = Message(
        message_id=2,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, first_name="Ada", is_bot=False),
        photo=(PhotoSize("f", "u", 100, 100),),
    )
    return Update(update_id=2, message=message)


def _open_bill(deps, user_id: int = 1, *, at_count: bool = False) -> None:
    now = datetime.now(UTC)
    bill = deps.oneoff_bills.create(user_id=user_id, chat_id=user_id, first_name="Ada", now=now)
    if at_count:
        deps.oneoff_bills.put(set_bill_line(bill, BillLine("Dinner", 8450, "SGD")), now)


def test_private_text_and_photos_with_no_bill_get_the_start_intro() -> None:
    deps = make_deps(owner_user_id=OWNER_ID)
    application = _application(deps)
    for update in (_text_update("hello there"), _text_update("84.50 dinner"), _photo_update()):
        callbacks = _matching(application, update)
        assert oneoff_text_handler not in callbacks and oneoff_photo_handler not in callbacks
        assert [c.__name__ for c in callbacks] == ["upsert_seen_user", "private_fallback_handler"]


def test_the_start_intro_fallback_stays_out_of_the_way() -> None:
    deps = make_deps(owner_user_id=OWNER_ID)
    application = _application(deps)

    def falls_back(update: Update) -> bool:
        return private_fallback_handler in _matching(application, update)

    assert not falls_back(_text_update("hi", chat_type="supergroup"))
    assert not falls_back(_text_update("hi", edited=True))
    deps.feedback_waits.start(1, datetime.now(UTC))
    assert feedback_text_handler in _matching(application, _text_update("hi"))
    assert not falls_back(_text_update("hi"))
    deps.feedback_waits.clear(1)
    _open_bill(deps, at_count=True)
    assert not falls_back(_photo_update())  # a bill is open: unexpected messages stay quiet
    assert not falls_back(_text_update("/nosuchcommand", command=True))
    assert falls_back(_text_update("hi", user_id=2))


def test_the_fallback_is_the_last_handler_of_its_group() -> None:
    # It matches nearly every private message, so anything registered after it would
    # never fire in a private chat.
    application = _application(make_deps())
    assert application.handlers[0][-1].callback is private_fallback_handler


def test_an_unknown_private_command_gets_the_intro_and_known_ones_keep_their_handler() -> None:
    application = _application(make_deps(owner_user_id=OWNER_ID))
    unknown = _matching(application, _text_update("/strat", command=True))
    assert private_fallback_handler in unknown
    for name, _callback, _filters in bot_app._COMMANDS:
        callbacks = _matching(application, _text_update(f"/{name}", command=True))
        assert private_fallback_handler not in callbacks, name
    assert private_fallback_handler not in _matching(
        application, _text_update("/strat", command=True, chat_type="supergroup")
    )


def _fallback_context(deps):
    return SimpleNamespace(
        bot=_bot_stub(), application=SimpleNamespace(bot_data={"deps": deps}), args=[]
    )


async def test_the_fallback_sends_the_start_text_and_both_buttons() -> None:
    deps = make_deps()
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1))
    await private_fallback_handler(update, _fallback_context(deps))
    text = message.reply_text.await_args.args[0]
    assert text.startswith(PRIVATE_START_TEXT)
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert labels == ["Add me to a group for a trip", "Split a bill now"]


async def test_a_photo_after_a_bill_expired_gets_the_intro_and_no_late_expiry_notice() -> None:
    deps = make_deps()
    application = _application(deps)
    start = datetime.now(UTC) - timedelta(hours=2)
    deps.oneoff_bills.create(user_id=1, chat_id=1, first_name="Ada", now=start)
    # The photo is not read (the bill is gone) and falls through to the intro.
    callbacks = _matching(application, _photo_update())
    assert oneoff_photo_handler not in callbacks and private_fallback_handler in callbacks
    assert deps.oneoff_bills.has_expired_notice(1)
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1))
    await private_fallback_handler(update, _fallback_context(deps))
    # The intro stood in for the notice: the next text is not told about an old bill.
    assert not deps.oneoff_bills.has_expired_notice(1)
    assert oneoff_text_handler not in _matching(application, _text_update("hi"))


def test_a_bill_hears_private_text_and_a_photo_only_at_the_bill_line_step() -> None:
    deps = make_deps()
    application = _application(deps)
    _open_bill(deps)
    assert oneoff_text_handler in _matching(application, _text_update("84.50 dinner"))
    assert oneoff_photo_handler in _matching(application, _photo_update())
    _open_bill(deps, at_count=True)  # now at the how-many step
    assert oneoff_text_handler in _matching(application, _text_update("3"))
    assert oneoff_photo_handler not in _matching(application, _photo_update())


def test_another_users_text_and_group_text_are_not_heard() -> None:
    deps = make_deps()
    application = _application(deps)
    _open_bill(deps, user_id=1)
    assert oneoff_text_handler not in _matching(application, _text_update("hi", user_id=2))
    group = _text_update("1200 yen ramen", chat_type="supergroup")
    callbacks = _matching(application, group)
    assert oneoff_text_handler not in callbacks
    assert text_expense_handler in callbacks  # group text expenses still work


def test_an_edited_message_is_never_taken_as_input() -> None:
    deps = make_deps()
    application = _application(deps)
    _open_bill(deps)
    callbacks = _matching(application, _text_update("84.50 dinner", edited=True))
    assert oneoff_text_handler not in callbacks


def test_an_expired_bill_text_still_routes_for_the_one_notice() -> None:
    deps = make_deps()
    application = _application(deps)
    deps.oneoff_bills.create(
        user_id=1, chat_id=1, first_name="Ada", now=datetime.now(UTC) - timedelta(minutes=31)
    )
    assert oneoff_text_handler in _matching(application, _text_update("hello"))


async def test_feedback_wait_and_bill_together_the_text_is_feedback_and_the_bill_survives(
    monkeypatch,
) -> None:
    deps = make_deps(owner_user_id=OWNER_ID)
    application = _application(deps)
    _open_bill(deps)
    deps.feedback_waits.start(1)

    callbacks = _matching(application, _text_update("the bot is great"))

    # Registration order decides: the feedback handler is first in group 0.
    assert feedback_text_handler in callbacks and oneoff_text_handler not in callbacks

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_user=SimpleNamespace(id=1, first_name="Ada", last_name=None, username="ada"),
        effective_message=SimpleNamespace(text="the bot is great", reply_text=AsyncMock()),
    )
    context = SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock()),
        application=SimpleNamespace(bot_data={"deps": deps}),
    )
    await feedback_text_handler(update, context)
    context.bot.send_message.assert_awaited_once()
    assert context.bot.send_message.await_args.args[0] == OWNER_ID
    assert deps.oneoff_bills.for_user(1, datetime.now(UTC)) is not None
    assert not deps.feedback_waits.is_waiting(1)
    # With the wait gone the next text belongs to the bill.
    assert oneoff_text_handler in _matching(application, _text_update("next"))


async def test_split_drops_a_feedback_wait_like_any_command(monkeypatch) -> None:
    deps = make_deps(owner_user_id=OWNER_ID)
    application = _application(deps)
    deps.feedback_waits.start(1)

    callbacks = _matching(application, _text_update("/split", command=True))

    assert callbacks.index(drop_feedback_wait) < callbacks.index(split_command)
    chatter = Chatter(monkeypatch, deps=deps)
    await drop_feedback_wait(SimpleNamespace(effective_user=chatter.user), chatter.context)
    await chatter.command()
    assert not deps.feedback_waits.is_waiting(1)
    assert chatter.bill is not None and chatter.bill.state == "awaiting_bill"


def test_slash_cancel_routes_to_the_bill_only_while_one_is_open() -> None:
    deps = make_deps()
    application = _application(deps)
    update = _text_update("/cancel", command=True)
    assert oneoff_cancel_command not in _matching(application, update)
    assert settrip.cancel_command in _matching(application, update)
    _open_bill(deps)
    callbacks = _matching(application, update)
    assert oneoff_cancel_command in callbacks
    assert settrip.cancel_command not in callbacks  # one /cancel handler, nothing else sent


def test_slash_cancel_in_a_group_is_untouched_by_a_bill() -> None:
    deps = make_deps()
    application = _application(deps)
    _open_bill(deps)
    callbacks = _matching(
        application, _text_update("/cancel", chat_type="supergroup", command=True)
    )
    assert oneoff_cancel_command not in callbacks and settrip.cancel_command in callbacks


async def test_a_command_does_not_end_a_bill(monkeypatch) -> None:
    deps = make_deps()
    application = _application(deps)
    chatter = Chatter(monkeypatch, deps=deps)
    await chatter.to_names()
    for text in ("/help", "/start", "/join", "/recent"):
        callbacks = _matching(application, _text_update(text, command=True))
        assert oneoff_cancel_command not in callbacks and oneoff_text_handler not in callbacks
    # /help itself runs and leaves the bill open.
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=1, type="private")
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.application.bot_data = {"deps": deps}
    await help_command(update, context)
    assert chatter.bill is not None and chatter.bill.state == "names"


def test_one_callback_handler_covers_every_oneoff_button() -> None:
    application = _application(make_deps())
    handlers = [
        h for h in application.handlers[0] if getattr(h, "callback", None) is oneoff_callback
    ]
    # `Try again` has its own non-blocking handler (sprint 18); every other button goes
    # to the general one, which blocks as before.
    retry, general = handlers
    assert retry.block is False and bool(general.block)
    assert retry.pattern.match("oneoff:abc123:retry") and not retry.pattern.match(
        "oneoff:abc123:drop:yes"
    )
    pattern = general.pattern
    assert pattern.match("oneoff:start") and pattern.match("oneoff:abc123:drop:yes")
    assert not pattern.match("menu:join") and not pattern.match("st:abc")


# ---- /split in a group --------------------------------------------------------


async def test_split_in_a_group_gives_the_hint_and_does_nothing_else(monkeypatch) -> None:
    chatter = Chatter(monkeypatch)
    chatter.chat = SimpleNamespace(id=-100, type="supergroup")
    await chatter.command()
    assert chatter.bot.texts() == [
        "/split works in a private chat with me. In this group, type the expense, "
        "like <code>1200 yen ramen</code>."
    ]
    assert chatter.bill is None


def test_split_is_a_known_command_so_the_unbound_gate_stays_out() -> None:
    assert "split" in bot_app._KNOWN_COMMANDS


# ---- /start and /help ---------------------------------------------------------


def _private_update():
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=1, type="private")
    update.message.reply_text = AsyncMock()
    return update


def _command_context(deps):
    context = MagicMock()
    context.application.bot_data = {"deps": deps}
    context.bot.username = "whopaidbot"
    context.args = []
    return context


async def test_private_start_has_the_pinned_text_and_two_buttons() -> None:
    update = _private_update()
    await start_command(update, _command_context(make_deps(bot_version="abc1234")))
    args, kwargs = update.message.reply_text.await_args
    assert args[0] == (
        "<b>Who Paid For This</b> tracks who paid for what on a group trip, then works out who "
        "owes whom. Add me to your trip group to start.\n\n"
        "Just one bill to split? You can do that right here.\n\n"
        "Who Paid For This abc1234"
    )
    assert kwargs["parse_mode"] == "HTML"
    assert _keyboard(kwargs["reply_markup"]) == [
        [("Add me to a group for a trip", "https://t.me/whopaidbot?startgroup=true")],
        [("Split a bill now", "oneoff:start")],
    ]


async def test_private_help_keeps_the_command_list_with_the_same_two_buttons() -> None:
    update = _private_update()
    await help_command(update, _command_context(make_deps(bot_version="abc1234")))
    args, kwargs = update.message.reply_text.await_args
    assert args[0] == f"{HELP_TEXT}\n\nWho Paid For This abc1234"
    assert _keyboard(kwargs["reply_markup"]) == [
        [("Add me to a group for a trip", "https://t.me/whopaidbot?startgroup=true")],
        [("Split a bill now", "oneoff:start")],
    ]


def test_help_text_lists_split_directly_before_feedback() -> None:
    lines = HELP_TEXT.splitlines()
    index = lines.index("/split — split one bill in a private chat, no group needed")
    assert lines[index + 1].startswith("/feedback")


async def test_group_start_is_unchanged(monkeypatch) -> None:
    from app.bot.handlers import basic

    posted = AsyncMock()
    monkeypatch.setattr(basic, "post_welcome", posted)
    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=-100, type="supergroup")
    update.message.reply_text = AsyncMock()
    await start_command(update, _command_context(make_deps()))
    posted.assert_awaited_once()
    update.message.reply_text.assert_not_awaited()


# ---- no database, no ledger ---------------------------------------------------


class Exploding:
    """Fails on any attribute access or call, standing in for every store and service."""

    def __getattr__(self, name: str):
        raise AssertionError(f"one-off bill touched {name!r}")

    def __call__(self, *args, **kwargs):
        raise AssertionError("one-off bill called a forbidden dependency")


async def test_no_ledger_or_store_call_is_made_anywhere_on_the_path(monkeypatch) -> None:
    deps = Deps(
        ledger=Exploding(),
        currencies=Exploding(),
        bot_version="test",
        draft_service=Exploding(),
        # The photo path reads a receipt, so the reader and the budget are real here.
        receipt_reader=FakeReceiptReader(extraction=receipt_fixtures.dinner()),
        settrip_sessions=Exploding(),
        feedback_limiter=Exploding(),
    )
    chatter = Chatter(monkeypatch, deps=deps)
    # equal split to the end
    await chatter.to_preview()
    await chatter.press("Edit")
    await chatter.press("Next")
    await chatter.press("Ali")
    await chatter.press("Type amounts instead")
    await chatter.say("me 40\nAli 44.50\nZed 1")
    await chatter.press("Confirm")
    await chatter.press("Done")
    assert chatter.bill is None
    # start again, drop, replace, photo, expiry, /cancel
    await chatter.to_names()
    await chatter.press_data("oneoff:start", 1)
    await chatter.press("Yes, start again")
    await chatter.photo()
    await chatter.say("1200 yen ramen")
    await chatter.say("5")
    await chatter.command("cancel")
    await chatter.command()
    chatter.advance(minutes=31)
    await chatter.say("hello")
    assert chatter.bot.texts()[-1] == render.STALE_TEXT


# ---- logging ------------------------------------------------------------------


async def test_no_log_record_carries_a_description_a_name_or_an_amount(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    chatter = Chatter(monkeypatch, first_name="Quillfeather")
    await chatter.to_names("7777.77 zorbaburger")
    await chatter.say("Glimmerwick, Snodgrass")
    await chatter.press("Next")
    await chatter.press("Me")
    await chatter.press("Split equally")
    await chatter.press("Done")  # finished
    await chatter.command()
    await chatter.say("12 taxi")
    await chatter.press("Cancel")  # cancelled
    await chatter.command()
    chatter.advance(minutes=31)
    await chatter.say("hello")  # expired
    text = "\n".join(f"{r.getMessage()} {r.args} {r.__dict__}" for r in caplog.records)
    for secret in (
        "zorbaburger",
        "Zorbaburger",
        "Quillfeather",
        "Glimmerwick",
        "Snodgrass",
        "7777",
    ):
        assert secret not in text
    events = [r.getMessage() for r in caplog.records if r.name == "app.services.oneoff"]
    assert events.count("oneoff started") == 3
    assert "oneoff finished" in events and "oneoff cancelled" in events
    assert "oneoff expired" in events
    assert set(events) == {
        "oneoff started",
        "oneoff finished",
        "oneoff cancelled",
        "oneoff expired",
    }


# ---- callback data ------------------------------------------------------------


def test_callback_data_stays_under_64_bytes_for_every_button() -> None:
    store = OneOffStore()
    bill = store.create(user_id=1, chat_id=1, first_name="Sufyan", now=START)
    bill = set_count(set_bill_line(bill, BillLine("Dinner", 100, "SGD")), 20)
    longest = 0
    for state in ("count", "names", "payer", "method", "amounts", "preview"):
        split = TypedSplit() if state == "amounts" else EqualSplit({n: 0 for n in range(1, 21)})
        card = replace(bill, state=state, payer=1, split=split)
        _text, markup = render.render_card(card)
        for row in markup.inline_keyboard:
            for button in row:
                size = len(button.callback_data.encode())
                assert size <= 64
                longest = max(longest, size)
    drop = keyboards.drop_keyboard(bill.bill_id)
    for button in drop.inline_keyboard[0]:
        longest = max(longest, len(button.callback_data.encode()))
    assert longest == len("oneoff:") + len(bill.bill_id) + len(":payer:20") == 7 + 8 + 9
    assert len(keyboards.START_CALLBACK) == 12


def test_receipt_callback_data_and_button_counts_with_forty_items_and_twenty_persons() -> None:
    """Sprint 18: every receipt card, at the worst case the bill allows (40 items, 20
    persons), keeps callback data within 64 bytes and the buttons within Telegram's 100.
    """
    from app.models.receipt import CandidateTotal, ReceiptLineItem

    lines = tuple(
        ReceiptLineItem(
            description=f"Dish number {n} with a very long name indeed",
            quantity=1,
            amount_minor=1000 + n,
            kind="item",
        )
        for n in range(40)
    )
    candidates = tuple(
        CandidateTotal(label_as_printed="Total", amount_minor=1000 * n, is_grand_total_guess=False)
        for n in (1, 2, 3)
    )
    names = ("Sufyan", *(f"{'N' * 28}{n:02d}" for n in range(2, 21)))
    total = sum(i.amount_minor for i in lines)
    base = OneOffBill(
        bill_id="abcdefgh",
        user_id=1,
        chat_id=1,
        first_name="Sufyan",
        message_id=1,
        amount_minor=total,
        count=20,
        names=names,
        payer=1,
        photo_file_id="f",
        line_items=lines,
        candidates=candidates,
        read_problem="temporarily down",
    )
    claims = {n: tuple(range(1, 21)) for n in range(40)}
    cards = {
        "couldnt_read": base,
        "choose_total": base,
        "reconcile": base,
        "count": base,
        "method": base,
        "item_walk": replace(base, split=ItemsSplit(walk_index=39)),
        "fix_list": replace(base, claims=claims, split=ItemsSplit()),
        "preview": replace(base, claims=claims, split=ItemsSplit()),
    }
    longest = most = 0
    for state, bill in cards.items():
        card = replace(bill, state=state)
        _text, markup = render.render_card(card)
        buttons = [b for row in markup.inline_keyboard for b in row]
        assert len(buttons) <= 100, state
        most = max(most, len(buttons))
        for button in buttons:
            size = len(button.callback_data.encode())
            assert size <= 64, (state, button.callback_data)
            longest = max(longest, size)
    # the longest is a toggle on the last of 40 items for the 20th person
    assert longest == len("oneoff:abcdefgh:wtoggle:39,20") == 29
    # a 40-item fix list: one button per item plus `Back to summary`
    assert most == 41
    walk = render.render_card(replace(cards["item_walk"], state="item_walk"))[1]
    assert (
        sum(len(row) for row in walk.inline_keyboard) == 24
    )  # 20 + Everyone + Back + Next + Cancel
    assert [len(row) for row in walk.inline_keyboard] == [3] * 6 + [2, 1, 2, 1]


# ---- review B: "3 people" at the how-many step --------------------------------


@pytest.mark.parametrize(("text", "count"), [("3 people", 3), ("4 pax", 4), ("2 PPL", 2)])
async def test_a_count_with_a_people_word_sets_the_count(
    chat: Chatter, text: str, count: int
) -> None:
    await chat.command()
    await chat.say("84.50 dinner")
    bill_id = chat.bill.bill_id
    await chat.say(text)
    assert chat.bill.bill_id == bill_id and chat.bill.count == count
    assert chat.bill.amount_minor == 8450 and chat.bill.state == "names"


async def test_21_people_is_refused_and_12_lunch_replaces_the_bill(chat: Chatter) -> None:
    await chat.command()
    await chat.say("84.50 dinner")
    await chat.say("21 people")
    assert chat.bot.texts()[-1] == "Type a number from 2 to 20."
    assert chat.bill.state == "count"
    await chat.say("12 lunch")
    assert chat.card().startswith("🧾 <b>Lunch</b> · S$12.00")


# ---- review C: keyboard rows --------------------------------------------------


def _rows(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


def _bill_with(count: int, state: str = "payer") -> OneOffBill:
    bill = OneOffStore().create(user_id=1, chat_id=1, first_name="Sufyan", now=START)
    bill = set_count(set_bill_line(bill, BillLine("Dinner", 8450, "SGD")), count)
    return replace(bill, state=state)


def test_count_keyboard_rows() -> None:
    assert _rows(keyboards.count_keyboard("id")) == [["2", "3", "4", "5", "6"], ["Cancel"]]


def test_names_keyboard_rows() -> None:
    assert _rows(keyboards.names_keyboard("id")) == [["Next", "Cancel"]]


def test_payer_keyboard_rows_are_three_per_row_then_cancel() -> None:
    rows = _rows(keyboards.payer_keyboard(_bill_with(7)))
    assert [len(r) for r in rows] == [3, 3, 1, 1]
    assert rows[0][0] == "Me" and rows[-1] == ["Cancel"]
    assert [len(r) for r in _rows(keyboards.payer_keyboard(_bill_with(3)))] == [3, 1]


def test_method_keyboard_is_one_button_per_row() -> None:
    assert _rows(keyboards.method_keyboard("id")) == [
        ["Split equally"],
        ["Type amounts instead"],
        ["Cancel"],
    ]


def test_amounts_keyboard_matches_the_groups_custom_keyboard_layout() -> None:
    # The group's: one row, `Confirm` (only once balanced) beside `Cancel`.
    assert _rows(keyboards.amounts_keyboard("id", True)) == [["Confirm", "Cancel"]]
    assert _rows(keyboards.amounts_keyboard("id", False)) == [["Cancel"]]
    for balanced in (True, False):
        theirs = _rows(split_keyboards.custom_keyboard("id", balanced))
        assert _rows(keyboards.amounts_keyboard("id", balanced)) == theirs


def test_preview_keyboard_rows() -> None:
    assert _rows(keyboards.preview_keyboard("id")) == [["Done", "Edit"], ["Cancel"]]


# ---- review D: an edited /cancel -----------------------------------------------


def test_an_edited_slash_cancel_does_not_cancel_a_bill() -> None:
    deps = make_deps()
    application = _application(deps)
    _open_bill(deps)
    edited = _text_update("/cancel", command=True, edited=True)
    assert oneoff_cancel_command not in _matching(application, edited)


# ---- review E: a deleted card does not strand the user -------------------------


async def test_yes_start_again_still_starts_when_the_old_card_is_gone(chat: Chatter) -> None:
    await chat.to_names()
    old = chat.bill
    await chat.command()
    chat.bot.deleted.add(old.message_id)  # the user deleted the old card
    await chat.press("Yes, start again")
    assert chat.bill.bill_id != old.bill_id and chat.bill.state == "awaiting_bill"
    assert chat.bot.messages[chat.bill.message_id]["text"] == render.START_PROMPT_TEXT
    await chat.say("10 taxi")
    assert chat.bill.state == "count"


async def test_yes_start_again_sends_a_new_prompt_when_the_question_is_gone_too(
    chat: Chatter,
) -> None:
    await chat.to_names()
    old = chat.bill
    await chat.command()
    question = chat.bot.sent[-1]
    chat.bot.deleted.update({old.message_id, question})
    await chat.press("Yes, start again")
    assert chat.bill.message_id not in (old.message_id, question)
    assert chat.bot.messages[chat.bill.message_id]["text"] == render.START_PROMPT_TEXT


async def test_a_button_press_on_a_deleted_card_sends_the_card_as_a_new_message(
    chat: Chatter,
) -> None:
    await chat.to_names()
    gone = chat.bill.message_id
    chat.bot.deleted.add(gone)
    await chat.press("Next")
    assert chat.bill.state == "payer"  # saved before the edit
    assert chat.bill.message_id != gone
    assert chat.bot.messages[chat.bill.message_id]["text"] == f"{TITLE}\n\nWho paid?"
    await chat.press("Me")  # the flow carries on from the new card
    assert chat.bill.state == "method" and chat.card() == METHOD_CARD


async def test_typed_text_on_a_deleted_card_sends_the_card_as_a_new_message(
    chat: Chatter,
) -> None:
    await chat.to_names()
    gone = chat.bill.message_id
    chat.bot.deleted.add(gone)
    await chat.say("Ali, Mei")
    assert chat.bill.message_id != gone
    assert chat.card() == NAMES_CARD_RENAMED
    await chat.say("Ali")  # the stored ID is the new card, so this edits it quietly
    assert chat.bot.sent[-1] == chat.bill.message_id


async def test_done_on_a_deleted_card_sends_the_summary_as_a_new_message(chat: Chatter) -> None:
    await chat.to_preview()
    chat.bot.deleted.add(chat.bill.message_id)
    await chat.press("Done")
    assert chat.bot.texts()[-2] == SUMMARY
    assert chat.bot.texts()[-1].startswith("Forward the message above")
    assert chat.bill is None


async def test_cancel_on_a_deleted_card_still_removes_the_bill(chat: Chatter) -> None:
    await chat.to_names()
    chat.bot.deleted.add(chat.bill.message_id)
    await chat.press("Cancel")
    assert chat.bill is None


# ---- review I: the warning line cannot push the card past 4096 ------------------


async def test_a_5000_character_paste_keeps_the_card_under_the_limit(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("x" * 5000)
    card = chat.card()
    assert len(card) < 4096
    assert card.splitlines()[-1] == "⚠️ Couldn't read '" + "x" * 30 + "…'"


async def test_a_long_unknown_name_is_cut_in_the_warning(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("y" * 100 + " 20")
    assert chat.card().splitlines()[-1] == "⚠️ Who is '" + "y" * 30 + "…'?"


async def test_twelve_bad_lines_show_five_then_a_count(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("\n".join(f"Zed{i} 5" for i in range(12)))
    last = chat.card().splitlines()[-1]
    assert last == ("⚠️ " + "; ".join(f"Who is 'Zed{i}'?" for i in range(5)) + "; …and 7 more")
    assert len(chat.card()) < 4096


async def test_exactly_five_bad_lines_show_no_more_marker(chat: Chatter) -> None:
    await chat.to_amounts()
    await chat.say("\n".join(f"Zed{i} 5" for i in range(5)))
    assert "more" not in chat.card().splitlines()[-1]
