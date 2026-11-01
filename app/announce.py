"""Send one message, through the bot, to every group with an open trip.

    python -m app.announce --file whats-new.txt            # dry run: counts only
    python -m app.announce --file whats-new.txt --only -100123   # one group, to preview
    python -m app.announce --file whats-new.txt --send     # every open trip

Run on a laptop with the same `.env` the bot uses (`uv run python -m app.announce ...`),
or on Fly (`fly ssh console -a whopaid-bot -C "python -m app.announce --text '...'"`).
`--text` turns a literal `\\n` into a new line, for a message passed on one command line.
The message is HTML, as every bot reply is: `<b>bold</b>`, `<i>italic</i>`.

Nothing is sent without `--send` or `--only`. Prints counts only: never a chat id or
the message, in line with the logging rules. A group that removed the bot is counted
and skipped, not retried.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from telegram import Bot
from telegram.error import BadRequest, Forbidden, TelegramError

from app.config.settings import load_settings
from app.services.postgres_store import PostgresLedgerStore

# Telegram allows about 30 messages a second across chats; stay well under it.
PAUSE_SECONDS = 0.1

SendFn = Callable[[int, str], Awaitable[None]]


@dataclass(frozen=True)
class AnnounceResult:
    groups: int
    sent: int = 0
    bot_removed: int = 0
    failed: int = 0


async def announce(
    chat_ids: list[int],
    text: str,
    send: SendFn,
    *,
    pause: Callable[[], Awaitable[None]] = lambda: asyncio.sleep(PAUSE_SECONDS),
) -> AnnounceResult:
    """Sends `text` to each chat in turn; one chat failing never stops the rest. A
    message Telegram rejects as malformed (bad HTML) stops at once, since every
    other chat would reject it too.
    """
    sent = bot_removed = failed = 0
    for position, chat_id in enumerate(chat_ids):
        if position:
            await pause()
        try:
            await send(chat_id, text)
            sent += 1
        except Forbidden:
            bot_removed += 1
        except BadRequest as exc:
            if "parse entities" in str(exc).lower():
                raise
            failed += 1
        except TelegramError:
            failed += 1
    return AnnounceResult(len(chat_ids), sent, bot_removed, failed)


def message_from(args: argparse.Namespace) -> str:
    text = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    text = text.replace("\\n", "\n").strip()
    if not text:
        raise SystemExit("The message is empty.")
    return text


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.announce", description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="a UTF-8 text file holding the message")
    source.add_argument("--text", help="the message itself; \\n for a new line")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--send", action="store_true", help="send to every open trip")
    target.add_argument("--only", type=int, metavar="CHAT_ID", help="send to this chat only")
    return parser


async def _main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    text = message_from(args)
    settings = load_settings()

    if args.only is not None:
        chat_ids = [args.only]
    else:
        store = PostgresLedgerStore(settings.database_url.get_secret_value())
        await store.connect()
        try:
            chat_ids = await store.list_open_trip_chat_ids()
        finally:
            await store.close()

    print(f"Message ({len(text)} characters):\n{text}\n")
    if not args.send and args.only is None:
        print(f"Dry run: would send to {len(chat_ids)} group(s) with an open trip.")
        print("Add --only CHAT_ID to try one group, or --send to send to all.")
        return 0

    async with Bot(settings.telegram_bot_token.get_secret_value()) as bot:

        async def send(chat_id: int, message: str) -> None:
            await bot.send_message(chat_id, message, parse_mode="HTML")

        try:
            result = await announce(chat_ids, text, send)
        except BadRequest:
            print("Telegram rejected the message's formatting; nothing more was sent.")
            return 1
    print(
        f"Sent to {result.sent} of {result.groups} group(s). "
        f"Bot no longer in {result.bot_removed}; other failures {result.failed}."
    )
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
