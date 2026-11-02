# Sprint 15 — Receipts and expenses in a third currency

## Goal
An expense in a currency that is neither the trip's nor the home currency (MYR on a THB trip
with SGD home, say) is recorded and split, rather than refused. Today a receipt read in such a
currency gets "I can only record THB or SGD for this trip" and nothing else happens, and so does
`50 usd taxi`. Decided with Sufyan on 2026-11-02.

## In scope
- Receipts read in a third currency: confirm the currency with buttons, then go on as any
  receipt (items, item by item, Not on this bill, custom amounts).
- Typed expenses with a third currency's code or unambiguous symbol.
- Rates from the third currency to the trip currency (and on to home) on the expense's date;
  a currency with no published rate gets a typed rate.
- Cards show the original amount, then trip and home.
- Spec §4, §6, §7, §10, §12 amendments; README; manual checklist; tests.

## Out of scope
- Balances, `/balances`, `/settle`, `/mine`: home currency only, unchanged (spec §4).
- Changing a trip's own currencies.
- Confirming the currency of trip- or home-currency receipts (declined: they go straight through
  as today).
- Any change to share maths (spec §5).

## Sprint pins (decided with Sufyan, 2026-11-02)

### Which currencies
- Any ISO 4217 code. Frankfurter publishes rates for about 30 (AUD BGN BRL CAD CHF CNY CZK DKK
  EUR GBP HKD HUF IDR ILS INR ISK JPY KRW MXN MYR NOK NZD PHP PLN RON SEK SGD THB TRY USD ZAR).
  Any other code (VND, TWD, AED, …) is accepted with a typed rate (below).
- Minor units follow ISO 4217 for every code used (VND 0 decimals, KRW 0, JPY 0, the rest as
  ISO), so amounts are never read 100 times off.

### Receipts: confirm the currency
- When the reader's currency is neither the trip's nor the home currency, before the total
  step, the card reads:
  ```
  🧾 <description>
  This receipt looks like it's in Malaysian ringgit (RM). Is that right?
  ```
  with buttons `[Yes, MYR]` / `[It's THB]  [It's SGD]` / `[Cancel]`. Initiator only.
- `Yes` keeps the currency and goes on to the total step as today. `It's THB` / `It's SGD`
  re-read the amounts as that currency (same minor units only if decimals match; otherwise the
  amounts are rescaled from what was printed, as the 100-times rule does) and go on with no
  third currency.
- A receipt in the trip or home currency goes straight through, unchanged.

### Typed expenses
- A third currency is recognised from its three-letter code (`50 usd taxi`, `myr 120 dinner`,
  either case) or a symbol that means only one currency: `€`, `£`, `₩`, `₹`, `₱`, `RM` directly
  before a number (`RM120`, `RM 120`). `$` keeps its current rule (spec §4); `¥` keeps meaning
  the trip or home currency when that is JPY or CNY, else JPY.
- No confirm step: the sender typed it. The card shows the conversion; `Cancel` is there.
- "I can only record <TRIP> or <HOME>" is retired, except for a word that looks like a
  currency but is not an ISO code (still silent, as now, so chat is not misread).

### Rates
- The expense keeps today's fields: `original_currency` / `original_amount_minor`, `trip_amount`,
  `home_amount`, and `rate` (trip → home) with `rate_date`. A third-currency expense adds one
  more rate: original → trip (`original_rate`, nullable, plus its `rate_date` and provider).
  One migration adds the columns.
- `trip_amount = round_half_even(original × original_rate)`, then `home_amount` from the trip
  amount as today. Shares are worked out in the original currency (as a home-currency expense's
  are today) and converted to trip and to home with the same largest-remainder reconciliation
  (spec §4/§5), so both columns still sum exactly.
- Both rates are for the expense's transaction date, with the existing cache and weekend rule.
  Rate failure: the existing "Couldn't fetch the exchange rate" card with `Retry`.

