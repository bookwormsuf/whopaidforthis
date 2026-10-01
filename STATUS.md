# whopaid — status log

Updated: 2026-10-01 (sprint 2 signed off)

This file is committed and may become public. Content rule: no real names of testers or friends
(use "tester A"), no chat IDs, no Sheet IDs, no amounts or merchants from real receipts, no key
fragments. Agents append an entry after material work; Sufyan's sign-off is recorded as its own entry.

## Current progress

| Sprint | Spec | Build | Tests | Review | Manual checklist | Sign-off | Next action / owner |
|---|---|---|---|---|---|---|---|
| 0 Accounts, keys, skeleton | written | done | green (10) | 2026-09-29, findings fixed | 2026-09-29 | 2026-09-29 | Sprint 0 closed. Next: run sprint 1 / agent |
| 1 Trip binding and users | written | done | green (112) | 2026-09-29, findings fixed | 2026-09-29, all six pass | 2026-09-29 | Sprint 1 closed. Next: run sprint 2 / agent |
| 2 Text expenses, just me | written (+ carry-overs) | done | green (236) | 2026-09-29, findings fixed | 2026-10-01, all nine pass | 2026-10-01 | Sprint 2 closed. Next: run sprint 3 / agent |
| 3 First deployment | written | not started | – | – | – | – | Run sprint 3 / agent |
| 4 Receipt photos, total only | written | not started | – | – | – | – | after sprint 3 |
| 5 Splitting and balances | written | not started | – | – | – | – | after sprint 4; needs tester B |
| 6 Item claiming | written | not started | – | – | – | – | after sprint 5; needs tester B |
| 7 Hardening and go-live | written | not started | – | – | – | – | after sprint 6; needs all friends |

Column meanings: **Build** not started / in progress / done. **Tests** green / red with count. **Review**
`/review` run date and whether findings were addressed. **Manual checklist** the sprint's "After"
list, done by Sufyan, date. **Sign-off** Sufyan's explicit "sign off sprint N", date.

## Work log

### 2026-09-28 → 29 — Grilling and specification

- Performed by: Claude (Fable 5.1) in conversation with Sufyan.
- Scope and reason: turn the original brief into pinned implementation decisions before any code.
- Evidence / outputs: `CONTEXT.md`, `docs/spec/pinned-decisions.md`, `docs/adr/0001`, `docs/adr/0002`,
  `docs/sprints/*.md`, this file. Local, uncommitted at time of writing.
- Checks and limitations: Frankfurter API behaviour verified by a lookup agent against frankfurter.dev
  (no key, weekend fallback returns the prior business day with its date). Claude model IDs and
  structured-output method taken from the bundled API reference dated 2026-06-24. No code exists yet.
- Decisions that changed from the brief: settle in home currency (SGD) not yen; currency-neutral trips
  via `/settrip`; privacy mode off; a control Sheet holds group→Sheet bindings; Fly.io hosting.
- Reviewed by / disposition: Sufyan approved every round in conversation; no independent review.
- Next action / owner: run sprint 0 / agent, then sprint 0 checklist / Sufyan.

### 2026-09-29 — Sprint 0 build and review

