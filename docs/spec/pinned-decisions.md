# Pinned implementation decisions

Settled in the grilling session on 2026-09-28/29. Implementing agents execute these **verbatim**.
If one turns out to be impossible, stop and report; do not substitute.
Vocabulary: see `/CONTEXT.md`. Rationale for the two irreversible ones: `/docs/adr/`.

## 1. Repo, bot, stack

- Repo: this one (`whopaid`). Bot username `@whopaidforthisbot`; read the actual username from `getMe` at startup, never hardcode it.
- Python 3.12+, `uv` + `pyproject.toml`, `ruff`, `pytest` + `hypothesis`, `python-telegram-bot` v21 (async, long polling), `asyncpg`, `pydantic` v2, `httpx`, Docker.
- Layout as in the brief (`app/bot/{handlers,keyboards,conversations}`, `app/services`, `app/models`, `app/config`, `tests/`).
- Secrets only via environment variables. `.env` is gitignored and used only for local runs; production uses `fly secrets set`. Commit `.env.example` with blank values. Add a pre-commit hook (`gitleaks` or equivalent) so the repo can go public. Never log credentials, receipt images, or LLM image payloads.
- Env vars: `TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `LOG_LEVEL`. No `FX_API_KEY`, no `DEFAULT_TIMEZONE` (these are per trip).

## 2. Trips (multi-trip, one Sheet each)

> Superseded 2026-10-15 by §18 (Sprint 9): no Sheet, no binding, no control Sheet. The button order and timezone default below still hold.

- One Telegram group = one trip. Binding is manual: the user creates a blank Sheet, shares it with the service-account email as editor, then runs `/settrip <sheet id or url>` in the group.
- `/settrip` then asks, via buttons, trip currency, home currency, timezone (default: the timezone conventional for the trip currency, e.g. JPY→Asia/Tokyo, SGD→Asia/Singapore). It writes the `Trip` tab and creates all other tabs, headers and Summary formulas. Re-running `/settrip` on a bound group asks for confirmation and rebinds; it never wipes the old Sheet.
- The group→Sheet mapping is persisted in a `Bindings` tab (Chat ID | Sheet ID | Bound At) of a single **control Sheet** (`WHOPAID_CONTROL_SHEET_ID`). Nothing is stored on the machine running the bot. The `Trip` tab inside each trip Sheet is for humans and holds the same facts.
- Groups with no binding get one reply, "Run /settrip first", to commands and are otherwise silent. There is no `ALLOWED_CHAT_IDS`; an unbound group is ignored.
- Currency minor-unit table lives in code: 0 decimals for JPY, KRW; 2 for everything else supported. Supported currencies = the Frankfurter list.

### 2a. Wording (added 2026-09-29 after the Sprint 1 checklist)

- Users never see the word "bind". Re-running `/settrip` on a connected group asks `This group already has a Sheet. Switch to the new one?` with buttons `Switch Sheet` / `Keep current`. The final message is `Connected ✅ <trip name> · <T> → <H> · <tz>`. Code and docs may keep "binding" internally.

## 3. Users

- `/join` (and `/start` in a group) upserts the sender into `Users` with `Joined = TRUE`.
- Joining others (added 2026-09-29): `/join` sent as a **reply** to another person's message joins that person (their ID comes from the replied-to message) and replies `Joined ✅ <their name>`. The `Connected ✅` message and every `Joined ✅` reply carry an `I'm in too` button (callback `join:<chat_id>`) that joins whoever taps it and edits the message to append their name to a "Joined so far" line. The button label shows a counter after the first tap: `I'm in too (2 joined)`. A second button `Everyone's in` (any joined user may press it) removes the keyboard and appends the line `Everyone's in. Latecomers can type /join.` Callback `join:<chat_id>:done`. `/join @username` is **not** supported because Telegram does not give bots the user ID behind a plain @mention. Anyone who merely sends a message is also upserted with `Joined = FALSE` (for names) but is **not** part of Everyone.
- Everyone = users in this chat with `Joined = TRUE`, evaluated at the moment the button is pressed.
- Name resolution for "split between me Amir and John" and "paid by Amir": case-insensitive match against username, first name, and full display name of joined users. Zero or several matches → the draft asks with buttons; never guess.
- Sending an expense joins the sender (upsert `Joined = TRUE`) so the payer is always a trip user.

## 4. Currencies, rates, conversion

- Provider abstraction `ExchangeRateProvider.get_rate(base, quote, on_date) -> ExchangeRate(rate, rate_date, provider, retrieved_at)`. First implementation: Frankfurter, `GET https://api.frankfurter.dev/v1/{date}?from={base}&to={quote}`, no key (verified 2026-09-29; `base`/`symbols` also accepted). Use the `date` in the response as `rate_date`.
- Cache: the `Exchange Rates` tab plus an in-memory dict, keyed per trip. Lookup order: memory → tab → provider. Cache key `(rate_date, base, quote)`; also record the requested date so a weekend lookup is a hit next time.
- Identity rate: when the trip currency equals the home currency, the rate is `1` (provider `identity`), returned without calling the provider or the ledger.
- A rate for "today" that is not yet published (Frankfurter updates ~16:00 CET) resolves to the previous business day. Accept it; no provisional re-fetch.
- Provider failure: the draft is not saved. Reply "Couldn't fetch the exchange rate, try again in a minute" with a `Retry` button on the draft.
- Original currency must be trip or home currency. Text parser: `yen|¥|jpy` → trip currency when trip is JPY; `sgd|s$` → home currency when home is SGD; `$` → home currency when home is SGD or USD, else USD; bare number → trip currency. Generalise via a per-currency alias table. Anything else → "I can only record <TRIP> or <HOME> for this trip."
- Conversion order for a **trip-currency** expense: shares in trip minor units → reconcile → `home_amount = round_half_even(trip_amount × rate)` → each `share_home = round_half_even(share_trip × rate)` → reconcile share_home to home_amount with the largest-remainder method (§5), ties by ascending User ID, payer last. Plain round-robin is not used here because it can move a share more than one minor unit from its exact value. For a **home-currency** expense: shares in home minor units directly; `trip_amount` and `share_trip` are informational, converted with the same rate and reconciled the same way.
- Balances, `/balances`, `/settle`, Summary net figures: **home currency only**. Trip-currency figures are shown alongside where cheap, never used for settlement.

