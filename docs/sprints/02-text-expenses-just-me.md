# Sprint 2 — Text expenses, just me

## Goal
Type "1200 yen ramen" and a confirmed expense lands in the Sheet with that day's SGD rate. First real
end-to-end path: parser → rate → draft → confirm → commit → read back.

## In scope
Master spec §4 (Frankfurter provider, cache, conversion for a single participant), §6 (text parser,
without name resolution), §8 (draft flow with `Just me` and `Cancel` only), §10 (commit, dedup),
§12 commands `/recent`, `/today`, `/summary`, `/undo`.

## Out of scope
Photos, other participants, splits, `/balances`, `/mine`, `/settle`, "paid by", "split between".

## Sprint pins
- The detected message shows only `[Just me] [Cancel]` in this sprint. `Everyone` and `Choose people` are added in Sprint 5, not rendered greyed out.
- `Just me` goes straight to the preview (`[Confirm] [Cancel]`, no `Edit` yet since there is nothing to edit).
- Frankfurter client uses `httpx.AsyncClient` with a 5 s timeout, 2 retries. `ExchangeRate` is stored in the `Exchange Rates` tab on first fetch; memory cache is a plain dict.
- Money is handled with `int` minor units everywhere in the domain. A `Money(amount_minor: int, currency: str)` value type with `format()` producing `¥1,200` / `S$10.42` using the decimals table. `Decimal` is used only inside conversion, rounding `ROUND_HALF_EVEN`.
- Parser lives in `app/services/expense_parser.py` and returns `ParsedExpense | None`. The 12-word gate counts whitespace-separated tokens after stripping the bot mention.
- Commit path is `DraftService.confirm(draft_id, presser_id)` → builds `Expense` (validator enforces invariants) → `LedgerStore.append_expense(expense, splits, line_items)` → adds `(chat_id, message_id)` to the dedup set → edits the draft message to `Saved ✅ Ramen ¥1,200 (S$10.42)` with no keyboard.
- Dedup set is loaded at first use per chat and kept in memory; a duplicate message replies nothing and logs at INFO.
- `/recent`: last 5 confirmed, oldest first, one line each: `8 Oct · Ramen · ¥1,200 · S$10.42 · <payer first name>`.
- `/today`: same format, today in trip timezone, with a total line.
- `/summary`: count, total trip currency, total home currency.
- `/undo`: as master spec §12. Confirmation buttons `[Yes, cancel it] [Keep]`, initiator-only.
- Read commands fetch the `Expenses` tab once per command; no background caching yet.

## Before you start (you)
Nothing new. Sprint 1 must be signed off.

## Agent builds
`app/models/money.py`, `app/models/expense.py` (+ `ExpenseSplit`), `app/models/exchange_rate.py`, `app/services/exchange_rates.py` (`ExchangeRateProvider`, `FrankfurterProvider`, `CachedRates`), `app/services/expense_parser.py`, `app/services/drafts.py` (`Draft`, `DraftStore`, `InMemoryDraftStore`, `DraftService`), `app/bot/handlers/text_expense.py`, `app/bot/handlers/callbacks.py`, `app/bot/handlers/reads.py`, `app/bot/keyboards/draft.py`, `app/bot/formatting.py`, extensions to `google_sheets.py`, tests, README sections "Recording an expense", "Exchange rates".

## Tests required
Brief §27 items: the six parse examples, JPY default, "I paid ¥4,850 for dinner", non-expense text is `None`, 13-word message is `None`; historical rate used not today's; weekend date stores the returned rate date; SGD original needs no conversion of shares; `Expense` refuses mismatched shares; duplicate `(chat, message)` does not commit; commit request order Line Items → Splits → Expenses; `/undo` writes status not delete; stale/foreign callback rejected; row formatting to Sheet values and back. Frankfurter and gspread mocked.

## After: your checklist
1. `uv run python -m app`
2. Send `1200 yen ramen`. A card appears with ¥1,200, an SGD amount, your name, today's date, the rate. Tap **Just me**, then **Confirm**. The card becomes `Saved ✅ …`.
3. Open the Sheet: `Expenses` has one row, `Splits` has one row for you with the same amounts, `Exchange Rates` has one row.
4. Send `I paid ¥4,850 for dinner`, `taxi 3200`, `750 coffee`, `S$12 grab`. Confirm each. Check the SGD one has Original Currency SGD and its SGD amount equals 12.00.
5. Send `lol that ramen was so good`. The bot must stay silent.
6. Send `/recent`, `/today`, `/summary`. Numbers match the Sheet.
7. Send `/undo`, tap **Yes, cancel it**. In the Sheet the last row's Status is `cancelled`; `/summary` no longer counts it.
8. Send a new expense, tap **Cancel**. Nothing is written.
9. Scroll up to the first saved card and try tapping where the buttons were: there are none. Send an expense, wait, then stop and restart the bot, then tap **Confirm** on that card: you get "expired, please send again".

## Exit criteria
- [ ] All nine checklist steps pass
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