- Performed by: Claude (Fable 5.1) orchestrating a Sonnet build agent, four Opus review agents and six Haiku verifiers.
- Scope and reason: Sprint 0 "Agent builds" list, per `docs/sprints/00-accounts-keys-skeleton.md`.
- Evidence / outputs: `pyproject.toml`, `uv.lock`, `app/` (config, JSON logging, PTB application, `/start` and `/help`), `Dockerfile`, `.dockerignore`, `.gitignore`, `.pre-commit-config.yaml`, `.env.example`, `README.md`, `tests/test_smoke.py`. 23 files staged, not committed.
- Checks: `uv run pytest` 10 passed; `uv run ruff check .` and `ruff format --check` clean; `uvx pre-commit run --all-files` passed (gitleaks v8.30.1, ruff v0.16.9; pre-commit bootstrapped Go itself). Startup with the three secrets unset or blank exits 1 naming each variable. `uv` 0.12.19 was installed on Sufyan's laptop by the agent (checklist step A is done).
- Review (`/review`, four axes): 4 findings confirmed at or above threshold and fixed: blank `.env` values passed as present (now `env_ignore_empty`); bad or lowercase `LOG_LEVEL` crashed with a raw traceback (now validated, upper-cased, one-line error); logging docstring overclaimed that the token can never appear (token is now redacted by the formatter); a test used the shared `/tmp` folder (now `tmp_path`). Also adopted: `SecretStr` for the three secrets so a printed settings object masks them; handler test asserts on command names, not a count; uv image pinned to 0.12.19. Dropped below threshold: ruff hooks in pre-commit beyond the gitleaks pin, an extra `app/logging_setup.py` file not in the pin list, an f-string log call.
- Deviations from pins, kept: `.pre-commit-config.yaml` uses hook id `ruff-check` (current name; `ruff` is a legacy alias that silently checks nothing); `pyproject.toml` has a hatchling build section so `uv sync` can install `app/`; `.env.example` also lists `WHOPAID_CONTROL_SHEET_ID` (spec §1) although `Settings` reads only the four Sprint 0 variables.
- Deferred to Sprint 1: `build_application(token)` has no seam for the Sheets store or draft store; add one as Sprint 1's first step.
- Not done: the bot has not been run against Telegram (no token on this machine). `pre-commit install` not run; Sufyan can run it once so the hooks fire on every commit.
- Reviewed by / disposition: agent review only; awaiting Sufyan's checklist and sign-off.
- Next action / owner: checklist steps B to G in the sprint doc / Sufyan; then "sign off sprint 0".

### 2026-09-29 — Sprint 0 sign-off

- Performed by: Sufyan (manual checklist), recorded by Claude.
- Checklist: uv installed (by the agent); BotFather token issued and privacy mode turned off; test group created with the bot in it; Google Cloud project created, Sheets API enabled, service account `whopaid-bot` created with a JSON key stored outside the repo; Anthropic key created; `.env` filled and confirmed untracked; bot run locally and `/help` answered in the test group.
- Process note: the agent wrote the Google key into `.env` by script without showing the value. Sufyan ruled that agents must never write env files; from now on the agent hands over the command and Sufyan runs it.
- Disposition: Sufyan said "sign off sprint 0". Sprint 0 closed.
- Next action / owner: create the `whopaid control` Sheet and share it with the service account / Sufyan; then "run sprint 1" / agent.

### 2026-09-29 — Sprint 1 build and review

