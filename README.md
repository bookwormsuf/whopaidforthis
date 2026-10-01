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

## Receipts

Send a photo of a receipt in a bound group and the bot reads it with an LLM: merchant,
total, date, and the individual line items. Just photograph the whole receipt, including the
totals section at the bottom — the clearer and flatter the photo, the better the read.

If the receipt shows more than one number that could be the total (for example a
tax-included and a tax-excluded total both printed), the bot asks "Which is the final
total?" with buttons for each candidate. If the total you pick doesn't match the line items
added up, it shows the difference and asks "Use the receipt total anyway?" — the receipt's
printed total is always what gets saved.

If the bot can't read a receipt at all (too blurry, not a receipt), it says "Couldn't read
that receipt" with two buttons: **Try again** (re-reads the same photo) and **Enter amount
manually** (reply to that message with just the amount, e.g. `2400`).

A caption on the photo overrides the description the LLM guessed — a receipt sent with the
caption `dinner` saves as "Dinner" rather than whatever the LLM extracted.

**Limitation:** Telegram albums (several photos sent together) are processed as separate
messages, each producing its own draft — there's no "this is one receipt across 3 photos"
merging in this sprint.

Line items are extracted and stored in the Sheet's `Line Items` tab for your own reference,
but aren't yet splittable by item — a receipt is shared equally or with custom amounts, like
a text expense (per-item claiming is a later sprint). Item amounts are always shown in the trip
currency; for a receipt paid in the home currency they are converted at that expense's rate.

## Splitting model

Every expense has one **payer** (who handed over the money) and one or more **participants**
(who owe a share). The draft card asks who should share it:

- **Just me** — the sender is the only participant.
- **Everyone** — every trip user who has run `/join`, counted at the moment you tap it.
- **Choose people** — one toggle per joined user (you start selected), then **Next**.

With more than one participant the card asks how to split:

- **Equally** — everyone gets `total // n`; the leftover minor units (yen, cents) go one each
  to participants in Telegram user ID order, skipping the payer while anyone else is left.
- **Custom** — reply to the card with one line per person, e.g. `Amir 2000`. The card shows
  each person's amount and `Remaining: ¥0 ✅` once they add up exactly; **Confirm** only
  appears then. Anyone you don't mention gets nothing and is dropped.

The preview lists every share in both currencies, payer marked 💳, with **Confirm**, **Edit**
(back to choosing people, amount kept) and **Cancel**. Only the person who sent the expense can
press its buttons; anyone else gets "Only <name> can edit this".

You can skip the buttons in the message itself:

- `6000 yen dinner split everyone` (or `split all`)
- `6000 yen dinner split between me Amir and John` (also `among`, `with`)
- `3000 yen lunch paid by Amir` — the payer doesn't have to be a participant

Names match a joined user's username, first name or full name, ignoring case; `me` is you. If a
name matches nobody or more than one person, the card asks "Who did you mean by 'Jon'?" with
buttons — it never guesses.

Shares are computed in the currency the expense was entered in, then converted to the other
currency share by share and reconciled with the largest-remainder method, so shares always add
up exactly to the expense amount in both currencies.

## Settlement logic

Balances and settling up are in the home currency only, each expense converted at its own
day's rate (see `docs/adr/0001`). For each person: **paid** is the total of expenses they paid,
**share** the total of their shares, and **net** = paid − share (positive means they're owed).
Cancelled expenses don't count.

- `/balances` — paid, share and net for everyone with any activity, most owed first.
- `/mine` — your own three numbers and your last 3 expenses with your share of each.
- `/settle` — who pays whom, one line per transfer, e.g. `Amir → Sufyan S$31.20`, or
  "All square 🎉".

`/settle` is greedy: it repeatedly has the person who owes most pay the person owed most, as
much as one of them needs. That takes at most one transfer fewer than the number of people with a
balance. It is near-minimal, not guaranteed minimal.

## LLM API setup

Receipt reading uses the Anthropic API (`ANTHROPIC_API_KEY` in your `.env`, already covered in
Setup above). In the [Anthropic Console](https://console.anthropic.com/), set a monthly spend
limit on the API key so a bug or a flood of photos can't run up a surprise bill — a few dollars
is plenty for a trip's worth of receipts. Each receipt read costs a fraction of a cent to a
couple of cents; you can watch usage accrue under Usage in the Console.

## Privacy

Receipt photos are downloaded, resized, and sent to the Anthropic API entirely in memory —
they are never written to disk and never logged (not the image bytes, not the base64, not the
LLM request). Once the read finishes, the bot keeps only the Telegram file ID (so "Try again"
can re-download the same photo) and the structured facts it extracted: merchant, total, date,
and line items. Those facts, not the photo, are what end up in the Sheet.

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

Merging to `main` deploys automatically: `.github/workflows/deploy.yml` runs ruff and the
tests, then the same deploy as `./scripts/deploy.sh` (remote build, `--ha=false`). Watch it in
the repo's **Actions** tab; **Run workflow** there redeploys `main` by hand. Confirm `/help` in
the test group shows the new version. `./scripts/deploy.sh` from a laptop still works too.

The workflow needs one repo secret, `FLY_API_TOKEN`: a deploy token for this app only. Create
it in the Fly dashboard (the app → **Tokens** → create a deploy token, or `fly tokens create
deploy` on a laptop) and save it in GitHub under Settings → Secrets and variables → Actions →
New repository secret. If the token leaks, revoke it in the same Fly page and make a new one.

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
