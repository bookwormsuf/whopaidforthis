# Sprint 7 — Hardening and go-live

## Goal
The repo is safe to make public, the README lets a stranger set it up, and the real trip group is live.

## In scope
Full-suite regression, secret history scan, README completion (all 16 items in brief §28), limitations
list, the real trip setup, and post-trip shutdown instructions.

## Out of scope
New features. Anything not in the master spec.

## Sprint pins
- `gitleaks detect --source . --log-opts="--all"` must report zero findings on the whole history. If it finds one, the key is rotated first, then history is rewritten, and both are logged in `STATUS.md`.
- README must contain, in this order: what it is, architecture diagram (Mermaid), data model, splitting model, BotFather setup, adding to a group, privacy mode, Google Cloud and Sheets, sharing with the service account, LLM setup, FX provider, environment variables, local testing, deployment, example usage with screenshots (test group only, no real names), settlement logic, limitations, stopping after the trip.
- Limitations list must include: single machine only; drafts lost on restart; albums are separate drafts; third currencies refused; SGD figures depend on ECB reference rates; Summary formulas assume Google Sheets, not Excel.
- A `docs/manual-test-checklist.md` consolidates every sprint's checklist into one regression run, with a place to record the date and result.
- `LICENSE` file: MIT, your name.
- The test Sheet and test group are kept; the real trip gets its own Sheet and group.

## Before you start (you)
Decide the trip group name and create it with all friends in it. Create a blank Sheet for the real trip, share it with the service account as Editor.

## Agent builds
README completion, limitations, `docs/manual-test-checklist.md`, `LICENSE`, any fixes from the full regression, `STATUS.md` final entries.

## Tests required
Whole suite green; regression checklist run by you against the deployed bot in the **test** group.

## After: your checklist
1. Run the full `docs/manual-test-checklist.md` in the test group. Record the date and result at the top of that file.
2. Add `@whopaidforthisbot` to the real trip group. Send `/settrip <real sheet url>`, choose JPY, SGD, Asia/Tokyo.
3. Ask every friend to send `/join`. Check `Users` in the real Sheet.
4. Pin a message in the group with three lines: how to log ("just type `1200 ramen` or send a receipt photo"), `/balances`, `/undo`.
5. Send one real-looking test expense, confirm, then `/undo` it, so everyone sees the flow once.
6. Check `fly status` shows one machine and `fly logs` is quiet.
7. After the trip: run `/settle`, agree transfers, then `fly scale count 0` to stop paying. To resume for the next trip, `fly scale count 1`.

## Exit criteria
- [ ] Regression checklist recorded
- [ ] Zero gitleaks findings on full history
- [ ] Real group bound, all friends joined
- [ ] `STATUS.md` marked go-live with date
