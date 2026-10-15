# Sprint 10 — `/start` menu with buttons

## Goal
A group that has never seen the bot can run a whole trip without typing a command name. The
bot posts a welcome with buttons when it is added to a group, pins it, and every command is one
tap away. Decided with Sufyan on 2026-10-15 (grilled after Sprint 9 sign-off); see spec §19.

## In scope
Welcome message with a state-aware button menu; posting it on being added to a group, on
`/start` and on `/help`; pinning it; two-page menu; menu presses that run the existing commands;
an "Add me to a group" button in private chat; tests; checklist.

## Out of scope
Telegram's own command menu (`setMyCommands`): declined, buttons under messages only. A
step-by-step "add expense" wizard: expenses stay typed or photographed. A persistent keyboard at
the bottom of the chat. Editing old menus when the trip's state changes. Tracking pinned
message IDs in the database.

## Sprint pins (decided with Sufyan, 2026-10-15)

### When the welcome is posted (amended 2026-10-15 after review)
- The welcome is posted on being added to a group (a `new_chat_members` service update whose
  members include the bot's own user ID) and on `/start` in a group. `/help` in a group posts
  the help text with the menu under it, not the welcome, and is never pinned. In private chat
  `/start` and `/help` post the help text with one URL button `Add me to a group` pointing at
  `https://t.me/<bot_username>?startgroup=true`, the username taken from `getMe` as today
  (spec §1).
- `/start` in a group no longer joins the sender. Joining is the `Join` button or `/join`.
  Spec §12's "`/start` in group = `/join`" line is superseded by §19.
- Typed commands all keep working unchanged (amended 2026-10-15 after review). `/help` keeps
  its command list, moved below the explanation paragraph; in a group the menu sits under the
  message too, re-reading state the same way the welcome does.

### Welcome text (verbatim, HTML)
```
<b>whopaid</b> records this group's trip expenses.
Type what you spent, like <code>1200 yen ramen</code>, or send a photo of a receipt. I convert
it to your home currency at the day's rate, split it between whoever was there, and track who
owes whom.

Tap a button below. Typed commands work too, see /help.
```
When the trip is open the second paragraph is followed by one line naming the trip:
`Trip: <trip name> (<trip currency> → <home currency>)`. When the latest trip is closed:
`The last trip is closed. Start a new one or export it.` When there is no trip: `No trip yet.`

### Pinning
- After posting the welcome in a group the bot calls
  `bot.pin_chat_message(chat_id, message_id, disable_notification=True)`. On
  `telegram.error.BadRequest` or `telegram.error.Forbidden` (no pin right) it logs at INFO and
  edits the welcome to append one line: `Pin this message so everyone can find the buttons.`
  It never asks to be made admin.
- When the pin succeeds nothing is appended. Several pins over time are acceptable; Telegram
  shows the newest. No unpin bookkeeping and no schema change.

### Menu shape (inline keyboard under the welcome)
- Callback data: `menu:<action>` for actions, `menu:page:<1|2>` for paging. Actions:
  `join`, `balances`, `settle`, `recent`, `today`, `summary`, `mine`, `undo`, `export`,
  `howto`, `deletetrip`, `settrip`. Registered as one `CallbackQueryHandler(pattern=r"^menu:")`.
- State is computed with `resolve_trip_status` at render time and again at press time.
- No trip (one page): `Start a trip`.
- Trip open, page 1: row `Join`; row `Balances` `Settle`; row `Recent` `Today` `Summary`;
  row `More ▸`.
  Page 2: row `Mine` `Undo last`; row `Export`; row `How to add an expense`; row `Delete trip`;
  row `◂ Back`.
- Latest trip closed (one page): row `Start a new trip`; row `Export`; row `Delete trip`.
- `More ▸` and `◂ Back` call `edit_message_reply_markup` on the same message; the text does not
  change. Everything else posts a new plain message in the chat (not a reply, no quote) and
  leaves the menu untouched. Every press calls `query.answer()` first.

### What a press does
- A press is exactly the typed command by the presser: same handler, same gates, same reply
  text, same permission checks (`Delete trip` refuses anyone but the trip starter; `Undo last`
  and `Mine` are the presser's). Implementation: the command handlers are refactored so each
  exposes a coroutine `run_<name>(deps, chat, user, send)` where `send(text, reply_markup=None)`
  posts to the chat; `CommandHandler` and the menu dispatcher both call it. No command logic is
  duplicated.
- Amended 2026-10-15 (checklist step 3): when someone taps `Everyone's in` on the "Connected"
  message, the bot posts a fresh welcome with the full open-trip menu and pins it. Not on
  "Connected" itself: that message's own buttons are the one call to action until joining is
  closed. The earlier `Start a trip` welcome is left untouched.
- `Start a trip` / `Start a new trip` runs the `/settrip` flow unchanged (currency, currency,
  timezone buttons; the "close the current trip?" confirm when one is open). The presser becomes
  the trip starter.
- `How to add an expense` posts, verbatim:
  ```
  Type an amount and what it was for:
  <code>1200 yen ramen</code>
  <code>45 sgd taxi split everyone</code>
  <code>3200 yen dinner paid by Amir split between me Amir</code>
  Or send a photo of the receipt and tap the items you had.
  ```
- Stale menus: the state is re-read at press time. A press on a closed trip answers with the
  closed-trip line; on no trip with "Run /settrip first"; on a store error with
  `LEDGER_UNAVAILABLE_TEXT`. The menu is never edited to match.

### Group-added event
- Handler on `filters.StatusUpdate.NEW_CHAT_MEMBERS | CHAT_CREATED` that posts the welcome when
  the bot itself is among the new members, or when the group was created with the bot already in
  it (Telegram sends `group_chat_created` then, not `new_chat_members`; found on checklist step 1,
  2026-10-15). Other joins are ignored. Posting failures (bot removed again,
  no send permission) are logged and swallowed.

## Agent builds
`run_<name>` refactor of the existing command handlers; `app/bot/handlers/menu.py` with the
welcome builder, keyboard builder, the `menu:` callback and the added-to-group handler; `/start`
and `/help` rewired; private-chat URL button; pin with fallback line; tests. Each piece its own
commit.

## Tests required
Keyboard builder for each of the three states (button labels, rows, callback data, paging);
`More ▸`/`◂ Back` edit the keyboard and not the text; each action dispatches to the same
coroutine the typed command uses (one test per action with `InMemoryLedgerStore`); press by a
non-starter on `Delete trip` is refused; press on a closed trip gets the closed-trip line; press
on no trip gets the unbound line; `/start` in a group no longer joins; private `/start` carries
the `startgroup` URL built from the live username; added-to-group with the bot in the member
list posts the welcome, with another user does not; pin failure appends the pin line, pin
success does not; every existing command test still passes.

## After: your checklist
1. Add the bot to a fresh group as a plain member: welcome appears with `Start a trip`, plus the
   "Pin this message" line. Pin it yourself.
2. Make the bot an admin in another fresh group with pin rights: the welcome is pinned, no line.
3. Tap `Start a trip`, pick SGD / MYR / Asia/Singapore with buttons only.
4. `/start` again: menu now shows page 1. Tap `More ▸` then `◂ Back`; the text never changes.
5. Type one expense, then tap `Recent`, `Today`, `Summary`, `Balances`, `Mine`: each posts the
   same text as the typed command.
6. Tap `Settle`, then `Close this trip`. Tap `Balances` on the old menu: closed-trip line.
7. `/start`: menu shows `Start a new trip` / `Export` / `Delete trip`. Tap `Export`: file arrives.
8. From a second account tap `Delete trip`: refused. From yours: deleted.
9. Message the bot privately: `/start` shows the `Add me to a group` button and it opens the
   group picker.

## Exit criteria
- [ ] Checklist passes
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
