# Sprint 17 — One-off bill

## Goal
Someone with one bill to split (a dinner, a cab) can do it in a private chat with the bot, with
no group, no trip and no `/join`. They get a summary to forward. Nothing is saved. Decided with
Sufyan on 2026-11-12.

## In scope
- A one-off bill in a private chat: typed amount, 2 to 20 people, optional names, who paid,
  equal or typed amounts, a forwardable summary.
- `/split`, and a `Split a bill now` button on the private `/start` and `/help`.
- New private `/start` text: the trip first, the one-off bill second.
- Spec §22; CONTEXT.md (*one-off bill*, *person*); manual checklist; tests.

## Out of scope
- Receipt photos in a private chat, the item-by-item split and the per-person daily photo limit
  (Sprint 18).
- Saving a one-off bill, listing past ones, editing one after `Done`, `/undo`.
- More than one bill at a time, or several bills settled together (that is a trip).
- Currency conversion and exchange rates.
- Telling the other people: the user forwards the summary.
- Any change to the group flow, `DraftService`, the database or the ledger.
- README (updated once, after this sprint).

## Sprint pins (decided with Sufyan, 2026-11-12)

### Words
- **One-off bill**: a single bill split in a private chat. Never saved.
- **Person**: a named slot on a one-off bill. A label only. Not a trip user: no Telegram
  account, cannot be tagged or messaged, never written to the database.
- "Never saved" means: the bill lives in process memory while it is being built, and is
  removed when the summary is sent, on `Cancel`, after 30 minutes without an accepted action,
  or on restart. The summary message in the user's Telegram chat is the only record.

### Where it runs
- Private chats only. One bill per Telegram user at a time, keyed on the user's ID.
- `/split` in a group replies ``/split works in a private chat with me. In this group, type
  the expense, like <code>1200 yen ramen</code>.`` and does nothing else. It needs no trip.
- Plain text and photos in a private chat are read only while a one-off bill (or Sprint 16's
  feedback wait) is waiting for them. Otherwise, with no bill open, they get the private
  `/start` intro and its two buttons (amended 2026-11-13; was: no reply). With a bill open, a message
  the current step does not expect still gets no reply.
- If both a feedback wait and a bill exist, the next text is the feedback; the bill is kept.
  `/split` drops a feedback wait, like any other command.

### Private `/start` and `/help`
- `/start` in a private chat replies (HTML):
  ```
  <b>Who Paid For This</b> tracks who paid for what on a group trip, then works out who owes whom. Add me to your trip group to start.

  Just one bill to split? You can do that right here.

  Who Paid For This <version>
  ```
  Buttons, one per row, in this order: `Add me to a group for a trip` (the existing
  `?startgroup=true` URL button, relabelled), then `Split a bill now` (callback `oneoff:start`).
- `/help` in a private chat keeps the command list and carries the same two buttons.
- The command list (`HELP_TEXT`) gains `/split — split one bill in a private chat, no group
  needed`, placed directly before `/feedback`.
- Group `/start`, the group welcome and the group menu are unchanged.

### People
- People are numbered 1 to N. Person 1 is the user, shown with their Telegram first name.
  The others default to `Person 2`, `Person 3`, … These integers are the IDs passed to the
  split functions.
- On cards while building, the user is `<first name> (you)` in lists and `Me` on buttons.
  On the summary the user is `<first name>` only, because the summary is read by other people.

### The flow
One card (one bot message) is edited in place from step 2 to step 8, as the group draft card is.
Every card from step 2 on starts with the title line `🧾 <b><description></b> · <amount>`,
then a blank line. Amounts use `fmt_money`. Names and descriptions are HTML-escaped.

1. **Start** (`Split a bill now` or `/split`). The bot sends
   ``Send the bill, like <code>84.50 dinner</code>.``
   If the user already has an unfinished bill, the bot instead asks `Drop the bill you
   started?` with `[Yes, start again]` `[No, keep it]`. Yes: the old card becomes `Cancelled.`
   and the flow starts at step 1. No: the question message becomes `OK. Carry on with the bill
   above.` and nothing else changes.
