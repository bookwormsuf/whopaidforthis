# Manual regression checklist

One run of every sprint's checklist, against the **deployed** bot in the **test** group with the
test Sheet. Never run it in the real trip group. Part B needs a second person: they join the
test group for about 15 minutes. It covers the two-person steps that Sprints 5 and 6 deferred
(decision 2026-10-01).

Before a run: `/help` in the test group shows the version you expect, and the Users tab has no
leftover test people you don't want counted in Everyone.

## Runs

| Date | Version (`/help`) | Who | Result | Notes |
|---|---|---|---|---|
| | | | | |

Result is `pass`, or `fail` with the step numbers. Fixes go in `STATUS.md`.

## Part A — one person

### Deployment and health
1. `fly status` shows exactly one machine, `started`. `fly logs` shows `whopaid started` and no
   errors.
2. `/help` replies with every command and the version.

### Binding and joining (Sprints 1–2)
3. `/settrip <test Sheet url>` in the bound test group asks "Switch Sheet / Keep current". Tap
   **Keep current**: nothing changes.
4. Un-share the test Sheet from the service account, then send `/settrip <url>`. You get the
   "share it with …" message with the right email. Re-share it.
5. `/join` replies `Joined ✅ <you>` with **I'm in too** and **Everyone's in**. Tapping **I'm in
   too** yourself says "You're already in ✅". **Everyone's in** removes the buttons.
6. The Users tab has one row for you, User ID in column A (unhide it), Joined TRUE. A second
   `/join` doesn't add a row.
6a. The Sheet layout: Summary is the first tab and opens by default, with "Who owes whom" and the
   positive/negative note; Read Me is last; header rows are frozen; Summary shows no `#REF!`; ID
   columns are hidden (Summary User ID; Expenses, Splits and Line Items IDs; Users User ID).
6b. On a spare Sheet (shared with the service account), `/settrip <spare url>` → **Switch Sheet**
   → JPY, SGD, Asia/Tokyo → Create builds every tab once. Switch back to the test Sheet the same
   way; nothing in it is duplicated.

### Text expenses (Sprint 2)
7. `1200 yen ramen` → card with ¥1,200, S$, your name, date and rate. **Just me** → **Confirm** →
   `Saved ✅ …`. Expenses, Splits and Exchange Rates each have a new row.
8. `I paid ¥4,850 for dinner`, `taxi 3200`, `750 coffee` all parse. `S$12 grab` reads
   `Grab — S$12.00`, home currency only; confirm it and check its Expenses row has Original
   Currency `SGD` and SGD Amount 12.00. Cancel the others.
9. `lol that ramen was so good` → silence. `500 baht massage` → "I can only record JPY or SGD
   for this trip."
10. `6000 split dinner` → "Dinner" with Just me / Everyone / Choose people. Cancel.
11. `/recent`, `/today`, `/summary` match the Sheet.
12. Send an expense and tap **Cancel**: nothing is written. Tap a button on an old saved card:
    there are none.
12a. Send an expense and leave the card open. Restart the bot (`fly machine restart <id>`, or a
    deploy), then tap **Just me**: "This expense expired, please send it again".

### Receipts (Sprints 4 and 6)
13. A clear receipt → card with merchant, total, S$, date and item count. **Just me** →
    **Confirm**. The Line Items rows are written as `unassigned`.
14. A receipt printing two different totals → "Which is the final total?" with the totals,
    **Other amount** and **Cancel**. Pick one; the saved amount matches.
15. On that card, **Other amount** → reply with a number the lines don't make → the difference
    prompt with **Fix items** / **Use total anyway** / **Cancel**.
16. **Fix items** → item card with "Still to claim: ¥x", tax listed in the text, an **Unlisted**
    item. Tap a 🍽 item: your initial appears. **✅ Done (¥x left)** shows a pop-up of what's
    left. Reply `mine 2,3` to the card: those get your initial. **↪ Rest → everyone** → **✅ Done**
    → your total with `items ¥a + tax ¥b` under it → **Confirm**. Line Items show `claimed`/`everyone` and the tax
    `proportional`.
17. A blurry photo or a non-receipt → "Couldn't read that receipt" with **Try again** / **Enter
    amount manually**. Enter `2400` as a reply → normal card → Confirm.
18. A receipt with the caption `dinner` saves as "Dinner". With the caption `split everyone`, it
    goes straight to the share step.
19. The same photo sent twice quickly → only one card.

### Splitting and balances alone (Sprint 5)
20. `6000 yen dinner split everyone` → check card, "Each person's share: • <you> ¥6,000" → **Confirm**.
21. `6000 taxi` → **Choose people** → untick yourself → **Next** → "Pick at least one person".
    Tick yourself → **Next** → check card → **Edit** → back to the tick list → **Cancel**.
22. `3000 yen lunch paid by Zed` → "Who did you mean by 'Zed'? (paid by)" → **Not sure, choose
    people** → tick list, still "<you> paid". Cancel.
23. `/balances`, `/mine`, `/settle` (with you alone: "All square 🎉").
24. `/undo` → **Yes, cancel it** → Status `cancelled` in Expenses; `/balances` and `/summary`
    move accordingly.

### Costs
25. Anthropic Console usage shows cents, not dollars.

## Part B — two people (A = you, B = the second person)

B sends `/join` in the test group first (or taps **I'm in too** on your `/join` reply, which then
reads "Joined so far: A, B" and "(2 joined)").

### Splitting (Sprint 5 steps 1–7)
26. A: `6000 yen dinner split everyone` → split-method card → **Equally** → both at ¥3,000 →
    **Confirm**. `/balances` shows B owes A half.
27. A: `4850 yen dinner` → **Everyone** → **Equally**: ¥2,425 each.
28. A: `6000 taxi` → **Choose people** → tick B, untick A → **Next** → B ¥6,000, no share for A
    → **Confirm**.
29. B: `3000 yen lunch paid by <A's first name> split between me and <A's first name>` → payer
    A, ¥1,500 each → B confirms.
30. B taps **Confirm** on one of A's open cards → "Only <A> can edit this"; nothing changes.
31. A: `9000 yen izakaya` → **Everyone** → **Custom** → reply `me 4000` (no Confirm yet), then
    reply `<B's name> 5000` → `Remaining: ¥0 ✅` and Confirm appears → **Confirm** →
    **Confirm**.
32. `/balances`, `/mine` (each of you), `/settle`. Check by hand that the transfers zero out
    `/balances`.

### Item claiming (Sprint 6 steps 1–7)
33. A sends a restaurant receipt with a tax line → **Everyone** → **By items**. Each taps their
    own dishes; both tap one shared dish (its button shows both initials) → A taps **Done**.
    The per-person totals add up exactly to the receipt total → **Confirm**. Line Items show
    the right user IDs; the tax row says `proportional`.
34. A supermarket receipt: claim two items, **Rest → everyone**, **Done**, **Confirm**.
35. B taps **Done** on A's item card → "Only <A> can edit this".
36. `/balances` reflects the itemised expenses. `/undo` the test expenses afterwards if the test
    Sheet should stay tidy.
