# whopaid — domain glossary

Terms used throughout the code, the spec and the export. Implementation details do not belong here.

## People

- **Trip user** — a person who has run `/join` or tapped `Join` in the trip's Telegram group. Identified only by Telegram User ID. Username and display name are decoration and may change.
- **Everyone** — the set of trip users in this group at the moment a participant choice is made. Not the Telegram member list, which bots cannot see.
- **Payer** — the trip user who handed money to the merchant. Defaults to the sender of the expense message; may be overridden with "paid by <name>".
- **Creator** — the trip user who sent the message that produced the expense. Usually the payer, not always.
- **Initiator** — the creator of a draft; the only person allowed to move that draft through participants, split method, item by item, Confirm, Edit and Cancel.
- **Participant** — a trip user who consumed part of an expense and therefore owes a share of it.

## Trips and money

- **Trip** — one stretch of shared spending in one Telegram group, started with `/settrip`, with a trip currency, a home currency and a timezone. A group has at most one open trip; earlier trips of the same group are closed.
- **Closed trip** — a trip that takes no new expenses. Closed by `/settrip` starting a new trip or by `Close this trip` after `/settle`. Still readable and exportable.
- **Trip starter** — the trip user who ran `/settrip` for this trip; the only person who can delete it.
- **Export** — a spreadsheet file of one trip's records that anyone in the group can download from the chat.
- **Welcome** — the bot's pinned message in a group carrying the menu. Posted when the bot joins the group, on `/start` and on `/help`.
- **Menu** — the buttons under the welcome. What it offers depends on whether the group has no trip, an open trip or a closed trip; a press is the same as typing the command.
- **Trip currency** — the currency merchants are paid in during the trip (JPY for Japan).
- **Home currency** — the currency friends settle with each other in (SGD). Balances and settlement are computed in home currency.
- **Original amount / original currency** — the amount exactly as paid. Must be either the trip currency or the home currency.
- **Trip amount / home amount** — the expense expressed in each of the two trip currencies. One of them equals the original amount; the other is converted.
- **Rate** — the exchange rate used for one expense, trip→home. **Rate date** — the date that rate was published for, which may be earlier than the transaction date (weekends, holidays, rate not yet published).
- **Transaction date** — the day the money was spent, in the trip timezone. From the receipt if printed, else from the Telegram message time.
- **Minor unit** — the smallest unit of a currency (1 yen, 1 cent). Shares are always whole minor units; fractions never exist.

## Expenses

- **Expense** — a confirmed, saved record of one payment by one payer, split among participants. Has a status of `confirmed` or `cancelled`. Never deleted.
- **Draft** — an expense that is still being built through the button flow. Lives in memory only, expires after 30 minutes, never appears in the ledger.
- **Share** — the portion of an expense that belongs to one participant, in both currencies. Shares of an expense always sum exactly to the expense amount.
- **Split method** — how shares were derived: `equal`, `custom`, `items`. Percentage is a planned future method.
- **Line item** — one priced line from a receipt. Regular items are claimable. **Adjustment lines** (tax, service charge, discount) are never claimable and are allocated proportionally.
- **Unlisted / rounding** — the adjustment line for a receipt total the lines don't reach (or pass); stored as a service charge or discount and shared like tax.
- **Claim** — a participant marked as having had a line item, by the initiator on its "Who had this?" screen. An item with several claims is **shared** between those claimants equally.
- **Item by item** — the split that walks the receipt's claimable items one screen at a time, asking "Who had this?".
- **Left out** — an item marked `Not on this bill` on its "Who had this?" screen: someone else paid for it (a group order where each pays their own part). An itemised split bills only the kept items, with adjustments scaled to their share of the item sum.
- **Candidate total** — a number on a receipt that might be the grand total. The printed grand total is authoritative once chosen.
- **Reconciliation** — the check that line items plus adjustments equal the receipt total, and that shares sum to the expense amount.
- **Receipt read** — one attempt to have the model read a receipt photo, whether a new photo or `Try again`.
- **Receipt read budget** — the daily number of receipt reads allowed per chat and across all chats; resets at midnight Singapore time.

## Balances

- **Paid** — the sum of expense home amounts where the person is payer.
- **Share total** — the sum of the person's shares in home currency.
- **Net balance** — paid minus share total. Positive means owed money; negative means owes money.
- **Settlement** — a list of transfers (from, to, amount in home currency) that brings every net balance to zero.