2. **The bill line.** Parsed with `parse_expense(text, "SGD", "SGD", bot_username)`.
   - No amount, or an amount of zero or less: ``I couldn't find an amount. Send it like
     <code>84.50 dinner</code>.`` The bot keeps waiting.
   - The currency is what was typed (`1200 yen ramen` is ¥1,200); with none typed it is SGD.
     No rate is fetched and nothing is converted.
   - `paid by …`, `split …` clauses and @tags in the line are ignored.
   - A description that comes back as the parser's default `Expense` is shown as `Bill`.
   - A photo at this step gets ``Photos aren't read here yet. Type the amount, like
     <code>84.50 dinner</code>.``
   The card:
   ```
   🧾 <b>Dinner</b> · S$84.50

   How many people, including you?
   ```
   and, only when the currency is SGD, a last line in italics:
   ``<i>Not SGD? Send it again with the currency, like 1200 yen ramen.</i>``
   Buttons: `[2] [3] [4] [5] [6]` in one row, then `[Cancel]`.
3. **How many.** A button, or a typed whole number from 2 to 20. A number from 2 to 20
   followed only by `people`, `persons`, `person`, `pax` or `ppl` is that count too, not a new
   bill (amended at review, 2026-11-12: `3 people` had been read as a S$3.00 bill). Any other typed number:
   `Type a number from 2 to 20.` Typed text that is not a number but parses as a bill line
   replaces the bill: the old card becomes `Replaced by the bill below.` with no buttons and a
   new card is sent at step 2. Any other text: `Type a number from 2 to 20.`
4. **Names.**
   ```
   🧾 <b>Dinner</b> · S$84.50

   Sufyan (you), Person 2, Person 3

   Send names to rename them, like <code>Ali, Mei</code>, or tap Next.
   ```
   Buttons: `[Next]` `[Cancel]`.
   - Typed names are split on commas and line breaks, trimmed, and given in order to
     Person 2 onwards. Fewer names than people: the rest keep their defaults. The user's own
     name cannot be changed. The card is re-rendered with the new names and stays on this step.
   - More names than other people: `That's more names than people. There are <k> others on
     this bill.` (`is 1 other` when k is 1). Nothing is renamed.
   - A name over 30 characters: `Keep each name under 30 characters.` Nothing is renamed.
   - Two people with the same name, ignoring case, including the user's first name:
     `Two people can't share a name. Change one.` Nothing is renamed.
5. **Who paid.** Body `Who paid?`. One button per person, the user first as `Me`, three per
   row, then `[Cancel]`.
6. **Split method.** Body `How should it be split?`. Buttons, one per row as on the group
   card: `[Split equally]`, `[Type amounts instead]`, `[Cancel]`.
   - Split equally: `split_equal(total_minor, [1..N], payer_id)`, unchanged. Leftover minor
     units go to the lowest-numbered people, the payer last. Go to step 8.
   - Type amounts instead: go to step 7.
7. **Typed amounts.**
   ```
   🧾 <b>Dinner</b> · S$84.50

   Send one line per person, e.g. <code>Ali 20</code>
   • Sufyan (you) –
   • Ali S$20.00
   • Mei –
   Total S$20.00 of S$84.50
   Remaining: S$64.50 ❌
   ```
   - The example uses person 2's current name.
   - A line is `<name> <amount>`. The name is the longest person name (ignoring case), or
     `me`, `i`, `myself` for the user, that the line starts with; the rest of the line is the
     amount, so `Person 2 20`, `Mei 500 yen` and `Ali S$20` all read. If the line starts with no
     known name, the last word is taken as the amount and the rest as the name, so the error
     can say who was not found. A bare number is in the bill's currency; the bill's own symbol,
     code or word is accepted, before or after the number. (Amended at review, 2026-11-12.)
   - Each line replaces that person's earlier amount. Lines may come in one message or several.
   - Errors, joined with `; ` on one `⚠️ ` line under the card body, other lines still apply:
     `Couldn't read '<line>'`, `Who is '<name>'?`, `Use <symbol> amounts for this bill`.
   - `Remaining: S$0.00 ✅` when the amounts add up; an overshoot shows a minus sign and ❌.
   - Buttons: `[Confirm]` only while the amounts add up exactly, and `[Cancel]`, laid out as
     the group's typed-amounts keyboard is. `split_custom` validates. People with no amount
     count as zero.
8. **Preview.**
   ```
   🧾 <b>Dinner</b> · S$84.50
   Sufyan (you) paid

   Check before sending
   • Sufyan (you) S$28.16
   • Ali S$28.17
   • Mei S$28.17
   ```
   Buttons: `[Done]` `[Edit]`, then `[Cancel]`.
   - Edit goes back to step 4 with the names kept; payer and amounts are chosen again. The
     number of people cannot be changed: Cancel and start again.
9. **Done.** The card becomes the summary, with no buttons, and the bill is removed from
   memory. The bot then sends one more message: `Forward the message above to the people on
   the bill. I haven't saved it.`

