# Sprint 4 — Receipt photos, total only

## Goal
Send a photo of a receipt and it becomes an expense with the right total, merchant and date. Line
items are extracted and stored but not yet claimable.

## In scope
Master spec §7 in full except the `Fix items` branch, §13 photo handling, `Line Items` rows written on
commit with `Allocation Method = everyone` placeholder is **not** written; in this sprint line items
are stored with `Assigned User IDs` empty and `Allocation Method = unassigned`.

## Out of scope
Item claiming UI, proportional adjustments, `Fix items`. Participants (still `Just me`).

## Sprint pins
- Anthropic Python SDK, `client.messages.parse()` with the `ReceiptExtraction` Pydantic model, model `claude-sonnet-5`, `max_tokens` 4096, adaptive thinking omitted (not needed for extraction). The implementing agent must read the `claude-api` skill's Python README for the exact `parse()` and image-block shapes rather than recall them.
- Image handling: download the largest `PhotoSize`; if the longest side exceeds 1568 px, downscale with Pillow; JPEG quality 85; send as base64. Bytes live only in the request scope.
- System prompt states: currency defaults, "list every number that could be a total", Japanese receipt conventions (税込 = tax included, 小計 = subtotal, 合計 = total, お預り = cash tendered which is never the total, お釣り = change which is never the total).
- One retry on schema validation failure with the validation errors appended to the prompt; then give up with the two buttons from §7.
- Total selection and reconciliation exactly per §7; the reconciliation prompt offers `[Use total anyway] [Cancel]` only in this sprint (`Fix items` arrives in Sprint 6).
- The detected card for a receipt shows merchant, total in both currencies, date source ("from receipt" or "from message time"), and "N items found" when items exist.
- A caption that parses as an expense (Sprint 2 parser) overrides description. The photo's message ID is the dedup key; Telegram albums (several photos in one send) are processed as separate messages each producing its own draft, documented as a limitation.
- Cost guard: refuse photos larger than 10 MB; log token usage per call at INFO (numbers only).

## Before you start (you)
1. Collect 5 to 8 receipts of your own: at least two Japanese ones if you have any from a past trip (or photograph a printed sample), one long supermarket one, one with a service charge, one blurry. These are test inputs only; do not commit them.
2. Confirm the Anthropic spend limit from Sprint 0 is set.

## Agent builds
`app/models/receipt.py` (domain), `app/services/llm_schemas.py` (`ReceiptExtraction`), `app/services/receipt_parser.py` (LLM call, validation, total selection), `app/services/images.py`, `app/bot/handlers/photo_expense.py`, keyboards for total choice and reconciliation, `Line Items` write path, tests, README sections "Receipts", "LLM API setup", "Privacy".

## Tests required
Schema rejects missing fields and negative amounts; total rule: one confident candidate within ±5 saves, two candidates asks, no items uses the confident candidate; reconciliation message numbers; date within 30 days rule; currency mismatch refusal; malformed LLM output path shows the two buttons; image downscale keeps aspect ratio; no image bytes appear in any log record (test with a capturing handler). LLM calls mocked with recorded fixtures.

## After: your checklist (against the deployed bot)
1. Deploy the sprint (`fly deploy`).
2. Send a clear receipt photo. The card shows merchant, total, SGD, date, item count. Just me → Confirm. Check `Expenses`, `Splits`, `Line Items` rows.
3. Send a receipt where tax-included and tax-excluded totals both appear. You should get "Which is the final total?" with buttons. Pick one. Check the saved amount.
4. Send the blurry receipt. Expect "Couldn't read that receipt" with Try again / Enter amount manually. Tap Enter amount manually, type `2400`, confirm.
5. Send a photo of something that is not a receipt (a menu, a sign). Expect the same failure path, not a saved expense.
6. Send a receipt with the caption `dinner`. The saved description is "Dinner".
7. Send the same photo twice quickly. Only one draft appears.
8. In the Anthropic console, check usage shows a handful of requests and cents, not dollars.

## Exit criteria
- [ ] All eight checklist steps pass
- [ ] Tests, ruff green; `/review` findings addressed
- [ ] `STATUS.md` entry with your sign-off
