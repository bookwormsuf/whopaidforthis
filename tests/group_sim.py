"""A fake Telegram group for multi-user tests (sprint 5 decision 2026-10-01: these
stand in for the two-person checklist steps until a second tester is available).

`FakeGroup` wires the real handlers to an `InMemoryLedgerStore` and records every
card the bot sends or edits, so a test reads like the checklist: Ada sends a
message, Bo presses a button on Ada's card, someone runs `/balances`.
"""

from __future__ import annotations

import io
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from PIL import Image

from app.bot.handlers.photo_expense import photo_expense_handler
from app.bot.handlers.text_expense import draft_callback, text_expense_handler
from app.models.trip import Trip
from app.models.user import TripUser
from app.services.ledger_store import InMemoryLedgerStore
from app.services.llm_schemas import ReceiptExtraction
from tests.helpers import FakeReceiptReader, make_deps

CHAT_ID = -100


def _jpeg_bytes() -> bytes:
    image = Image.new("RGB", (100, 100), color=(10, 20, 30))
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


@dataclass(frozen=True)
class Member:
    id: int
    first_name: str
    username: str | None
    last_name: str | None = None


ADA = Member(1, "Ada", "ada")
BO = Member(2, "Bo", "bo")
CY = Member(3, "Cy", "cy")


@dataclass
class Card:
    message_id: int
    text: str
    markup: object

    @property
    def buttons(self) -> list[str]:
        if self.markup is None:
            return []
        return [b.text for row in self.markup.inline_keyboard for b in row]

    @property
    def draft_id(self) -> str:
        assert self.markup is not None, "card has no buttons"
        return self.markup.inline_keyboard[0][0].callback_data.split(":", 1)[0]

    def callback_for(self, label: str) -> str:
        for row in self.markup.inline_keyboard:
            for button in row:
                if button.text == label:
                    return button.callback_data
        raise AssertionError(f"no button {label!r} on card: {self.buttons}")


@dataclass
class FakeGroup:
    ledger: InMemoryLedgerStore
    trip: Trip
    deps: object
    cards: dict[int, Card] = field(default_factory=dict)
    replies: list[str] = field(default_factory=list)
    reader: FakeReceiptReader | None = None
    _next_message_id: int = 100
    _next_update_id: int = 1

    @classmethod
    async def create(cls, *members: Member, trip_currency: str = "JPY") -> FakeGroup:
        ledger = InMemoryLedgerStore()
        trip = Trip(
            chat_id=CHAT_ID,
            sheet_id="sheet-id-1234567890",
            name="Test Trip",
            trip_currency=trip_currency,
            home_currency="SGD",
            timezone="Asia/Tokyo",
            created_at=datetime.now(UTC),
        )
        await ledger.save_binding(trip)
        await ledger.initialise_trip_sheet(trip)
        reader = FakeReceiptReader()
        group = cls(ledger=ledger, trip=trip, deps=make_deps(ledger, receipt_reader=reader))
        group.reader = reader
        for member in members:
            await group.join(member)
        return group

    async def join(self, member: Member) -> None:
        now = datetime.now(UTC)
        user = TripUser.from_telegram_user(self._tg_user(member), now)
        await self.ledger.upsert_user(self.trip, user, joined=True)

    # -- plumbing ---------------------------------------------------------

    def _tg_user(self, member: Member) -> SimpleNamespace:
        return SimpleNamespace(
            id=member.id,
            first_name=member.first_name,
            last_name=member.last_name,
            username=member.username,
        )

    def _new_id(self) -> int:
        self._next_message_id += 1
        return self._next_message_id

    def _context(self) -> MagicMock:
        context = MagicMock()
        context.application.bot_data = {"deps": self.deps}
        context.bot.username = "whopaidforthisbot"

        async def edit_message_text(*, chat_id, message_id, text, parse_mode, reply_markup):
            self.cards[message_id] = Card(message_id, text, reply_markup)

        context.bot.edit_message_text = AsyncMock(side_effect=edit_message_text)
        context.bot.get_file = AsyncMock(
            return_value=SimpleNamespace(
                file_size=1000,
                download_as_bytearray=AsyncMock(return_value=bytearray(_jpeg_bytes())),
            )
        )
        return context

    def _reply_recorder(self) -> AsyncMock:
        async def reply_text(text, parse_mode=None, reply_markup=None):
            message_id = self._new_id()
            self.replies.append(text)
            if reply_markup is not None:
                self.cards[message_id] = Card(message_id, text, reply_markup)
            return SimpleNamespace(message_id=message_id)

        return AsyncMock(side_effect=reply_text)

    def _update(self, member: Member, text: str, reply_to: int | None) -> MagicMock:
        self._next_update_id += 1
        update = MagicMock()
        update.update_id = self._next_update_id
        update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
        update.effective_user = self._tg_user(member)
        message = MagicMock()
        message.text = text
        message.message_id = self._new_id()
        message.date = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
        message.reply_to_message = (
            SimpleNamespace(message_id=reply_to) if reply_to is not None else None
        )
        message.reply_text = self._reply_recorder()
        update.effective_message = message
        update.message = message
        return update

    # -- what a test does -------------------------------------------------

    async def send(self, member: Member, text: str, reply_to: Card | None = None) -> Card | None:
        """`member` sends `text`; returns the draft card the bot replied with, if any."""
        before = set(self.cards)
        update = self._update(member, text, reply_to.message_id if reply_to else None)
        await text_expense_handler(update, self._context())
        new = [mid for mid in self.cards if mid not in before]
        return self.cards[new[-1]] if new else None

    async def send_receipt(
        self, member: Member, extraction: ReceiptExtraction, caption: str | None = None
    ) -> Card | None:
        """`member` sends a receipt photo that the (fake) reader reads as `extraction`."""
        assert self.reader is not None
        self.reader._extraction = extraction
        before = set(self.cards)
        update = self._update(member, "", None)
        update.effective_message.text = None
        update.effective_message.caption = caption
        update.effective_message.photo = [SimpleNamespace(file_id="photo", file_size=1000)]
        await photo_expense_handler(update, self._context())
        new = [mid for mid in self.cards if mid not in before]
        return self.cards[new[-1]] if new else None

    async def press(self, member: Member, card: Card, label: str) -> str | None:
        """`member` taps the button labelled `label` on `card`. Returns the toast
        text (None when the press was answered silently). `card` is refreshed.
        """
        data = card.callback_for(label)
        update = MagicMock()
        update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
        query = MagicMock()
        query.data = data
        query.from_user = self._tg_user(member)
        query.answer = AsyncMock()

        async def edit_message_text(text, parse_mode=None, reply_markup=None):
            self.cards[card.message_id] = Card(card.message_id, text, reply_markup)

        query.edit_message_text = AsyncMock(side_effect=edit_message_text)
        update.callback_query = query
        await draft_callback(update, self._context())
        latest = self.cards[card.message_id]
        card.text, card.markup = latest.text, latest.markup
        query.answer.assert_awaited_once()
        (args, _kwargs) = query.answer.call_args
        return args[0] if args else None

    async def command(
        self, member: Member, handler: Callable[[MagicMock, MagicMock], Awaitable[None]]
    ) -> str:
        """Runs a command handler as `member`; returns its reply text."""
        update = self._update(member, "/cmd", None)
        await handler(update, self._context())
        (args, _kwargs) = update.effective_message.reply_text.call_args
        return args[0]
