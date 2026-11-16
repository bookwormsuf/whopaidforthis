# Sprint 19 — Easier trip setup

## Goal
A group can start a trip without getting stuck at the currency questions. Testers did not know
what to type and did not understand "home currency". Setup becomes: pick the currency you spend,
pick the currency you pay each other back in, check one summary card, start. Decided with Sufyan
on 2026-11-16 and 2026-11-17 after tester feedback.

## In scope
- New wording for the two currency questions.
- `Other…` opens a page of every supported currency as buttons. No typed currency code.
- The timezone is no longer its own step. It is picked from the trip currency and shown on the
  summary card.
- `Change` buttons on the summary card for each of the three lines.
- A short hand-written list of timezones for currencies that span several timezones.
- Spec §18 amendment; CONTEXT.md (*trip currency*, *home currency*, *summary card*); manual
  checklist; tests.

## Out of scope
- The pinned card, the "/" command menu and `Everyone's in` (Sprint 20).
- Currencies the rate service does not publish (VND, TWD and others).
- Detecting privacy mode.
- The `Connected ✅` message text, `I'm in too`, `/join`.
- Who may press setup buttons (still the person who ran `/settrip`), the 30-minute setup
  lifetime, the "close the current trip?" prompt.
- Any database change.
- README (updated after Sprint 20).

## Sprint pins (decided with Sufyan, 2026-11-16/17)

### Words
- User-visible text never says "home currency" or "trip currency" on the setup path. It says
  **spend** (trip currency) and **pay back** / **pay each other back** (home currency). Code
  identifiers, the database and `CONTEXT.md` keep `trip_currency` / `home_currency`.

### Step 1: spend currency
- Card text: `Which currency will you spend on this trip?`
- Buttons unchanged: `THB`, `MYR`, `JPY`, `CNY`, then `Other…` (`TRIP_CCY_CHOICES`).

### Step 2: pay-back currency
- Card text: `Which currency do you want to use to pay each other back?`
- Buttons unchanged: `SGD`, `MYR`, `JPY`, `CNY`, then `Other…` (`HOME_CCY_CHOICES`).
- After step 2 the bot goes straight to the summary card. There is no timezone step.

### `Other…`
- Pressing `Other…` edits the same card: the question text stays the same, the keyboard becomes
  the full currency page.
- The page lists every code from `currencies.supported()`, sorted A to Z, 4 buttons per row,
  then one last row `◂ Back`. Codes already on the short keyboard stay on the page, so the page
  is the complete list. Buttons show the 3-letter code only.
- `◂ Back` restores the short keyboard for the same question.
- Picking a code behaves exactly like picking it from the short keyboard.
- If the live list cannot be fetched, the page uses the same fallback `supported()` uses today.
- The typed-currency path is removed: no `Type the trip currency code` / `Type the home
  currency code` prompts, and the setup no longer reads typed text for a currency. The
  `Unknown currency code. Try again or /cancel.` text goes with it.

### Timezone default
- The timezone is set to the first entry of the trip currency's timezone list (below) if it has
  one, else `default_timezone_for(trip_currency)`, else `UTC`. This is today's rule, plus the
  new list.
- Changing the spend currency from the summary card re-picks the default timezone **unless** the
  user has already changed the timezone in this setup. A timezone the user chose is never
  overwritten.

### Timezone lists
Hand-written, in `app/services/currencies.py` beside `TIMEZONE_DEFAULTS`, named
`TIMEZONE_CHOICES: dict[str, list[str]]`. The first entry is the default. Exactly these:

| Code | Timezones, in this order |
|---|---|
| USD | `America/New_York`, `America/Chicago`, `America/Denver`, `America/Los_Angeles`, `Pacific/Honolulu` |
| CAD | `America/Toronto`, `America/Winnipeg`, `America/Edmonton`, `America/Vancouver` |
| AUD | `Australia/Sydney`, `Australia/Brisbane`, `Australia/Adelaide`, `Australia/Perth` |
| IDR | `Asia/Jakarta`, `Asia/Makassar`, `Asia/Jayapura` |
| MXN | `America/Mexico_City`, `America/Cancun`, `America/Tijuana` |
| BRL | `America/Sao_Paulo`, `America/Manaus` |
| EUR | `Europe/Paris`, `Europe/Lisbon`, `Europe/Athens` |

