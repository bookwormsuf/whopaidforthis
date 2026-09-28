# Sprint 0 — Accounts, keys, skeleton

## Goal
Every account and key exists, the repo skeleton runs, and the bot answers `/help` from your laptop.

## In scope
Repo scaffolding, config loading, logging, a bot that answers `/start` and `/help`, Dockerfile,
secret-scanning hook, `.env.example`, README skeleton, `STATUS.md` in place.

## Out of scope
Google Sheets calls, exchange rates, any expense logic. Deployment (Sprint 3).

## Sprint pins
- `uv` manages Python; `pyproject.toml` pins `requires-python = ">=3.12"`. `uv run` is the only way anything is executed.
- Config: `pydantic-settings` class `Settings` reading `TELEGRAM_BOT_TOKEN`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `ANTHROPIC_API_KEY`, `LOG_LEVEL` (default `INFO`). Startup fails loudly with the missing variable's name if any is absent. `GOOGLE_SERVICE_ACCOUNT_JSON` is the JSON text itself on one line, not a file path.
- Logging: stdlib `logging`, JSON lines, never logs message text, image bytes or any settings value.
- Entry point: `python -m app` → `app/__main__.py` builds the PTB `Application` and runs `run_polling(allowed_updates=[message, callback_query])`.
- `/help` text is the one-screen summary of what the bot does, written for a friend who has never seen it.
- Secret scanning: `pre-commit` with `gitleaks`. `.gitignore` covers `.env`, `*.json` at repo root except `package*.json`, `.venv`, `__pycache__`, `.pytest_cache`.
- Dockerfile: multi-stage on `python:3.12-slim`, copies `uv` from `ghcr.io/astral-sh/uv`, non-root user, `CMD ["python", "-m", "app"]`.
- `tests/test_smoke.py`: `Settings` fails on a missing variable; the PTB application builds with a fake token.

## Before you start (you)
Nothing. The agent builds first, then you do the account work below while it is fresh.

## Agent builds
`pyproject.toml`, `uv.lock`, `app/__init__.py`, `app/__main__.py`, `app/config/settings.py`, `app/bot/handlers/basic.py` (`/start`, `/help`), `app/bot/app.py`, `Dockerfile`, `.dockerignore`, `.gitignore`, `.pre-commit-config.yaml`, `.env.example`, `README.md` (title, one-paragraph description, "Setup" heading pointing at this file, "Status" heading pointing at `STATUS.md`), `tests/test_smoke.py`, `STATUS.md` (already exists; append the entry).

## Tests required
`uv run pytest` green; `uv run ruff check .` clean; `pre-commit run --all-files` clean.

## After: your checklist
Do these in order. Each takes a few minutes.

**A. Install uv**
1. In Terminal run: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. Close and reopen Terminal. Run `uv --version`. You should see a version number.

**B. Telegram bot (BotFather)**
1. In Telegram, open a chat with `@BotFather`.
2. Send `/mybots`, pick `@whopaidforthisbot`.
3. Tap **API Token**. Copy it. This is `TELEGRAM_BOT_TOKEN`. Treat it like a password.
4. Go back, tap **Bot Settings** → **Group Privacy** → **Turn off**. You should see "Privacy mode is disabled".
5. Optional but nice: **Edit Bot** → **Edit Commands** and paste:
   ```
   help - What this bot does
   join - Join this trip
   settrip - Bind this group to a Google Sheet
   recent - Last 5 expenses
   today - Today's expenses
   summary - Group totals
   mine - What I paid and owe
   balances - Everyone's balances
   settle - Who pays whom
   undo - Cancel my last expense
   ```

**C. Test group**
1. Create a new Telegram group called "whopaid test" with just you in it (Telegram may ask you to add one contact; add the bot as that member).
2. Add `@whopaidforthisbot` to the group. If you had added it before step B4, remove it and add it again, or privacy mode stays on.

**D. Google Cloud service account** (the bot's own Google identity)
1. Go to https://console.cloud.google.com and sign in with your Gmail.
2. Top bar → project dropdown → **New project** → name it `whopaid` → Create. Wait, then select it.
3. Left menu → **APIs & Services** → **Library**. Search "Google Sheets API" → Enable.
4. Left menu → **APIs & Services** → **Credentials** → **Create credentials** → **Service account**. Name `whopaid-bot`. Create and continue. Skip the optional role steps. Done.
5. Click the new service account → **Keys** tab → **Add key** → **Create new key** → JSON → Create. A file downloads.
6. Note the service account's email on that page. It looks like `whopaid-bot@whopaid-xxxxx.iam.gserviceaccount.com`. You will share Sheets with this address later.
7. Move the downloaded file out of Downloads to somewhere private, for example `~/.config/whopaid/service-account.json`. Never copy it into the repo.

**E. Anthropic API key**
1. Go to https://console.anthropic.com, sign in, **API Keys** → **Create key**, name `whopaid`. Copy it. This is `ANTHROPIC_API_KEY`.
2. **Settings** → **Limits** (or Billing) → set a monthly spend limit, for example US$10. Receipts cost about one cent each.

**F. Local `.env`**
1. In the repo, copy `.env.example` to `.env`.
2. Fill `TELEGRAM_BOT_TOKEN` and `ANTHROPIC_API_KEY`.
3. For `GOOGLE_SERVICE_ACCOUNT_JSON`, run in Terminal: `python3 -c "import json;print(json.dumps(json.load(open('/Users/<you>/.config/whopaid/service-account.json'))))"` and paste the single line it prints as the value, wrapped in single quotes.
4. Run `git status`. `.env` must **not** appear. If it does, stop and tell the agent.

**G. Run it**
1. `uv run python -m app`
2. In the test group, send `/help`. The bot replies within a second or two.
3. Press Ctrl+C to stop it.

## Exit criteria
- [ ] `/help` answered in the test group from your laptop
- [ ] Privacy mode shows disabled in BotFather
- [ ] Service account email noted somewhere you can find it
- [ ] `.env` filled and not tracked by git
- [ ] Tests, ruff, pre-commit green
- [ ] `STATUS.md` entry with your sign-off
