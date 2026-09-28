# whopaid — status log

Updated: 2026-09-29

This file is committed and may become public. Content rule: no real names of testers or friends
(use "tester A"), no chat IDs, no Sheet IDs, no amounts or merchants from real receipts, no key
fragments. Agents append an entry after material work; Sufyan's sign-off is recorded as its own entry.

## Current progress

| Sprint | Spec | Build | Tests | Review | Manual checklist | Sign-off | Next action / owner |
|---|---|---|---|---|---|---|---|
| 0 Accounts, keys, skeleton | written | not started | – | – | – | – | Run sprint 0 / agent |
| 1 Trip binding and users | written | not started | – | – | – | – | after sprint 0 sign-off |
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
