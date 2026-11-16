# Sprint 20 — Commands card and menu

## Goal
People stop having to remember commands. Once a trip starts, the group has one pinned card
that says how to add an expense and lists the commands people use every day. The bot also
fills Telegram's "/" command menu itself. Testers' groups often had no pinned message,
because the bot was not an admin and the hint did not say so. Decided with Sufyan on
2026-11-16 and 2026-11-17 after tester feedback. Stacked on Sprint 19.

## In scope
- The open-trip welcome becomes the **commands card** (new text).
- Posting and pinning a fresh card right after `Connected ✅`.
- Quiet pins; unpinning the bot's own previous card.
- A clearer one-time message when the pin fails.
- `Everyone's in` no longer posts a welcome.
- `setMyCommands` on startup, one list for groups and one for private chats.
- README and the Sprint 17 checklist: drop the BotFather command-list step.
- Spec §19 amendment; CONTEXT.md (*welcome*, *commands card*, *command menu*); manual
  checklist; tests.

## Out of scope
- The no-trip and closed-trip welcome texts (unchanged).
- `/help` text and its list (unchanged).
- Storing a pinned message ID, or any database change.
- Checking later whether the card is still pinned, or reminding more than once.
- Asking for any admin right other than pinning.
- Detecting privacy mode.

## Sprint pins (decided with Sufyan, 2026-11-16/17)

### Words
- **Commands card**: the welcome when the group has an open trip. Still the one message the bot
  pins.
- **Command menu**: the list Telegram shows when someone taps the menu button or types `/`.

### Commands card text (open trip)
Replaces `welcome_text` output when a trip is open. HTML, verbatim:
```
📌 <b>How to use Who Paid For This</b>
Type what you spent, like <code>1200 yen ramen</code>, or send a receipt photo.
/balances who owes whom
/mine what you paid and owe
/settle who pays whom
/undo remove your last expense
/help for everything else
```
- Keyboard: today's open-trip menu (`menu_keyboard("open")`, both pages, unchanged).
- No trip line is added. The first line is what Telegram shows in the pinned bar.
- No-trip and closed-trip welcomes keep today's text and keyboards.

### When a card is posted and pinned
- When the bot is added to a group and on `/start` in a group: unchanged (the welcome for the
  current state).
- **New:** right after a trip is created, immediately after the `Connected ✅` message is sent,
  the bot posts the commands card and pins it. A failure here is logged and swallowed; the trip
  stays created and `Connected ✅` stays.
- `/help` in a group: unchanged, never pinned.

### `Everyone's in`
- No longer posts or pins a welcome. Remove the `_post_menu_after_done` call.
- Everything else stays: it edits the `Connected ✅` message, removes its keyboard and appends
  the existing line `Everyone's in. Latecomers can type /join.` That line is the short reply.

### Pinning rules (every welcome and commands card)
In this order:
1. Before pinning, `get_chat(chat_id)` and read `pinned_message`. If it exists and was sent by
   this bot (`from_user.id == bot.id`), remember its message ID as the old card. If `get_chat`
   fails, log and treat it as no old card.
2. Pin the new message with `disable_notification=True`. No one gets a ping.
3. If the pin succeeded and there is an old card with a different message ID, unpin it by that
   ID. A failed unpin is logged at DEBUG and ignored.
4. If the pin failed, edit the new message once to append the pin-failed text below. Do not
   unpin anything. Never send a separate message, never retry, never remind again.

Messages the group pinned themselves (any sender other than the bot) are never unpinned.

### Pin-failed text
Replaces `PIN_HINT`. Appended after a blank line, verbatim:
```
I can't pin this. Make me an admin with 'Pin messages' so I can keep it up to date, or pin it yourself.
```

### Command menu
Set once in `_post_init`, after the store connects, with `set_my_commands`:
- Scope `BotCommandScopeAllGroupChats`, in this order:
  ```
  balances - Everyone's balances
  mine - What you paid and owe
  settle - Who pays whom
  recent - Last 5 expenses
  undo - Remove your last expense
  settrip - Start a trip for this group
  help - Everything else
  ```
- Scope `BotCommandScopeAllPrivateChats`, in this order:
  ```
  split - Split one bill
  feedback - Tell the person who built this what to fix
  help - What this bot does
  ```
- The default scope is not touched. An old BotFather list stays as the fallback but groups and
  private chats show the lists above.