- Performed by: Claude (Fable 5.1) orchestrating a Sonnet build agent, four Opus review agents and five Haiku verifiers.
- Scope and reason: Sprint 1 "Agent builds" list per `docs/sprints/01-trip-binding-and-users.md`: `LedgerStore` interface with Sheets and in-memory implementations, `/settrip` button flow, `/join`, seen-user upsert, Summary formulas, README sections.
- Evidence / outputs: `app/models/{trip,user}.py`, `app/services/{currencies,ledger_store,sheet_layout,summary_formulas,google_sheets}.py`, `app/bot/{deps,session_state}.py`, `app/bot/conversations/settrip.py`, `app/bot/keyboards/settrip.py`, `app/bot/handlers/{join,seen_users}.py`, 10 test files. 31 files staged, not committed.
- Checks: `uv run pytest` 106 passed; ruff and pre-commit clean. Frankfurter currencies endpoint (`/v1/currencies`) verified live by the build agent; gspread 6 API names read from the installed package. Not run against real Telegram or real Sheets; that is the checklist.
- Review (`/review`, four axes): fixed after verification: Users rows were written as text so the Summary "joined" filter would never match (now real numbers and booleans, RAW input); Share formula multiplied a header cell (now comma-form SUMPRODUCT over header-free open ranges); rebind left the old Trip row (now overwritten); concurrent `/join` and seen-user upsert could double-append (per-sheet lock); gspread errors leaked past the store (now `LedgerUnavailable`, with Retry button on `/settrip` and a one-line reply on `/join`); `open_by_key` and worksheet lookups lacked retries and caching (now retried, cached per sheet); a broad group-text handler would have swallowed Sprint 2 expense messages (now a filter that matches only while awaiting settrip input); `/settrip` worked in private chats; access check ran after the rebind prompt; replies were not HTML mode; unbound-group lookups hit the control Sheet on every message (60 s negative cache); dead `SheetIdError` removed; settrip sessions and the seen-user throttle moved from module globals into `Deps`. Dropped below threshold: "initiator" naming for settrip sessions, 20-row cap on the per-person block.
- Spec amendments made: §10 pins IDs as numbers and Joined as a real boolean; §11 pins the User ID join-key column and header-free open ranges for array formulas.
- Deviations from pins, kept: retry is 1 attempt + 3 retries (4 calls) with 1/2/4 s backoff; Bot Version is a constant in `app/bot/app.py`; the seen-user upsert refreshes a user at most once per hour to protect Sheets quota.
- Reviewed by / disposition: agent review only; awaiting Sufyan's six-step checklist and sign-off.
- Next action / owner: checklist in the sprint doc / Sufyan; then "sign off sprint 1".

### 2026-09-29 — Sprint 1 live run and checklist

- Performed by: Sufyan (Telegram and Sheets), Claude and the build agent (fix).
- First live `/settrip` failed: the seen-user handler and the `/settrip` handler both ran `get_binding` on the same update and both tried to create the `Bindings` tab; Google rejected the second (400 "already exists") and, with no error handler registered, the user saw nothing. Fixed: Bindings tab creation is locked and cached, an "already exists" reply is recovered from, 4xx other than 429 are never retried, a PTB error handler replies "Couldn't reach Google Sheets, please try again." for `LedgerUnavailable`, and the three raw `get_binding` call sites catch it. Tests 112, ruff and pre-commit clean.
- Second run: all six checklist steps passed (connect, seven tabs with frozen headers and formulas, `/join`, re-run with confirmation, unshared-Sheet error with the right email, Bindings row in the control Sheet).
- Feedback from Sufyan, pinned into the spec as §2a, §3 addition and §10a and added to the top of Sprint 2: no "rebind" wording; an `I'm in too` button and reply-to-join because `/join @username` cannot resolve an ID; Summary first with plumbing columns hidden, plain-English block titles, number formats and a Read Me tab.
- Reviewed by / disposition: checklist verified by Sufyan; explicit sign-off phrase not yet given.
- Next action / owner: "sign off sprint 1" / Sufyan; create `whopaid test trip 2` / Sufyan; run sprint 2 / agent.

### 2026-09-29 — Sprint 1 sign-off

- Performed by: Sufyan, recorded by Claude.
- Disposition: Sufyan said "sign off sprint 1" after all six checklist steps passed. Sprint 1 closed. `whopaid test trip 2` Sheet created and shared for Sprint 2.
- Next action / owner: run sprint 2 / agent.

### 2026-09-29 — Sprint 2 build and review

