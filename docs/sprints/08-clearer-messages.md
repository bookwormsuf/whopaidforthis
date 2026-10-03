# Sprint 8 — Clearer messages

## Goal
Anyone in the group can tell at a glance how much was spent, who paid, and who owes what, and
the item card's buttons read as three different kinds of control. Requested by Sufyan on day 3
of the trip, from real use.

## In scope
Wording of `/mine`, `/balances`, `/settle`, `/recent`, `/today`, the expense cards, the Saved
line; the item card's buttons; short receipt titles. No change to amounts, splitting,
settlement or what is stored.

## Out of scope
Button colours or styles (Telegram bots can't style inline buttons). New commands.

## Sprint pins (decided with Sufyan, 2026-10-03)
- `/mine` is plain sentences: "You paid S$x for the group." / "Your share of everything is
  S$y." / "➡️ You owe S$z overall." (or "are owed", or "You're all square."), then "Recent:"
  bullets: `• 3 Oct · Ramen ¥1,200 (S$9.72)` and under it `Ada paid · your share
  S$4.86`, or `you're not in this one`.
- `/balances`: one bullet per person, "Ada is owed S$31.20" / "Amir owes S$31.20" / "Cy is
  all square", with "(paid S$a, share S$b)" after it; ends "Settle up with /settle".
- `/settle`: "To settle up:" then `• Amir pays Ada S$31.20`.
- `/recent` and `/today` use the same bullets as `/mine`, without "your share".
- Expense cards lead with the description and amount on one line, then who paid; the check
  card lists "Each person's share" as bullets.
- Saved line: `Saved ✅ Ramen ¥1,200 (S$9.72) · Ada paid · split 2 ways` (or
  `just Ada`, `split by amounts`, `split by items`).
- Item card: items prefixed 🍽, one `👤 Tapping for: <name> ▸` button (sender only) that cycles
  through the joined people, actions `↪ Rest → everyone` and `✅ Done`.
- Receipt titles: at most about 4 words, shop name plus kind ("Moon Coffee"); a longer title
  from the reader falls back to the shop name.

## Tests required
Updated exact-text tests for every changed message; the toggle cycles and is sender-only; a long
reader title falls back to the merchant.

## After: your checklist
1. `/mine`, `/balances`, `/settle`, `/recent`, `/today` in the trip group read clearly.
2. A text expense: the cards and the Saved line read clearly.
3. A receipt: a short title; By items shows 🍽 items, one Tapping for button that cycles, and
   the two action buttons.

## Exit criteria
- [ ] Checklist passes
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
