# Who Paid For This

Who Paid For This is a Telegram bot. It tracks who paid for what on a group trip and works out who owes whom. It can also split a single bill in a private chat, with no group and nothing saved.

The repo and the Python package are called `whopaid`. The bot calls itself "Who Paid For This" in its messages. The export file is still named `whopaid-<trip>-<date>.xlsx`.

<p align="center">
  <img src="docs/images/welcome.png" alt="The welcome message with a Start a trip button" width="560">
</p>
<p align="center">
  <img src="docs/images/receipt.jpg" alt="A receipt photo saved as an expense, split by items" width="330">
  &nbsp;&nbsp;
  <img src="docs/images/settle.png" alt="/settle listing who pays whom" width="330">
</p>

The screenshots are from a real trip. The tester's name is blacked out.

- [Using the bot](#using-the-bot): for people who want to use it.
- [Running your own copy](#running-your-own-copy): for people who want to host it.
- [How it works](#how-it-works): design, data and maths.

Project status and sign-offs: `STATUS.md`. Settled design decisions: `docs/spec/pinned-decisions.md`. Terms used in the code and docs: `CONTEXT.md`.

## Using the bot

### On a trip (in a group)

Use one Telegram group per trip. A group has one open trip at a time.

1. **Add the bot to the group.** It posts a welcome message with a **Start a trip** button and tries to pin it. If it can't pin, it says "Pin this message so everyone can find the buttons." It never asks to be made an admin.
2. **Start the trip.** Tap **Start a trip** or run `/settrip`. The bot asks three questions with buttons:
   - The trip currency: what you pay in (THB, MYR, JPY and CNY are shown first, then **Other…** for any other 3-letter code).
   - The home currency: what you will settle up in.
   - The timezone. It defaults to the one that matches the trip currency, for example Japan gives Asia/Tokyo.

   After the timezone, a summary card shows the trip name, currencies and timezone with **Start trip** and **Cancel**. The trip is saved only when you tap **Start trip**.

   If the group already has an open trip, the bot first asks "Close the current trip and start a new one?" with **Start new trip** and **Keep current**. The old trip is closed only when the new one is confirmed. It is never deleted and stays exportable.
3. **Everyone joins.** Each person taps **I'm in too** or sends `/join`. The bot needs to know who is splitting, because Telegram does not let a bot see a group's member list. You join a trip in one of four ways: `/join`, the **I'm in too** button, sending an expense by text or photo, or being @-tagged in an expense or its caption. **Everyone's in** closes joining and posts the full button menu. You can also run `/join` as a reply to someone's message to join them.

   `/start` in a group shows the welcome menu again. It does not join you.
4. **Add expenses.** Type an amount and a description, like `1200 yen ramen`. Or send a photo of a receipt. The bot converts the amount to your home currency at that day's rate and shows a card (a message with buttons) for you to check before it saves anything. An unfinished card is called a draft. A draft expires after 30 minutes without a button press or reply.

   The bot only reacts to messages that hold an amount and are at most 12 words, to commands, to receipt photos, to replies to its own cards, and to button presses. It ignores everything else.
5. **Check the balance.** `/balances` shows what everyone is owed or owes. `/mine` shows your own position. `/settle` shows who pays whom.
6. **Close the trip.** `/settle` ends with a **Close this trip** button. The trip starter or whoever ran `/settle` can press it. A closed trip takes no new expenses. You can still run `/export`, and `/settrip` starts a new trip.
7. **Export or delete.** `/export` sends a spreadsheet of the trip. `/deletetrip` erases every trip in the group.

Privacy mode is a Telegram setting that stops a bot from reading ordinary group messages. The bot needs it switched off. The person who set the bot up does this (see [Running your own copy](#running-your-own-copy)). If the bot ignores `1200 ramen`, ask them to check it.

#### How splits work

Every expense has one **payer**, the person who handed over the money. It has one or more **participants**, the people who owe a share. The card asks who should share it:

- **Just me**: you are the only participant.
- **Everyone**: every person who has joined the trip, counted at the moment you tap it.
- **Choose people**: one toggle per joined person (you start selected), then **Next**.

Before that, if nobody was tagged, the message does not say `paid by`, and more than one person has joined, the bot asks **Who paid?** with a button per person (you are **Me**).

With more than one participant the card asks how to split:

- **Split equally**: everyone gets the same share. Any leftover cents (or yen) go one each to people in Telegram user ID order, skipping the payer while anyone else is left.
- **Type amounts instead**: reply to the card with one line per person, like `Amir 2000`. The card shows each person's amount and `Remaining: ¥0 ✅` once they add up exactly. **Confirm** only appears then. Anyone you don't mention gets nothing and is dropped. If you forget to reply to the card, the same lines sent on their own still fill your open card, as long as every line names someone on it. If the card expired (30 minutes without a press or reply, or the bot was updated), a reply to it says so instead of starting a new expense.
- **Item by item**: only for a receipt with 1 to 40 items. See below.

The last card lists every share in both currencies, with the payer marked 💳, and three buttons: **Confirm**, **Edit** (back to choosing people, amount kept) and **Cancel**. Only the person who sent the expense can press its buttons. Anyone else gets "Only <name> can edit this".

You can skip the buttons by saying it in the message:

- `6000 yen dinner split everyone` (or `split all`)
- `6000 yen dinner split between me Amir and John` (also `among`, `with`)
- `3000 yen lunch paid by Amir`: the payer does not have to be a participant
- `3000 yen lunch @amir`: tagging someone means they paid (also in a receipt caption)

Names match a joined person's username, first name or full name, ignoring case. `me` is you. If a name matches nobody or more than one person, the card asks "Who did you mean by 'Jon'?" with buttons. It never guesses.

#### Receipt photos

Send a photo of the whole receipt, including the totals at the bottom. A clear, flat photo reads best. An AI model (Anthropic's API) reads it: merchant, total, date and the individual lines. The photo is not stored.

- **More than one possible total.** If the receipt shows several numbers that could be the total (for example a tax-included and a tax-excluded total), the bot asks "Which is the final total?" with a button for each, plus **Other amount** to type it yourself. The total you pick is always what gets saved.
- **Total does not match the lines.** If your picked total differs from the lines added up, the bot shows the difference with **Continue with <total>** and **Cancel**. The difference is shared like tax.
- **Can't read it.** If the photo is too blurry or is not a receipt, the bot says "Couldn't read that receipt" with **Try again** (re-reads the same photo) and **Enter amount manually** (reply with just the amount, like `2400`).
- **Captions.** A caption overrides the description the AI model guessed. A receipt sent with the caption `dinner` is saved as "Dinner". A caption can also carry `split everyone`, `split between me Amir` or `paid by Amir`, as in a typed expense.
- **Albums.** Several photos sent together each become their own expense.
- **Too large.** A photo over 10 MB gets "That photo's too large (over 10 MB), please send a smaller one."

Receipt reading can be wrong. Check the total on the card before you confirm.

#### Splitting a receipt item by item

On a receipt with 1 to 40 items, the split card asks "Who had what?" and offers, one per row, **🍽 Go item by item ▶**, **Split equally** and **Type amounts instead**.

Item by item shows one item at a time, in the order printed on the receipt:

```
Who had this?  (2 of 4)
Egg Sando Set (2/2) · ฿333.73
[✓ Ada] [✓ Ben] [Amir]
[Everyone]
[🚫 Not on this bill]
[◀ Back]  [Next ▶]
[Cancel]
```

Tap the names of whoever had the item, then **Next ▶**. Two or more names means they shared it, split equally. **Everyone** gives the item to everyone and moves on. **Next ▶** with nobody ticked says "Pick who had this first". A line with a quantity of 2 to 10 gets one screen per unit, `(1/2)` and `(2/2)`, so one set can go to one person and the other be shared. Only the person who sent the receipt can press these buttons.

After the last item, the check card shows each person's total with `items ¥a + tax ¥b` under it, and the tax, service, discount and unlisted lines it spread. **Fix an item** lists every item with the initials of who had it. Pick one, answer it again, and you are back on the check card. **Edit** goes back to choosing people. Picks for people still in are kept.

Tax, service charge and discounts are spread in proportion to what each person's items cost. Someone with no items pays none of them and is saved with a zero share.

If the lines don't add up to the receipt total, the difference becomes one **Unlisted / rounding** line, shared like tax. A total you picked or typed gets a prompt first showing the difference. A total the bot picked on its own is at most 5 off the lines and needs no prompt.

**Group orders where everyone paid their own part.** Some delivery receipts list the whole order but also say what you paid ("you paid", "your share"). The bot puts that figure first on the total buttons. Pick it, then **Continue**. The prompt suggests marking the rest. Go item by item and tap **🚫 Not on this bill** on each dish someone else paid for. Fees and discounts are scaled to the dishes you kept. Whatever is still left over (the app's own rounding of its split) goes on the **Unlisted / rounding** line. The check card lists what was left out. **Fix an item** shows those dishes with a long dash and can bring one back by ticking a name. At least one dish must stay. **Split equally** and **Type amounts instead** ignore these marks.

#### Other currencies

An expense can be in a third currency, say ringgit on a Thailand trip with SGD at home. Type it with its three-letter code (`50 usd taxi`, `myr 120 dinner`) or a symbol that means only one currency (`€30`, `£8`, `₩15000`, `RM120`). `$` and `¥` keep their usual meaning. Codes that are also words (`all`, `cup`, `top`, `try`) only count in capitals.

A receipt the bot reads in a third currency asks first: "This receipt looks like it's in Malaysian ringgit (RM). Is that right?", with **Yes, MYR**, **It's THB** and **It's SGD**. The correction re-reads the amounts in that currency.

The expense is split in its own currency. Then each share is converted to the trip and home currency at that day's rates, so both still add up exactly. Cards show `RM120.00 (฿849.60 · S$34.49)` and both rates. Balances and settling stay in the home currency.

For a currency Frankfurter (the rate source) doesn't publish, such as VND, TWD or AED, the card asks you to reply with what 1 unit is in the trip currency (`0.0013`), or with the total in the trip currency (`฿130`). That rate is saved with that expense only.

Each expense uses the rate for its own date. On weekends and holidays, or before the day's rate is out (around 16:00 CET), that is the previous business day's rate, and the card shows which date it used. If the rate source can't be reached, the card says so with a **Retry** button and nothing is saved.

#### Balances, settling and export

- `/balances` shows one bullet per person with any activity, most owed first: "Ada is owed S$31.20", "Amir owes S$31.20", "Cy is all square". Their paid and share amounts are under each.
- `/mine` says in plain sentences what you paid for the group, your share of everything, and whether you owe or are owed overall. Then it lists your last 3 expenses with your share of each.
- `/settle` lists who pays whom, one bullet per transfer, like `• Amir pays Ada S$31.20`, or "All square 🎉". It ends with the **Close this trip** button.
- `/undo` cancels your last expense, after you confirm. It only touches expenses you created.
- `/export` sends one `.xlsx` file for the open trip, or for the most recently closed trip if none is open. Anyone in the group can run it. It has the sheets Expenses, Shares, Line items, People and Balances.
- `/deletetrip` erases every trip in the group, closed ones included, and everything under them. Only the person who started the open trip can run it (or, if none is open, the latest closed one). That person also removes earlier trips that others started. It asks you to confirm first.

#### Example

In the trip group (names here are examples):

```
Ada:     1200 yen ramen
bot:     🧾 Ramen · ¥1,200 (S$9.72)
         2 Oct 2026
         Who paid?
         [Me] [Amir] [Cancel]
Ada:     (taps Me)
bot:     🧾 Ramen · ¥1,200 (S$9.72)
         Ada paid · 2 Oct 2026
         Rate 1 JPY = 0.00810 SGD (1 Oct)
         Who should share this?
         [Just me] [Everyone] [Choose people]
         [Cancel]
Ada:     (taps Everyone, then Split equally)
bot:     Check before saving
         🧾 Ramen · ¥1,200 (S$9.72)
         Ada paid
         Each person's share:
         • Ada ¥600 (S$4.86)
         • Amir ¥600 (S$4.86)
         [Confirm] [Edit] [Cancel]
Ada:     (taps Confirm)
bot:     Saved ✅ Ramen ¥1,200 (S$9.72) · Ada paid · split 2 ways

Amir:    /balances
bot:     Who owes what (SGD)
         • Ada is owed S$4.86
            (paid S$9.72, share S$4.86)
         • Amir owes S$4.86
            (paid S$0.00, share S$4.86)
         Settle up with /settle
Amir:    /settle
bot:     To settle up:
         • Amir pays Ada S$4.86
Amir:    /mine
bot:     Amir
         You paid S$0.00 for the group.
         Your share of everything is S$4.86.
         ➡️ You owe S$4.86 overall.
         Recent:
         • 2 Oct · Ramen ¥1,200 (S$9.72)
            Ada paid · your share S$4.86
```

### One bill, no group (private chat)

For a single bill, such as one dinner or one cab, you don't need a group or a trip. Open a private chat with the bot and send `/split`, or tap **Split a bill now** under the private `/start` or `/help` message. Nothing is saved, and you don't need to run `/join`.

The people on a one-off bill are slots, not Telegram accounts. They have no account behind them, so the bot can't tag or message them. You forward the summary yourself.

**The typed flow.** One card is edited in place at each step.

1. The bot says "Send the bill, like `84.50 dinner`, or a photo of the receipt." Send the amount and a description.
2. The currency is whatever you typed (`1200 yen ramen` is ¥1,200). With none typed it is SGD. The bill stays in that one currency. There are no rates and no conversion. `paid by` and `split` words and @tags in the line are ignored.
3. Pick how many people share it, you included, from 2 to 20. Tap a button or type a number.
4. Optionally rename the others. Send names like `Ali, Mei`, or tap **Next**. People you don't rename are called Person 2, Person 3 and so on.
5. Pick who paid.
6. Pick **Split equally** or **Type amounts instead**. For typed amounts, send one line per person, like `Ali 20`. **Confirm** appears when the amounts add up.
7. Check the preview and tap **Done**. **Edit** goes back to the names step. Names and who-had-what picks are kept, and you choose the payer and the split again. The number of people cannot change. Cancel and start again for that.

**Starting from a receipt photo.** At the first step you can send a photo of the receipt instead of typing. The bot reads it with the same reader as in a group, with two differences: the caption is ignored, and the "doesn't add up" card appears only when the gap is more than 5 minor units (in a group it also appears for any gap on a total you picked or typed). It then offers the item split too: "Who had this?" for each item, shared items, and tax and service shared in proportion. **Fix an item** works as in a group. **Not on this bill** is not offered on a one-off bill. Before your first read the bot says "I read receipts with an AI model. The photo isn't stored."

**The summary.** When you tap **Done**, the card becomes a summary that names who paid and who owes the payer what. The bot then says "Forward the message above to the people on the bill. I haven't saved it." Forward the summary to them. The summary ends with "Split with @" and the bot's username.

**Never saved.** A one-off bill lives only in the bot's memory while you build it. It is removed when you tap **Done** or **Cancel**, when you send `/cancel`, after 30 minutes without an accepted action, or when the bot restarts. The summary message in your chat is the only record. The bot does not touch its database for a one-off bill.

If a bill is already open and you start another, the bot asks "Drop the bill you started?" If you press a button on a bill that has expired, the bot says "That bill expired. Tap Split a bill now to start again."

**Limits.**

- Typed bills are unlimited.
- Receipt photos: each person can have one receipt read a day in private chats, and three tries a day. A successful read ends the day's allowance. A failed read leaves you two more tries. The day is the calendar day in Singapore time.
- One receipt is read at a time per person. A second photo while the first is being read gets "One receipt at a time. Still reading the first."
- The bot's own daily cap on receipt reads across all chats also applies (see [Limits](#limits)).

If you hit a limit, the bot tells you and the bill stays open for you to type it instead.

### Feedback

`/feedback` sends a message to the person who runs the bot. It works in groups and in private chats, with no trip needed.

- `/feedback your message` sends it at once.
- Bare `/feedback` in a private chat asks "What's your feedback? Send it as one message." and takes your next text message. The wait lasts 30 minutes, and any command ends it. A photo or other non-text message gets "Text only for now."
- Bare `/feedback` in a group replies with a one-line hint and waits for nothing. This keeps the bot from mistaking your next chat message for feedback.
- Text only, at most 1,000 characters, and 5 delivered messages per person per day (Singapore time).

The owner receives your first name, your `@username` if you have one, and whether it came "from a group" or "from a DM". They do not get the group's name, chat ID or any trip data. The bot does not store feedback or log its text.

When someone presses **Close this trip**, the bot posts one prompt in the group: "How was Who Paid For This on this trip? Send `/feedback` and your message. It goes straight to the person who built it." It posts this whenever the button closes a trip. It does not post it when `/settrip` closes a trip.

If the person who runs the bot has not set up feedback, `/feedback` replies "Feedback isn't set up on this bot." and the prompt is not posted.

### Commands

Typed commands do the same things as the menu buttons. A private chat has no trip, so the trip commands do not work there. `/settrip` says "Run /settrip inside your trip group." and the others say "Run /settrip first".

| Command | Where | What it does |
|---|---|---|
| `/start` | Group, private | In a group: posts the welcome menu and tries to pin it. It does not join you. In a private chat: a short intro with **Add me to a group for a trip** and **Split a bill now**. |
| `/help` | Group, private | What the bot does and the command list, with the bot's version. In a group it shows the menu under it. In a private chat it shows the two buttons. |
| `/join` | Group | Joins you to the open trip. Send it as a reply to join someone else. |
| `/settrip` | Group | Starts a trip: trip currency, home currency, timezone. Asks first if the group already has an open trip. |
| `/cancel` | Group, private | In a group: drops your unfinished `/settrip` setup. In a private chat: cancels your open one-off bill. |
| `/recent` | Group | The last 5 expenses. |
| `/today` | Group | Today's expenses. |
| `/summary` | Group | Group totals. |
| `/mine` | Group | What you paid and owe. |
| `/balances` | Group | Everyone's balances. |
| `/settle` | Group | Who pays whom, with a **Close this trip** button. |
| `/undo` | Group | Cancels your last expense, after you confirm. |
| `/export` | Group | Sends the trip's records as an `.xlsx` file. |
| `/deletetrip` | Group | Deletes every trip in the group and every expense in them. Only the starter of the open trip (or the latest closed one) can run it. |
| `/feedback` | Group, private | Sends a message to the person who runs the bot. |
| `/split` | Private | Starts a one-off bill. In a group it replies that `/split` needs a private chat. |

### Limits

| Limit | Value |
|---|---|
| Receipt reads per group chat per day | 40 by default |
| Receipt reads across the whole bot per day | 400 by default |
| Receipt reads per person in a private chat | 1 successful read and 3 tries per day (the owner is exempt) |
| Typed expenses | Unlimited |
| Draft expense card | Expires 30 minutes after the last press or reply |
| One-off bill | Expires 30 minutes after the last accepted action |
| Pending `/settrip` setup | Expires after 30 minutes |
| Photo size | 10 MB at most |
| Items for an item-by-item split | 1 to 40 |
| People on a one-off bill | 2 to 20, names up to 30 characters |
| Feedback | 1,000 characters, 5 messages per person per day |

A "day" for every daily limit is the calendar day in Singapore time. Counts are kept in memory and start again from zero when the bot restarts. The group limits can be changed by the person who hosts the bot (see [Environment variables](#environment-variables)).

When a group hits its limit, the bot says "This chat has hit today's limit for receipt photos. Type the amount instead, like 1200 yen ramen." When the bot-wide limit is reached in a group, it says "Today's limit for receipt photos across all groups has been reached. Type the amount instead, like 1200 yen ramen." When a group has 5 reads left for the day, the bot posts "Heads up: 5 receipt photos left in this chat today. Text expenses are unlimited."

Other things you can run into:

- A group has one open trip at a time.
- Only the person who sent an expense can press its buttons.
- Names are matched against people who have joined the trip (by `/join`, the **I'm in too** button, sending an expense, or being @-tagged).
- Only the starter of the open trip (or the latest closed one) can run `/deletetrip`, which deletes every trip in the group. The trip starter or whoever ran `/settle` can close it.
- A card that was open when the bot restarted says "This expense expired, please send it again".

### What is stored, and what is not

**Stored.** The database holds Telegram user IDs, usernames and display names. It holds expense descriptions, amounts, categories and timestamps. It holds the trip's settings (currencies, timezone) and a cache of exchange rates. For a receipt it holds only the facts the AI model read off it: merchant, total, date and line items. Anyone who posts in a group with an open trip gets a row with their Telegram ID, username and name, marked as not joined, even if they never join.

**Not stored.**

- **Receipt photos.** An AI model (Anthropic's API) reads each photo. The photo is downloaded, resized and sent for reading in memory only. It is never written to disk and never logged: not the image bytes, not the base64, not the request sent to the model. Once the read finishes, the bot keeps only the Telegram file ID (so **Try again** can fetch the same photo again) and the facts it extracted.
- **One-off bills.** They live in memory and are never saved. No database access happens on this path.
- **Feedback.** It is forwarded to the person who runs the bot and not kept. Its text is not logged.

**Deleting a trip.** `/deletetrip`, run by whoever started the open trip (or the latest closed one if none is open), hard-deletes every trip in that chat, not just the current one, and every row under them (people, expenses, shares and receipt lines) in one transaction. This cannot be undone. The cache of exchange rates is not tied to a chat and is not part of it. Nothing is deleted any other way: `/undo` marks an expense `cancelled` and keeps the row, and `/settrip` closes the old trip without deleting it.

## Running your own copy

You need a Telegram bot token, a Postgres database, an Anthropic API key, and somewhere to run one small process. The steps below use Neon for Postgres and Fly.io to run the bot.

### BotFather setup

1. In Telegram, open a chat with `@BotFather` and create a bot with `/newbot` (or pick yours with `/mybots`).
2. **API Token**: copy it into `TELEGRAM_BOT_TOKEN`. Treat it like a password.
3. **Bot Settings, Group Privacy, Turn off** (see Privacy mode below).
4. Optional but recommended: **Edit Bot, Edit Commands**, and paste this complete list. It matches the commands the bot registers. The bot does not set its command list itself.
   ```
   start - Open the menu in a group, or the intro in a private chat
   help - What this bot does
   join - Join this trip
   settrip - Start a trip for this group
   cancel - Cancel a trip setup or a one-off bill
   recent - Last 5 expenses
   today - Today's expenses
   summary - Group totals
   mine - What I paid and owe
   balances - Everyone's balances
   settle - Who pays whom
   undo - Cancel my last expense
   export - Download this trip's records
   deletetrip - Delete every trip in this group
   feedback - Tell the person who built this what to fix
   split - Split one bill in a private chat
   ```
5. Open the bot in a private chat and press **Start** once. This lets the bot message you, which feedback needs if you are the owner.

### Privacy mode

The bot reads ordinary messages like `1200 ramen`, so Telegram's privacy mode must be **off** (BotFather, Bot Settings, Group Privacy, Turn off). If the bot was already in a group when you changed it, remove it and add it again, or it keeps the old setting.

### Neon (Postgres)

Postgres on Neon is the only store (ADR 0003). There is no spreadsheet, no service account and no per-trip setup beyond `/settrip` itself.

1. Create a [Neon](https://neon.tech) project. Pick the region closest to where the bot runs (the Fly app uses `sin`, Singapore).
2. Copy the pooled connection string (it includes `sslmode=require`).
3. Put it in your local `.env` as `DATABASE_URL=...`.
4. `fly secrets set DATABASE_URL=...` for `whopaid-bot` in production.
5. Install Docker Desktop or Colima if you want to run the database tests locally (see Local testing).

At startup the bot creates a small connection pool and applies any pending migrations (`app/db/migrations/`). A missing or unreachable `DATABASE_URL` fails fast with a one-line error.

### Environment variables

Set these in `.env` locally (copy `.env.example`, and never commit `.env`) and as Fly secrets in production. This table gives names only.

| Variable | Required or default | What it does |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Required | The bot's token from BotFather. |
| `DATABASE_URL` | Required | Neon's pooled Postgres connection string (`sslmode=require`). |
| `ANTHROPIC_API_KEY` | Required | API key from console.anthropic.com, used to read receipts. |
| `LOG_LEVEL` | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. Any case is accepted. |
| `BOT_VERSION` | `dev` | Short git SHA. It is baked into the image at deploy time and shown by `/help`. You don't set it by hand. |
| `OWNER_USER_ID` | Unset | Your numeric Telegram user ID. You become the owner: `/feedback` messages are sent to you. Leaving it unset turns feedback off. It also means nobody is exempt from the private-chat photo allowance. |
| `RECEIPT_READS_PER_CHAT_PER_DAY` | `40` | Daily cap on receipt reads for one group chat. `0` refuses every read. |
| `RECEIPT_READS_PER_DAY` | `400` | Daily cap on receipt reads across all chats, private chats included. `0` refuses every read. |

`./scripts/set-fly-secrets.sh` copies only the three required secrets from `.env` to Fly. Set the others by hand, except `BOT_VERSION`, for example `fly secrets set OWNER_USER_ID=<your numeric Telegram ID> -a whopaid-bot`. Setting a secret restarts the bot. Leave `BOT_VERSION` alone: the deploy bakes it into the image, and setting it as a Fly secret would freeze the version `/help` shows.

### LLM setup and cost

Receipt reading uses the Anthropic API (`ANTHROPIC_API_KEY`). In the [Anthropic Console](https://console.anthropic.com/), set a monthly spend limit on the API key, so a bug or a flood of photos can't run up a surprise bill. A few dollars is plenty for a trip's worth of receipts. Each receipt read costs a fraction of a cent to a couple of cents. You can watch usage under Usage in the Console.

The daily caps in the table above limit the cost further. A private-chat read by anyone, the owner included, counts against `RECEIPT_READS_PER_DAY`.

### FX provider

Rates come from [Frankfurter](https://frankfurter.dev) (no key), which publishes the European Central Bank's daily reference rates. Rates are cached in the `exchange_rates` table. Rates are the ECB's reference rates, not what your card or money changer charged.

### Local testing

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uvx pre-commit run --all-files
uv run python -m app
```

Most tests need no network and no keys. They use an in-memory store, fixed exchange rates and a fake receipt reader, and simulate several Telegram users pressing buttons.

Tests marked `postgres` need a real Postgres. They look for it at `TEST_DATABASE_URL`, which defaults to `postgresql://postgres:postgres@localhost:5433/whopaid_test`. `docker compose up -d` starts a matching Postgres 16 on port 5433. If no database answers within two seconds, the `postgres` tests are skipped with a message that names the host and port. If the `CI` environment variable is set, they fail the run instead of skipping, so a CI run can't drop that coverage by accident.

Run the bot locally only while the deployed one is stopped (see Deployment). `docs/manual-test-checklist.md` is the full manual regression run against a test group.

### Deployment

The bot runs on Fly.io as one machine, long-polling Telegram. **Stop the local bot before deploying, and never run both at once.** Two pollers on the same bot token make Telegram return 409 and both go deaf.

1. Install flyctl (`brew install flyctl`, or see [fly.io/docs/flyctl/install](https://fly.io/docs/flyctl/install/)) and run `fly auth login`.
2. `fly launch --no-deploy --copy-config --name whopaid-bot --region sin`. Answer **No** to Postgres, Redis and any other add-on. If Fly says the name is taken, accept the name it suggests (or use `whopaid-bot-<4 chars>`). `--copy-config` writes it into `fly.toml`, and every later command uses that name.
3. `./scripts/set-fly-secrets.sh` copies the secrets from `.env` to Fly. It prints the variable names it set, never the values.
4. `./scripts/deploy.sh`. Wait for "deployed successfully". It refuses to run with uncommitted changes.
5. `fly status`. Exactly one machine, state `started`. If `fly machine list` shows two, run `fly scale count 1`.
6. `fly logs`. You see `whopaid started`.
7. Stop any local copy of the bot, then send `/help` and `500 coffee` in your test group and confirm it. The database gets the row.

#### Updating (GitHub Actions)

A push to `main` runs `.github/workflows/deploy.yml`. It has two jobs:

1. **test** starts a Postgres 16 service, then runs `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest -q`. `CI` is set, so the `postgres` tests must run.
2. **deploy** runs only if **test** passes. It runs `flyctl deploy --remote-only --ha=false` with `BOT_VERSION` set to the short git SHA.

A push that changes only files under `docs/` or Markdown files anywhere (`**.md`) skips the whole workflow, so neither the tests nor the deploy run. A deploy restarts the bot, which clears every unfinished card, so a docs-only change should not trigger one. **Run workflow** in the repo's **Actions** tab redeploys `main` by hand. Confirm `/help` in the test group shows the new version. `./scripts/deploy.sh` from a laptop still works too.

The workflow needs one repo secret, `FLY_API_TOKEN`: a deploy token for this app only. Create it in the Fly dashboard (the app, then **Tokens**, then create a deploy token, or `fly tokens create deploy` on a laptop) and save it in GitHub under Settings, Secrets and variables, Actions, New repository secret. If the token leaks, revoke it in the same Fly page and make a new one.

#### Log level

`LOG_LEVEL` is not set by `./scripts/set-fly-secrets.sh`, because it is not a secret. If you need it, set it by hand: `fly secrets set LOG_LEVEL=DEBUG`.

#### Why no port

The bot uses long polling: it calls out to Telegram, and nothing listens for incoming connections. There is no `[http_service]` in `fly.toml` and no Fly health check. The `whopaid started` line in `fly logs` is the health signal. If it is there, the bot is up.

### Announcing an update

`python -m app.announce` sends one message, through the bot, to every group with an open trip. Write the message in a file (HTML, like every bot reply: `<b>bold</b>`), then:

```bash
uv run python -m app.announce --file whats-new.txt              # dry run: counts only
uv run python -m app.announce --file whats-new.txt --only <id>  # try it in one group
uv run python -m app.announce --file whats-new.txt --send       # every open trip
```

It reads `TELEGRAM_BOT_TOKEN` and `DATABASE_URL` from `.env`, like the bot. On Fly, pass the text inline instead: `fly ssh console -a whopaid-bot -C "python -m app.announce --text '<b>New</b>\n…' --send"` (a literal `\n` becomes a new line). It prints counts only, never chat IDs. A group that removed the bot is skipped.

### Stopping after the trip

1. In the group, run `/settle` and agree the transfers.
2. Tap **Close this trip** under the `/settle` message. The trip then takes no new expenses, and you can still run `/export`.
3. `fly scale count 0` stops the machine and the billing, keeping the app and its config. To use it for the next trip, `fly scale count 1`.
4. `fly apps destroy <app name from fly.toml>` removes the app entirely, if you are done for good.

The database keeps everything. Expenses, balances and settlement live in Postgres, not on the machine. `/settrip` again starts a new trip for the same group. `/deletetrip` erases every trip of the group.

## How it works

### Architecture

```mermaid
flowchart LR
    TG[Telegram group] -- long polling --> BOT[bot<br/>one Fly.io machine]
    BOT -- replies, draft cards --> TG
    BOT -- receipt photo, in memory --> LLM[Anthropic API<br/>receipt reading]
    BOT -- day's rate --> FX[Frankfurter<br/>ECB reference rates]
    BOT -- one transaction per save --> DB[(Postgres on Neon)]
```

- **Bot** (`app/bot`): Telegram handlers, the draft cards and their buttons, the menu, feedback and the one-off bill handlers.
- **Services** (`app/services`): pure logic. The text parser, name resolution, splitting, settlement, receipt reading and total selection, exchange rates, the receipt read budget, the photo allowance, the one-off bill and feedback helpers. It also holds `PostgresLedgerStore`.
- **Drafts** live in memory for 30 minutes after their last press or reply (`_DRAFT_TTL` in `app/services/drafts.py`). Only a confirmed expense is written to the database, in one transaction.
- The private-chat paths (one-off bill and feedback) sit beside this and send nothing to the database.

### Data model

Postgres tables, created by `app/db/migrations/001_initial.sql`. `002_original_rate.sql` adds the third-currency rate columns to `expenses`.

| Table | One row is | Key columns |
|---|---|---|
| `trips` | one trip of one chat | currencies, timezone, who started it, `closed_at` (one open trip per chat) |
| `trip_users` | one person in a trip | Telegram user ID, name, joined |
| `expenses` | one confirmed or cancelled expense | payer, description, original amount, trip and home amounts, rate, split method, status |
| `splits` | one person's share of one expense | share in trip and home currency |
| `line_items` | one line from a receipt | amount, who had it, `claimed` / `everyone` / `proportional` for an item-by-item split (`unassigned` otherwise), kind |
| `exchange_rates` | one cached rate | currency pair, requested date, rate date, rate |

Amounts are stored in minor units (yen as whole numbers, SGD in cents). Every expense's shares add up exactly to its amount in both currencies. Expenses are never deleted: `/undo` marks them `cancelled`. `/export` writes them to an `.xlsx` with sheets for expenses, shares, line items, people and balances. `expenses` and `trip_users` rows belong to a trip, and `splits` and `line_items` rows belong to an expense. Each is deleted with its parent (`on delete cascade`). That is how `/deletetrip` clears every trip of a chat with one `delete from trips`.

### Splitting model

Every expense has one **payer** (who handed over the money) and one or more **participants** (who owe a share). The buttons and message shortcuts that choose them are described in [How splits work](#how-splits-work).

- **Split equally**: everyone gets `total // n`. The leftover minor units go one each to participants in Telegram user ID order, skipping the payer while anyone else is left.
- **Typed amounts**: the card shows `Remaining: ¥0 ✅` only once the lines add up exactly, and only then offers **Confirm**.

Shares are computed in the currency the expense was entered in. They are then converted to the other currency share by share and reconciled with the largest-remainder method, so shares always add up exactly to the expense amount in both currencies.

### Itemised receipts

Tax, service charge and discounts are spread in proportion to what each person's items cost, again with the largest-remainder method so the yen add up exactly. The `line_items` table records who had each item and whether it was `claimed`, went to `everyone`, or was spread `proportional`ly.

If the lines don't add up to the receipt total, the difference becomes one **Unlisted / rounding** line, stored as a service charge or discount and shared like tax. A total the bot picked on its own is at most 5 minor units off the lines and needs no prompt. A total you picked or typed gets a prompt first.

Items marked **Not on this bill** are left out. An itemised split bills only the kept items, with adjustments scaled to their share of the item sum. Item amounts in `line_items` are always in the trip currency. For a receipt paid in the home currency they are converted at that expense's rate.

### Settlement logic

Balances and settling up are in the home currency only, each expense converted at its own day's rate (see `docs/adr/0001`). For each person, **paid** is the total of expenses they paid, **share** is the total of their shares, and **net** is paid minus share. A positive net means they are owed. Cancelled expenses don't count.

`/settle` is greedy. It repeatedly has the person who owes most pay the person owed most, as much as one of them needs. That takes at most one transfer fewer than the number of people with a balance. It is near-minimal, not guaranteed minimal.

### The one-off bill

A one-off bill is built beside the group draft engine, not inside it. It lives in `app/services/oneoff.py` (the bill, its steps and the in-memory store, with no Telegram import), with its own render, keyboard and handler modules. It does not use `DraftService`, the ledger or the stores.

- **In memory only.** Bills are keyed by Telegram user ID, one per user. A bill expires 30 minutes after its last accepted action and is removed on `Done`, `Cancel`, `/cancel` or restart. No database access happens on this path.
- **People are slots.** A person is a numbered label (1 to N, 2 to 20), with the user first. Person numbers are the IDs passed to the same `split_equal`, `split_custom` and `split_items` functions the group flow uses, so shares always add up to the bill amount.
- **One currency.** The bill is in the currency as typed, SGD by default. There are no rates and no conversion.
- **Who gets the next private message.** Two things can wait for the next private text: a feedback wait and a one-off bill. Registration order in `app/bot/app.py` decides. Feedback comes first, so a user with both has their next text taken as feedback and keeps the bill. All of these handlers require an original message, so an edited message is never taken as input. Commands do not end a bill, except `/cancel`.

### The photo allowance and the receipt gate

Receipt reads are limited at two levels. Both are in memory and use the Singapore calendar day.

- **Receipt read budget** (`app/services/receipt_budget.py`). It has a per-chat cap and a bot-wide cap, from `RECEIPT_READS_PER_CHAT_PER_DAY` and `RECEIPT_READS_PER_DAY`. A group read takes one from both (`try_take`, bot-wide cap checked first). A private-chat read takes one from the bot-wide count only (`try_take_global`). A refused attempt never counts.
- **Photo allowance** (`app/services/photo_allowance.py`). In a private chat, a person who is not the owner may have 1 successful read and 3 attempts a day. The owner is never refused by it. It is called through three async functions (`may_try`, `record_success`, `record_failure`), so a database-backed version could replace it without changing the callers.

In a private chat, one gate (`app/bot/oneoff_receipt_gate.py`) owns the order of checks: mark that this person has a read running, check the allowance, show the AI-model notice and download the photo, take from the bot-wide cap just before the model is called, call the model, record the result, and release the mark. Only an attempt that reaches the model counts, on the cap and on the allowance. A refused attempt, a failed download and an image that cannot be decoded count nowhere. A receipt the model cannot read counts as one of the 3 tries and takes one from the bot-wide cap. The reader takes two currency codes instead of a trip, and a private chat passes SGD for both.

The group path checks the trip gate, then the budget, before it downloads the photo. Text expenses and every other command are never capped.

### The owner and feedback

The owner is one Telegram user ID, set in `OWNER_USER_ID`. It is the only identity the bot treats differently from any other user. Leaving it unset turns feedback off and exempts nobody from the photo allowance.

`/feedback` sends the text to the owner's private chat, as one message with the sender's first name, `@username` if any, and `from a group` or `from a DM`. A per-person daily counter (5 a day, Singapore time) sits in memory, as the receipt budget does. Only a message actually delivered counts. If the send fails, the user is told and it does not count. The bot logs events only (`feedback sent`, `feedback refused: limit`, `feedback failed`), never the text, names or the owner's ID. After `Close this trip` succeeds, the bot posts a feedback prompt in the group, outside the code that edits the settle message, so a failed edit does not block it.

### Limitations

- **Single machine only.** Exactly one bot process may poll Telegram. Two (for example a laptop and Fly) make both stop receiving messages.
- **Drafts and one-off bills are lost on restart.** A trip card that was mid-way when the bot restarted says "This expense expired, please send it again". A one-off bill is gone, and text sent after a restart gets no reply.
- **Counts reset on restart.** The receipt budget, the photo allowance and the feedback limit are in memory.
- **Albums are separate drafts.** Several photos sent together each become their own expense.
- **A typed rate is yours.** For a currency with no published rate, the bot uses whatever you reply with, for that expense only.
- **Home-currency figures depend on ECB reference rates.** These are published once per business day. They are not the rate your card or money changer used.
- Receipt reading can be wrong. Check the total on the card before confirming.
- Names are matched against people who have joined the trip (by `/join`, the **I'm in too** button, sending an expense, or being @-tagged). Telegram doesn't let bots see the member list.
- A one-off bill has one currency and no conversion. It trusts the currency the receipt shows if that is a real currency code, and otherwise uses SGD, with no confirm step.
