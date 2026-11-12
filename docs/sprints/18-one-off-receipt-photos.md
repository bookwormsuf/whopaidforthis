# Sprint 18 — Receipt photos on a one-off bill

## Goal
A one-off bill can start from a photo of the receipt, and be split item by item among its
persons. Each person can have one receipt read a day in a private chat; the owner has no such
limit. Decided with Sufyan on 2026-11-12.

## In scope
- A photo at the first step of a one-off bill: read, total chosen, then the Sprint 17 flow.
- The item-by-item split for persons: "Who had this?", shared items, tax and service shared in
  proportion, `Fix an item`.
- A per-user daily photo allowance in private chats, with the owner exempt.
- A one-line notice about the AI model before a user's first read.
- Spec §23; CONTEXT.md (*photo allowance*); manual checklist; tests.

## Out of scope
- `Not on this bill` (left-out items) on a one-off bill.
- Photos sent as files (documents), captions, several photos for one bill.
- Starting a bill by sending a photo with no bill open (a photo then gets the `/start` intro; amended 2026-11-13, was: no reply).
- Currency conversion, a confirm-the-currency step, typed rates.
- Storing the allowance in Postgres. It is in memory, built so that swap is one class.
- Any change to group behaviour. The group photo flow, its budget rules and its texts stay.
- README (updated once, after this sprint).

## Sprint pins (decided with Sufyan, 2026-11-12)

### Where a photo is read
- Only at step 1 of a one-off bill, where the bot is waiting for the bill line. The step 1
  prompt becomes ``Send the bill, like <code>84.50 dinner</code>, or a photo of the receipt.``
  Sprint 17's "Photos aren't read here yet…" reply is retired.
- A photo at any other step gets no reply. With no bill open it gets the private `/start`
  intro (amended 2026-11-13; was: no reply).
- Compressed photos only (as in groups). Over 10 MB: the group's text, unchanged: `That
  photo's too large (over 10 MB), please send a smaller one.` Not counted.
- The caption is ignored.
- While a read is running for this user, another photo gets `One receipt at a time. Still
  reading the first.` (see the allowance section).

### Photo allowance (per user, per day)
- A day is a calendar day in `Asia/Singapore`. Counts are in memory and reset on restart.
- A user who is not the owner may have **1 successful read** a day and **3 attempts** a day
  (the first try and two more after failures). A successful read ends the day's allowance.
- The owner (`OWNER_USER_ID`) is never refused by the allowance. Unset owner: nobody is exempt.
- Every attempt that reaches the model, by anyone including the owner, also takes one from the
  existing bot-wide daily cap (`RECEIPT_READS_PER_DAY`, 400). The per-chat cap (40) does not
  apply in a private chat: `ReceiptReadBudget` gains a method that takes from the global count
  only. Its existing `try_take` and the group path are unchanged.
- Order (amended at review, 2026-11-12): size as Telegram reports it; then "is this user
  already having a receipt read?"; then the user's allowance; then download and image
  preparation; then the bot-wide cap, taken immediately before the model is called; then the
  read. Only an attempt that reaches the model counts, on the cap and on the allowance. A
  refused attempt, a failed download, an undecodable image and a photo found too large at
  download count nowhere.
- One read at a time **per user**, not per bill: from the moment an attempt is accepted until
  its read ends, any other photo or `Try again` from that user, on any bill, gets `One receipt
  at a time. Still reading the first.` The user is marked before the first `await`, so two
  photos or two taps arriving together cannot both pass. (Amended at review: the guard had been
  per bill, which let a user cancel mid-read and start another.)
- A photo found too large only at download gets the too-large text and the bill goes back to
  step 1; there is no `Try again` for it.
- Texts:
  - already had today's read: ``You can read 1 receipt photo a day here. Type the bill
    instead, like <code>84.50 dinner</code>. For more, add me to a group for a trip.``
  - three failed attempts today: ``That's 3 tries today. Type the bill instead, like
    <code>84.50 dinner</code>.``
  - bot-wide cap reached: ``Receipt photos are used up for today across the whole bot. Type
    the bill instead, like <code>84.50 dinner</code>.``
  After any of these the bill stays at step 1, waiting for a typed bill line.
- The allowance is one small class with no Telegram import; the caller passes an aware `now`.
  It is used only through three **async** calls (may this user try, record a success, record a
  failure), so a Postgres-backed class can replace it later without touching the caller.
  The whole sequence above (in-flight mark, allowance, cap, recording) has one owner: a gate
  beside `app/bot/receipt_gate.py` that returns a verdict; the handler only shows cards.
  (Amended at review.)

### The notice
- Before a user's first allowed read since the bot last started, the bot sends, as its own
  message: `I read receipts with an AI model. The photo isn't stored.` Once per user per
  process (in memory). Not sent when the attempt is refused.