`Cancel` at any step: the card becomes `Cancelled.` with no buttons and the bill is removed.
`/cancel` in a private chat does the same to the user's bill, if there is one.

### The summary
```
🧾 <b>Dinner</b> · S$84.50
Sufyan paid · split 3 ways

• Ali owes Sufyan S$28.17
• Mei owes Sufyan S$28.17

Split with @<bot username>
```
- Line 2 ends `split <N> ways` for an equal split (N is the number of people) and `split by
  amounts` for typed amounts.
- One bullet per person who is not the payer and whose share is above zero, in person order.
  The payer's own share is not listed.
- No such person (the payer carries the whole bill): the bullets are replaced by `Nobody owes
  anything.`
- The last line uses the bot's real username.

### Expiry and stale buttons
- A bill expires 30 minutes after its last accepted action (sliding, as group drafts do).
  Expired bills are swept whenever a new bill starts.
- Every button except `Split a bill now` carries the bill's ID. A press for a bill that no
  longer exists (expired, finished, cancelled, replaced, or lost in a restart) answers with a
  new message `That bill expired. Tap Split a bill now to start again.` carrying a
  `Split a bill now` button, and removes the buttons from the pressed card.
- Text sent after a bill expired in the same process gets the same message once. A photo or
  an unknown command sent first gets the `/start` intro, which replaces that message (amended 2026-11-13). After a
  restart, text gets the `/start` intro (amended 2026-11-13; the bot no longer knows there was a bill).

### Structure
- New files only, plus registration and the private `/start`/`/help` change:
  `app/services/oneoff.py` (the bill, its states and transitions, the in-memory store, the
  typed-amounts line parser; no Telegram import), `app/bot/render/oneoff.py`,
  `app/bot/keyboards/oneoff.py`, `app/bot/handlers/oneoff.py`.
- Reused as they are: `parse_expense`, `split_equal`, `split_custom`, `fmt_money`, the currency
  tables. Not imported and not edited: `DraftService`, `Draft`, the ledger, the stores, the
  exchange-rate service, the receipt reader, the receipt budget.
- Callback data: `oneoff:start`, and `oneoff:<bill id>:<action>[:<argument>]`. One
  `CallbackQueryHandler` for `^oneoff:`, registered beside the other callback handlers.
- Text and photo handlers for private chats sit behind a filter that is true only while that
  user has a bill waiting for input. The feedback-wait handler is checked first.