## 5. Splitting and rounding

Pure functions in `app/services/splitting.py`, no I/O, property-tested.

- `split_equal(total_minor, participant_ids, payer_id)`: base = `total // n` each; remainder `r = total % n` distributed one unit each to participants sorted by Telegram User ID ascending, **skipping the payer** while other participants remain. If payer is the only participant, they get everything.
- `split_custom(amounts_by_participant, total_minor)`: reject unless the sum equals total exactly. The UI does not enable Confirm until it does.
- `split_items(items, claims, participant_ids, payer_id)`: each claimable item is split equally (same remainder rule) among its claimants; unclaimed items after `Rest → everyone` go to all participants. Then each adjustment line (tax, service charge, positive; discount, negative) is allocated across participants proportionally to their pre-adjustment subtotal using the **largest-remainder method**, ties broken by ascending User ID, payer last. A participant with zero subtotal receives zero adjustment. Final shares must equal the receipt total exactly; if they do not, raise, never silently fix.
- Percentage split: represent split inputs as a `SplitSpec` union (`Equal`, `Custom(amounts)`, `Items(claims)`) so `Percent(weights)` can be added without touching callers.
- Invariant enforced in the `Expense` model validator: `sum(share_trip) == trip_amount` and `sum(share_home) == home_amount`. An `Expense` that violates it cannot be constructed.

## 6. Text expense parser

