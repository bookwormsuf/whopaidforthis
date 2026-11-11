# Sprint 16 — Feedback

## Goal
Anyone using the bot can send feedback to the person who runs it, without knowing who that is.
After a trip is closed with the button, the group is asked for it. The bot calls itself
"Who Paid For This" in what it says. Decided with Sufyan on 2026-11-12.

## In scope
- `/feedback` in groups and in a private chat; the text is sent to the owner by the bot.
- A feedback prompt posted in the group after `Close this trip`.
- One new optional setting, `OWNER_USER_ID`.
- The bot's name in user-visible text: "Who Paid For This".
- Spec §21; CONTEXT.md (*owner*, *feedback*); manual checklist; tests.

## Out of scope
- Replying to feedback through the bot.
- Photos, files or voice as feedback.
- Storing feedback anywhere (no table, no file).
- A feedback prompt when `/settrip` closes a trip by starting a new one.
- A `Feedback` button on the group menu.
- The private `/start` text and buttons (Sprint 17 changes them).
- README (updated once, after Sprint 17).

## Sprint pins (decided with Sufyan, 2026-11-12)

### Owner
- `OWNER_USER_ID`: optional setting, a Telegram User ID (integer), default unset. Not a secret
  in the cryptographic sense, but never committed and never written to `STATUS.md`: it is set
  as a Fly secret by Sufyan.
- Unset means feedback is off: `/feedback` replies `Feedback isn't set up on this bot.` and the
  prompt after a trip closes is not posted. Nothing else changes.

### `/feedback`
- Works in groups and in private chats. No trip is needed: it bypasses the trip gate and works
  in a group with no trip, an open trip or a closed trip.
- `/feedback <text>` (also `/feedback@<bot> <text>`): the text is everything after the command,
  trimmed. It is sent at once.
- Bare `/feedback` in a **private chat**: the bot replies `What's your feedback? Send it as one
  message.` and takes that user's next text message in that private chat as the feedback.
  The wait lasts 30 minutes, lives in memory, and is dropped by `/cancel`, by any other command,
  and by a restart. A photo or any non-text message while waiting gets `Text only for now.` and
  the wait continues.
- Bare `/feedback` in a **group**: the bot replies ``Send it in one line, like <code>/feedback
  your message</code>.`` and waits for nothing. (A waiting bot would read the next line as an
  expense.)
- Longer than 1,000 characters: `Keep it under 1,000 characters.` Nothing is sent; in a private
  chat the wait continues.
- Limit: 5 sent feedback messages per user per calendar day in `Asia/Singapore`, counted in
  memory, reset on restart. Only a message actually delivered to the owner counts. Over the
  limit: `There's a limit of 5 feedback messages a day. Try again tomorrow.`
- On success the bot replies `Sent. Thank you.`
- If sending to the owner fails for any reason (owner never started the bot, blocked it,
  Telegram error): the user gets `Couldn't send that. Try again later.`, the failure is logged
  as a warning, and it does not count against the limit.

### What the owner receives
One message from the bot in the owner's private chat, HTML, names and text escaped:
```
💬 <b>Feedback</b> · from a group
Ada (@ada)

<the text>
```
- Second half of line 1 is `from a group` or `from a DM`.
- Line 2 is the sender's Telegram first name, then ` (@username)` only if they have one.
- No group name, no chat ID, no user ID, no trip data.

### Prompt after a trip closes
- When `Close this trip` succeeds, after the settle message is edited, the bot posts one new
  message in the group:
  ``How was Who Paid For This on this trip? Send <code>/feedback</code> and your message. It
  goes straight to the person who built it.``
- Every trip, every time. Not posted when the press is refused, when the trip was already
  closed (second press), or when `OWNER_USER_ID` is unset.
- It is sent outside the `try` that edits the settle message, so a failed edit does not block
  it. If the prompt itself fails to send, that is logged and swallowed; the trip stays closed.

### Privacy and logging
- The feedback text is never logged and never stored. Log lines carry the event only
  (`feedback sent`, `feedback refused: limit`, `feedback failed`), with no text, no names.

### The bot's name
- User-visible text says **Who Paid For This** wherever it said `whopaid`: the help header
  (`<b>Who Paid For This</b> — records trip expenses for the group.`), the version line
  (`Who Paid For This <version>`), and any other message, button or caption a user can read.
- Unchanged: the repo and package name, the Fly app name, log lines, the export file name,
  code identifiers, and docs other than this sprint's.
- `/help` (group and private) lists `/feedback` with the line `/feedback — tell the person who
  built this what to fix`, placed last before `/help`.