- Two things can now wait for private text: Sprint 16's feedback wait and a bill. They keep
  separate stores. Which one gets a message is decided by registration order (feedback
  handlers first, then the bill's), stated in one comment block in `app/bot/app.py` and pinned
  by a routing test through the built application. All private-wait handlers require
  `filters.UpdateType.MESSAGE`, so an edited message is never taken as input.
- Commands do not end a bill. Sprint 16's group -2 handler stays feedback-only. A bill ends
  only by `Done`, `Cancel`, `/cancel`, being replaced, or expiry. So `/help` in the middle of a
  bill leaves it open.
- `/cancel` in a private chat with a bill open: the card becomes `Cancelled.` and nothing else
  is sent. With no bill, `/cancel` behaves as it does today.
- Only the user who owns the bill can press its buttons (a private chat has one user; the
  check is still made).

### Privacy and logging
- Log lines carry the event only: `oneoff started`, `oneoff finished`, `oneoff cancelled`,
  `oneoff expired`. No amounts, descriptions, names or user IDs' display names.
- No database access of any kind on this path.

### Settled at build (agent's calls, open to change)
- Line breaks inside pinned strings in this doc are wrapping (single spaces in code). The `Not SGD?` hint is the line directly under `How many people, including you?` (one newline, no blank line). The how-many, who-paid and split-method cards are the title line, a blank line, then the pinned body. The typed-amounts card follows the fenced block literally: no blank line between the `Send one line per person` line and the bullets.
- Bill ID is `secrets.token_urlsafe(6)` (8 characters, no colon). The longest callback is `oneoff:<8>:payer:20`: 7 + 8 + 9 = 24 bytes (all ASCII), far under Telegram's 64.
- The `Send the bill, like ...` prompt belongs to the bill (a bill exists from that moment). A second `Split a bill now` at that step also asks `Drop the bill you started?`, and `/cancel` at that step turns the prompt message into `Cancelled.` The prompt has no buttons, as pinned.
- At step 2 the card is sent as a new message under the user's line, not an edit of the prompt. The prompt stays above it. `Yes, start again` edits the answered question message into the step 1 prompt (no extra message), and the old card becomes `Cancelled.`
- A bill replaced by a typed bill line at the how-many step is a new bill with a new ID. Its old card becomes `Replaced by the bill below.` Log lines: `oneoff cancelled` for the old one, `oneoff started` for the new one (the four event names are the only ones allowed).
- Typed text is read only in the steps that wait for text: bill line, how many, names, typed amounts. At who-paid, split-method and preview there is nothing to type, so text gets no reply. A photo is answered only at the bill-line step; at later steps it gets no reply.
- Typed names: empty entries (`Ali,, Mei`) are dropped, not read as "skip this person". Names have their inner whitespace collapsed to single spaces so `Ali  Khan 20` can match `Ali Khan`. A message with no names in it changes nothing and gets no reply. A name that equals an untouched default (`Person 3`) counts as a duplicate. A person named `Me`, `I` or `Myself` is allowed but cannot be addressed in typed amounts (those words always mean the user).
- Typed count: a "number" is anything shaped like digits with optional `.` or `,` parts, with an optional leading minus (`2.5`, `1,000`, `-3` all get `Type a number from 2 to 20.`). `3 people`, `4 pax` read as the count; a number followed by anything else (`12 lunch`) is a new bill line and replaces the bill.
- Typed amounts: a line starts with the longest known name (`me`, `i`, `myself` for the user); the rest must be exactly an amount, read by the existing `_locate_amount` rule (the one `parse_expense` uses), so `Ali 20 sgd`, `Ali SGD 20`, `Mei 500 yen` read, and `Ali 20 for dinner` is `Couldn't read`. `parse_custom_line` is not used: it finds the first number and would read `Person 2 20` as amount 2. With no known name, the last word is the amount and the rest the unknown name. A zero amount is allowed. Error lines on the card are cleared by the next message. A message in which no line applied does not restart the 30 minutes.
- A description longer than 80 characters is cut to 80 with `…`, so a card cannot pass Telegram's message limit.
- The typed-names and typed-amounts cards are re-edited in place; an edit that would change nothing ("Message is not modified") is swallowed.
- A button that does not fit the bill's current step (double tap, old keyboard) is answered and ignored, with no message. A press by someone other than the owner is ignored the same way.
- `Done` edits the card to the summary first, then removes the bill, then sends the forward hint. If the card was deleted the summary goes out as a new message instead.
- The user's own typed messages are left in the chat, never deleted.
- A stale press also clears the user's pending expiry notice, so one expiry never produces two stale messages. Expired bills are noticed lazily (on a press, a text, or a sweep when any user starts a bill); the in-memory notice list holds only user IDs.
- `/cancel` with a bill open is handled by a separate `CommandHandler` registered before the `_COMMANDS` loop and filtered to a private chat with a live bill, so `cancel_command` is untouched and does not also run. With no bill (or an expired one) the ordinary `/cancel` runs.
- Registration order in group 0: the feedback handlers, then the bill's text and photo handlers, then the `oneoff:` callback handler, then `settrip`'s awaiting-text handler, and last of all the private fallback that sends the `/start` intro (amended 2026-11-13; it must stay the last handler of group 0). A user with a bill and a private `/settrip` text wait would have the bill read the text first (private `/settrip` only replies that it needs a group, so this cannot happen in practice).
- `Deps` gains one field, `oneoff_bills: OneOffStore`, as Sprint 16 added `feedback_waits`. `/split` is added to `_COMMANDS`, so the unbound-group gate leaves it alone.
- Private `/start` and `/help` use `private_start_keyboard` in `menu.py` (relabelled, plus the second button). Tests that asserted the old label or one button were updated: `tests/test_smoke.py::test_help_command_replies_with_html_and_mentions_balances`, `tests/test_menu.py::test_private_start_keyboard_builds_startgroup_url`, `tests/test_join.py::test_start_in_private_shows_the_add_to_group_url_button_first` (renamed).

Changed at review (2026-11-12):
- A. Typed-amounts lines start with the longest known name (or `me`/`i`/`myself`); the rest is the amount and may carry the currency word, before or after (`Ali 20 sgd`, `Ali SGD 20`, `Mei 500 yen`). Extra words make the line unreadable. No known name falls back to last-word-is-the-amount.
- B. At the how-many step `3 people`, `4 pax`, `2 ppl` (people, persons, person, pax, ppl, any case) are the count; `21 people` is refused; `12 lunch` still replaces the bill.
- C. Keyboard rows are asserted in tests. The group's `custom_keyboard` is one row, `[Confirm] [Cancel]` (Confirm only once balanced); the one-off one is identical.
- D. The one-off `/cancel` handler requires `filters.UpdateType.MESSAGE`, so an edited message cannot cancel a bill.
- E. A card the user deleted does not strand them: a failed edit sends the current card (or the summary, or the step 1 prompt) as a new message and keeps its ID on the bill; closing a deleted card never blocks `Cancel`, `/cancel` or `Yes, start again`.
- F. `OneOffStore.put` returns False and saves nothing for a bill that was removed, replaced or has expired; handlers stop when it does.
- G. One table of text readers (`TEXT_READERS`) drives both what the filters listen for (`TEXT_STATES`) and what the text handler runs; a state with no reader raises `StepError`.
- H. The bill holds its split in one field, `split`: `None`, `EqualSplit` or `TypedSplit` (shares included). Going back sets it to `None`. `method`, `amounts`, `amount_errors` and `shares` remain as read-only properties.
- I. In the typed-amounts warning line each echoed line or name is cut to 30 characters plus `…`, at most 5 errors are shown, then `…and <n> more` (joined with `; `), so the card stays under 4096 characters.

## Before you start (you)
Nothing.

## Agent builds
1. `app/services/oneoff.py`, with unit tests for every transition and for the line parser.
2. Render and keyboards.
3. Handlers and registration in `app/bot/app.py`; `/split`; `/cancel` in a private chat.
4. Private `/start` and `/help` text and buttons; `HELP_TEXT` line.
5. Spec §22 and CONTEXT.md are already written on this branch: check the code matches them.
6. `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.

## Tests required
- The whole path, equal split: `84.50 dinner`, 3 people, names `Ali, Mei`, `Me` paid, Split
  equally, Done. The summary equals the pinned text exactly, shares 28.16 / 28.17 / 28.17.
- The whole path, typed amounts, with another person as payer: bullets read `<name> owes
  <payer>`, the user's bullet uses their first name, the line ends `split by amounts`.
- Shares always sum to the bill amount (Hypothesis: random totals, 2 to 20 people, any payer).
- Bill line: no amount; zero; a yen amount stays in yen with no hint line; `paid by Ali` and
  `split everyone` are ignored; an empty description shows `Bill`.
- How many: buttons; typed `7`; typed `1`, `21`, `abc`; a new bill line replaces the bill.
- Names: fewer names than people; too many; over 30 characters; a duplicate; a duplicate of
  the user's first name; HTML in a name is escaped.
- Typed amounts: several lines in one message; `me 20`; `Person 2 20` (a default name with a
  space and a digit); an unknown name; an unreadable line; the wrong currency; over the total;
  Confirm absent until the amounts add up.
- Payer carries everything: `Nobody owes anything.`
- Edit returns to names with names kept. Cancel at each step. `/cancel`.
- A second `Split a bill now` with a bill open: both answers.
- Expiry: 30 minutes sliding; a stale button gets the pinned message and loses its buttons; a
  text after expiry gets it once; a press on a finished bill's button.
- Private text and photos with no bill: the `/start` intro (amended 2026-11-13; was: no reply). A photo at the bill-line step:
  the pinned text.
- `/split` in a group: the pinned text; group text expenses still work (regression).
- Feedback wait and bill together: the text goes to feedback, the bill survives.
- Private `/start` and `/help`: the pinned text, two buttons in the pinned order. Group
  `/start` unchanged (regression).
- No ledger or store call is made anywhere on the path (a store fake that fails on any call).
- No log record contains a description, a name or an amount (caplog).

## After: your checklist
0. In BotFather, `/setcommands`: add `split - Split one bill in a private chat`.
1. Private chat, `/start`: the new text; the trip button first, `Split a bill now` second.
2. `Split a bill now`, `84.50 dinner`, `3`, `Ali, Mei`, Next, `Me`, Split equally, Done. The
   summary reads as pinned. Forward it to someone: it makes sense on their phone.
3. `/split`, `1200 yen ramen`, `2`, Next, `Person 2` paid, Type amounts instead, `me 500` and
   `Person 2 700`, Confirm, Done. The summary says you owe Person 2 ¥500.
4. Start a bill, then `Split a bill now` again: `Drop the bill you started?` both ways.
5. Start a bill, wait 31 minutes, press a button: `That bill expired…`.
6. In a trip group: `/split` gives the hint; `1200 yen ramen` still makes an expense card.
7. Send plain text to the bot in the private chat with no bill open: the `/start` intro with
   both buttons (amended 2026-11-13; was: no reply).

## Exit criteria
- [ ] Checklist passes
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
