# Sprint 14 — Not on this bill

## Goal
Make a group-order receipt split with no maths, when the payer paid only part of it. The case
came from the second trip: a Grab group order where "everyone pays their own share". The
receipt lists every dish (฿1,087 total) and also shows "you paid" (฿764). Sufyan had paid for
his own dishes separately. Sprint 13's walk makes every dish go to someone, so there is no way to
leave his dishes out. If ฿764 is used as the total, the −฿323 difference is shared out like a
discount over everyone's dishes. Irina did the sums by hand and typed the amounts. Decided with
Sufyan on 2026-11-01: add a `Not on this bill` button on each item, and use the "you paid"
figure as the amount.

Starts only after Sprint 13 is signed off.

## In scope
- A `Not on this bill` button on every "Who had this?" screen.
- Leaving dishes out: the amount, the adjustments and the rounding line are worked out from the
  dishes kept.
- Receipt reading: when a receipt prints how much this person paid ("you paid", "your share",
  `คุณจ่าย`) as well as the order total, that figure is the amount.
- Spec §7 and §9 amendments; README, manual checklist and tests.

## Out of scope
- A list for removing dishes before the walk (offered and declined: the per-item button only).
- Recording the left-out dishes as a second expense for whoever paid them. They log their own.
- Any change to share maths (spec §5) other than where adjustments are spread.
- Merchant-specific parsing for Grab, foodpanda and similar. The receipt-reading instruction
  stays generic.

## Sprint pins (decided with Sufyan, 2026-11-01)

### Item screen
- The "Who had this?" buttons gain one row above `[◀ Back] [Next ▶]`:
  `[🚫 Not on this bill]`. Initiator only, like every walk button.
- Pressing it marks the item left out, clears any names ticked on it, and moves on, as
  `Everyone` does. It moves to the summary on the last item, and back to the summary when fixing
  one item.
- An item already marked left out shows the line `Not on this bill` under its description, in
  place of ticked names. Ticking a name brings it back on the bill.
- At least one item must stay on the bill. A press that would leave none does not move. It
  shows the toast "Keep at least one item, or Cancel".
- A one-participant receipt (`Just me`, or only one person picked) still skips the walk. The
  "you paid" amount is enough there.
- The button is not shown on the only item still on the bill (a one-item receipt, or every
  other item already left out), where it could only be refused. Added at review.

### Amount and adjustments
- The expense amount is the receipt total picked as today (spec §7). With the reading rule
  below, that is the "you paid" figure when the receipt prints one.
- When items are left out, tax, service charge, delivery, fees and discounts are scaled to the
  kept items' share of the item sum, each rounded half up to the minor unit (any rounding lands
  on the rounding line below). Example: kept items worth ฿750 of ฿1,069 get 750/1,069 of each
  adjustment. An adjustment that scales to zero is dropped.
- The `Unlisted / rounding` line is worked out again when the walk ends, from the kept items
  plus the scaled adjustments against the amount. It is spread like tax, as in Sprint 13. On
  Irina's receipt the line is only Grab's own rounding of its group split, a few baht.
- With nothing left out, the maths is exactly as in Sprint 13.

### Reconciliation prompt
- When the picked total is **less** than items + adjustments and item-by-item is on offer, the
  reconciliation prompt adds a line under the Sprint 13 one: "Paid for part of the order? Go
  item by item and mark the rest Not on this bill." (Changed at review: the Sprint 13 "shared
  like tax" line stays, as it is still true for `Split equally` or a walk with nothing left
  out.) `Continue with <total>` / `Cancel` stay as they are.
- Otherwise the prompt is unchanged from Sprint 13.

### Summary and saved expense
- The summary lists left-out items once, in one line: `Not on this bill: <desc>, <desc>, …`
  (shortened with `…` after 3). Per-person totals cover kept items only.