- The button for `Asia/Makassar` reads `Asia/Makassar (Bali)`. Every other button shows the
  IANA name as is.
- Each list's first entry must equal `TIMEZONE_DEFAULTS[code]` where that key exists. Add
  `MXN: America/Mexico_City` and `BRL: America/Sao_Paulo` to `TIMEZONE_DEFAULTS`.
- A currency not in the table has a list of one: its default.

### Summary card
Replaces today's confirm card. HTML, names escaped:
```
<b>{trip name}</b>
Spend in: {trip currency}
Pay back in: {home currency}
Timezone: {timezone}
```
Keyboard, one button per row, in this order:
- `Change spend currency`
- `Change pay-back currency`
- `Change timezone`
- `Start trip` and `Cancel` on one row (unchanged behaviour).

### `Change` buttons
- `Change spend currency`: edits the card to step 1 (same text and keyboard, `Other…` works).
  A pick returns to the summary card, not to step 2.
- `Change pay-back currency`: the same, with step 2.
- `Change timezone`: edits the card to:
  ```
  Which timezone? Expense dates use it.
  ```
  Keyboard: one button per entry of the trip currency's timezone list, one per row, then
  `Type it`, then `◂ Back`. `◂ Back` returns to the summary card unchanged.
- `Type it`: edits the card to `Type the timezone, like Asia/Bangkok.` and reads the starter's
  next text in the chat, as the `Other…` timezone path does today. Matching ignores case:
  `asia/tokyo` is accepted and stored as `Asia/Tokyo` (the canonical name from
  `available_timezones()`). No match: `Unknown timezone. Try again or /cancel.` (unchanged) and
  it keeps waiting.
- A picked or typed timezone returns to the summary card.
- Timezone buttons carry the list **index** in callback data, not the name
  (`st:{session}:tzi:{n}`), so long names cannot pass Telegram's 64-byte limit. An index out of
  range is treated as a stale button: answer the press and change nothing.

### Unchanged rules that still apply
- Only the person who ran `/settrip` can press any setup button, `Change` and `◂ Back`
  included. Others get the existing `Only <name> can do this` toast.
- The setup lives in memory for 30 minutes; a restart or timeout gives the existing
  `This setup expired, run /settrip again`.
- Trip and pay-back currency may be the same.
- `Start trip` saves exactly what the summary card shows, then posts `Connected ✅` as today.

### Settled at build (agent's calls, open to change)
- Callback data beyond the pinned `tzi`: `chg:trip`, `chg:home`, `chg:tz` for the three `Change` buttons; `tz:type` and `tz:back` on the timezone list; `<step>:back` on the `Other…` page (`<step>` is `trip_ccy` or `home_ccy`). The old `tz:<name>` buttons are gone; a stale one is ignored.
- The default timezone is set when the spend currency is picked (not after step 2), unless the user already chose one. Every pick or change of spend currency goes through the same rule.
- `◂ Back` on the `Other…` page restores the short keyboard of the same question, also when the step was opened from the summary card. That step has no way back to the summary except picking a currency.
- The `Type it` prompt has no buttons. Any later button press, or a valid timezone, ends the wait.
- The typed-text wait now exists only for the timezone. The text handler ignores any session not waiting on `tz`. Typed text is trimmed before matching.
- `Change timezone` uses the list of the trip currency at the moment of the press. `tzi` index 0 to len-1 only; anything else (negative, text, too big) is a stale button.
- The `Retry` button after a failed save is unchanged (`confirm:yes`).
- The catalog fallback is built from `TIMEZONE_DEFAULTS` keys, so adding `MXN` and `BRL` there adds them to the fallback currency page too.
- `Start a trip` from the menu and `Start new trip` after the rebind prompt now show the new step 1 text (same code path). `tests/test_menu.py` was updated for that.

