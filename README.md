# whopaid

Telegram group expense tracker for trips. Records into a shared Google Sheet, converts amounts
to the home currency at the day's rate, splits receipts among participants, and tells you who
owes whom.

## Setup

See `docs/sprints/00-accounts-keys-skeleton.md` for account setup (Telegram bot, Google service
account, Anthropic key) and the local `.env`.

## Status

See `STATUS.md` for sprint progress and sign-offs.

## Run locally

```
uv sync
uv run python -m app
uv run pytest
uv run ruff check .
uvx pre-commit run --all-files
```