- **Added at review (agent's call, open to change):** when items are left out but the amount
  is still about the whole order's, the left-out dishes come back on the rounding line. When
  that line is 10% of the amount or more, the summary warns: "⚠️ <x> of <amount> isn't on any
  kept dish and is shared like tax. If you paid less, Cancel and pick the amount you paid."
  Units of one line are named once on the `Not on this bill` line.
- `Fix an item` lists every claimable item, left-out ones included, with `—` in place of
  initials, so a mistake can be undone.
- The saved expense holds only the kept items, the scaled adjustments and the rounding line, so
  its lines add up to the amount. No migration.
- `Split equally` and `Type amounts instead` ignore left-out marks: they split the amount, as
  today.

### Receipt reading
- The receipt instruction gains a rule: if the receipt prints an amount this person paid as well
  as an order total (wording such as "you paid", "your share", "amount paid by you", `คุณจ่าย`),
  list both in `candidate_totals`, and set `is_grand_total_guess` on the paid figure only.
- If the reader marks both, or neither, the existing total buttons ask (spec §7). No new
  schema field.
- **Agent's call at build, open to change:** spec §7's rule only picks a guess on its own when
  it is within 5 of the lines. A "you paid" figure is far from them, so the bot still asks,
  with the paid figure as the first button, and then shows the reconciliation prompt with the
  "Paid for part of the order?" line. Picking it automatically would mean loosening §7 for every
  receipt, which also guards against a misread total. So the amount is the paid figure after one
  tap, not straight away.

## Agent builds
1. `app/services/drafts.py`: a `left_out: frozenset[int]` field on `Draft` (indices into
   `line_items`, like `item_claims`);
   `walk_left_out(draft_id, presser_id, position)`; toggling a name on a left-out item brings it
   back; the "keep at least one" guard; `billed(draft)`, the kept items with scaled adjustments
   and the rounding line worked out again, used for the shares, the summary and the saved line
   items. Tests first.
2. `app/services/splitting.py`: unchanged; `split_items` runs on the billed lines. Shares still
   sum to the amount in both currencies.
3. `app/bot/keyboards/items.py`: the `Not on this bill` row; `—` in the fix list.
4. `app/bot/render/draft.py`: "Not on this bill" on the walk card; the summary line; the new
   reconcile line.
5. `app/bot/handlers/text_expense.py`: route `wleftout` (arg = position), with stale-position
   handling as for the other walk buttons.
6. `app/services/receipt_parser.py`: the "you paid" instruction.
7. Spec §7 and §9 amended in place with "(amended YYYY-MM-DD, Sprint 14)"; CONTEXT.md term
   *left out*; README "Splitting a receipt"; manual checklist.
8. `/review` on the branch, fix findings, STATUS entry. One PR.

## Tests required
- `Not on this bill` marks the item, clears its names and advances. On the last item it goes to
  the summary, and when fixing one item it returns to the summary.
- Ticking a name on a left-out item brings it back.
- Leaving out every item is refused with the toast, and the walk does not move.
- Non-initiator press is refused. A stale position changes nothing.
- Adjustments are scaled to the kept share. Hypothesis test over random receipts, exclusions,
  claims and amounts: shares sum exactly to the amount in both currencies, and the saved lines
  add up to the amount.
- The Grab case end to end: 9 lines summing to ฿1,069; delivery ฿16, fee ฿18, discounts ฿15
  and ฿32; amount ฿764. Some dishes are left out and the rest go to two people; shares sum to
  ฿764 and the home amount.
- With nothing left out, the results match Sprint 13 exactly (regression on the Eggtisan case).
- The reconcile prompt shows the "Paid for part of the order?" line only when the total is
  below the lines and item-by-item is on offer.
- `Split equally` after leaving items out splits the full amount.
- Receipt reading: the instruction names the "you paid" wording; a Grab-style reading with both
  figures offers the paid figure as the first total button.

## After: your checklist
1. Photo of a group-order receipt that shows both an order total and what you paid. The paid
   figure is the first total button; pick it. The prompt says "Paid for part of the order?";
   `Continue`.
2. `Everyone` or pick people → `Go item by item`. Mark two dishes `Not on this bill`, assign the
   rest.
3. Try to mark the last remaining dish `Not on this bill` too: "Keep at least one item, or
   Cancel".
4. Summary: the left-out dishes are on one line, and the totals add up to what you paid.
5. `Fix an item` → a left-out dish (shows `—`) → tick a name → back on the summary with it
   included.
6. Confirm; `/balances` matches.
7. A normal receipt with nothing left out behaves as in Sprint 13.

## Exit criteria
- [ ] Checklist passes
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
