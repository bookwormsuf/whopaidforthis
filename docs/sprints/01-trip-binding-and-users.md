# Sprint 1 — Trip binding and users

## Goal
`/settrip` turns a blank Sheet you own into the full ledger layout, and `/join` records people.

## In scope
Master spec §2, §3, §10 (tab creation and headers), §11 (Summary formulas), the `LedgerStore`
interface with its Sheets implementation for Trip and Users, currency and timezone tables.

## Out of scope
Expenses, rates, drafts. Reading commands.

## Sprint pins
- `/settrip <sheet id or full URL>` from any group member. Extract the ID from a URL with a regex on `/d/([a-zA-Z0-9-_]+)`.
- Access check first: open the Sheet by key. On a 403/404 reply exactly: "I can't open that Sheet. Share it with `<service account email>` as Editor, then try again." The email is read from the `client_email` field of the service account JSON.
- Then three button prompts, one message edited in place: trip currency (buttons: JPY, KRW, THB, EUR, GBP, USD, MYR, IDR, Other→type code), home currency (buttons: SGD, then same list), timezone (default derived from trip currency via a table; buttons: the default, "Other→type IANA name"). Free-typed values are validated against the Frankfurter currency list (fetched once, cached in memory for the process lifetime) and `zoneinfo.available_timezones()`.
- Trip name = the Telegram group title at the time of `/settrip`.
- On confirm, create tabs in this order if absent: `Trip`, `Users`, `Expenses`, `Splits`, `Line Items`, `Exchange Rates`, `Summary`. Delete the default empty `Sheet1` only if it is empty. Headers exactly as master spec §10 with `<T>`/`<H>` replaced. Freeze row 1 on every tab. Never touch a tab that already has a header row (idempotent re-run).
- Group→Sheet bindings live in the `Bindings` tab (Chat ID | Sheet ID | Bound At) of the control Sheet named by `WHOPAID_CONTROL_SHEET_ID`. This is the only place the mapping exists; nothing is written to the machine running the bot. Bound trips are cached in memory `{chat_id: Trip}` and re-read from the control Sheet on a cache miss. The `Trip` tab inside each trip Sheet holds the same facts for humans to read.
- `/join`: upsert into `Users` with `Joined = TRUE`, reply "Joined ✅ <display name>". `/start` in a group is an alias. Display name = first name + last name if present, else username, else "User <id>".
- Any handled message from a non-joined user upserts them with `Joined = FALSE` (names only) without a reply.
- Summary formulas: written as strings in `app/services/summary_formulas.py` so they can be unit-tested. Per-person block uses `Users` rows where `Joined = TRUE`. Formulas reference whole columns. Currency symbols in headers, never in cells.
- Every Sheets call goes through `SheetsLedgerStore`; handlers never import `gspread`.

## Before you start (you)
1. Create a blank Google Sheet named `whopaid control`. Share it with the service account email as **Editor**. Copy its ID from the URL (the long string between `/d/` and `/edit`). Add `WHOPAID_CONTROL_SHEET_ID=<that id>` to `.env`.
2. Create a second blank Sheet named `whopaid test trip`. Share it with the service account email as **Editor**. Keep the URL handy.

## Agent builds
`app/models/trip.py`, `app/models/user.py`, `app/services/currencies.py` (decimals table, timezone defaults, alias table), `app/services/ledger_store.py` (interface), `app/services/google_sheets.py` (implementation), `app/services/summary_formulas.py`, `app/bot/conversations/settrip.py`, `app/bot/handlers/join.py`, `app/bot/keyboards/settrip.py`, tests, README sections "Google Cloud setup", "Sharing the Sheet", "Binding a trip".

## Tests required
Sheet ID extraction from three URL shapes; header lists match master spec exactly for JPY/SGD and for EUR/GBP; formula strings contain `Status` filter and whole-column references; currency decimals table; timezone default table; `/settrip` on an unshared Sheet produces the exact error text (mocked gspread); `/join` upsert is idempotent; non-joined users are excluded from Everyone. gspread is mocked everywhere.

## After: your checklist
1. `uv run python -m app`
2. In the test group send `/settrip <url of whopaid test trip>`. Follow the buttons: JPY, SGD, accept Asia/Tokyo.
3. Open the Sheet in your browser. Check: seven tabs exist, first rows are frozen headers, `Trip` has one row with your group's name, `Summary` shows formulas (they will read 0 or empty for now, not `#REF!`).
4. Send `/join`. Check `Users` has a row for you with `Joined` = TRUE.
5. Send `/settrip` again with the same URL. It asks to confirm rebinding; say yes; nothing in the Sheet is duplicated.
6. Un-share the Sheet from the service account, send `/settrip` again, confirm you get the "share it with …" message with the correct email, then re-share.

## Exit criteria
- [ ] All six checklist steps pass
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] README sections written
- [ ] `STATUS.md` entry with your sign-off
