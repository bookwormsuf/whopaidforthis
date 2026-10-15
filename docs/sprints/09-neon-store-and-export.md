# Sprint 9 — Neon store and export

## Goal
A group Sufyan is not in can start a trip with buttons alone, record and settle a whole trip,
download its records as a spreadsheet file, and erase them. Google Sheets is gone from the
store. Decided with Sufyan on 2026-10-15 after the post-trip review; see ADR 0003 and spec §18.

## In scope
`PostgresLedgerStore` on Neon replacing `SheetsLedgerStore`; schema and migrations; `/settrip`
without a Sheet link; closing a trip; `/deletetrip`; `/export` to `.xlsx`; removal of the
control Sheet, Bindings, tab layout, Summary formulas, Read Me tab, `TripSheetInaccessible`,
and the service-account env var; README and `.env.example` updated; CI runs tests against a
local Postgres.

## Out of scope
A `/start` menu with buttons (Sprint 10). Export to a Google Sheet, drafts that survive a
restart (Sprint 11, if still wanted). Importing old Sheets trips (decided: never; the Japan trip
is settled and its Sheet is its archive). Webhooks instead of polling. WhatsApp.

## Sprint pins (decided with Sufyan, 2026-10-15)

### Store
- One store: `PostgresLedgerStore` behind the existing `LedgerStore` interface. Driver `asyncpg`
  with a small pool (min 1, max 5). Connection string from env var `DATABASE_URL` (Neon's
  pooled string, `sslmode=require`). The agent never sees or writes the value; Sufyan sets it with
  `fly secrets set` and in his local `.env`.
- Migrations are numbered SQL files in `app/db/migrations/NNN_*.sql`, applied in order at startup
  inside a transaction, tracked in a `schema_migrations(version int primary key, applied_at)`
  table. No ORM, no Alembic.
- Startup fails fast with a one-line error if `DATABASE_URL` is missing or the database is
  unreachable, same as the token checks today.
- Every `asyncpg` error is mapped to `LedgerUnavailable` at the store boundary. Retries: 3 with
  1/2/4 s backoff on connection errors only (Neon waking from idle can take up to a second or
  two). Writes are never retried blindly; a unique-constraint violation on insert means
  "already saved" and is returned as success.

### Schema (money is always `bigint` minor units; IDs are Telegram `bigint`; ULIDs are `text`)
- `trips`: `id bigserial pk`, `chat_id bigint not null`, `name text`, `trip_currency char(3)`,
  `home_currency char(3)`, `timezone text`, `created_by bigint`, `created_at timestamptz`,
  `closed_at timestamptz null`. Partial unique index on `chat_id where closed_at is null`: one
  open trip per chat.
- `trip_users`: `trip_id fk`, `user_id bigint`, `username text`, `display_name text`,
  `joined bool`, `first_seen_at`, `last_seen_at`; pk `(trip_id, user_id)`.
- `expenses`: `id text pk` (the draft ULID), `trip_id fk`, `chat_id bigint`,
  `message_id bigint`, `created_by bigint`, `payer_id bigint`, `description text`,
  `merchant text null`, `category text`, `original_amount_minor bigint`,
  `original_currency char(3)`, `trip_amount_minor bigint`, `home_amount_minor bigint`,
  `rate numeric(18,8)`, `rate_date date`, `transaction_date date`, `source_type text`,
  `split_method text`, `status text`, `created_at timestamptz`, `cancelled_at timestamptz null`,
  `notes text`. Unique `(chat_id, message_id)`. Index `(trip_id, status, transaction_date)`.
- `splits`: `expense_id fk`, `user_id bigint`, `share_trip_minor bigint`, `share_home_minor bigint`,
  `split_method text`; pk `(expense_id, user_id)`.
- `line_items`: `expense_id fk`, `position int`, `description text`, `quantity int`,
  `amount_trip_minor bigint`, `kind text`, `allocation_method text`,
  `assigned_user_ids bigint[]`; pk `(expense_id, position)`.
- `exchange_rates`: `base char(3)`, `quote char(3)`, `requested_date date`, `rate_date date`,
  `rate numeric(18,8)`, `provider text`, `retrieved_at timestamptz`; pk
  `(base, quote, requested_date)`. Global, not per trip.
- Enumerations keep the spec §10 vocabularies: `source_type ∈ text, receipt`;
  `status ∈ confirmed, cancelled`; `kind ∈ item, tax, service_charge, discount`;
  `allocation_method ∈ claimed, everyone, proportional, unassigned`. Enforced with `check`
  constraints.

### Writes
- Saving an expense is one transaction: insert `expenses`, `splits`, `line_items`. A unique
  violation on `expenses.id` or `(chat_id, message_id)` rolls back and returns "already saved".
  `confirm` in `DraftService` no longer calls `expense_exists` first; the store's insert is the
  idempotency check. The in-memory `Dedup` class is deleted.
- `/undo` is an update of `status`, `cancelled_at`, `notes` on one row, as today.
- `upsert_user` is `insert ... on conflict (trip_id, user_id) do update`.

### Trips
- `/settrip` in a group: no arguments, no Sheet link. Buttons only, in this order: trip
  currency, home currency, timezone (default from the trip currency, as today). The currency
  keyboard shows, in this order, SGD, MYR, JPY, CNY, then an `Other…` button that asks for a
  three-letter code by reply. Private chat `/settrip` keeps today's "use this in a group" reply.
