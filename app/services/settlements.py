"""Balances and settlement, in home-currency minor units. Pure, no I/O.

Spec references: pinned-decisions §12 (`/balances`, `/settle`), ADR 0001 (settle in
the home currency, each expense at its own rate), sprint 5 pins.

- Paid = the sum of home amounts of confirmed expenses the person paid.
- Share total = the sum of their home shares on confirmed expenses.
- Net = paid - share total; positive is owed money, negative owes money.

`settle` is the greedy debtor/creditor matching from §12: repeatedly move
`min(debt, credit)` from the largest debtor to the largest creditor. Every step
zeroes at least one side and the last step zeroes both, so there are at most n-1
transfers. It is near-minimal, not guaranteed minimal.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.models.expense import ExpenseRecord, SplitRecord


@dataclass(frozen=True)
class Balance:
    user_id: int
    paid_minor: int
    share_minor: int

    @property
    def net_minor(self) -> int:
        return self.paid_minor - self.share_minor


@dataclass(frozen=True)
class Transfer:
    from_user_id: int
    to_user_id: int
    amount_minor: int


def compute_balances(
    expenses: Iterable[ExpenseRecord], splits: Iterable[SplitRecord]
) -> dict[int, Balance]:
    """Balances per user from confirmed expenses only. Splits join through their
    Expense ID; a split whose expense is missing or cancelled is ignored (spec §10:
    orphan rows are ignored). Users with no paid and no share do not appear.
    """
    confirmed = {e.expense_id: e for e in expenses if e.status == "confirmed"}
    paid: dict[int, int] = {}
    share: dict[int, int] = {}
    for expense in confirmed.values():
        paid[expense.payer_user_id] = paid.get(expense.payer_user_id, 0) + expense.home_amount_minor
    for split in splits:
        if split.expense_id in confirmed:
            share[split.user_id] = share.get(split.user_id, 0) + split.share_home_minor
    return {
        user_id: Balance(user_id, paid.get(user_id, 0), share.get(user_id, 0))
        for user_id in sorted(paid.keys() | share.keys())
        if paid.get(user_id, 0) or share.get(user_id, 0)
    }


def settle(nets: Mapping[int, int]) -> list[Transfer]:
    """Greedy settlement of `nets` (user ID -> net minor units, summing to zero).
    Ties between equal balances go to the lower user ID, so the result is stable.
    """
    if sum(nets.values()) != 0:
        raise ValueError("net balances must sum to zero")
    debts = {uid: -net for uid, net in nets.items() if net < 0}
    credits = {uid: net for uid, net in nets.items() if net > 0}
    transfers: list[Transfer] = []
    while debts and credits:
        debtor = min(debts, key=lambda uid: (-debts[uid], uid))
        creditor = min(credits, key=lambda uid: (-credits[uid], uid))
        amount = min(debts[debtor], credits[creditor])
        transfers.append(Transfer(debtor, creditor, amount))
        debts[debtor] -= amount
        credits[creditor] -= amount
        if debts[debtor] == 0:
            del debts[debtor]
        if credits[creditor] == 0:
            del credits[creditor]
    return transfers
