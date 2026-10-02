# whopaid — status log

Updated: 2026-10-02 (sprint 7 built; go-live checklist pending)

This file is committed and may become public. Content rule: no real names of testers or friends
(use "tester A"), no chat IDs, no Sheet IDs, no amounts or merchants from real receipts, no key
fragments. Agents append an entry after material work; Sufyan's sign-off is recorded as its own entry.

## Current progress

| Sprint | Spec | Build | Tests | Review | Manual checklist | Sign-off | Next action / owner |
|---|---|---|---|---|---|---|---|
| 0 Accounts, keys, skeleton | written | done | green (10) | 2026-09-29, findings fixed | 2026-09-29 | 2026-09-29 | Sprint 0 closed. Next: run sprint 1 / agent |
| 1 Trip binding and users | written | done | green (112) | 2026-09-29, findings fixed | 2026-09-29, all six pass | 2026-09-29 | Sprint 1 closed. Next: run sprint 2 / agent |
| 2 Text expenses, just me | written (+ carry-overs) | done | green (236) | 2026-09-29, findings fixed | 2026-10-01, all nine pass | 2026-10-01 | Sprint 2 closed. Next: run sprint 3 / agent |
| 3 First deployment | written | done | green (247) | 2026-10-01, findings fixed | 2026-10-01, all nine pass | 2026-10-01 | Sprint 3 closed. Next: run sprint 4 / agent |
| 4 Receipt photos, total only | written (+ §7/§10 amendments) | done | green (302) | 2026-10-01, findings fixed | 2026-10-01, all eight pass | 2026-10-01 | Sprint 4 closed. Next: run sprint 5 / agent |
| 5 Splitting and balances | written (+ §6 amendment) | done | green (363) | 2026-10-01, findings fixed | 2026-10-02, all six single-person steps pass; two-person steps automated, deferred to Sprint 7 | 2026-10-02 | Sprint 5 closed. Next: merge Sprint 6 / Sufyan approves |
| 6 Item claiming | written (+ §7 amendment) | done | green (404) | 2026-10-01, findings fixed | 2026-10-02, single-person steps pass; two-person steps automated, deferred to Sprint 7 | 2026-10-02 | Sprint 6 closed. Next: run sprint 7 / agent |
| 7 Hardening and go-live | written | done | green (404) | 2026-10-02, findings fixed (two need Sufyan) | – | – | Regression run (incl. deferred two-person checks) and go-live / Sufyan |

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

### 2026-10-01 — Sprint 3 build

- Performed by: Claude (build agent).
- Scope and reason: Sprint 3 "Agent builds" list per `docs/sprints/03-first-deployment.md`: `fly.toml`,
  Dockerfile version build-arg, startup/shutdown logging, deploy and secrets scripts, README
  Deployment section.
- Evidence / outputs: `fly.toml` (new); `Dockerfile` (`ARG`/`ENV BOT_VERSION`); `app/config/settings.py`
  (`bot_version` field, env `BOT_VERSION`, default `dev`); `app/bot/app.py` (`BOT_VERSION` constant
  removed, `STOP_SIGNALS` module constant, `_post_init` logs `whopaid started` with
  `bot_username`/`version` fields, new `_post_shutdown` logs `whopaid stopped`, `stop_signals`
  passed explicitly to `run_polling`); `app/logging_setup.py` (`JsonFormatter` emits only an
  allowlisted set of `extra=` fields, written before the core fields so an extra can never
  overwrite them; `default=str` removed so a non-serialisable extra raises); `app/bot/handlers/
  basic.py` (`/help` footer appends `whopaid <version>`); `scripts/export_fly_secrets.py` (new --
  reads `.env` with `dotenv.dotenv_values`, derives the required-variable list from `Settings`,
  validates with `load_settings()` first); `scripts/deploy.sh` (`--ha=false`), `scripts/
  set-fly-secrets.sh` (rewritten to feed `fly secrets import` from the Python exporter),
  `scripts/README.md` (all three scripts executable); `README.md` Deployment/Updating/
  Stopping-after-the-trip/Why-no-port sections; `.dockerignore` (added `scripts`, `fly.toml`,
  `.env.*`); `docs/spec/pinned-decisions.md` §15 and `docs/sprints/03-first-deployment.md` sprint
  pins and checklist (rewritten to match the no-`[http_service]`/`--ha=false` design and the
  `fly secrets import` flow); `tests/test_smoke.py` (post_init/post_shutdown hook tests,
  `STOP_SIGNALS` test, allowlisted-extra/dropped-extra/redacted-extra logging tests, `/help`
  version-footer test); `tests/test_export_fly_secrets.py` (new).