- Performed by: Claude (Fable 5.1) orchestrating a Sonnet build agent and four Opus review agents. No separate verifier pass this sprint: every fixed finding was confirmed by two or more axes or by reading the cited code.
- Scope and reason: Sprint 2 per `docs/sprints/02-text-expenses-just-me.md`: the three carry-overs (wording, join button and reply-to-join, readable Sheet layout), then Money/conversion, Frankfurter rate provider with two-level cache, deterministic text parser, drafts with `Just me`, atomic commit to Sheets, dedup, `/recent`, `/today`, `/summary`, `/undo`.
- Evidence / outputs: 46 files staged, not committed; 221 tests, ruff and pre-commit clean. Frankfurter v1 endpoint verified live by the build agent. Not run against real Telegram or Sheets.
- Review (`/review`, four axes) fixed: reads used the Sheet's formatted text so ¥1,000+ crashed every read command (now unformatted values); hiding Summary column A would have hidden every block title (User ID moved to a hidden last column; category/day/payer tables side by side so they cannot overlap); `repeatCell` ranges exceeded the tab size; `$` was refused instead of meaning the home currency; the joined-so-far edit used plain text instead of HTML; home shares split directly instead of derived from trip shares; usernames blank in the Sheet; identity rate when trip and home currency match; JSON parse errors from Frankfurter not wrapped; Confirm could double-write after a lost acknowledgement (now `committing` state and an existence check); draft ids added to error logs. Structure: Draft made source-neutral for receipts, rendering moved to `app/bot/render`, dedup and rate caching each in one place, shared expense-line formatter. Dropped below threshold or judged fine: double-tap on Confirm (PTB processes updates sequentially), `append_expense` signature shape, wording nits.
- Spec amendments: §4 (verified URL, identity rate, `$` rule, largest-remainder reconciliation of converted shares), §3 (sending an expense joins the sender), §6 (the six brief examples written in), §10 (commit via `spreadsheets.batchUpdate` + `appendCells`), §10a and §11 (Summary layout, dates as text), §12 (amount order).
- Deviations kept: converted-share reconciliation uses largest-remainder rather than round-robin (round-robin can move a share more than one minor unit; pinned in §4).
- Reviewed by / disposition: agent review only; awaiting Sufyan's nine-step checklist and sign-off.
- Next action / owner: checklist / Sufyan; then "sign off sprint 2".

### 2026-10-01 — Sprint 2 live run and checklist

- Performed by: Sufyan (Telegram and Sheets), Claude and the build agent (fixes).
- First live `/settrip` to the new Sheet failed with "Couldn't reach Google Sheets": the group's old Sheet had been un-shared in Sprint 1 step 6, and reading its Trip tab during the switch prompt raised a 403 that was treated as an outage. Fixed: a bound Sheet that returns 403/404 is reported as `TripSheetInaccessible` (cached 60 s); `/settrip` now asks "This group's current Sheet can't be opened any more. Switch to the new one?"; other commands reply that the bot lost access to the Sheet.
- Feedback pinned into §3 and built: the `I'm in too` button shows a counter (`I'm in too (N joined)`) and an `Everyone's in` button that removes the prompt and appends "Latecomers can type /join."; a bot restart losing an in-progress `/settrip` ("This setup expired") is by design, state is in memory until Sprint 3.
- Feedback pinned into §12 and built: an expense entered in the home currency is shown in the home currency only (card, shares, Saved line, `/recent`, `/today`, `/undo`); the trip-currency equivalent is still stored. Trip-currency expenses keep the `¥X (S$Y)` form.
- Checklist note: sending the four step-4 examples in one message produced one expense with the first amount and the rest as its title. Candidate for Sprint 7 hardening: refuse a message with more than one amount.
- Checks: `uv run pytest` 236 passed; ruff, ruff format and pre-commit clean after each fix.
- Second run: all nine checklist steps passed (connect with Switch Sheet, readable Summary and hidden IDs, text expenses with the day's rate, Sheet rows, SGD expense, silence on chatter, read commands, `/undo`, Cancel, stale buttons and expired draft after restart).
- Reviewed by / disposition: checklist verified by Sufyan.

### 2026-10-01 — Sprint 2 sign-off

- Performed by: Sufyan, recorded by Claude.
- Disposition: Sufyan said "sign off sprint 2" after all nine checklist steps passed. Sprint 2 closed.
- Next action / owner: run sprint 3 (first deployment to Fly.io) / agent.
