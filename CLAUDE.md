# whopaid

Telegram group expense tracker for trips. Records into Postgres, converts at historical rates,
splits receipts, settles in the home currency.

Before writing any code, read in this order:
1. `CONTEXT.md` — the vocabulary. Use these terms in code, comments and messages.
2. `docs/spec/pinned-decisions.md` — settled decisions. Execute verbatim; if one is impossible, stop and report rather than substitute.
3. `docs/adr/` — why the two irreversible choices were made.

Rules:
- Never commit secrets. `.env` is gitignored; only `.env.example` with blank values is tracked. The repo may become public.
- Never log receipt images, LLM image payloads, or credentials.
- Every confirmed expense must satisfy: sum of shares == expense amount, in both currencies. Enforce it in the model.
- Tests run with `uv run pytest`. Lint with `uv run ruff check .`.
- Build in the phase order in the spec (§16). Do not start Phase 3 UI before Phase 2 tests pass.

Sprints and status:
- Work is cut into sprints under `docs/sprints/`. Read `docs/sprints/README.md` for how a sprint runs, then the sprint's own file.
- `STATUS.md` is the project's status log. Append a work-log entry after material work and update the progress table. Never write real names, chat IDs, Sheet IDs, or receipt details into it.
- End every sprint with `/review` on that sprint's changes and fix the findings before handing over the manual checklist.
- Never mark a sprint done yourself. Sufyan's manual checklist and sign-off close a sprint.