- A group with an open trip that runs `/settrip` again is asked "Close the current trip and start
  a new one?" with `Start new trip` | `Keep current`. Starting a new one sets `closed_at` on the
  old trip. Nothing is deleted; the closed trip remains exportable.
- `/settle` ends with a `Close this trip` button (initiator of the trip or the presser of
  `/settle`). A closed trip answers every expense message and command except `/export`,
  `/settrip`, `/help`, `/start` with one line: "This trip is closed. Run /settrip to start a new
  one." The unbound-group gate keeps its one-line "Run /settrip first" reply.
- `/deletetrip`: only the user who ran `/settrip` for the open trip may use it. Asks "Delete this
  trip and every expense in it? This cannot be undone." with `Delete everything` | `Keep`. Hard
  deletes the trip and its rows in one transaction (`on delete cascade`). Closed trips of the
  same chat are deleted too. Reply: "Deleted. Run /settrip to start a new trip."
- Trip name: the group's title at `/settrip` time, as today.
- Amended 2026-10-15: `/start` in a group with a closed trip gets the closed-trip line like any
  other command (it is `/join`); `/deletetrip` is exempt from the closed-trip gate so a closed
  latest trip can be deleted.

### Export
- `/export` in a group with any trip (open or closed) sends one `.xlsx` built with `openpyxl`,
  named `whopaid-<trip name slug>-<YYYY-MM-DD>.xlsx`, for the open trip; if the only trips are
  closed, the most recently closed. Sheets, in order: `Expenses`, `Shares`, `Line items`,
  `People`, `Balances`. Columns follow spec §10's tab columns with amounts in major units and a
  number format per currency; `Balances` is the output of `compute_balances` (paid, share total,
  net) plus the `settle()` transfers underneath. Cancelled expenses are included with their
  status. Anyone in the group may export.
- Export is deterministic code, no model call. It is built in memory and sent with
  `send_document`; nothing is written to disk.

### Removal
- Delete `app/services/google_sheets.py`, `sheet_layout.py`, `summary_formulas.py`,
  `sheet_rows.py`, the Bindings/control-Sheet code, `TripSheetInaccessible`, `can_open`,
  `initialise_trip_sheet`, and `gspread` from dependencies. Remove `GOOGLE_SERVICE_ACCOUNT_JSON`
  and `WHOPAID_CONTROL_SHEET_ID` from `Settings`, `.env.example`, `scripts/` and the README.
  `Trip.sheet_id` goes; `Trip.id` and `Trip.closed_at` arrive.
- The README's "how to set up" loses the Google section and gains: Neon project in Singapore,
  copy the pooled connection string, `fly secrets set DATABASE_URL=...`. A "What is stored and
  how to delete it" section states: Telegram user IDs, usernames, display names, expense
  descriptions and amounts; `/deletetrip` erases all of it; receipt images are never stored.

### Tests
- Store tests run against a real Postgres: `docker compose` service `postgres:16` locally and a
  `services:` block in CI. `InMemoryLedgerStore` stays for handler tests. A conftest fixture
  creates a fresh database per test session and truncates between tests.

## Before you start (you)
1. Create a Neon project in region AWS Singapore. Copy the pooled connection string.
2. Put it in your local `.env` as `DATABASE_URL=...` (the agent hands you the command, you run it).
3. `fly secrets set DATABASE_URL=...` for `whopaid-bot`. Do not unset the Google secrets yet; the
   old build still needs them until this sprint deploys.
4. Install Docker Desktop or Colima so the store tests can run.

## Agent builds
Migrations and store; `DraftService` without `Dedup` and `expense_exists`; `/settrip` buttons;
close and delete flows; `/export`; removal list; README, `.env.example`, `fly.toml` unchanged
except env; CI Postgres service; tests. Each piece its own commit; stacked PRs if large.

## Tests required
Store round-trips for every `LedgerStore` method; one expense per message (second insert with
the same `(chat_id, message_id)` returns "already saved" and leaves one row); transaction
rollback when a split insert fails leaves no expense row; one open trip per chat; `/settrip` on
an open trip closes it and starts another; closed trip rejects an expense with the pinned line;
`/deletetrip` by a non-starter is refused, by the starter removes every row; `/export` produces
the five sheets with the right headers and the balances match `/balances`; startup fails fast on
a missing `DATABASE_URL`; `asyncpg` connection error becomes `LedgerUnavailable`; the Hypothesis
invariants from §17 still pass.

## After: your checklist
1. In a fresh test group: `/settrip` with buttons only (SGD, MYR, JPY, CNY shown first), no Sheet
   asked for.
2. A text expense and a receipt save; rows visible in the Neon table browser.
3. `/balances`, `/settle`, `Close this trip`; an expense after closing gets the one-line reply.
4. `/settrip` again starts a new trip; `/export` of the closed trip opens on your phone with five
   sheets and balances matching what `/balances` showed.
5. `/deletetrip` from a second account is refused; from yours deletes everything.
6. Stop the bot for 20 minutes, send an expense: it saves (Neon woke up).
7. After deploy, `fly secrets unset GOOGLE_SERVICE_ACCOUNT_JSON WHOPAID_CONTROL_SHEET_ID`.

## Exit criteria
- [ ] Checklist passes
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
