# whopaid — status log

Updated: 2026-09-29 (sprint 0 signed off)

This file is committed and may become public. Content rule: no real names of testers or friends
(use "tester A"), no chat IDs, no Sheet IDs, no amounts or merchants from real receipts, no key
fragments. Agents append an entry after material work; Sufyan's sign-off is recorded as its own entry.

## Current progress

| Sprint | Spec | Build | Tests | Review | Manual checklist | Sign-off | Next action / owner |
|---|---|---|---|---|---|---|---|
| 0 Accounts, keys, skeleton | written | done | green (10) | 2026-09-29, findings fixed | 2026-09-29 | 2026-09-29 | Sprint 0 closed. Next: run sprint 1 / agent |
| 1 Trip binding and users | written | not started | – | – | – | – | Run sprint 1 / agent; first step: give `build_application` a dependencies seam. Sufyan: create the `whopaid control` Sheet first |
| 2 Text expenses, just me | written | not started | – | – | – | – | after sprint 1 |
| 3 First deployment | written | not started | – | – | – | – | after sprint 2 |
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