- A failure is logged as a warning and startup continues. Commands not in a list still work
  when typed.

### Docs cleanup
- README "BotFather setup" step 4: replace with one line saying the bot sets its own command
  menu on startup, so no command list is needed in BotFather. Remove the pasted list.
- `docs/sprints/17-one-off-bill.md` checklist step 0: mark the BotFather `/setcommands` line
  as no longer needed (amended by Sprint 20), do not delete it.
- `STATUS.md` past entries are history and are not edited.

### Settled at build (agent's calls, open to change)
- The card after `Connected ✅` is posted by `post_commands_card(bot, chat_id)` in `menu.py`. It
  sends the card text with the open-trip menu and pins it, with no store read, using the new
  trip's chat ID. A ledger failure cannot turn it into a "ledger unavailable" line. Any other
  failure is logged and swallowed; the trip stays created.
- A pinned message with no `from_user`, or from anyone but the bot, counts as "not the bot's".
- Only `BadRequest` and `Forbidden` from the pin count as "pin failed". Any other error from the
  pin call is not caught by the helper. `settrip` and the group-add handler already swallow it.
- A `get_chat` failure is logged at WARNING, without a traceback.
- `/start` and the group-add welcome follow the same pinning rules, so they also unpin the bot's
  older card and send the pin-failed text.
- `PIN_HINT` stays as a name, now `"\n\n" + PIN_FAILED_TEXT`.
- `set_my_commands` is tried per scope. If the group list fails, the private list is still tried.
  Each failure logs one warning naming the scope.
- README beyond step 4: the "How to use" lines that quoted the old pin hint and said
  `Everyone's in` posts the menu were updated to match. They were wrong after this sprint.
- The old `_post_menu_after_done` was deleted, not left unused.
- Existing tests that expected the old open-trip welcome, the `Everyone's in` menu, or "no message
  after trip creation" were changed to the new behaviour.

## Before you start (you)
Nothing. Merge Sprint 19 first.

## Agent builds
1. `app/bot/handlers/menu.py`: the commands card text as a module-level constant;
   `welcome_text` returns it for an open trip; the pinning rules as one helper used by every
   welcome post; `PIN_HINT` replaced by the pin-failed text.
2. `app/bot/conversations/settrip.py`: after `Connected ✅` is sent, post the welcome (local
   import, as `callbacks.py` does today).
3. `app/bot/handlers/callbacks.py`: stop posting the welcome after `Everyone's in`.
4. `app/bot/app.py`: `set_my_commands` for both scopes in `_post_init`. The two lists as
   module-level constants. Each command in them must exist in `_COMMANDS` or the private
   handlers.
5. README and the Sprint 17 checklist, as pinned.
6. `docs/spec/pinned-decisions.md` §19 amendment and `CONTEXT.md` are already written on this
   branch; check the code matches them.
7. `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.

## Tests required
- `welcome_text` with an open trip returns exactly the pinned card; no-trip and closed texts are
  unchanged (regression).
- Creating a trip sends `Connected ✅` and then the commands card, in that order, and pins the
  card with `disable_notification=True`.
- A pin that raises: the card is edited once with the pin-failed text; no other message is
  sent; the trip is still created.
- Old card from the bot: unpinned by its ID after the new pin succeeds. Old pinned message from
  a user: not unpinned. No pinned message: no unpin call. `get_chat` raising: pin still
  attempted, no unpin. Pin failing: no unpin.
- Unpin raising does not break anything.
- `Everyone's in` posts no welcome and still appends the existing line (regression).
- Bot added to a group: welcome posted and pinned quietly.
- `_post_init` calls `set_my_commands` twice with the pinned lists and scopes; a raising call
  does not stop startup.
- Every command in both lists is a registered command (guard test against drift).

## After: your checklist
1. Restart the bot (any deploy does it). In a group, tap the menu button or type `/`. You see
   the seven group commands. In a private chat with the bot, you see `split`, `feedback`,
   `help`.
2. In a test group where the bot is **not** an admin, start a trip. After `Connected ✅`, the
   commands card appears with the "I can't pin this…" line. Nobody got a pin notification.
3. Make the bot an admin with "Pin messages". Run `/settrip` and start a new trip. The new card
   is pinned without a ping and the old one is no longer pinned.
4. Pin any message of your own, then start another trip. Your message stays pinned.
5. Tap `Everyone's in`. No new card is posted.

## Exit criteria
- [ ] Checklist passes
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
