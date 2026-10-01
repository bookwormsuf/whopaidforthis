# whopaid

Telegram group expense tracker for trips. Records into a shared Google Sheet, converts amounts
to the home currency at the day's rate, splits receipts among participants, and tells you who
owes whom. Runs on Fly.io; see Deployment below.

## Setup

See `docs/sprints/00-accounts-keys-skeleton.md` for account setup (Telegram bot, Google service
account, Anthropic key) and the local `.env`.

## Status

See `STATUS.md` for sprint progress and sign-offs.

## Google Cloud setup

The bot writes to Google Sheets using a **service account**, not your personal Google login.

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a project (or reuse
   one) and enable the **Google Sheets API**.
2. Create a service account under IAM & Admin → Service Accounts, then add a JSON key for it.
   Download the key file.
3. Put the whole contents of that JSON file, on one line, into `GOOGLE_SERVICE_ACCOUNT_JSON` in
   your `.env`. Not a file path — the JSON text itself.
4. Create one blank Google Sheet named `whopaid control` for the bot's own bookkeeping (it holds
   the map of Telegram group → trip Sheet). Share it with the service account's email (the
   `client_email` field in the JSON key, looks like `something@project.iam.gserviceaccount.com`)
   as **Editor**. Copy the Sheet's ID from its URL — the long string between `/d/` and `/edit` —
   into `WHOPAID_CONTROL_SHEET_ID` in your `.env`.

## Sharing the Sheet

Each trip gets its own Google Sheet, created and owned by you.

1. Create a blank Google Sheet for the trip.
2. Share it with the service account's email (same one as above) as **Editor**. Without this,
   `/settrip` will tell you it can't open the Sheet.
3. Copy the Sheet's URL or ID — you'll paste it into `/settrip` in the next step.

## Binding a trip

In the Telegram group for the trip, run:

```
/settrip <Sheet URL or ID>
```

The bot checks it can open the Sheet, then asks three quick questions with buttons: the trip
currency (what you're paying in), the home currency (what you'll settle up in), and the
timezone (defaults to the one that matches the trip currency, e.g. Japan → Asia/Tokyo). Confirm,
and it lays out all the tabs and formulas in the Sheet. Running `/settrip` again in an already-
bound group asks before rebinding — it never wipes the old Sheet.

Once bound, everyone in the group should send `/join` (or `/start`) so the bot knows who's
splitting expenses.

## Run locally

```
uv sync
uv run python -m app
uv run pytest
uv run ruff check .
uvx pre-commit run --all-files
```

## Deployment

The bot runs on Fly.io as one machine, long-polling Telegram. **Stop the local bot before
deploying, and never run both at once** — two pollers on the same bot token make Telegram
return 409 and both go deaf.

1. `fly auth login` (opens a browser; you already have flyctl installed).
2. `fly launch --no-deploy --copy-config --name whopaid-bot --region sin`. Answer **No** to
   Postgres, Redis and any other add-on.
   If Fly says the name is taken, accept the name it suggests (or use `whopaid-bot-<4 chars>`);
   `--copy-config` writes it into `fly.toml`, and every later command uses that name.
3. `./scripts/set-fly-secrets.sh` . It prints the variable names it set, never the values.
4. `./scripts/deploy.sh`. Wait for "deployed successfully".
5. `fly status`. Exactly one machine, state `started`.
6. `fly logs`. You see `whopaid started`.
7. Close your laptop. From your phone, send `/help` and `500 coffee` in the test group, confirm
   it. Reopen the laptop; the Sheet has the row.
8. `fly machine list` shows one machine. If two, run `fly scale count 1`.
9. Make a trivial change with the agent, commit it, `./scripts/deploy.sh` again, confirm `/help`
   shows the new version.

### Updating

Run `./scripts/deploy.sh` again. Confirm `/help` in the test group shows the new version.

### Stopping after the trip

- `fly scale count 0` stops the machine (and billing) but keeps the app and its config, so you
  can scale back up later.
- `fly apps destroy <app name from fly.toml>` removes the app entirely.
- Either way, the Sheets keep everything — expenses, balances, and settlement live in Google
  Sheets, not on the machine.
- `LOG_LEVEL` isn't set by `./scripts/set-fly-secrets.sh` (it isn't a secret). If you ever need
  it, set it by hand: `fly secrets set LOG_LEVEL=DEBUG`.

### Why no port

The bot uses long polling: it calls out to Telegram, nothing listens for incoming connections.
There is no `[http_service]` in `fly.toml` and no Fly health check configured. The `whopaid
started` line in `fly logs` is the health signal — if it's there, the bot is up.