## Before you start (you)
Nothing.

## Agent builds
1. `app/services/currencies.py`: `TIMEZONE_CHOICES`, the two new `TIMEZONE_DEFAULTS` keys, and
   a pure `timezone_choices_for(code) -> list[str]` (the list, or a list of one default, or
   `["UTC"]`); a pure case-insensitive `match_timezone(raw) -> str | None`.
2. `app/bot/keyboards/settrip.py`: the full currency page with `◂ Back`; the summary keyboard;
   the timezone list keyboard with `Type it` and `◂ Back`. Remove `timezone_keyboard` once
   nothing calls it.
3. `app/bot/session_state.py`: the setup session records whether the timezone was chosen by the
   user, and whether a step was opened from the summary card (so a pick returns there).
4. `app/bot/conversations/settrip.py`: new step texts as module-level constants; step 2 goes to
   the summary card; `Change`, `Other…`, `◂ Back`, `Type it` handlers; remove typed-currency
   handling; case-insensitive typed timezone.
5. `docs/spec/pinned-decisions.md` §18: amend the `/settrip` line (already written on this
   branch); check the code matches it and `CONTEXT.md`.
6. `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.

## Tests required
- Pure: every `TIMEZONE_CHOICES` entry is a valid IANA name; each list's first entry equals
  `TIMEZONE_DEFAULTS` where the key exists; `timezone_choices_for` for a listed code, an
  unlisted tabulated code, and an unknown code (`["UTC"]`); `match_timezone` for `asia/tokyo`,
  `ASIA/TOKYO`, `Asia/Tokyo` (all `Asia/Tokyo`) and `Mars/Base` (None).
- Every callback data string the setup keyboards can produce is at most 64 bytes, for every
  supported currency and every listed timezone.
- Happy path through the handlers: `/settrip` → step 1 text → `JPY` → step 2 text → `SGD` →
  summary card with `Asia/Tokyo` → `Start trip` saves JPY, SGD, `Asia/Tokyo`.
- `Other…` shows every supported code A to Z, 4 per row, then `◂ Back`; `◂ Back` restores the
  short keyboard; a code from the page is saved.
- A typed currency code during setup is not read as a currency (no prompt, no reply from the
  setup).
- `Change spend currency` from JPY to USD: card shows USD and `America/New_York`. After
  `Change timezone` to `America/Chicago`, changing spend currency to CAD keeps
  `America/Chicago`.
- `Change pay-back currency` returns to the summary card, not to another step.
- `Change timezone` on IDR lists the three entries with `Asia/Makassar (Bali)`; picking it saves
  `Asia/Makassar`. On a currency with no list it shows the single default, `Type it`, `◂ Back`.
- `Type it` then `asia/bangkok` saves `Asia/Bangkok`; `nowhere` gets the unknown-timezone text
  and keeps waiting.
- A non-starter pressing `Change` gets the toast and changes nothing.
- An out-of-range `tzi` index changes nothing.
- An expired session on a `Change` press gets the existing expired text.

## After: your checklist
1. In a test group with no trip, tap `Start a trip`. The first question reads `Which currency
   will you spend on this trip?`.
2. Tap `Other…`. A page of currency codes appears. Tap `◂ Back`, then `JPY`.
3. The second question reads `Which currency do you want to use to pay each other back?`. Tap
   `SGD`. The summary card appears with `Timezone: Asia/Tokyo`. No timezone question came first.
4. Tap `Change spend currency`, pick `USD`. The card shows USD and `America/New_York`.
5. Tap `Change timezone`, pick `America/Los_Angeles`. Then `Change spend currency` back to
   `JPY`. The timezone stays `America/Los_Angeles`.
6. Tap `Change timezone`, `Type it`, type `asia/tokyo`. The card shows `Asia/Tokyo`.
7. Have someone else tap a `Change` button. They get the "Only … can do this" toast.
8. Tap `Start trip`. `Connected ✅` shows JPY → SGD · Asia/Tokyo.

## Exit criteria
- [ ] Checklist passes
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
