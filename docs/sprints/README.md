# Sprints

The initiative was cut into eight sprints; Sprints 9 to 11 were added on 2026-10-15 after the first trip. Each has a sub-spec in this folder. The master spec
(`../spec/pinned-decisions.md`) is the source of truth; a sub-spec only adds what is specific to its
sprint and points at the master spec sections it implements.

| # | Sprint | You can test | Needs a 2nd person |
|---|---|---|---|
| 0 | [Accounts, keys, skeleton](00-accounts-keys-skeleton.md) | Bot answers `/help` from your laptop | No |
| 1 | [Trip binding and users](01-trip-binding-and-users.md) | `/settrip` builds the whole Sheet, `/join` fills Users | No |
| 2 | [Text expenses, just me](02-text-expenses-just-me.md) | "1200 yen ramen" becomes a Sheet row with the day's rate | No |
| 3 | [First deployment](03-first-deployment.md) | Bot works with the laptop closed | No |
| 4 | [Receipt photos, total only](04-receipt-photos-total-only.md) | A photo becomes an expense | No |
| 5 | [Splitting and balances](05-splitting-and-balances.md) | Everyone / Choose people, equal, custom, `/balances`, `/settle` | Yes, ~10 min |
| 6 | [Item claiming](06-item-claiming.md) | Tap items on a receipt, tax spread proportionally | Yes, ~10 min |
| 7 | [Hardening and go-live](07-hardening-and-go-live.md) | Real group, real Sheet, friends joined | Yes, everyone |
| 8 | [Clearer messages](08-clearer-messages.md) | Spent, paid and owed read at a glance; item card in three kinds | Yes, ~5 min |
| 9 | [Neon store and export](09-neon-store-and-export.md) | `/settrip` with buttons only, no Google; `/export` opens on your phone; `/deletetrip` | Yes, ~5 min (delete refusal) |
| 10 | [`/start` menu with buttons](10-start-menu.md) | Add the bot to a group, run a trip by tapping | Yes, ~2 min (delete refusal) |
| 11 | Dropped 2026-10-15: Google Sheet export, persistent drafts and older-trip export were judged not core. Revisit only on request. | | |
| 12 | [Receipt read budget](12-receipt-read-budget.md) | Daily cap on receipt reads before going public | Yes, ~5 min (cap at 7 locally) |
| 13 | [Guided item split](13-guided-item-split.md) | "Who had this?" one item at a time; shared dishes by ticking two names; no typing | Yes, ~2 min (non-initiator tap) |
| 14 | [Not on this bill](14-not-on-this-bill.md) | Leave out dishes someone else paid for on a group order; amount is "you paid" | No |

## How a sprint runs

1. Open a Claude Code session in this repo and say "run sprint N".
2. The agent reads `CLAUDE.md`, `CONTEXT.md`, the master spec, this sprint's sub-spec, and `STATUS.md`.
3. It does any "before" checks, builds the "agent builds" list, runs `uv run pytest` and `uv run ruff check .`,
   then runs `/review` against the sprint's changes and fixes what the review finds.
4. It appends a work-log entry to `STATUS.md` and hands you the "after" checklist.
5. You do the checklist in Telegram. Every item ticked → you say "sign off sprint N". The agent records
   your sign-off in `STATUS.md`. Only then does the next sprint start.

A sprint is **done** only when all three hold: tests pass, you ticked every checklist item, and
`STATUS.md` has your sign-off entry. An agent saying "done" is not done.

**Decision 2026-10-01: Sprints 5 and 6 run without a second tester.** No second person is
available before the trip. For those two sprints the agent writes automated multi-user tests
(two or more fake Telegram users pressing buttons, claiming items, and settling) in place of the
two-person checklist steps. Sufyan still does every single-person step against the deployed bot.
The deferred two-person checks are carried into Sprint 7 and done with the real group on day one,
so Sprint 7's checklist must list them explicitly.

## Sub-spec shape

Goal · In scope · Out of scope · Sprint pins · Before you start (you) · Agent builds · Tests required ·
After: your checklist · Exit criteria.
