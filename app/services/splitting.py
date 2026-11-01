"""Pure splitting functions, no I/O. Spec references: pinned-decisions §4, §5.

`split_equal` and `split_custom` are used from Sprint 5; `split_items` from Sprint 6.
Split inputs are a `SplitSpec` union (`Equal`, `Custom`, `Items`, plus the
placeholder `Percent`), discriminated by `kind`, so a new method is one more member
and one more branch in `split_shares`, without touching callers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from app.models.receipt import ReceiptLineItem
from app.services.conversion import reciprocal_rate
from app.services.currencies import decimals_for


def split_equal(total_minor: int, participant_ids: list[int], payer_id: int) -> dict[int, int]:
    """Split `total_minor` equally among `participant_ids`.

    Base share = total // n each; the remainder (total % n) is distributed one unit
    each to participants sorted by Telegram User ID ascending, skipping the payer
    while other participants remain. If the payer is the only participant, they get
    everything.
    """
    if not participant_ids:
        raise ValueError("split_equal requires at least one participant")
    unique_ids = list(dict.fromkeys(participant_ids))  # de-dup, preserve first order
    n = len(unique_ids)
    base = total_minor // n
    remainder = total_minor % n
    shares = {pid: base for pid in unique_ids}

    if remainder:
        # Payer sorts last so the remainder goes to other participants first,
        # ascending by ID; only reaches the payer once everyone else already has one.
        priority = sorted(unique_ids, key=lambda pid: (pid == payer_id, pid))
        for pid in priority[:remainder]:
            shares[pid] += 1

    return shares


@dataclass(frozen=True)
class Equal:
    kind: Literal["equal"] = "equal"


@dataclass(frozen=True)
class Custom:
    """Amounts in the expense's original currency, minor units."""

    amounts_by_participant: dict[int, int] = field(default_factory=dict)
    kind: Literal["custom"] = "custom"


@dataclass(frozen=True)
class Items:
    """`claims` maps a claimable line item (its index in the draft's line items, as
    a string) to its claimants. `everyone` lists the items that went to all
    participants via `Everyone`, so the Line Items row can say so.
    """

    claims: dict[str, list[int]] = field(default_factory=dict)
    everyone: tuple[str, ...] = ()
    kind: Literal["items"] = "items"


@dataclass(frozen=True)
class Percent:
    weights: dict[int, float] = field(default_factory=dict)  # placeholder, not implemented
    kind: Literal["percent"] = "percent"


SplitSpec = Annotated[Equal | Custom | Items | Percent, Field(discriminator="kind")]

# Serialises and restores a `SplitSpec` (e.g. for logs or a future persistent draft
# store); `kind` picks the member.
SPLIT_SPEC_ADAPTER: TypeAdapter[Equal | Custom | Items | Percent] = TypeAdapter(SplitSpec)

SplitMethod = Literal["equal", "custom", "items"]


class SplitError(ValueError):
    """A split that cannot be made (custom amounts off, an unsupported method)."""


def split_custom(amounts_by_participant: dict[int, int], total_minor: int) -> dict[int, int]:
    """Reject unless the sum equals `total_minor` exactly. Negative amounts are
    rejected too: a share is never below zero.
    """
    if any(amount < 0 for amount in amounts_by_participant.values()):
        raise SplitError("custom split amounts must not be negative")
    if sum(amounts_by_participant.values()) != total_minor:
        raise SplitError("custom split amounts do not sum to the total")
    return dict(amounts_by_participant)


def split_shares(
    spec: Equal | Custom | Items | Percent,
    total_minor: int,
    participant_ids: list[int],
    payer_id: int,
    line_items: Sequence[ReceiptLineItem] = (),
) -> dict[int, int]:
    """Shares of `total_minor` (in the expense's original currency) per `spec`.
    `line_items` is needed only for `Items`.
    """
    if isinstance(spec, Equal):
        return split_equal(total_minor, participant_ids, payer_id)
    if isinstance(spec, Custom):
        return split_custom(spec.amounts_by_participant, total_minor)
    if isinstance(spec, Items):
        return split_items(line_items, spec, participant_ids, payer_id, total_minor).shares
    raise SplitError(f"split method {spec.kind} is not available yet")