- Deterministic, no LLM. Regex grammar over the message after stripping the bot mention. Recognised: amount with optional thousands separators and currency token before or after; optional leading "I paid"/"paid"; "for <desc>"; trailing `split (between|among|with) <names>` or `split everyone` or `split all`; trailing `paid by <name>`; "me" resolves to the sender.
- Tags (added 2026-10-02 at go-live): a tagged person (`@amir`, or a tag of someone without a username) in an expense message or receipt caption is the payer, unless the message also says `paid by`; tags are never part of the description. A tag of someone without a username joins them, like `/join` sent as a reply.
- A message is an expense candidate only if an amount is found **and** the message is ≤ 12 words. Otherwise the bot stays silent.
- Description = leftover words, trimmed, title-cased; empty description → "Expense". A bare `split` that is not one of the clauses above is dropped from the description and selects nobody, so `6000 split dinner` is "Dinner" with the usual Just me / Everyone / Choose people card (amended 2026-10-02 after the Sprint 5 checklist). Category from a keyword table (`taxi|uber|grab|train|jr|metro|bus` → transport, `ramen|dinner|lunch|breakfast|sushi|food|snack` → food, `coffee|beer|drinks|bar|sake` → drinks, `hotel|airbnb|ryokan` → accommodation, `ticket|museum|onsen|entry` → activities, `konbini|7-eleven|lawson|familymart|supermarket` → groceries, `shop|uniqlo|donki|mall` → shopping), default `other`.
- Parse failure examples that must stay silent: "lol that ramen was so good", "who's paying for dinner". Must parse, for a JPY/SGD trip (the six examples from the original brief, kept here because the brief is not in the repo):
  1. `1200 yen ramen` → ¥1,200, "Ramen", food
  2. `I paid ¥4,850 for dinner` → ¥4,850, "Dinner", food
  3. `taxi 3200` → ¥3,200, "Taxi", transport
  4. `750 coffee` → ¥750, "Coffee", drinks
  5. `¥6000 dinner split between me Amir and John` → ¥6,000, "Dinner", participants clause `me Amir and John` (resolved from Sprint 5)
  6. `¥2400 taxi split everyone` → ¥2,400, "Taxi", participants clause `everyone`
  Plus `S$12 grab` → S$12.00 original SGD, "Grab", transport.

## 7. Receipt reading (LLM)

- Anthropic Python SDK, model `claude-sonnet-5`, `client.messages.parse()` with a Pydantic schema (structured outputs). Image sent as base64; the image bytes are discarded after the call and never logged or written to disk. No OCR library.
- LLM schema `ReceiptExtraction` (separate from domain models): `merchant: str|None`, `currency: str|None`, `transaction_datetime: str|None` (ISO, only if printed), `line_items: list[{description, quantity: int, unit_amount_minor: int|None, amount_minor: int, kind: item|tax|service_charge|discount}]`, `candidate_totals: list[{label_as_printed, amount_minor, is_grand_total_guess: bool}]`, `description: str`, `category: enum`, `confidence: float`, `notes: str|None`. Instruct the model to list every number that could plausibly be a total.
- Validation into the domain `Receipt` happens in `receipt_parser.py`; malformed or schema-invalid output → "Couldn't read that receipt" with `Try again` and `Enter amount manually` buttons. Never retry silently more than once.
- Total selection rule: let `computed = items + tax + service − discount`. If exactly one candidate has `is_grand_total_guess` and `|candidate − computed| ≤ 5` minor units (or line items are absent entirely), choose it. Otherwise ask, showing up to 3 candidates (grand-total guesses first, then largest) as buttons plus `Other amount` and `Cancel`. `Other amount` asks for a reply with the total, like `Enter amount manually`; a typed total in the receipt's currency keeps the line items and gets the reconciliation prompt below if they don't add up to it (added 2026-10-02 after the Sprint 6 checklist). The chosen printed total is authoritative. Candidates are compared by amount: the same amount printed on several lines (e.g. 内税品計, 合計, 現金計) is one candidate, and a receipt with only one distinct amount uses it without asking; if that amount is more than 5 off the line items, the reconciliation prompt below still applies (amended 2026-10-01 after the Sprint 4 checklist).
- Reconciliation prompt when the chosen total ≠ computed and line items exist (amended 2026-10-01: a total chosen automatically by the rule above is already within ±5 and goes straight to the card without this prompt; the prompt applies to a total the user picked from the buttons): show items sum, adjustments sum, total, difference, with `Fix items` (opens item mode with an extra editable "Unlisted" item preset to the difference), `Use total anyway` (split modes other than items), `Cancel`.
- Amounts read 100 times off (amended 2026-10-02 at go-live): the prompt spells out minor units (¥6,850 is 6850; S$12.50 is 1250), and each candidate total also carries `amount_as_printed` (e.g. "¥6,850"). If a candidate's printed amount shows its `amount_minor` is exactly 100 times too big or too small, every amount in the reading is rescaled before validation.
- Included tax read as a line (amended 2026-10-02 after the Sprint 6 checklist): when the lines match no grand-total guess, but leaving out exactly one `tax` line makes them match one exactly, that line was tax already inside the prices (内税) and is dropped before total selection.
- Receipt currency must match trip or home currency; otherwise refuse as in §4. Missing currency → assume trip currency.
- Transaction date: printed date if present and within 30 days of the message time, else the message time in the trip timezone.
- Categories enum (shared with §6): `food, drinks, transport, accommodation, shopping, activities, groceries, other`.

