# Sprint 13 — Guided item split ("Who had this?")

## Goal
Splitting a receipt with items should need no maths and no typing, even when a dish is shared.
Today the split step offers `Equally / By items / Custom` as equals. On a real receipt (two of one
set: one eaten by one person, the other shared by two) Sufyan picked `Custom`, because nothing
suggested `By items` could do it, and then had to work out and type each person's amount. The
person who assigns is almost always one person doing it for the whole group, so the flow is
rebuilt around that person: the bot asks "Who had this?" for each item in turn, and sharing is
just picking more than one name. Decided with Sufyan on 2026-11-01.

## In scope
- A new item-by-item flow (draft state `item_walk`) replacing the all-items card as the way
  items are assigned.
- The split-method screen: item-by-item first, typing pushed to a small fallback.
- A summary step with per-person totals and `Fix an item`.
- Any gap between receipt lines and the receipt total is always shared like tax; the
  reconciliation prompt shrinks to `Continue` / `Cancel`.
- Retiring the all-items card, `Tapping for ▸`, and the item text replies.
- Spec §7, §8, §9 amendments; README and manual checklist updates; tests.

## Out of scope
- Any change to share maths: per-item equal split with the remainder rule, proportional
  adjustments with largest remainder, and the "shares sum to the total" invariants (spec §5) are
  reused as they are.
- Merging the participants step into the receipt card (asked and declined: two screens stay).
- A "Same as last" button (declined: keep the item screen lean).
- Assigning a receipt-total gap to particular people (declined: always shared like tax).
- Portion-based splitting for receipts without items, percentage split.
- Other people helping to tap (declined: initiator only).

## Sprint pins (decided with Sufyan, 2026-11-01)

### Split-method screen
- After `Everyone` or `Choose people` (and `Next`), when `By items` is available today (1 to 40
  claimable items) the screen reads "Who had what?" with buttons, one per row:
  `🍽 Go item by item ▶` / `Split equally` / `Type amounts instead` / `Cancel`.
- With no items, or more than 40: `Split equally` / `Type amounts instead` / `Cancel`; the
  "Too many items to split individually" line stays for the over-40 case.
- `Type amounts instead` opens today's `Custom` card unchanged (reply `<name> <amount>` lines,
  Confirm only when exact). Only its button label changes.
- The participants step is unchanged and stays a separate screen. `Just me` still skips all of
  this.

### Item-by-item (`item_walk`)
- One screen per claimable item, in receipt order. Units of a quantity line (`(1/2)`, `(2/2)`)
  are separate screens, exactly as they are separate buttons today (spec §9 unit rule).
- Card text, edited in place on the draft's one message:
  ```
  <header as on other draft cards>

  Who had this?  (<n> of <N>)
  <description> · <amount>
  ```
- Buttons: one toggle per participant (the people chosen on the participants step), three per
  row, `✓ ` prefix when ticked, labels by `short_name`; then `[Everyone]`; then
  `[◀ Back]  [Next ▶]` (`Back` absent on the first item); then `[Cancel]`.
- A name tap toggles that person on this item. Two or more ticked means shared equally, by the
  existing claim rule.
- `Everyone` sets this item to all participants and moves to the next item in the same press.
- `Next` with nobody ticked does not move; it answers with a toast "Pick who had this first".
  Telegram cannot grey out a button, so the toast is the disabled state.
- `Back` shows the previous item with its picks kept.
- `Next` (or `Everyone`) on the last item goes to the summary.
- Every button on these screens is initiator only; others get the usual
  "Only <name> can edit this" toast (spec §8). This replaces "item buttons may be pressed by any
  joined user" for receipts.
- Adjustment lines (tax, service charge, discount, and the gap line below) never get a screen;
  they are listed on the summary.

### Summary
- The existing `preview` card for items (per-person totals in both currencies, adjustments
  listed), with buttons `[Confirm] [Fix an item]` / `[Edit] [Cancel]`.
- `Fix an item` shows one button per claimable item, `"{n} {desc} {amount} · {initials}"` (the
  existing item-button text, initials of whoever has it, `all` when everyone), plus
  `[◀ Back to summary]`. Tapping one opens that item's "Who had this?" screen; its `Next` or
  `Everyone` returns to the summary rather than walking on.
- `Edit` keeps its current meaning (back to participants, spec §8). Picks for people still in
  the participant list are kept; picks for people removed are dropped, and the walk restarts at
  the first item that has nobody.

### Receipt total gap
- Whenever a receipt total differs from lines + adjustments (the automatic ±5 case, or a total
  picked or typed by the user), the difference becomes one adjustment line described
  `Unlisted / rounding`, kind `service_charge` when positive and `discount` when negative. No
  new kind and no migration. It is spread like tax (spec §5 largest remainder) and never gets an
  item screen. This replaces the claimable "Unlisted" item.
- The reconciliation prompt keeps its text (items sum, adjustments sum, total, difference) and
  adds the line "(<difference> isn't on any line; it'll be shared like tax)". Buttons:
  `[Continue with <total>]` / `[Cancel]`. `Fix items` and `Use total anyway` are removed.
  `Continue` keeps item-by-item available and goes on as `Fix items` does today, including the
  "Who paid?" step when it applies.
- `items_allowed` (set by `Use total anyway`) is removed.

### Edge cases (decided with Sufyan, 2026-11-01, second round)
- A participant with no items stays on the expense with a zero share, as today; the summary
  lists them with 0.