### No published rate: typed rate
- The card reads "No exchange rate for VND. Reply to this message with what 1 VND is in ฿, e.g.
  `0.0013`." with `[Cancel]`. Initiator only, a reply to the card.
- The reply may also be the converted total, `฿850` or `850 thb`, read as the trip-currency
  amount; the rate is then worked out from it. Agent's call, open to change.
- A typed rate is saved on the expense with provider `typed`, used for that expense only, and
  never cached for others.

### Display
- Draft and saved cards: `RM 120.00 (฿850.00 · S$34.50)`, original first, then trip and home.
  The rate line names both rates: `1 RM = ฿7.08 · 1 ฿ = S$0.0406, 2 Nov`.
- Custom amounts on a third-currency expense are typed in the original currency, as today's
  rule ("Use RM amounts for this expense").
- Line Items: amounts converted to the trip currency at `original_rate`.
- `/balances`, `/settle`, `/mine`, export summary: home currency, unchanged. `/export` adds
  nothing new; `Original Amount`/`Original Currency` already exist.

## Agent builds
1. Migration: `original_rate`, `original_rate_date`, `original_rate_provider` on `expenses`
   (nullable). Model, both stores, export.
2. `app/services/currencies.py`: ISO 4217 minor units for every code; the symbol table above;
   a check for "is an ISO code".
3. `app/services/exchange_rates.py`: rate for any `base → trip currency` on a date, using the
   existing provider/cache; a "no rate published" result distinct from a fetch failure.
4. `app/services/conversion.py` / `splitting.py`: amounts and shares from the original currency
   to trip and home, exact in both.
5. `app/services/expense_parser.py`: third-currency codes and symbols, strict as above.
6. `app/services/drafts.py`: states `confirm_currency` and `typed_rate`; their transitions;
   receipts go through `confirm_currency` only for a third currency.
7. Keyboards, render (cards, rate line), handlers (buttons and the typed-rate reply).
8. Spec §4, §6, §7, §10, §12 amended in place with "(amended YYYY-MM-DD, Sprint 15)";
   CONTEXT.md (*original currency*, *typed rate*); README; manual checklist.
9. `/code-review` on the branch, fix findings, STATUS entry. One PR.

## Tests required
- Receipt in MYR on a THB/SGD trip: the confirm card; `Yes, MYR` → total → split; shares sum to
  the trip amount and the home amount exactly.
- `It's THB` re-reads as THB and continues with no third currency; amounts rescaled when the
  decimals differ (a VND reading corrected to THB, say).
- Non-initiator presses on the confirm card are refused.
- Trip- and home-currency receipts: no confirm card (regression).
- Text: `50 usd taxi`, `myr 120 dinner`, `RM120 lunch`, `€30 museum` start drafts in the right
  currency; `rm -rf` and `20 rmb` (not ISO) stay silent; `$20` follows the existing rule.
- No published rate: VND gets the typed-rate card; a rate reply and a `฿850` reply both work;
  shares exact; provider `typed` saved.
- Rate fetch failure: `Retry` card.
- Item by item and Not on this bill on a MYR receipt: shares exact in both currencies.
- Hypothesis: random amounts, three currencies with different decimals, random rates and
  splits: shares sum exactly to trip and home amounts.
- Postgres: the new columns round-trip; line items in trip currency.
- Cards show `RM 120.00 (฿850.00 · S$34.50)` and both rates.

## After: your checklist
1. Send a receipt in a third currency (or a photo of one). The card asks "This receipt looks
   like it's in …"; `Yes`.
2. Split it item by item; the summary shows the original amount, then ฿ and S$.
3. Confirm; `/balances` moves by the S$ amount on the card.
4. A receipt the bot reads in the wrong currency: `It's THB` corrects it.
5. Type `50 usd taxi`: a card in US$ with ฿ and S$; Split equally; Confirm.
6. Type `100000 vnd pho`: the typed-rate card; reply `0.0013`; the card shows ฿130.00.
7. A normal ฿ receipt and `200 baht taxi` behave as before.

## Exit criteria
- [ ] Checklist passes
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
