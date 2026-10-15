# ADR 0003: Postgres on Neon is the only store; Google Sheets becomes an export target

Date: 2026-10-15. Status: accepted. Supersedes ADR 0002.

## Context
ADR 0002 chose Google Sheets so friends could read and audit the ledger in a spreadsheet. After
the first real trip (Japan, October 2026) two things changed. First, every command that people
actually used (`/summary`, `/today`, `/recent`, `/mine`, `/balances`, `/settle`) computes in
Python from rows; the Summary tab was the only Sheet-only view. Second, the bot should serve
groups Sufyan is not in. The Sheets store makes that hard: a new group has to create a Sheet
and share it with a service-account email, the bot's dedup set lives in one process's memory,
`appendCells` is not idempotent so a retried save can double-write, and reads fetch whole tabs.

Alternatives weighed: keep Sheets and add a setup script (still asks every group for Google
setup); two storage modes or two bots (two code paths forever for a minority); SQLite on a Fly
volume (no browser view of the data, hand-rolled backups); Supabase (pauses after 7 idle days
on the free plan and needs a human to resume it, which fails exactly when a group starts a trip
months after the last one). Neon scales to zero and wakes itself on the next query, keeps a
6-hour restore window on the free plan, has a Singapore region, a table browser, and is plain
Postgres over a pooled connection string.

## Decision
Postgres on Neon is the only persistent store. There is no Sheets store, no control Sheet, no
service-account share step in `/settrip`. Reliability comes from the database: every multi-row
write is one transaction; one expense per Telegram message and one write per draft are unique
constraints, not an in-memory set or a pre-retry read. The `LedgerStore` interface stays and is
implemented once, by `PostgresLedgerStore`. Google Sheets survives only as a future export
target: `/export` first produces an `.xlsx` file in the chat; a "create a Google Sheet" export
may follow and reuses the same rows.

## Consequences
- Anyone can start a trip with `/settrip` and buttons; nobody touches Google Cloud.
- The bot must stay up for groups Sufyan is not in: one Fly machine always on, no more
  `fly scale count 0` after a trip. Roughly US$2 to 3 a month; Neon is free at this size.
- Sufyan holds other groups' spending records. The person who ran `/settrip` can hard-delete
  the trip with `/deletetrip`; the README states what is stored and how to delete it.
- The write-order rule and the in-memory dedup set from ADR 0002 are retired, as is the
  "exactly one machine or dedup breaks" consequence.
- Exchange rates are shared across trips, since a rate is a fact about a currency pair and a
  date, not about a trip.
- Tests run against a real local Postgres in Docker, not a stand-in.