- No shortcut on long receipts: every claimable item gets its screen (up to 40). `Split equally`
  is the way out for a receipt nobody wants to walk.
- A person missing from the names is added through `Edit` (back to participants, picks kept).
  No extra button on the item screens.
- Draft expiry becomes 30 minutes from the last press or reply on the draft, not from when it
  was created (applies to all drafts; spec §8 amended). Each accepted press or reply refreshes it.
- Agent handles without asking: every walk button carries the item's position
  (`{draft_id}:walk_next:{index}` etc.) and a press for a position other than the current one is
  ignored with no change, so a double tap cannot skip an item; one participant only → no walk,
  every item is theirs, straight to the summary; items of zero amount never get a screen (they
  are already not claimable); an `item` line with a negative amount is treated as a `discount`
  adjustment line.

### Retired
- The all-items card as a state, the `Rest → everyone` and `Done` buttons, `👤 Tapping for ▸`
  and `claiming_for`.
- Text replies on item cards: `mine 1,4`, `split 5 everyone`, `unclaim 2`, `<name> 1,3`. A reply
  to an item card gets no special handling.
- Old callbacks from drafts made before deploy: drafts live in memory, so a deploy drops them
  and their buttons already answer "This expense expired" (spec §8). Nothing extra needed.

## Agent builds
1. `app/services/drafts.py`: state `item_walk` with a cursor (`walk_index`) and a
   `fixing_one: bool`; transitions `start_walk`, `walk_toggle`, `walk_everyone`, `walk_next`,
   `walk_back`, `fix_list`, `fix_pick`; gap as adjustment line in place of `_with_unlisted`
   items; `continue_total` replacing `fix_items` / `use_total_anyway`; remove `items` state,
   `claiming_for`, `rest_everyone`, `items_allowed` and the item text shortcuts; expiry from a
   `last_touched_at` refreshed on every accepted press or reply. Tests first.
2. `app/bot/keyboards/split.py` and `items.py`: split-method rows; walk keyboard; fix-list
   keyboard; summary keyboard. `app/bot/keyboards/receipt.py`: reconcile keyboard.
3. `app/bot/render/draft.py`: walk card, fix-list card, reconcile line.
4. `app/bot/handlers/text_expense.py` / `split.py` / `callbacks.py`: route the new actions; drop
   the old ones and the item text replies.
5. Spec §7, §8, §9 amended in place with "(amended 2026-11-01, Sprint 13)"; CONTEXT.md
   (claim wording, initiator); README "Splitting a receipt" section and screenshots note;
   `docs/manual-test-checklist.md` steps that mention the old card.
6. `/review` on the branch, fix findings, STATUS entry. One PR.

## Tests required
- Walk order is receipt order; quantity units are separate screens.
- Toggle on/off; two ticks share equally (shares match `split_items` for the same claims).
- `Everyone` assigns all participants and advances; on the last item it goes to the summary.
- `Next` with nobody ticked does not advance and returns the toast; `Back` keeps picks; no
  `Back` on the first item.
- Non-initiator presses on every walk, fix-list and summary button are refused.
- `Fix an item` → pick → `Next` returns to the summary, not the following item.
- `Edit` → drop a participant → their picks are gone and the walk resumes at the first empty
  item.
- Gap: positive and negative gaps become one `Unlisted / rounding` adjustment line with the
  right kind; never claimable; shares still sum exactly to the total in both currencies
  (hypothesis test over random gaps and claims).
- The Eggtisan case end to end: 2 × Egg Sando Set ฿667.46, KhaoSoi ฿340.00, Earl Grey ฿139.00,
  VAT ฿80.25, total ฿1,227.00 with the rounding line missing; set 1/2 → A, set 2/2 → B+C,
  KhaoSoi → B, tea → C; shares sum to ฿1,227.00 and the home amount.
- Split-method screen offers `Go item by item` only for 1 to 40 items; `Type amounts instead`
  opens the custom card.
- Reconcile prompt has only `Continue` and `Cancel`; `Continue` reaches the split step with
  item-by-item on offer; `Continue` with "Who paid?" pending asks it first.
- Item text replies (`mine 1`) are ignored.
- A participant with no items is saved with a zero share and shown with 0 on the summary.
- A stale `Next` / `Everyone` / name press carrying an old position changes nothing.
- One participant: no walk, straight to the summary with everything theirs.
- Negative `item` line becomes a discount adjustment; zero-amount items get no screen.
- Expiry: a draft touched 29 minutes after the last press survives; 31 minutes idle expires;
  a draft older than 30 minutes but pressed recently survives.

## After: your checklist
1. Photo of a receipt with 3+ items in a group with 3 joined people. `Everyone` → the split
   screen shows `Go item by item` first and `Type amounts instead` last.
2. Go item by item: tick one name, `Next`; tick two names on another item, `Next`; use
   `Everyone` on one. `Next` with nobody ticked shows "Pick who had this first".
3. `Back` once and see your picks kept.
4. On the summary, totals look right; `Fix an item`, change one, land back on the summary.
5. From a second account, press a name on a walk screen: "Only <name> can edit this".
6. Confirm; `/balances` matches the summary.
7. A receipt whose total you pick from the buttons and that doesn't match its lines: the prompt
   shows the gap line and only `Continue` / `Cancel`; `Continue` still offers item by item, and
   the summary lists `Unlisted / rounding`.
8. `Type amounts instead` still works as the old Custom.

## Exit criteria
- [ ] Checklist passes
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
