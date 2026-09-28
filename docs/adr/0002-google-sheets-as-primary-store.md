# ADR 0002: Google Sheets is the primary datastore; commit order stands in for transactions

Date: 2026-09-29. Status: accepted.

## Context
A trip produces a few hundred rows. The users want to read and audit the ledger in a spreadsheet.
Sheets has no transactions, ~1 s latency per call, and no unique constraints.

## Decision
Sheets is the only persistent store. Reliability comes from three rules rather than a database:
(1) the Expenses row is appended last in the same batch request as its Splits and Line Items, and
every reader joins through Expenses, so a partial write is invisible; (2) duplicate protection is an
in-memory set of (Chat ID, Message ID) seeded from the sheet at startup; (3) drafts never touch the
sheet. Access goes through a `LedgerStore` interface so SQLite can be added without touching handlers.

## Consequences
- Zero infrastructure beyond the bot process. The spreadsheet is the backup.
- Two bot instances running at once would defeat the dedup set. Run exactly one machine.
- Reads for `/balances` fetch whole tabs; fine at trip scale, would not scale to thousands of rows.