def allocate_proportional(
    amount_minor: int, weights: dict[int, int], payer_id: int
) -> dict[int, int]:
    """Splits `amount_minor` across `weights` in proportion, by the largest-remainder
    method (spec §5): each gets the floor of its exact share, then the leftover units
    go one each to the largest fractional remainders, ties by ascending user ID with
    the payer last. A zero weight gets zero. A negative amount (a discount) is
    allocated as its magnitude, then negated.
    """
    if any(w < 0 for w in weights.values()):
        raise SplitError("proportional weights must not be negative")
    total_weight = sum(weights.values())
    if amount_minor == 0:
        return {uid: 0 for uid in weights}
    if total_weight == 0:
        raise SplitError("nothing to allocate an adjustment against")
    sign = -1 if amount_minor < 0 else 1
    magnitude = abs(amount_minor)
    base = {uid: magnitude * w // total_weight for uid, w in weights.items()}
    remainders = {uid: magnitude * w % total_weight for uid, w in weights.items()}
    leftover = magnitude - sum(base.values())
    order = sorted(
        (uid for uid, w in weights.items() if w > 0),
        key=lambda uid: (-remainders[uid], uid == payer_id, uid),
    )
    for uid in order[:leftover]:
        base[uid] += 1
    return {uid: sign * value for uid, value in base.items()}


AdjustmentKind = Literal["tax", "service_charge", "discount"]


@dataclass(frozen=True)
class ItemsSplit:
    """The result of `split_items`: per-person item subtotals, each adjustment
    kind's allocation, and the final shares (subtotal + tax + service - discount).
    `assignments` maps each line item's index to `(user IDs, allocation method)` for
    the Line Items tab.
    """

    subtotals: dict[int, int]
    adjustments: dict[AdjustmentKind, dict[int, int]]
    shares: dict[int, int]
    assignments: dict[int, tuple[list[int], str]]
    # Each adjustment line's own allocation, by its index in the line items, so a
    # card can show one line (the `Unlisted / rounding` gap) apart from its kind.
    line_allocations: dict[int, dict[int, int]] = field(default_factory=dict)


def split_items(
    line_items: Sequence[ReceiptLineItem],
    spec: Items,
    participant_ids: list[int],
    payer_id: int,
    total_minor: int,
) -> ItemsSplit:
    """Spec §5 `split_items`: each claimable item is split equally (same remainder
    rule as `split_equal`) among its claimants; an item with no claimants goes to all
    participants. Each adjustment line is then allocated across participants in
    proportion to their item subtotal, by the largest-remainder method. A negative
    item is taken off its claimants in proportion to their other items. Raises
    `SplitError` unless the shares add up to `total_minor` exactly -- never fixes a
    mismatch silently.
    """
    if not participant_ids:
        raise SplitError("split_items requires at least one participant")
    everyone = set(spec.everyone)
    subtotals = {uid: 0 for uid in participant_ids}
    assignments: dict[int, tuple[list[int], str]] = {}

    def claimants_of(index: int) -> tuple[list[int], str]:
        key = str(index)
        claimants = list(dict.fromkeys(spec.claims.get(key, [])))
        if not claimants or key in everyone:
            return list(participant_ids), "everyone"
        return claimants, "claimed"

    # Positive items first: each split equally between its claimants.
    for index, item in enumerate(line_items):
        if item.kind != "item" or item.amount_minor < 0:
            continue
        claimants, method = claimants_of(index)
        for uid, part in split_equal(item.amount_minor, claimants, payer_id).items():
            subtotals[uid] = subtotals.get(uid, 0) + part
        assignments[index] = (sorted(claimants), method)

    # A negative item (before Sprint 13, an "Unlisted" correction when the lines
    # came to more than the total; the gap is now an adjustment line) reduces its
    # claimants in proportion to what their items cost, so a
    # claimant with nothing else is not pushed below zero; with no such items
    # between them it is split equally.
    for index, item in enumerate(line_items):
        if item.kind != "item" or item.amount_minor >= 0:
            continue
        claimants, method = claimants_of(index)
        claim_weights = {uid: max(subtotals.get(uid, 0), 0) for uid in claimants}
        if sum(claim_weights.values()) > 0:
            parts = allocate_proportional(item.amount_minor, claim_weights, payer_id)
        else:
            parts = {
                uid: -part
                for uid, part in split_equal(-item.amount_minor, claimants, payer_id).items()
            }
        for uid, part in parts.items():
            subtotals[uid] = subtotals.get(uid, 0) + part
        assignments[index] = (sorted(claimants), method)

    # Adjustments are spread over positive subtotals only.
    weights = {uid: max(value, 0) for uid, value in subtotals.items()}
    weighted_ids = sorted(uid for uid, w in weights.items() if w > 0)
    adjustments: dict[AdjustmentKind, dict[int, int]] = {}
    shares = dict(subtotals)
    line_allocations: dict[int, dict[int, int]] = {}
    for index, item in enumerate(line_items):
        if item.kind == "item":
            continue
        amount = -abs(item.amount_minor) if item.kind == "discount" else item.amount_minor
        allocated = allocate_proportional(amount, weights, payer_id)
        line_allocations[index] = allocated
        bucket = adjustments.setdefault(item.kind, {})
        for uid, part in allocated.items():
            bucket[uid] = bucket.get(uid, 0) + part
            shares[uid] += part
        assignments[index] = (weighted_ids, "proportional")

    if sum(shares.values()) != total_minor:
        raise SplitError(
            f"item shares sum to {sum(shares.values())}, receipt total is {total_minor}"
        )
    return ItemsSplit(subtotals, adjustments, shares, assignments, line_allocations)


def shares_in_both_currencies(
    original_shares: dict[int, int],
    *,
    original_currency: str,
    trip_currency: str,
    home_currency: str,
    trip_to_home_rate: Decimal,
    trip_amount_minor: int,
    home_amount_minor: int,
    payer_id: int,
) -> tuple[dict[int, int], dict[int, int]]:
    """`(trip_shares, home_shares)` from shares in the original currency (spec §4):
    the other currency's shares are converted per share and reconciled to that
    currency's total with `convert_shares`, so both sums are exact.
    """
    if original_currency == trip_currency:
        home_shares = convert_shares(
            original_shares,
            trip_to_home_rate,
            trip_currency,
            home_currency,
            home_amount_minor,
            payer_id,
        )
        return dict(original_shares), home_shares
    trip_shares = convert_shares(
        original_shares,
        reciprocal_rate(trip_to_home_rate),
        home_currency,
        trip_currency,
        trip_amount_minor,
        payer_id,
    )
    return trip_shares, dict(original_shares)


def _exact_converted(amount_minor: int, from_ccy: str, to_ccy: str, rate: Decimal) -> Decimal:
    """Same scaling as `convert_minor`, without rounding: the exact `to_ccy` minor
    value a share would be worth, kept for the remainder comparison below.
    """
    from_decimals = decimals_for(from_ccy)
    to_decimals = decimals_for(to_ccy)
    major = Decimal(amount_minor) / (Decimal(10) ** from_decimals)
    return major * rate * (Decimal(10) ** to_decimals)


def convert_shares(
    shares: dict[int, int],
    rate: Decimal,
    from_ccy: str,
    to_ccy: str,
    total_to_minor: int,
    payer_id: int,
) -> dict[int, int]:
    """Convert each share from `from_ccy` to `to_ccy` at `rate` (round half-even, per
    share), then reconcile the sum to `total_to_minor` per spec §4/§5.

    Each adjustment unit goes to whichever participant's rounded value is currently
    furthest from its exact (unrounded) value -- the largest-remainder method, same
    principle §5 already uses for proportional adjustment allocation -- so every
    final share stays within one minor unit of its exact value; ties are broken
    ascending by Telegram User ID, the payer sorting last for a shortfall (skipped
    while others still need their unit) and first for a surplus (given back first).
    A pure ID-round-robin reconciliation (ignoring which share is already closest to
    correct) cannot guarantee that bound: a share that already rounded up can also be
    first in ID order and receive a further +1, landing 1.5 minor units off truth.
    """
    exact = {
        pid: _exact_converted(amount, from_ccy, to_ccy, rate) for pid, amount in shares.items()
    }
    converted = {
        pid: int(value.to_integral_value(rounding=ROUND_HALF_EVEN)) for pid, value in exact.items()
    }
    diff = total_to_minor - sum(converted.values())
    if diff == 0:
        return converted

    if diff > 0:
        gap = lambda pid: exact[pid] - converted[pid]  # noqa: E731
        order = sorted(converted, key=lambda pid: (-gap(pid), pid == payer_id, pid))
        delta = 1
        remaining = diff
    else:
        gap = lambda pid: converted[pid] - exact[pid]  # noqa: E731
        order = sorted(converted, key=lambda pid: (-gap(pid), pid != payer_id, pid))
        delta = -1
        remaining = -diff

    index = 0
    while remaining > 0:
        pid = order[index % len(order)]
        converted[pid] += delta
        remaining -= 1
        index += 1
    return converted