### Reading
- When a read is allowed the bot sends `Reading the receipt…`; that message becomes the bill's
  card (edited in place) when the read ends.
- The reader's interface changes from taking a `Trip` to taking two currency codes
  (`trip_currency`, `home_currency`). The prompt text is unchanged. The group call site passes
  the trip's two codes; a private chat passes `SGD`, `SGD`. This is the only edit to the group
  photo path, and its tests must pass with only the call-shape updated.
- Currency: the reader's currency if it is an ISO code; otherwise SGD. No confirm step.
- Reused as they are, from the receipt parser: the image preparation, `to_receipt`, the
  100-times check, the included-tax rule, `select_total`. Nothing is written to disk. The bill
  keeps the Telegram photo file ID in memory for `Try again`, never the bytes.
- Description: the receipt's; `Receipt` if it has none.

### Cards after a read
Wording, button labels and layout are copied from the group cards (read
`app/bot/render/draft.py`, `app/bot/keyboards/receipt.py` and `keyboards/items.py` right before
writing). Differences are listed here. Every card keeps the Sprint 17 title line.

- **Couldn't read.** `🧾 <b>Receipt</b>`, blank line, `Couldn't read that receipt`; or, for a
  reader outage, the group's sentence with its ending changed: `Couldn't read that receipt: the
  receipt reader is <problem>, not a problem with your photo. Try again in a minute, or type
  the bill.` Buttons: `[Try again]` `[Type the bill instead]`, then `[Cancel]`.
  - `Try again` is a new attempt: allowance, then bot-wide cap, then read, with the card
    showing `Reading the receipt…` meanwhile. A refusal posts its text as a new message and
    leaves the card.
  - `Type the bill instead`: the card becomes the step 1 prompt text and the bill waits for a
    typed line.
- **Which total** (only when the receipt has more than one plausible total): `Which is the
  final total?`, one button per candidate (up to 3), `[Other amount]`, `[Cancel]`.
  `Other amount`: the card says `Send the amount.` and the next text is read as an amount in
  the receipt's currency; unreadable text gets `Send just the amount, like 84.50.`
- **Doesn't add up** (chosen total more than 5 minor units from items plus adjustments): the
  group's three lines (`Items … + adjustments … = …`, `Receipt total … (difference …)`,
  `(… isn't on any line; it'll be shared like tax)`), without the "Paid for part of the
  order?" line. Buttons `[Continue with <total>]` `[Cancel]`. The gap becomes an `Unlisted /
  rounding` line shared like tax, as in groups.
- Then the Sprint 17 flow from step 3 (how many people). The step 3 card for a receipt has the
  line `<n> items on the receipt` (`1 item on the receipt` for one, as the group card says)
  under the title, and its hint line is always shown, as
  ``<i>Wrong amount or currency? Type the bill instead, like 1200 yen ramen.</i>`` A typed bill
  line at step 3 replaces the bill, as in Sprint 17.
- **Split method** for a receipt with 1 to 40 claimable items: body `Who had what?`, buttons
  one per row: `[🍽 Go item by item ▶]`, `[Split equally]`, `[Type amounts instead]`,
  `[Cancel]`. Over 40 items: Sprint 17's two choices and the line `Too many items to split
  individually`. No readable items: Sprint 17's two choices.
- **Who had this?** As the group screen: `Who had this?  (n of N)`, the item in bold and its
  amount; one toggle per person, three per row, `✓ ` when ticked, the user as `Me`; then
  `[Everyone]`; then `[◀ Back]` (not on the first item unless opened from Fix) and
  `[Next ▶]`; then `[Cancel]`. No `Not on this bill` button.
  - Two or more ticks share the item equally. `Everyone` ticks all and moves on. `Next` with
    nobody ticked: toast `Pick who had this first`.
  - A line with quantity 2 to 10 becomes one screen per unit, as in groups, unless that would
    pass 40 items.
- **Preview** for an item split: `Check before sending`, `Each person's share:`, then per
  person the group's two lines (name and share; then `items X + tax Y + service Z − discount W
  ± unlisted U`, leaving out parts that are zero), then `Shared in proportion: …`. Buttons:
  `[Done]` `[Fix an item]`, then `[Edit]` `[Cancel]`.
  - `Edit` keeps who had what: claims belong to the receipt, not to the split method, so
    after `Edit` the item walk opens with the earlier ticks in place. (Amended at review.)
  - `Fix an item`: `Which item do you want to change?`, one button per item with its claimants'
    initials (`all` for everyone), then `[◀ Back to summary]`.
- **Summary**: as Sprint 17, with line 2 ending `split by items`.

### Maths
- `split_items`, `allocate_proportional` and `split_equal` unchanged, called with person
  numbers. Shares always sum to the bill amount.
- Where a rule lives in a helper private to the group draft engine (one screen per unit, the
  `Unlisted / rounding` line), the one-off bill has its own copy, and a parity test feeds the
  same receipt to both and compares the results.

### Privacy and logging
- The image goes Telegram → memory → the model, as in groups. Never logged, never on disk.
- Log lines carry the event only: `oneoff photo read`, `oneoff photo failed`, `oneoff photo
  refused: user`, `oneoff photo refused: tries`, `oneoff photo refused: global`. No item
  names, merchants, amounts or names.
- Still no database access on the one-off path.

### Settled at build (agent's calls, open to change)
- Cards before a total exists (which total, `Send the amount.`, doesn't add up) have the title line without an amount, `🧾 <b><description></b>`, as the group's cards do; the couldn't-read cards use the literal `Receipt`. From the how-many card on, the title carries the amount.
- `<n> items on the receipt` counts every `item` line as printed (a `Tea x3` line is one), before units are split, and reads `1 item` for one, as the group card does (`app/bot/render/draft.py`). Like the group card it has no such line when the receipt has no lines at all; a receipt with lines but no `item` among them says `0 items`. (Changed at review.)
- The walk and `Fix an item` cards have the title line, then `<payer> paid` on the next line, as the group's header does and as Sprint 17's preview does. The item summary is `Check before sending`, then `Each person's share:`.
- Preview terms: tax, service, discount and unlisted are left out for a person whose part is zero. `items X` stays (it is the only part when someone had nothing, so the line is never empty).
- `Other amount` card has no buttons, as the group's has; `/cancel` or `Split a bill now` then `Yes, start again` end it. The reply must be exactly an amount (no other words) in the receipt's currency, read by the Sprint 17 `_read_amount` rule. Zero, negative, another currency or extra words get `Send just the amount, like 84.50.`
- The doesn't-add-up card appears for a chosen, picked or typed total more than 5 minor units from items plus adjustments, as pinned. (The group asks on any difference for a picked or typed total; here a gap of 5 or less becomes the `Unlisted / rounding` line with no card.)
- `Edit` on an item summary keeps the picks: claims belong to the receipt, so the walk opens on item 1 with the earlier ticks in place. A new read, `Type the bill instead` and a replaced bill clear them. (Changed at review.)
- While a user has a read running, a second photo or `Try again` from that user, on any bill, gets `One receipt at a time…`. A photo on the couldn't-read card, or at any step after step 1, gets no reply. (Changed at review: per user, not per bill.)
- `Reading the receipt…` is a new message for a photo (the step 1 prompt stays above it); for `Try again` the card is edited in place. If the user deletes it, the result goes out as a new card.
- The allowance is called as async `may_try(user_id, now, *, is_owner)`, `record_success(user_id, now)`, `record_failure(user_id, now)`. Only an attempt that reaches the model counts. A read that reached the model counts by its result even if the bill was cancelled or replaced meanwhile. A reader outage or crash, an unreadable receipt, counts as a failed try (and one from the bot-wide cap). A download failure, an undecodable image, a photo found too large at download, and a cap refusal count nowhere. (Changed at review.)
- The notice is `PhotoNotices.take(user_id)` on `Deps`, marked when it is sent, after the allowance has passed. Because the bot-wide cap is now taken just before the model call, an attempt refused by the cap has already had the notice and `Reading the receipt…`; the card then goes back to the step 1 prompt and the cap text follows. (Changed at review.)
- The sequence has one owner, `ReceiptGate` (`app/bot/oneoff_receipt_gate.py`): in-flight mark (per user, before any await), allowance, then the handler's notice, `Reading…`, download and image preparation, then the bot-wide cap immediately before the model call, then the call and the recording, then release in a `finally`. A failure mid-attempt puts a `reading` bill back to `awaiting_bill` or `couldnt_read`. (Changed at review.)
- The private path has its own small download-and-read function. `read_receipt_photo` and the group handler are untouched apart from the reader's two codes.
- Logging: refusals at `warning`, `oneoff photo read` and `oneoff photo failed` at `info`. A reader that fails in an unexpected way logs the fixed text `oneoff photo: reader failed unexpectedly` at `warning`, with no exc_info and no data; that is the only other event name. Nothing else is logged from this path.
- `Try again` has its own `block=False` callback registration (`^oneoff:<id>:retry$`); every other button stays blocking. It enters the gate before the callback is answered and re-fetches the bill by ID; the query is answered once the attempt is accepted. A refused tap is answered by the callback's `finally`; a `Pick who had this first` toast answers with its text.
- Photo updates are de-duplicated with `Deps.recent_updates`, as the group handler does.
- The copied constants (40 items, 10 units, `Unlisted / rounding`, `Pick who had this first`) are asserted equal to the group's in a test, with the parity tests for units and the unlisted line.
- `Fix an item` initials: a person still called `Person 3` is `P3`; others as the group's `initials_for`.
- Callback data: `retry`, `type`, `total:<n>`, `manual`, `continue`, `items`, `wtoggle:<pos>,<person>`, `weveryone:<pos>`, `wnext:<pos>`, `wback:<pos>`, `fixlist`, `fixpick:<n>`, `fixback`. Longest is `oneoff:<8>:wtoggle:39,20`, 29 bytes. The most buttons on a card is the 40-item fix list (41); a 20-person item screen has 24.

