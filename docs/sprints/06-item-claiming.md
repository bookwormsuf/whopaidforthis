# Sprint 6 — Item claiming

## Goal
On an itemised receipt, people tap what they ate; tax, service charge and discounts are spread in
proportion; every total reconciles to the yen.

## In scope
Master spec §5 `split_items` and largest-remainder allocation, §7 `Fix items`, §9 in full, `Line Items` rows written with real assignments.

## Out of scope
Percent splits. Editing item prices by hand beyond the single "Unlisted" item.

## Sprint pins
- `By items` appears on the split-method card only when the draft has 1 to 40 claimable items.
- Item card layout exactly as §9. Initials = first letter of first name, uppercase; collisions get a second letter. The card header shows running "Unclaimed: ¥x" so people see what is left.
- Tapping an item is open to any joined user, not just participants; a tap adds the tapper to participants if absent.
- `Rest → everyone` assigns unclaimed items to all current participants and is reversible per item.
- `Done` (initiator only) is disabled while unclaimed remains and `Rest → everyone` has not been pressed; when disabled the button text is `Done (unclaimed: ¥x)` and pressing it only shows a toast.
- On Done: `split_items` runs; the preview shows per person "items ¥a + tax ¥b − discount ¥c = ¥d" on one line each, then `[Confirm] [Edit] [Cancel]`; Edit returns to the item card with claims intact.
- `Fix items` on the reconciliation prompt opens the item card with an extra item "Unlisted" preset to the difference (positive or negative), claimable like any other.
- Text shortcuts as §9, only as replies to the item card. Unknown item numbers → toast.
- `Line Items` rows: `Assigned User IDs` comma-joined, `Allocation Method` = `claimed` | `everyone` | `proportional` (adjustments).

## Before you start (you)
Two accounts again, and two itemised receipts with at least one tax line (a restaurant bill and a supermarket receipt).

## Agent builds
`split_items` and `allocate_proportional` in `splitting.py`, item card state in `drafts.py`, `app/bot/keyboards/items.py`, claim handlers (buttons and text), `Fix items` path, tests, README section "Itemised receipts".

## Tests required
Shared item between 2 and 3 claimants with remainders; proportional tax example from the brief (2000/1000 with 300 tax → 200/100); largest-remainder tie-break order; zero-subtotal participant gets zero adjustment; discount allocation; `Fix items` Unlisted item makes the total reconcile; hypothesis: random items, claims and adjustments always sum exactly to the receipt total; >40 items hides `By items`; non-initiator Done is refused; text `mine 1,4` equals two taps.

## After: your checklist (deployed bot, two people)
1. Send the restaurant receipt. Everyone → By items. Both testers tap their own dishes; both tap one shared dish. Tap Done. The preview's per-person totals add up to the receipt total exactly. Confirm. Check `Line Items` rows show the right user IDs and the tax row says `proportional`.
2. Send the supermarket receipt. Claim two items, then tap `Rest → everyone`, Done, Confirm.
3. Send a receipt, By items, reply `mine 1,3` as a reply to the item card. Buttons 1 and 3 show your initial.
4. Try Done before everything is claimed: the toast tells you what is unclaimed.
5. Tester B taps Done: refused.
6. If any receipt failed reconciliation: tap `Fix items`, claim the "Unlisted" item, confirm, and check the total.
7. `/balances` reflects the itemised expenses.

## Exit criteria
- [ ] All seven checklist steps pass
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