## 8. Draft flow and buttons

- `DraftStore` interface (`get`, `put`, `delete`, `sweep_expired`) with an in-memory implementation, TTL 30 min. Drafts lost on restart are acceptable; their buttons then reply "This expense expired, please send it again" and the message is edited to remove the keyboard.
- Draft ID: ULID. Every `callback_data` is `"{draft_id}:{action}:{arg}"` and is checked against the store before anything else. Unknown draft → expired message. Draft in `committed` state → "Already saved". Draft in `cancelled` state → "Already cancelled".
- Only the initiator may press participant/split/Done/Confirm/Edit/Cancel buttons. Others get `answer_callback_query("Only <name> can edit this", show_alert=False)`. Item-claim buttons (§9) may be pressed by any joined user.
- Flow: parse → `detected` (one message: description, amounts in both currencies, payer, date, rate; buttons `Just me / Everyone / Choose people`) → `participants` (Choose people: toggle buttons over joined users, then `Next`) → `split_method` (`Equally / By items / Custom`; `By items` only when line items exist and count ≤ 40) → for Custom: the initiator replies with lines `<name> <amount>`; the bot re-renders with the running total and a red/green reconciliation line; Confirm disabled until exact → `preview` (`Confirm / Edit / Cancel`) → `committed`.
- "Who paid?" step (added 2026-10-03): when nobody was tagged (outside a `split` clause), the message has no `paid by`, and more than one person has joined, the draft first asks "Who paid?" with one button per joined person (the sender as "Me"), initiator only; then the `detected` card. `Fix items` asks it too before opening the item card. A `split ...` clause doesn't skip it: the clause is applied once the payer is picked (fixed 2026-10-04).
- One bot message per draft, edited in place at every step. Never post a second message for the same draft except the final "Saved ✅ <desc> <amount>" one-liner.
- `Edit` returns to the participants step keeping amount, description, payer. Changing the amount requires Cancel and re-send.
- `Just me` skips the split-method step (single participant, `equal`).
- Concurrent drafts in one chat are allowed and independent.

## 9. Item claiming (Phase 3)

- The draft message in `items` state renders one button per claimable line item: `"🍽 {n} {desc} {amount} · {initials}"`, one per row, then a row `↪ Rest → everyone` | `✅ Done` (emoji per kind of button added 2026-10-03, sprint 8). Adjustment lines are listed in the message text, not as buttons. An item line with a quantity of 2 to 10 gets one button per unit, `"{desc} (1/3)"`, sharing the line's amount equally (remainder to the first units), so units of one line can go to different people; the line stays whole if that would pass 40 claimable items or its amount equals its unit price (amended 2026-10-04 after trip feedback).
- Tapping an item toggles the presser's claim. `Rest → everyone` assigns all currently unclaimed items to all participants; it is reversible by tapping items again. `Done` (initiator only) is disabled while any item is unclaimed, unless `Rest → everyone` has been applied.
- Text shortcuts, only as replies to the draft message or with the bot mentioned: `mine 1,4`, `split 5 everyone`, `unclaim 2`. Same effect as buttons.
- The sender can give items to someone else (added 2026-10-02 at go-live): a "Tapping for" control (sender only) makes the sender's item taps count for the picked person until it is back on Me — since sprint 8 one `👤 Tapping for: <name> ▸` button that moves to the next joined person on each press, wrapping back to Me; a reply `<name> 1,3` from the sender does the same. Others' taps are always their own.
- More than 40 claimable items → `By items` is not offered; the message says "Too many items to split individually".

