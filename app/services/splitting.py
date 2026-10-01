"""Pure splitting functions, no I/O. Spec references: pinned-decisions §5.

`split_custom` and `split_items` are Sprint 5/6 concerns; only `split_equal` is fully
implemented and used this sprint (`Just me`, n=1). `SplitSpec` is scaffolded now so
`Percent` can be added later without touching callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

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
    pass


@dataclass(frozen=True)
class Custom:
    amounts_by_participant: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Items:
    claims: dict[str, list[int]] = field(default_factory=dict)  # item_id -> claimant IDs


@dataclass(frozen=True)
class Percent:
    weights: dict[int, float] = field(default_factory=dict)  # placeholder, not implemented


SplitSpec = Equal | Custom | Items | Percent

SplitMethod = Literal["equal", "custom", "items"]


def split_custom(amounts_by_participant: dict[int, int], total_minor: int) -> dict[int, int]:
    """Reject unless the sum equals `total_minor` exactly."""
    if sum(amounts_by_participant.values()) != total_minor:
        raise ValueError("custom split amounts do not sum to the total")
    return dict(amounts_by_participant)


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