### Settled at build (agent's calls, open to change)
- `/cancel` in a private chat while waiting drops the wait silently (no "Cancelled." reply), the same as any other command. `cancel_command` is unchanged.
- Any command in a private chat drops the wait, `/feedback` included: the drop runs first, then a bare `/feedback` starts a fresh wait. A command in a group does not touch a private wait.
- `/feedback <text>` typed in the private chat while a wait is open sends the text, and the group -2 handler (which runs first) clears the wait. `/feedback <text>` in a group does not touch a private wait.
- In a private wait, a text that hits the daily limit or fails to send ends the wait (only "too long" keeps it). Pins only said the wait continues for "too long".
- Bare `/feedback` in a private chat asks the question even if the user is already at the daily limit; the refusal comes when they send the text.
- The failure log is `feedback failed` at WARNING with no `exc_info`, because Telegram error text can carry the owner's chat ID. Sent and limit lines are INFO (`feedback sent`, `feedback refused: limit`).
- Line breaks inside the pinned group hint and close prompt in this doc are treated as wrapping, not newlines (single spaces in the code).
- The close prompt goes out with `context.bot.send_message(chat.id, ...)`, a new message, not a reply.

Changed at review:
- The failure reply to the user is sent after the `except`, so a failing reply cannot chain the owner-send error (and the owner's ID) into the error handler's log.
- `feedback_command` no longer clears waits itself; only the group -2 handler ends a wait.
- Both wait handlers also require `filters.UpdateType.MESSAGE`, so an edited message is never taken as feedback.
- Blank text (whitespace only) in a private wait gets the question again; nothing is sent or counted and the wait continues.

## Before you start (you)
Nothing. The build needs no secret.

## Agent builds
1. `app/config/settings.py`: `OWNER_USER_ID: int | None = None`. Pass it into `Deps`.
2. `app/services/feedback.py`: pure helpers with no Telegram import: the per-user daily counter
   (same shape as `ReceiptReadBudget`: the caller passes an aware `now`, day rolls at midnight
   `Asia/Singapore`), text validation (empty, length), and the owner message formatter.
3. `app/bot/session_state.py`: an in-memory "waiting for feedback" store keyed on user ID, TTL
   30 minutes, following `SettripSessionStore`.
4. `app/bot/handlers/feedback.py`: `/feedback` command; the private-chat text handler behind a
   filter that is true only while that user is waiting; the non-text reply while waiting.
   Message texts as module-level constants, as other handlers do.
5. `app/bot/app.py`: register `/feedback` in `_COMMANDS`; register the waiting handlers for
   private chats only; `/cancel` and any other command drop the wait.
6. `app/bot/handlers/balances.py`: the prompt after `close_trip_callback`, as pinned.
7. The name change in user-visible strings. Grep `whopaid` under `app/`, change only text a
   user reads, and list every string changed and every one deliberately left.
8. Spec §21 and CONTEXT.md are already written on this branch: check the code matches them.
9. `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.

## Tests required
- `/feedback hello` in a group and in a private chat: the owner gets the pinned message with
  the right `from a group` / `from a DM`; the user gets `Sent. Thank you.`
- A sender with no username: line 2 has the first name only. A name or text with `<`, `&`:
  escaped.
- Bare `/feedback` in a group: the one-line hint, and the next text message is still read as an
  expense (regression).
- Bare `/feedback` in a private chat: the question, then the next text is sent; the wait is
  gone afterwards; a second plain text gets no reply.
- While waiting: a photo gets `Text only for now.` and the wait continues; `/cancel` drops the
  wait; the wait expires after 30 minutes.
- 1,001 characters: refused, nothing sent, not counted. Exactly 1,000: sent.
- Sixth message in a Singapore day: refused with the pinned text. After midnight Singapore
  time: allowed again. The count is per user, so another user is unaffected.
- Owner send raises: the user gets `Couldn't send that. Try again later.`; not counted.
- `OWNER_USER_ID` unset: `/feedback` replies `Feedback isn't set up on this bot.`; closing a
  trip posts no prompt.
- Close button: the prompt is posted once after a successful close; not on a refused press; not
  on a second press; still posted when the message edit raises; a failing prompt send does not
  undo the close.
- `/settrip` closing a trip posts no prompt.
- `/help` in a group and in a private chat names the bot "Who Paid For This" and lists
  `/feedback`.
- No log record contains the feedback text (caplog).

## After: your checklist
0. Set the owner once (this restarts the bot):
   `fly secrets set OWNER_USER_ID=<your numeric Telegram ID> -a whopaid-bot`.
   Add a blank `OWNER_USER_ID=` line to `.env.example`. In BotFather, `/setcommands`: add
   `feedback - Tell the person who built this what to fix`. Open the bot in a private chat and
   press Start if you never have.
1. In a private chat with the bot: `/feedback testing one two`. You get `Sent. Thank you.` and
   a second message from the bot with your name and `from a DM`.
2. In the private chat: bare `/feedback`, then a message. It arrives.
3. In a test group: bare `/feedback`. You get the one-line hint. Then `1200 yen ramen` still
   makes an expense card.
4. In the test group: `/feedback from the group`. It arrives with `from a group` and no group
   name.
5. `/settle`, then `Close this trip`. The feedback prompt appears once, under the settle
   message.
6. `/help` in the group and in the private chat says "Who Paid For This" and lists `/feedback`.

## Exit criteria
- [ ] Checklist passes
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
