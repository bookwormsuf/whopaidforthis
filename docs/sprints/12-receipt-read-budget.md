# Sprint 12 — Receipt read budget

## Goal
Before the repo and bot go public, bound what receipt reading can cost. A chat that sends too
many receipt photos in a day, or all chats together, is told to type amounts instead. Decided
with Sufyan on 2026-10-16; see spec §20.

## In scope
A daily cap on receipt reads per chat and across all chats; the refusal replies; a one-time
warning at five reads left; two settings; tests.

## Out of scope
Any cap on text expenses or other commands. Persisting counters. Per-user caps. A user-visible
counter. Changing the reader's own internal retry.

## Sprint pins (decided with Sufyan, 2026-10-16)

### Definitions
- A **receipt read** is one attempt to have the model read a receipt photo: a new photo message
  (each photo of an album is its own attempt) or a `Try again` press. The reader's internal
  retry on API failure is part of the same attempt and is not counted separately.
- An allowed attempt counts whether or not the read succeeds. A refused attempt never counts.

### Limits
- Per chat: 40 receipt reads per day. Across all chats: 400 per day.
- A day is a calendar day in `Asia/Singapore`, fixed, not the trip's timezone. Counters reset
  at midnight Singapore time, not on a rolling window.
- Counters live in memory. A restart resets them to zero; accepted.
- Settings: `RECEIPT_READS_PER_CHAT_PER_DAY` (default 40) and `RECEIPT_READS_PER_DAY` (default
  400), both `int >= 0` in `Settings`. Documented in `.env.example` with their defaults; nothing
  needs to be set to run. `0` means every read is refused.

### Where the check runs
- In `read_receipt_photo` callers, after the trip gate passes and **before** the photo is
  downloaded, for both the photo handler and the `Try again` callback. Order: global cap first,
  then per-chat cap. When both are hit the global reply is sent.
- The budget is `ReceiptReadBudget` in `app/services/receipt_budget.py`: pure Python, no
  Telegram imports, holds the Singapore date it belongs to, a global count and a
  `{chat_id: count}` map; `try_take(chat_id, now) -> Allowed | ChatCapped | GlobalCapped` and
  `remaining(chat_id, now) -> int`. `now` is an aware datetime injected by the caller; the class
  converts to `Asia/Singapore` and discards both counts when the date changes. Attached to `Deps`
  with a `default_factory`, like `SettripSessionStore`.

### Replies (verbatim)
- Per-chat cap hit, on each refused attempt:
  "This chat has hit today's limit for receipt photos. Type the amount instead, like 1200 yen ramen."
- Global cap hit, on each refused attempt:
  "Today's limit for receipt photos across all groups has been reached. Type the amount instead, like 1200 yen ramen."
- Warning, sent once per chat per day as its own message right after the bot's reply to the
  attempt that leaves exactly five remaining (the draft card, or the error reply if that
  attempt failed):
  "Heads up: 5 receipt photos left in this chat today. Text expenses are unlimited."
  No warning for the global cap. Not sent when the per-chat cap is 5 or less.
- A refused `Try again` posts the refusal as a new message and leaves the card and its buttons
  untouched, so `Enter amount manually` still works.
- Each refusal logs one `warning` line with the chat id and which cap (`chat` or `global`).
  Nothing else is logged or shown.

## Agent builds
`ReceiptReadBudget` and tests; settings and `.env.example`; the gate in the photo handler and
the retry callback; the warning; handler tests; CONTEXT.md glossary lines; README one-liner under
Limitations. One PR.

## Tests required
Budget class: allows up to the chat cap then returns `ChatCapped`; global cap wins over chat
cap; counts reset when the Singapore date rolls over (test with a clock at 23:59 and 00:01
Singapore, which is 15:59 and 16:01 UTC); refused attempts do not count; `remaining` is right
at 0, mid-way and after reset; caps of 0 refuse everything. Handlers: photo refused before
download when capped (the download function is never called); `Try again` refused leaves the
card unchanged; warning sent exactly once when five remain and not again on the next read; no
warning when cap <= 5; a failed read still counts.

## After: your checklist
1. Set `RECEIPT_READS_PER_CHAT_PER_DAY=7` locally, send 2 photos, see the warning after the
   second, send 5 more, the eighth gets the per-chat reply and `Try again` on an earlier card
   gets it too.
2. Text expense still saves.
3. On Fly with defaults, one photo reads normally and `fly logs` shows no warning.

## Exit criteria
- [x] Checklist passes (on Sufyan's word, 2026-10-16)
- [x] Tests, ruff green; `/review` findings addressed (513 tests, 2026-10-16)
- [x] `STATUS.md` entry with your sign-off (2026-10-16)
