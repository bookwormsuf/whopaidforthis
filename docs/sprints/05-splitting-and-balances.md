# Sprint 5 — Splitting and balances

## Goal
Expenses can be shared: Everyone, Choose people, equal or custom amounts, "paid by", "split between".
`/balances`, `/mine` and `/settle` work. This is the sprint that makes it Splitwise.

## In scope
Master spec §3 (name resolution), §5 (`split_equal`, `split_custom`, `SplitSpec`), §6 (split/paid-by clauses), §8 (participants, split method, custom entry, `Edit`), §12 `/balances`, `/mine`, `/settle`.

## Out of scope
Items mode (Sprint 6). Percentages.

## Sprint pins
- Detected card now shows `[Just me] [Everyone] [Choose people]`. `Choose people` renders one toggle button per joined user (✓ prefix when selected, initiator pre-selected), plus `[Next]`; `Next` needs at least one selected.
- Split method card `[Equally] [Custom]` (`By items` appears in Sprint 6 only when items exist). With exactly one participant, skip to preview.
- Custom entry: the card says "Reply to this message with one line per person, e.g. `Amir 2000`". The initiator's reply is parsed with the same name resolution; the card re-renders showing each person's amount, the running total, and `Remaining: ¥0 ✅` or `Remaining: ¥500 ❌`; `[Confirm]` is only rendered when remaining is zero. People not mentioned get zero and are dropped from participants.
- Preview lists every participant's share in both currencies, payer marked. `[Confirm] [Edit] [Cancel]`; `Edit` → participants card.
- "paid by <name>" sets payer; payer need not be a participant. "split between me Amir and John" pre-selects participants and skips the participants card. "split everyone" / "split all" selects Everyone.
- Ambiguous or unknown name → card asks "Who did you mean by 'Jon'?" with candidate buttons and `[Not sure, choose people]`.
- `/balances` in home currency per joined user with non-zero activity; sorted by net descending; last line "Settle with /settle".
- `/mine` = the caller's three numbers plus their last 3 expenses.
- `/settle` greedy algorithm per §12, home minor units, at most n−1 transfers, one line each `Amir → Sufyan S$31.20`; zero transfers → "All square 🎉".
- Balance and settlement code in `app/services/settlements.py`, pure, property-tested: sum of nets is zero; transfers zero every balance; transfer count ≤ n−1.

## Before you start (you)
You need a second Telegram account in the test group for steps 4 to 8. Either ask one friend for ten minutes, or add a second account to your Telegram app (Settings → Add account; it needs a different phone number). The second person must send `/join` once.

## Agent builds
`app/services/splitting.py`, `app/services/settlements.py`, `app/services/names.py`, parser extensions, draft state machine extensions, keyboards, `/balances` `/mine` `/settle` handlers, tests, README sections "Splitting model", "Settlement logic".

## Tests required
Equal split remainder rule incl. payer skip; custom exact-sum gate; `SplitSpec` union round-trips; six parse clauses; name resolution (exact, case-insensitive, ambiguous, unknown, "me"); balances from a fixture ledger match hand-computed values; settlement invariants (hypothesis); `/settle` example from the brief yields the two transfers; non-initiator pressing buttons gets the toast and no state change; Edit returns to participants keeping amounts.

## After: your checklist (deployed bot, two people)
1. Solo: send `6000 yen dinner split everyone`. Preview shows both joined users at ¥3,000. Confirm. `/balances` shows tester B owes you S$ for half.
2. Solo: send `4850 yen dinner`, tap Everyone. Shares are ¥2,425 each; try with three joined users later to see the ¥1 remainder go to a non-payer.
3. Solo: send `6000 taxi`, Choose people, deselect yourself, Next, Equally, Confirm. Your share is 0, the other person's is ¥6,000.
4. Tester B: send `3000 yen lunch paid by <your name> split between me and <your name>`. Payer is you, two shares of ¥1,500.
5. Tester B tries tapping Confirm on one of **your** open cards. They see "Only <you> can edit this"; nothing changes.
6. Solo: custom split: `9000 yen izakaya`, Everyone, Custom, reply `Me 4000` then `B 5000`. Confirm appears only after the second reply.
7. Send `/balances`, `/mine`, `/settle`. Check `/settle` transfers zero out `/balances` by hand.
8. Send `/undo` on your last expense and confirm `/balances` moves accordingly.

## Exit criteria
- [ ] All eight checklist steps pass with two accounts
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