## 10. Persistence (Google Sheets)

> Superseded 2026-10-15 by §18 (Sprint 9). Column vocabularies (`Source Type`, `Status`, `Kind`, `Allocation Method`) and the ID rules carry over; everything Sheet-specific is retired.

- `LedgerStore` interface with a `SheetsLedgerStore` implementation. All Sheets calls run in `asyncio.to_thread`. Retries: 3 with exponential backoff on 429/5xx; then the draft stays in `preview` with a `Retry save` button and an error line.
- Commit = one spreadsheets.batchUpdate containing appendCells requests in this order: Line Items rows, Splits rows, the Expenses row last. A single batchUpdate is applied atomically. Every reader joins through Expenses; orphan Splits/Line Items rows are ignored.
- IDs: Expense ID = the draft's ULID. Item ID = `"{expense_id}-{n:02d}"`.
- Amounts are written as numbers in **major units** (yen as integer, SGD as 2-dp decimal) so the Sheet reads naturally; the code converts to minor units on read. Rate stored to 8 dp. Timestamps ISO 8601 with offset. Dates `YYYY-MM-DD`.
- Telegram IDs are written as numbers and Joined as a real boolean (RAW input), so formula comparisons match.
- Tabs and columns (headers exactly as written; the trip's currency codes replace `<T>` and `<H>` at `/settrip` time, e.g. "JPY Amount"):
  - `Trip`: Chat ID | Trip Name | Trip Currency | Home Currency | Timezone | Created At | Bot Version
  - `Users`: Telegram User ID | Username | Display Name | First Seen | Last Seen | Joined
  - `Expenses`: Expense ID | Created At | Transaction Date | Chat ID | Message ID | Created By User ID | Payer User ID | Payer Username | Payer Display Name | Description | Merchant | Category | Original Amount | Original Currency | <T> Amount | <H> Amount | Rate <T>→<H> | Rate Date | Source Type | Split Method | Status | Cancelled At | Notes
  - `Splits`: Expense ID | Participant User ID | Participant Username | Participant Display Name | Share <T> | Share <H> | Split Method
  - `Line Items`: Expense ID | Item ID | Description | Quantity | Amount <T> | Assigned User IDs | Allocation Method | Kind
  - `Exchange Rates`: Requested Date | Rate Date | From | To | Rate | Provider | Retrieved At
  - `Summary`: formulas only (see §11)
- Line Items `Amount <T>` is always the trip currency: items from a receipt paid in the home currency are converted at the expense's own rate, rounded half-even per item (decided 2026-10-01 at the Sprint 4 review). Sprint 4 writes `Allocation Method = unassigned` with `Assigned User IDs` empty until claiming exists (Sprint 6).
- `Source Type` ∈ `text, receipt`. `Status` ∈ `confirmed, cancelled`. `Kind` ∈ `item, tax, service_charge, discount`. `Allocation Method` ∈ `claimed, everyone, proportional`. `Assigned User IDs` is a comma-joined list.
- Dedup: on startup load every `(Chat ID, Message ID)` from Expenses into a set; check before commit; add after commit. Also guard the PTB update handler with `update.update_id` in a bounded LRU.

### 10a. Readable layout (added 2026-09-29 after the Sprint 1 checklist)

Applied by `/settrip` when the Sheet is created; data and header strings in §10 are unchanged.
- Tab order as shown to humans: `Summary`, `Expenses`, `Splits`, `Line Items`, `Users`, `Exchange Rates`, `Trip`, `Read Me`. `Summary` is index 0 so it opens by default.
- Hidden columns (via `hideColumns`; formulas still work, users can unhide): Summary `User ID` (the LAST column of the per-person block, item 2 below); Expenses `Expense ID`, `Chat ID`, `Message ID`, `Created By User ID`, `Payer User ID`; Splits `Expense ID`, `Participant User ID`; Line Items `Expense ID`, `Item ID`, `Assigned User IDs`; Users `Telegram User ID`; Trip `Chat ID`.
- `Transaction Date` and `Rate Date` are stored as plain text `yyyy-mm-dd`, not real Sheets date serials -- that sorts and groups correctly as text, so they carry no DATE number format.
- Number formats (every other one unbounded downward, no `endRowIndex`, so appended rows are always covered): `<T> Amount`/`Share <T>`/`Amount <T>` use `#,##0` when the trip currency has 0 decimals else `#,##0.00`; `<H>` columns `#,##0.00`; `Rate` `0.00000000`.
- Summary layout (also see §11): per-person block columns are `A Display Name | B Paid <H> | C Share <H> | D Net <H> | E User ID` (User ID last, hidden). Block titles, each a bold cell above its block: `Who owes whom` (row 1, with the note row `Positive = should receive money · Negative = owes money`) above the per-person block; `Totals` (column A) sharing one row with `By category` (column G), `By day` (column J), and `By payer` (column M) -- each of those three has its title cell then its `QUERY` directly below, so their dynamic row growth (2 columns wide each) never collides with the next block.
- `Read Me` tab: one column, plain English, three lines per tab: what one row means, who writes it (the bot), and when to look at it. Ends with the line `Do not edit rows by hand; use /undo in Telegram.`

## 11. Summary tab

Written once at `/settrip`, never rewritten. All formulas filter `Expenses!Status = "confirmed"` (joined via Expense ID for Splits). Blocks (see §10a for exact cell positions):

1. Per person ("Who owes whom"): Display Name | Paid <H> | Share <H> | Net <H> | User ID (join key, last column, hidden) — up to 20 rows; `SUMIFS` over Expenses (paid) and a `SUMPRODUCT`/`QUERY` join over Splits (share). Net = Paid − Share.
2. Group totals ("Totals", column A/B): Total <T>, Total <H>, expense count.
3. By category / by day / by payer (`QUERY … group by` Category / Transaction Date / Payer): side by side in separate column groups (G, J, M) starting on the same row as the "Totals" title, each 2 columns wide with a 1-column gap before the next group.
Formulas must reference whole columns (`Expenses!A:W`) so appended rows are included.
- Formulas that multiply arrays use header-free open ranges (`Splits!F2:F`); lookups may use full columns.

## 12. Commands

`/start` (superseded by §19 on 2026-10-15: shows the welcome menu, no longer joins), `/help`, `/join`, `/settrip`, `/summary`, `/today`, `/mine`, `/recent` (last 5, oldest first), `/balances`, `/settle`, `/undo`. All read commands filter by chat id and `confirmed`. `/today` uses the trip timezone. Every reply is one compact message, HTML parse mode, amounts formatted with symbol and thousands separators. Amount order: per-expense lines show trip currency then home (`¥1,200 · S$10.42`); totals and balances show home currency first. An expense entered in the home currency is shown in the home currency only; the trip-currency equivalent is stored but not displayed.

- `/balances`: per joined user, Paid / Share / Net in home currency; trip-currency equivalents in a second line at the expense-weighted rate are **not** shown (they would be misleading). Just home currency.
- `/settle`: greedy debtor/creditor matching in home minor units: sort debtors and creditors by magnitude, repeatedly transfer `min(debt, credit)` from largest debtor to largest creditor. Produces at most n−1 transfers. Documented in README as near-minimal, not guaranteed minimal.
- `/undo`: finds the caller's most recent `confirmed` expense where Created By = caller in this chat; shows it with `Yes, cancel it / Keep`; on Yes sets Status = `cancelled`, Cancelled At = now, appends "Cancelled via /undo by <name>" to Notes. Row update, never deletion.

## 13. Group behaviour

- Privacy mode is **off** (documented BotFather steps in README, including re-adding the bot). The bot is intended for a dedicated expenses group.
- Handled: commands; text messages that parse as an expense (§6); every photo (largest size) in a bound group; replies to a draft message; callback queries. Everything else is ignored silently.
- A photo with a caption that parses as an expense uses the caption's description/participants as overrides after the receipt is read.

## 14. Errors

Every failure path ends in one of: silent ignore (non-expense text), a one-line reply with a button to retry or cancel, or the draft message edited with the error and its keyboard intact. The bot never raises to the user, never saves partial data, and logs exceptions with the draft ID but without message bodies or images.

## 15. Deployment

- Fly.io, one `shared-cpu-1x` machine, 256 MB, region `sin`, no volume. `fly.toml` has no `[http_service]` or `[[services]]` section (nothing listens), so Fly's auto-stop never applies; deploys use `strategy = "immediate"` and `fly deploy --ha=false` so there is exactly one machine and never two pollers. Secrets via `fly secrets import` fed from `.env` by `scripts/set-fly-secrets.sh`. README gives the exact commands, including how to stop the machine after the trip.
- Dockerfile: multi-stage `uv` build, non-root user, `CMD ["python", "-m", "app"]`.

## 16. Phases

Phase 1: repo scaffolding, config, models, `/settrip`, `/join`, Sheets store + tab creation + Summary formulas, Frankfurter provider + cache, text parser, receipt total extraction (no item mode yet), draft flow with `Just me` only, commit, dedup, `/recent`, `/today`, `/summary`, `/undo`, Dockerfile, README skeleton.
Phase 2: participants (`Everyone`, `Choose people`), `split_equal`, `split_custom`, "paid by", "split between", `/balances`, `/mine`, `/settle`.
Phase 3: line items in the receipt schema surfaced to the UI, item claiming, shared items, proportional adjustments, reconciliation prompt, `Fix items`.

## 17. Tests (minimum)

All items in brief §27, plus: remainder rule skips payer; largest-remainder tie-break; home-currency expense needs no conversion for shares; total-selection rule (single confident candidate saves, two candidates asks); ≤12-word gate; stale/foreign callback rejection; commit write order; `/undo` marks not deletes; Summary formula strings reference whole columns; hypothesis: for random totals, participant sets and rates, both invariants in §5 hold.

## 18. Persistence (Postgres on Neon) and multi-group use (added 2026-10-15, Sprint 9)

Decided with Sufyan after the post-trip review. ADR 0003 records the why; `docs/sprints/09-neon-store-and-export.md` holds the full pins. Summary:

- Postgres on Neon is the only store, via `PostgresLedgerStore` behind `LedgerStore`. `DATABASE_URL` (Neon pooled string) replaces `GOOGLE_SERVICE_ACCOUNT_JSON` and `WHOPAID_CONTROL_SHEET_ID`. No Sheets code remains.
- Money columns are `bigint` minor units. Expense ID stays the draft ULID. Rates are a global table keyed by `(base, quote, requested_date)`.
- Saving an expense is one transaction. One expense per Telegram message and one write per draft are unique constraints; a violation means "already saved". The in-memory dedup set and the pre-retry existence check are retired. The `update.update_id` LRU guard stays.
- `/settrip` is buttons only: trip currency, home currency, timezone. Currency keyboard order SGD, MYR, JPY, CNY, then `Other…`. One open trip per chat; `/settrip` again closes the old trip after confirmation. `/settle` offers `Close this trip`. A closed trip answers with "This trip is closed. Run /settrip to start a new one."
- `/deletetrip` by the trip starter hard-deletes the chat's trips and rows after a confirm button.
- `/export` sends one `.xlsx` (Expenses, Shares, Line items, People, Balances), built by code, no model call. A Google Sheet export may come later and reuses the same rows.
- Hosting: one Fly machine always on. Store tests run against a real Postgres in Docker.
- Roadmap after Sprint 9: Sprint 10 `/start` menu with buttons (fewer commands to remember); Sprint 11 Google Sheet export and drafts that survive a restart, if still wanted.

## 19. `/start` welcome menu (added 2026-10-15, Sprint 10)
- Buttons under messages only; no `setMyCommands`, no persistent reply keyboard.
- The welcome (text pinned in `docs/sprints/10-start-menu.md`) is posted when the bot is added to a group, on `/start` and on `/help` in a group. The bot tries to pin it; if it lacks the right it appends "Pin this message so everyone can find the buttons." and never asks to be made admin.
- Menu is state-aware (no trip / open / closed), two pages when open, paging edits the keyboard of the same message. Every other press posts a new plain message and leaves the menu alone.
- A press is the typed command by the presser: same coroutine, same gates, same permission checks. State is re-read at press time; old menus are never edited.
- `/start` in a group no longer joins the sender. Private `/start` and `/help` carry an `Add me to a group` URL button (`?startgroup=true`).
- Typed commands stay; `/help` keeps its list under the explanation paragraph.