- Checks: `uv run pytest -q` 247 passed. `uv run ruff check .` and `uv run ruff format --check .`
  clean. `uvx pre-commit run --all-files` clean (gitleaks, ruff check, ruff format). `bash -n`
  clean on both shell scripts. `set-fly-secrets.sh --dry-run` run against a throwaway fake `.env`
  in the scratchpad dir (via a `WHOPAID_ENV_FILE` override plus fake `TELEGRAM_BOT_TOKEN` etc. as
  real env vars, so `Settings()` validation never touched the real `.env` either): printed only
  `would set: NAME` lines for the four required variables, nothing for `LOG_LEVEL`/`BOT_VERSION`/
  an empty var, no value anywhere. A stub `fly` binary on `PATH` confirmed the full (non-dry-run)
  path: `Setting: NAME` lines and `Set 4 secrets.` print only after `fly secrets import` exits 0,
  and nothing prints if it exits 1. Docker was running, so built the real image (`docker build
  --build-arg BOT_VERSION=testsha123`); confirmed `BOT_VERSION=testsha123` in the container's
  environment via `docker run ... env`, then removed the test image. `fly config validate` needs
  `fly auth login`, which this machine doesn't have; not run.
- Review (`/review`, four axes) fixed: the hand-rolled bash `.env` parser (quote-stripping,
  skip-empty logic) replaced with the app's own `dotenv.dotenv_values` plus a required-names list
  derived from `Settings`, and secrets now flow through `fly secrets import` fed by one pipe
  rather than being assembled into a bash array, so a value never has to pass through argv or get
  typed into a terminal; `scripts/deploy.sh` gained `--ha=false` because Fly adds a stopped
  standby machine for a process group with no services, which the sprint pin's "exactly one
  machine, ever" didn't account for; `JsonFormatter` moved from emitting every `extra=` key to an
  explicit allowlist, with core fields written after extras so a colliding extra can't overwrite
  `ts`/`level`/`logger`/`msg`/`exc`, and `default=str` removed so a bad extra fails a test instead
  of silently stringifying; the stop-signal tuple moved into a `STOP_SIGNALS` module constant with
  its own test; spec §15 amended (the auto-stop/auto-start/min-machines keys never applied without
  a services block, so they're gone, replaced by the no-`[http_service]` + `--ha=false` + `fly
  scale count 1` story); the README Deployment section and the sprint doc's own checklist had
  drifted (README was missing the laptop-closed phone-test step and the trivial-change-redeploy
  step) and are now the same nine steps verbatim; the app-name-taken fallback is now written into
  both the sprint doc and the README instead of only the sprint pins.
- Dropped below threshold or kept as-is: the exact wording of the `[deploy]` strategy comment
  (bluegreen-vs-rolling explanation) was reworded for accuracy rather than restructured further;
  `scripts/deploy.sh`'s dirty-working-tree refusal, not explicitly requested by this round, was
  kept since nothing asked for its removal and it's still correct.
- Not done: none of the "After: your checklist" steps (Fly login, launch, deploy, live run) —
  those need Sufyan's Fly login per the sprint doc.
- Reviewed by / disposition: `/review` run with four Opus axes (standards, spec, architecture, divergent); findings fixed as listed above. Awaiting Sufyan's checklist.
- Next action / owner: sprint 3 checklist / Sufyan.

### 2026-10-01 — Sprint 3 live run and checklist

- Performed by: Sufyan (Fly.io CLI, Telegram), Claude (commits).
- Sprint 3 was committed before the checklist because `scripts/deploy.sh` refuses a dirty tree.
- `fly launch --copy-config` rewrote `fly.toml` with identical settings but stripped the comments; the commented version was restored (with Fly's `"10s"` form for `kill_timeout`) and committed before the first deploy.
- All nine steps passed: login, launch as `whopaid-bot` in `sin`, secrets imported by name only, deploy, one machine `started`, `whopaid started` in logs, `/help` and an expense confirmed from a phone with the laptop closed and the row present in the Sheet, one machine listed, a trivial README commit redeployed and `/help` showing the new short SHA.
- Reviewed by / disposition: checklist verified by Sufyan.

### 2026-10-01 — Sprint 3 sign-off

- Performed by: Sufyan, recorded by Claude.
- Disposition: Sufyan said "sign off sprint 3". Sprint 3 closed. From here on testing is against the Fly machine; the local bot must stay stopped.
- Next action / owner: run sprint 4 (receipt photos, total only) / agent.

### 2026-10-01 — Sprint 4 build

- Performed by: Claude (build agent).
- Scope and reason: Sprint 4 "Agent builds" list per `docs/sprints/04-receipt-photos-total-only.md`:
  receipt reading via the Anthropic API (structured outputs), image downscaling, total selection
  and reconciliation, the receipt draft flow (`choose_total`, `reconcile`, `manual_amount`), the
  photo handler, Line Items write path, and README sections.
- Evidence / outputs: new `app/models/receipt.py` (domain `Receipt`/`ReceiptLineItem`/
  `CandidateTotal`), `app/services/llm_schemas.py` (`ReceiptExtraction` structured-output schema),
  `app/services/images.py` (`prepare_image`, 10 MB refusal, downscale to 1568px longest side, JPEG
  quality 85), `app/services/receipt_parser.py` (`AnthropicReceiptReader` using
  `client.messages.parse(..., output_format=ReceiptExtraction)`, one retry on failure,
  `select_total`/`reconcile`/`resolve_transaction_date`/`resolve_receipt_currency` pure functions),
  `app/bot/handlers/photo_expense.py` (photo handler, retry-read helper, manual-amount reply
  handler), `app/bot/keyboards/receipt.py` (choose_total/reconcile/manual_amount keyboards).
  Extended: `app/services/drafts.py` (`Draft` gains receipt-only fields; `DraftService` gains
  `start_from_receipt`, `start_unreadable`, `choose_total`, `use_total_anyway`, `retry_receipt`,
  `retry_unreadable`, `request_manual_amount`, `set_manual_amount`, `get_for_retry`,
  `find_by_bot_message`; `confirm` now passes line items to `append_expense`), `app/bot/render/
  draft.py` (receipt detected/choose_total/reconcile/manual_amount cards), `app/bot/handlers/
  text_expense.py` (`draft_callback` routes `total`/`usetotal`/`retry`/`manual`),
  `app/services/expense_parser.py` (`parse_amount_only`, `parse_caption_description`),
  `app/services/ledger_store.py` + `app/services/google_sheets.py` + `app/services/sheet_rows.py`
  (`append_expense` takes `line_items`, writes the `Line Items` tab first in the commit batch),
  `app/services/sheet_layout.py` (Read Me wording for Line Items corrected: not yet split per
  person this sprint), `app/bot/deps.py`/`app/bot/app.py` (`receipt_reader` dependency, Anthropic
  client constructed once at startup from `anthropic_api_key`), `app/logging_setup.py`
  (`input_tokens`/`output_tokens` added to the extra-fields allowlist), `tests/helpers.py`
  (`FakeReceiptReader`). New tests: `tests/test_images.py`, `tests/test_receipt_parser.py`,
  `tests/test_receipt_drafts.py`, `tests/test_photo_expense_handler.py`, five hand-written
  fixtures under `tests/fixtures/receipts/`. README: "Receipts", "LLM API setup", "Privacy"
  sections. `pyproject.toml`/`uv.lock`: added `anthropic`, `pillow`.
- Checks: `uv run pytest -q` 283 passed (up from 247; 36 new). `uv run ruff check .` and
  `uv run ruff format --check .` clean. `uvx pre-commit run --all-files` clean (gitleaks, ruff
  check, ruff format). `uv lock` updated, `uv sync --frozen` succeeds. Docker build
  (`docker build -t whopaid-test .`) succeeded; image removed after.
- The exact `client.messages.parse()` call shape and the image content-block shape were read from
  the claude-api skill's Python README (`/private/tmp/claude-501/bundled-skills/.../claude-api/
  python/claude-api/README.md` and `tool-use.md`) before writing `receipt_parser.py`, not recalled
  from memory. The installed SDK version (`anthropic==1.11.0`) was checked directly
  (`inspect.signature(Messages.parse)`) and matches the README's `output_format=<PydanticModel>`
  shape exactly. Model pinned to `claude-sonnet-5` per spec §7, `max_tokens=4096`, no `thinking`
  parameter (adaptive thinking omitted per the sprint pin, not needed for extraction).
- Deviations from the pins, with reasons:
  - The sprint doc says "extend `app/bot/handlers/callbacks.py`" for the new draft actions
    (`total`, `usetotal`, `retry`, `manual`). `callbacks.py` only handles the unrelated
    `join:`-prefixed callback pattern; every `"{draft_id}:{action}:{arg}"` callback (spec §8) is
    already dispatched through `text_expense.draft_callback`, so the new actions were added there
    instead, matching the existing single-dispatcher architecture. `app/bot/handlers/
    photo_expense.py` exposes a `retry_receipt_read` helper that `draft_callback` imports locally
    (avoids a module-load cycle, since `photo_expense` also needs `text_expense`-adjacent helpers
    via the shared `app/bot/formatting.py`).
  - `unsupported_currency_text` moved from `text_expense.py` to `app/bot/formatting.py` so both
    the text and photo handlers can import it without a circular import.
  - The sprint pin "a caption that parses as an expense (Sprint 2 parser) overrides description"
    is read literally as using `parse_expense`, which requires an amount. The sprint doc's own
    checklist step 6 ("caption `dinner`" with no amount → description "Dinner") only works with a
    lighter caption-only extraction, so a new `parse_caption_description` (reusing the same
    bot-mention-stripping/trailing-clause/category logic, without requiring an amount) is used for
    captions instead of `parse_expense`.
  - The reconciliation prompt fires whenever the chosen total (however it was chosen — confident
    auto-select or the `choose_total` button) differs from `items + adjustments` at all, not only
    after the `ask` branch. Spec §7 states the reconciliation trigger ("chosen total ≠ computed")
    separately from the total-selection rule, and a confident auto-selection can still be a few
    minor units off the line-item sum within the ±5 tolerance — tested in
    `test_reconcile_state_renders_numbers`.
  - Line Items' `Amount <T>` column is always written using the trip currency's decimal places,
    even for a receipt read in the home currency (there is no separate `Amount <H>` column in the
    §10 schema). Noted as a known limitation in `sheet_rows.line_item_to_row`'s docstring and in
    code comments; not covered by a test since no fixture exercises a home-currency receipt with
    line items.
- Not done / open questions for the reviewer:
  - `/review` has not been run on this sprint yet.
  - No real receipt photos or a live Anthropic call were exercised — all tests use
    `FakeReceiptReader` or a fake Anthropic client; Sufyan's own checklist (sprint doc "After:
    your checklist") still needs a real deploy and real photos, including the Anthropic Console
    usage check (step 8).
  - Telegram album handling is unimplemented by design (documented limitation per the sprint
    pins), each photo in an album becomes its own draft.
  - Reconciliation rendering sign ("difference 238" without +/-) was kept unsigned/absolute per
    the existing codebase's terse-card-text style; worth a second opinion on whether it should say
    "receipt total is 238 higher" instead.
- Reviewed by / disposition: not yet reviewed.
- Next action / owner: `/review` on this sprint's changes, then the sprint 4 checklist / Sufyan.

### 2026-10-01 — Sprint 4 review and fixes

- Performed by: Claude (cloud session; work moved from the local machine to remote).
- Scope and reason: `/review` (code-review skill, high effort) on the Sprint 4 commit, then
  fixes, per `CLAUDE.md`.
- Review findings, all confirmed by reading the code, all fixed:
  1. An empty `candidate_totals` (what the prompt asks for on a blurry photo or a non-receipt)
     raised inside draft creation and was reported as "Couldn't reach Google Sheets"; on
     `Try again` it escaped the callback unanswered. Now an unreadable reading is caught when
     validating into the domain `Receipt` and always shows the two-button card.
  2. The manual-amount reply handler took every group reply to any bot message, silently
     dropping replies such as `1200 yen ramen` sent as a reply to a bot card. The separate
     handler is gone; `text_expense_handler` first checks whether the reply is for a draft
     awaiting a manual amount, and otherwise parses the message as usual.
  3. A caption with an amount (`¥3000 dinner`) kept the amount in the description. Captions now
     use the same amount/currency rule as the text parser, shared via one `_locate_amount`
     helper (also removing the copied block in `parse_amount_only`).
  4. Line items from a home-currency receipt were written to `Amount <T>` as if their minor
     units were trip-currency minor units. Decided with Sufyan: convert at the expense's rate,
     half-even per item. Spec §10 amended.
  5. `messages.parse` returning no parsed output (refusal, `max_tokens`) returned `None` to the
     handlers and crashed them. It now triggers the one retry, then the unreadable path.
  6. Telegram download and Pillow errors escaped both the photo handler and `Try again`. One
     `read_receipt_photo` now turns every failure into an outcome the card can show.
  7. The caption override was lost after `Try again` or `Enter amount manually`. The draft now
     keeps `caption_override` through both paths.
  8. A confident total within ±5 of the line-item sum still showed the reconciliation prompt.
     Decided with Sufyan: it now goes straight to the card; the prompt remains for a total
     picked from the buttons that differs. Spec §7 amended (this replaces the build entry's
     "deviation" note about reconciliation).
  9. The LLM read ran inline in a blocking handler, stalling every other update for its
     duration, and `Try again` answered its callback only after the read. The photo handler and
     a dedicated `retry` callback handler are now non-blocking; `Try again` answers immediately
     and refuses a second press while a read is in flight.
  10. The domain `Receipt` was never built, and `CandidateTotal`/`LineItemKind` were defined
     twice. `receipt_parser.to_receipt` now validates every reading into `Receipt` (spec §7);
     drafts, total selection and the keyboard use the domain types; the LLM schema's class is
     renamed `CandidateTotalExtraction`.
- Checks: `uv run pytest -q` 298 passed (15 new tests covering the paths above). `uv run ruff
  check .`, `ruff format --check` and `uvx pre-commit run --all-files` clean. Still no real
  photo or live Anthropic call; that is the checklist.
- Checklist step 7 ("Send the same photo twice quickly. Only one draft appears."): Sufyan
  confirmed it means one message delivered twice, which the message-ID dedup already handles.
  Sending the same image as two separate messages still gives two drafts, by design.
- Reviewed by / disposition: findings fixed; awaiting Sufyan's checklist and sign-off.
- Next action / owner: deploy, run the sprint 4 checklist / Sufyan.

### 2026-10-01 — Deploy from GitHub Actions

- Performed by: Claude (cloud session).
- Scope and reason: Sufyan now works from a phone and the cloud environment's network policy
  blocks Fly.io, so deploys move to GitHub Actions. `.github/workflows/deploy.yml` runs on every
  push to `main` (and by hand from the Actions tab): ruff, format check and pytest, then
  `flyctl deploy --remote-only --build-arg BOT_VERSION=<short sha> --ha=false`, the same deploy
  as `scripts/deploy.sh`. One deploy at a time (`concurrency`). README "Updating" section
  updated. Spec §15 is unchanged in substance (one machine, `--ha=false`, no services).
- Needs: repo secret `FLY_API_TOKEN`, a Fly deploy token scoped to the app, created by Sufyan.
- Checks: workflow YAML parses; not yet run on GitHub (it first runs when this merges).
- Next action / owner: add the secret / Sufyan; merge, watch the run, then the sprint 4
  checklist from step 2 / Sufyan.

### 2026-10-01 — Sprint 4 checklist: duplicate total buttons

- Performed by: Sufyan (Telegram), Claude (fix).
- Checklist step 1 passed: deploy from GitHub Actions, `/help` shows the new version.
- Step 3 found a bug: a receipt printing one total on three lines got "Which is the final
  total?" with three identical buttons. Candidates are now merged by amount; a single distinct
  amount is used without asking, and still gets the reconciliation prompt if it is more than 5
  off the line items. The reader prompt now says a tax printed as already included is not a
  `tax` line item (counting it made the line items exceed the total). Spec §7 amended.
- Checks: `uv run pytest -q` 302 passed; ruff clean.
- Next action / owner: merge and deploy, then resend that receipt and continue the checklist /
  Sufyan.

### 2026-10-01 — Sprint 4 live run and checklist

- Performed by: Sufyan (Telegram, Sheets, Anthropic Console), Claude (fix and deploys).
- Deploys now run from GitHub Actions on every merge to `main`; `/help` showed each new version.
- Step 3 found duplicate total buttons (fixed and deployed, see the entry above); the receipt
  was resent and saved at the printed total.
- All eight steps passed (deploy, clear receipt with Expenses/Splits/Line Items rows, total
  choice, unreadable receipt with manual amount, non-receipt, caption, duplicate delivery
  covered by the automated test as agreed, Anthropic usage in cents).
- Reviewed by / disposition: checklist verified by Sufyan.

### 2026-10-01 — Sprint 4 sign-off

- Performed by: Sufyan, recorded by Claude.
- Disposition: Sufyan said "sign off sprint 4". Sprint 4 closed.
- Next action / owner: run sprint 5 (splitting and balances; automated multi-user tests in place
  of a second tester, per the 2026-10-01 decision) / agent.

### 2026-10-01 — Sprint 5 build and review

- Performed by: Claude, in a cloud session.
- Scope and reason: Sprint 5 "Agent builds" list, per `docs/sprints/05-splitting-and-balances.md`,
  under the 2026-10-01 decision (automated multi-user tests in place of a second tester).
- Evidence / outputs:
  - `app/services/names.py`: name resolution by username, first name or full name, ignoring
    case; `me` is the sender.
  - `app/services/settlements.py`: balances and greedy settlement, pure.
  - `app/services/splitting.py`: `SplitSpec` discriminated by `kind`, `split_shares`, and shares
    in both currencies.
  - Draft states `resolve_name`, `participants`, `split_method`, `custom`, plus `Edit`.
  - `app/bot/keyboards/split.py`, `app/bot/handlers/split.py` (custom-split replies) and
    `app/bot/handlers/balances.py` (`/balances`, `/mine`, `/settle`).
  - `LedgerStore.list_splits`.
  - README sections "Splitting model" and "Settlement logic".
  - `tests/group_sim.py`, a fake group that drives the real handlers as several users, and
    `tests/test_split_flow.py`, covering checklist steps 1 to 8 with two or three users.
- Checks: `uv run pytest -q` 363 passed; `ruff check` and `ruff format --check` clean. Not yet
  run against Telegram.
- Review (`/review`): three findings, all fixed.
  - `/settle` raised on a ledger whose shares don't add up (hand-edited Sheet). It now warns
    instead, `/balances` shows the same warning, and a repeated Splits row counts once.
  - A plain number in a custom-split reply was read as the trip currency, so `Me 6` on an S$12
    expense was refused. It is now read in the expense's own currency.
  - The payer question used a non-pinned button label. Both questions now use the pinned
    `Not sure, choose people`. For a payer it keeps the sender as payer and opens the
    participants card.
- Decisions and readings of the pins:
  - The split-method card appears after Everyone/Choose people with two or more people, so
    checklist step 1 needs an `Equally` tap before the preview.
  - Custom amounts are in the expense's own currency; `Confirm` on the custom card goes to the
    preview (spec §8), whose `Confirm` saves.
  - The preview shows home currency only for an expense entered in the home currency (spec §12).
  - Custom lines may name a joined user who was not selected; they are added.
  - An unknown name offers every joined user as a candidate.
  - The original brief's `/settle` example is not in the repo; a three-person stand-in with two
    transfers is tested instead.
  - Not done: a receipt caption's `split ...`/`paid by` clauses (spec §13) still only override
    the description. Carry to Sprint 6, which reworks the receipt split flow.
- Deferred two-person checks for Sprint 7's checklist (covered by automated tests until then):
  step 4 (tester B sends `paid by`/`split between`), step 5 (tester B presses your buttons), and
  a real second account's balance in steps 1, 6 and 7.
- Next action / owner: merge and deploy / Sufyan approves; then the single-person checklist /
  Sufyan.

### 2026-10-01 — Sprint 5 merged and deployed

- Performed by: Claude, with Sufyan's approval before his flight.
- Sprint 5 PR merged to `main`; the GitHub Actions deploy run succeeded.
- Not yet done: the sprint 5 single-person checklist / Sufyan.

### 2026-10-01 — Sprint 6 build and review (built ahead of the sprint 5 sign-off)

- Performed by: Claude, in a cloud session, while Sufyan was travelling.
- Process exception, approved by Sufyan: Sprint 6 was built before Sprint 5's sign-off. It sits on an
  open PR and is not merged; sprint 5 checklist findings come first.
- Scope and reason: Sprint 6 "Agent builds" list, per `docs/sprints/06-item-claiming.md`, with
  automated multi-user tests in place of a second tester (2026-10-01 decision).
- Evidence / outputs:
  - `split_items` and `allocate_proportional` in `app/services/splitting.py`.
  - Draft state `items`: `By items`, item taps, `Rest → everyone`, `Done`, `Fix items`, and the
    reply shortcuts `mine 1,4`, `split 5 everyone`, `unclaim 2`.
  - `app/bot/keyboards/items.py`, with initials.
  - The itemised preview line `items ¥a + tax ¥b − discount ¥c = ¥d`.
  - Line Items rows with assigned user IDs and `claimed` / `everyone` / `proportional`.
  - README section "Itemised receipts".
  - Carried from Sprint 5: a receipt caption's `split ...` and `paid by` now apply.
  - Tests: `tests/test_item_claiming.py` (checklist steps 1 to 7 with two or three users) and
    unit and property tests for the split.
- Checks: `uv run pytest -q` 397 passed; ruff and format clean. Not run against Telegram.
- Review (`/review`): three findings, all fixed.
  - A negative "Unlisted" item (lines more than the total) could block Done. It is now taken
    off its claimants in proportion to their other items.
  - Pressing `Rest → everyone` added the presser as a participant. It no longer does.
  - Any reply to the item card added the sender as a participant. Now only a claim that
    applies does.
- Decisions and readings of the pins:
  - A total picked automatically within ±5 of the lines gets an "Unlisted" item for the gap
    when By items is chosen, so the split still reconciles exactly and the gap is visible.
  - `Use total anyway` turns By items off for that receipt (spec §7).
  - `Fix items` opens the item card with everyone joined as participants, or the caption's
    participants if it named them.
  - `Rest → everyone` is open to any joined user, like the item buttons; `Done` is the
    initiator's only.
  - A tap on an item that went to everyone makes it the tapper's alone.
  - Service charge shows as its own `+ service` term in the preview line.
  - The item card has no Cancel button, per the exact §9 layout; Cancel is on the preview.
  - A text-shortcut problem (`No item 9`) is shown on the card, since a reply cannot get a toast
    and a second message is not allowed.
- Deferred two-person checks for Sprint 7: steps 1, 2 and 5 with a real second account (the
  shared dish, B's taps, and B pressing Done).
- Next action / owner: Sprint 5 checklist and sign-off / Sufyan; then merge the Sprint 6 PR and do
  its single-person checklist / Sufyan.

### 2026-10-02 — Sprint 5 checklist: Users rows shifted, `split everyone` silent

- Performed by: Sufyan (Telegram, Sheets), Claude (fix).
- Step 2 found a bug: `6000 yen dinner split everyone` got no reply; a plain expense worked.
- Cause: appending to the Users tab without a table range let Google skip the hidden User ID
  column, so every row landed one column to the right. The bot never found the existing row (a
  new row on every message) and reading the user list crashed on the empty ID. Sprint 5 is the
  first feature to read that list; a non-Sheets error had no reply.
- Fix (hotfix PR, merged and deployed on its own): appends anchored at `A1`, unreadable Users
  rows skipped with a warning, and any unexpected error now replies "Something went wrong,
  please try again." (a toast for a button). Merged into the Sprint 6 branch too.
- Checks: `uv run pytest -q` 365 passed on `main`, 399 on the Sprint 6 branch; ruff clean.
- Next action / owner: delete the shifted rows on the test Sheet's Users tab, `/join`, then
  redo the checklist from step 2 / Sufyan.

### 2026-10-02 — Sprint 5 checklist and sign-off

- Performed by: Sufyan (Telegram, Sheets), recorded by Claude.
- Checklist (single-person steps, per the 2026-10-01 decision): `/help` shows the new version;
  `split everyone` saves with one participant (after the Users-tab hotfix); Choose people, the
  empty-selection toast, Next and Edit; an unknown `paid by` name and `Not sure, choose people`;
  `/balances`, `/mine`, `/settle` ("All square"); `/undo` moves `/balances`. All six pass.
- Found and fixed along the way: the Users-tab column shift (hotfix, see above) and a clearer
  join card (the count includes the person who sent `/join`; a second tap says "You're already
  in"). Both merged and deployed on their own.
- Decided: a bare `split` (`6000 split dinner`) is dropped from the description and selects
  nobody. Spec §6 amended; ships with Sprint 6.
- Deferred to Sprint 7 (automated until then): the two-person steps 1, 4, 5, 6 and 7.
- Disposition: Sufyan said "sign off sprint 5". Sprint 5 closed.
- Next action / owner: approve the Sprint 6 merge / Sufyan; then the Sprint 6 single-person
  checklist / Sufyan.

### 2026-10-02 — Sprint 6 merged; Other amount on the total question

- Performed by: Claude, with Sufyan's approval.
- Sprint 6 merged to `main` after the Sprint 5 sign-off; the deploy succeeded.
- Testing plan kept: Sprint 6's two-person steps stay automated until Sprint 7 (Sufyan, 2026-10-02).
- Added at Sufyan's request: "Which is the final total?" now has `Other amount`, which asks for
  a reply with the total. A typed total in the receipt's currency keeps the line items and gets
  the reconciliation prompt (with `Fix items`) if they don't add up to it. Spec §7 amended.
- Checks: `uv run pytest -q` 404 passed; ruff clean.
- Next action / owner: merge / Sufyan approves; Sprint 6 single-person checklist / Sufyan.

### 2026-10-02 — Sprint 6 checklist and sign-off

- Performed by: Sufyan (Telegram, Sheets), recorded by Claude.
- Checklist (single-person steps, per the 2026-10-01 decision): new version in `/help`; a bare
  `split` drops from the description; a receipt caption's `split everyone`; receipt totals,
  `Other amount`, and the item flow reachable alone; Line Items rows; `/balances`. All pass.
- Line Items rows for receipts shared by Just me / Equally / Custom read `unassigned`, as
  designed; only a By items split records claims.
- Open, carried to Sprint 7: a receipt's included tax (内税) was once read as a separate `tax`
  line. Proposed fix: drop a tax line when leaving it out makes the lines match the printed total
  exactly. Not yet approved.
- Deferred to Sprint 7 (automated until then): Sprint 6 steps 1, 2 and 5 with a second person
  (shared dish, their taps, their Done).
- Disposition: Sufyan said "sign off sprint 6". Sprint 6 closed.
- Next action / owner: run sprint 7 / agent, with the deferred two-person checks from Sprints 5
  and 6 listed in its checklist.

### 2026-10-02 — Sprint 7 build and review

- Performed by: Claude, in a cloud session.
- Scope and reason: Sprint 7 "Agent builds" list, per `docs/sprints/07-hardening-and-go-live.md`.
- Secret scan: gitleaks v8.30.1, `gitleaks detect --source . --log-opts="--all"` over every
  branch (26 commits): no leaks found. Nothing to rotate or rewrite.
- Evidence / outputs:
  - README rewritten in the pinned order: what it is, architecture (Mermaid), data model,
    splitting, BotFather, groups, privacy mode, Google Cloud and Sheets, sharing, LLM, FX,
    environment variables, local testing, deployment, example usage, settlement, limitations,
    stopping after the trip.
  - The limitations list includes all six pinned items.
  - Example names are made up.
  - `docs/manual-test-checklist.md`: every sprint's checklist as one regression run, with a
    results table. Part B is the two-person checks deferred from Sprints 5 and 6.
  - `LICENSE`: MIT, copyright "bookwormsuf" (the GitHub handle), pending Sufyan's choice of name.
- Not done: screenshots for "Example usage". The agent cannot take Telegram screenshots; the
  README has a text transcript and a note that screenshots go in `docs/images/` (test group
  only, no real names).
- Still open from Sprint 6: the included-tax (内税) misread; no fix approved.
- Checks: `uv run pytest -q` 404 passed; ruff and format clean.
- Review (`/review`): seven findings.
  - Fixed: STATUS recorded the review before it ran.
  - Fixed: the regression checklist missed the restart/expired card, the Sheet layout, switching
    Sheet without duplicates, and the SGD Original Currency column.
  - Fixed: the README didn't say how to put the JSON key on one line.
  - Fixed: the Deployment steps were Sufyan's personal checklist; they now install flyctl and
    read for a stranger.
  - Fixed: Line Items `unassigned` was missing from the data model.
  - Needs Sufyan: screenshots (`docs/images/`), and the LICENSE name (handle or real name).
- Next action / owner: run `docs/manual-test-checklist.md` (with the second person for Part B)
  and the go-live steps / Sufyan.