### Changed at review (2026-11-12)
1. One gate owns the sequence (`app/bot/oneoff_receipt_gate.py`): in-flight set per user, marked before any await; the cap is taken just before the model call; recording after; release in a `finally`. `Try again` enters it before answering and re-fetches the bill. Closes the `Try again` double-tap and the cancel-then-`/split` bypasses of the daily limit.
2. `PhotoAllowance.may_try`, `record_success` and `record_failure` are async; `InFlightReads` (per user) sits beside it on `Deps`.
3. Item claims live on the bill (the receipt), not on `ItemsSplit`; `Edit` keeps them and the walk reopens with the ticks in place.
4. `1 item on the receipt` for exactly one item; `0 items` as the group card says.
5. A reader that crashes unexpectedly logs the fixed text `oneoff photo: reader failed unexpectedly` (no exc_info, no data).

## Before you start (you)
Nothing. Tests use the fake reader; no key is needed.

## Agent builds
1. Reader interface: two currency codes in place of `Trip`; group call site; fake reader.
2. `ReceiptReadBudget`: a global-only take. Tests.
3. The photo allowance class. Tests (day roll, success ends the day, three tries, owner).
4. `app/services/oneoff.py`: receipt states (reading, couldn't read, which total, other
   amount, doesn't add up, item walk, fix list), units, the unlisted line. Parity tests.
5. Render, keyboards, handlers; the photo handler for private chats (`block=False`, as the
   group photo handler is, so a slow read does not hold up other updates).
6. Step 1 prompt text; retire the Sprint 17 photo reply.
7. Spec §23 and CONTEXT.md are already written on this branch: check the code matches them.
8. `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.

## Tests required
- A photo at step 1: notice, `Reading the receipt…`, then the how-many card with the items
  line and the hint; the whole path item by item to a summary ending `split by items`; shares
  sum to the total.
- Shared item between two persons; an odd cent; tax and service in proportion; a discount.
- Hypothesis: random receipts, 2 to 20 persons, random claims: shares sum exactly.
- Parity with the group engine for units and for the unlisted line.
- Which total: two candidates; `Other amount` with a good and a bad reply.
- Doesn't add up: the card, `Continue`, the unlisted line shared like tax.
- Over 40 items and zero items: no item-by-item button.
- `Fix an item` changes one claim and returns to the summary; `◀ Back` keeps picks; `Next`
  with nobody ticked; stale item buttons change nothing.
- Allowance: a second photo the same day is refused with the pinned text; a failed read can be
  retried twice, the fourth attempt is refused; a success after a failure ends the day; the
  next Singapore day allows again; another user is unaffected; the owner is never refused;
  with no owner set nobody is exempt.
- Bot-wide cap: refused with the pinned text; every allowed attempt takes one, a refused one
  takes none; the per-chat cap is not touched; group reads still use `try_take` (regression).
- The notice: once per user, not on a refused attempt.
- Couldn't read: both texts; `Try again` success and failure; `Type the bill instead`.
- A photo at another step, with no bill open, over 10 MB, and during a read.
- Reader: the prompt text is byte-for-byte what it was for the same two codes (regression);
  group photo tests pass.
- No ledger or store call on the path; no log record contains an item, merchant or amount;
  nothing is written to disk.

## After: your checklist
1. Private chat, `Split a bill now`, send a receipt photo. The notice, `Reading the
   receipt…`, then the card with the item count.
2. Three people, names, who paid, `Go item by item`. Share one dish between two. `Done`. The
   summary adds up to the receipt total.
3. Send a second receipt the same day from another account (or ask a friend): refused with the
   1-a-day text. From your own account: read again, no refusal.
4. A blurry photo from the other account: `Couldn't read that receipt`, `Try again` works twice
   more, then the 3-tries text.
5. A receipt whose items don't reach the total: the "doesn't add up" card, `Continue`.
6. A receipt photo in a trip group behaves exactly as before.

## Exit criteria
- [ ] Checklist passes
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
