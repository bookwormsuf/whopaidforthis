"""Receipt reading: the LLM call, validation, total selection and reconciliation.

Spec references: pinned-decisions §7, sprint 4 pins. `select_total`, `reconcile`,
`resolve_transaction_date`, and `resolve_receipt_currency` are pure functions, unit
tested directly. `ReceiptReader` is a `Protocol` so tests use a fake (recorded JSON
fixtures); `AnthropicReceiptReader` is the only thing that talks to the Anthropic
API.

The exact `client.messages.parse()` call shape and the image content-block shape
were read from the claude-api skill's Python README before writing this file
(structured outputs via `output_format=ReceiptExtraction`; image content block is
`{"type": "image", "source": {"type": "base64", "media_type": ..., "data": ...}}`).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

import anthropic
from pydantic import ValidationError

from app.models.receipt import CandidateTotal, Receipt, ReceiptLineItem
from app.models.trip import Trip
from app.services.images import PreparedImage
from app.services.llm_schemas import ReceiptExtraction

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"
MAX_TOKENS = 4096

# Reconciliation tolerance for the "one confident candidate" rule (spec §7).
_CONFIDENT_TOLERANCE_MINOR = 5

SYSTEM_PROMPT_TEMPLATE = """You are reading a photograph of a receipt for a trip expense tracker.

Trip currency: {trip_currency}. Home currency: {home_currency}. If the receipt does
not show a currency explicitly, assume {trip_currency}.

List every number printed on the receipt that could plausibly be the grand total, in
`candidate_totals`, even ones you are not sure about. Set `is_grand_total_guess=true`
on the one you believe is the actual total the customer paid, if you can tell.

Japanese receipt conventions, when this is a Japanese receipt:
- 税込 (zeikomi) means tax is already included in the shown amount.
- 小計 (shokei) is the subtotal, before tax or service charge -- not the total.
- 合計 (goukei) is the total.
- お預り (oazukari) is the cash handed over by the customer -- never the total.
- お釣り (otsuri) is the change given back -- never the total.
- A tax amount printed as already included -- 内税, (内 消費税等), 税込, "incl. tax" --
  is part of the item prices. Do not list it as a `tax` line item; only list tax that is
  added on top of the items.

List every priced line as a line item, with a `kind`: `item` for a regular purchased
item, `tax` for a tax line, `service_charge` for a service charge, `discount` for a
discount or coupon (its amount should be positive; the sign is handled downstream).

If the photo is blurry, not a receipt, or otherwise unreadable, still return your
best-effort guess: a low `confidence`, an empty `candidate_totals` list if you cannot
read any number with any confidence, and a `notes` field explaining what's wrong.
"""


class ReceiptUnreadable(Exception):
    """The LLM call failed twice (schema-invalid, no parsed output, or exception), or
    the reading failed validation into the domain `Receipt` (e.g. no candidate
    totals). The caller shows "Couldn't read that receipt" with `Try again` /
    `Enter amount manually`.
    """


class ReceiptReader(Protocol):
    async def read(self, image: PreparedImage, *, trip: Trip) -> ReceiptExtraction: ...


class AnthropicReceiptReader:
    """Calls `client.messages.parse()` with the `ReceiptExtraction` schema.

    One retry on failure (schema-invalid output, or any exception from the call
    itself), with the validation error text appended to the retry's user turn, per
    the sprint 4 pin. Never retries more than once. Logs token usage (numbers only)
    at INFO after each successful call.
    """

    def __init__(self, client: anthropic.Anthropic, model: str = MODEL) -> None:
        self._client = client
        self._model = model

    def _call(self, system: str, content: list[dict]) -> ReceiptExtraction:
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=ReceiptExtraction,
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.info(
                "receipt read: token usage",
                extra={
                    "input_tokens": getattr(usage, "input_tokens", None),
                    "output_tokens": getattr(usage, "output_tokens", None),
                },
            )
        if response.parsed_output is None:
            # A refusal or a max_tokens cut-off returns no parsed output rather than
            # raising; treat it like invalid output so the one retry applies.
            raise ValueError(f"no parsed output (stop_reason={response.stop_reason})")
        return response.parsed_output

    async def read(self, image: PreparedImage, *, trip: Trip) -> ReceiptExtraction:
        system = SYSTEM_PROMPT_TEMPLATE.format(
            trip_currency=trip.trip_currency, home_currency=trip.home_currency
        )
        image_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image.media_type,
                "data": image.data_b64,
            },
        }
        content = [image_block, {"type": "text", "text": "Read this receipt."}]

        try:
            return await asyncio.to_thread(self._call, system, content)
        except Exception as first_error:  # noqa: BLE001 - any failure triggers the one retry
            logger.warning("receipt read: first attempt failed, retrying once")
            retry_content = content + [
                {
                    "type": "text",
                    "text": (
                        "Your previous answer was invalid: "
                        f"{first_error}. Return valid JSON matching the schema."
                    ),
                }
            ]
            try:
                return await asyncio.to_thread(self._call, system, retry_content)
            except Exception as second_error:  # noqa: BLE001
                logger.warning("receipt read: retry also failed, giving up")
                raise ReceiptUnreadable() from second_error


def to_receipt(extraction: ReceiptExtraction, currency: str) -> Receipt:
    """Validates a raw LLM reading into the domain `Receipt` (spec §7). `currency`
    is the already-resolved trip or home currency (`resolve_receipt_currency`). An
    unparseable printed date becomes `None`. Raises `ReceiptUnreadable` if the
    reading is not a usable receipt (e.g. no candidate totals at all).
    """
    printed: datetime | None = None
    if extraction.transaction_datetime:
        try:
            printed = datetime.fromisoformat(extraction.transaction_datetime)
        except ValueError:
            printed = None
    try:
        candidate_totals = [
            CandidateTotal(
                label_as_printed=c.label_as_printed,
                amount_minor=c.amount_minor,
                is_grand_total_guess=c.is_grand_total_guess,
            )
            for c in extraction.candidate_totals
        ]
        line_items = [
            ReceiptLineItem(
                description=item.description,
                quantity=item.quantity,
                unit_amount_minor=item.unit_amount_minor,
                amount_minor=item.amount_minor,
                kind=item.kind,
            )
            for item in extraction.line_items
        ]
        return Receipt(
            merchant=extraction.merchant,
            currency=currency,
            transaction_datetime=printed,
            line_items=drop_included_tax(line_items, candidate_totals),
            candidate_totals=candidate_totals,
            description=extraction.description,
            category=extraction.category,
            confidence=extraction.confidence,
            notes=extraction.notes,
        )
    except ValidationError as exc:
        raise ReceiptUnreadable("reading is not a usable receipt") from exc


def drop_included_tax(
    line_items: list[ReceiptLineItem], candidate_totals: list[CandidateTotal]
) -> list[ReceiptLineItem]:
    """Removes a tax line that is already inside the prices (内税, "tax included"),
    which the reader sometimes lists as an extra `tax` line despite the prompt.
    Decided 2026-10-02: when the lines match no printed grand total, but leaving out
    exactly one tax line makes them match a grand-total guess exactly, that line is
    dropped. Otherwise the lines are returned unchanged.
    """
    if not line_items:
        return line_items
    guesses = {c.amount_minor for c in candidate_totals if c.is_grand_total_guess}
    items_sum, adjustments_sum = line_item_sums(line_items)
    computed = items_sum + adjustments_sum
    if not guesses or computed in guesses:
        return line_items
    for index, item in enumerate(line_items):
        if item.kind == "tax" and computed - item.amount_minor in guesses:
            return line_items[:index] + line_items[index + 1 :]
    return line_items


def line_item_sums(line_items: list[ReceiptLineItem]) -> tuple[int, int]:
    """`(items_sum_minor, adjustments_sum_minor)`. Adjustments = tax + service_charge
    - discount (spec §7: `computed = items + tax + service - discount`).
    """
    items_sum = sum(i.amount_minor for i in line_items if i.kind == "item")
    adjustments_sum = 0
    for item in line_items:
        if item.kind in ("tax", "service_charge"):
            adjustments_sum += item.amount_minor
        elif item.kind == "discount":
            adjustments_sum -= abs(item.amount_minor)
    return items_sum, adjustments_sum


@dataclass(frozen=True)
class TotalDecision:
    status: Literal["confident", "ask"]
    chosen_total_minor: int | None
    candidates: list[CandidateTotal]  # up to 3 when asking
    computed_total_minor: int | None


def _distinct_candidates(candidates: list[CandidateTotal]) -> list[CandidateTotal]:
    """One candidate per amount. Receipts often print the same total on several lines
    (内税品計, 合計, 現金計); they are one choice, not three. A merged amount counts as
    a grand-total guess if any of its lines was.
    """
    by_amount: dict[int, CandidateTotal] = {}
    for candidate in candidates:
        existing = by_amount.get(candidate.amount_minor)
        if existing is None or (
            candidate.is_grand_total_guess and not existing.is_grand_total_guess
        ):
            by_amount[candidate.amount_minor] = candidate
    return list(by_amount.values())


def select_total(receipt: Receipt) -> TotalDecision:
    """Spec §7 total-selection rule, over distinct amounts.

    `computed = items + tax + service - discount`. If exactly one amount has
    `is_grand_total_guess` and `|candidate - computed| <= 5` minor units (or there
    are no line items at all), choose it. If the receipt prints only one amount that
    could be the total, choose it too: there is nothing to ask (the caller still
    shows the reconciliation prompt if it is more than 5 off the line items).
    Otherwise ask, with up to 3 candidates (grand-total guesses first, then largest).
    """
    computed = None
    if receipt.line_items:
        items_sum, adjustments_sum = line_item_sums(receipt.line_items)
        computed = items_sum + adjustments_sum

    candidates = _distinct_candidates(receipt.candidate_totals)
    if len(candidates) == 1:
        return TotalDecision("confident", candidates[0].amount_minor, candidates, computed)

    guesses = [c for c in candidates if c.is_grand_total_guess]
    if len(guesses) == 1:
        candidate = guesses[0]
        if computed is None or abs(candidate.amount_minor - computed) <= _CONFIDENT_TOLERANCE_MINOR:
            return TotalDecision("confident", candidate.amount_minor, candidates, computed)

    ordered = sorted(candidates, key=lambda c: (not c.is_grand_total_guess, -c.amount_minor))
    return TotalDecision("ask", None, ordered[:3], computed)


def needs_reconciliation(chosen_total_minor: int, computed_total_minor: int | None) -> bool:
    """An automatically chosen total gets the reconciliation prompt only when it is
    more than 5 minor units off the line items (spec §7, amended 2026-10-01).
    """
    return (
        computed_total_minor is not None
        and abs(chosen_total_minor - computed_total_minor) > _CONFIDENT_TOLERANCE_MINOR
    )


@dataclass(frozen=True)
class Reconciliation:
    items_sum_minor: int
    adjustments_sum_minor: int
    total_minor: int
    difference_minor: int  # total - computed; positive = total is higher than computed


def reconcile(
    items_sum_minor: int, adjustments_sum_minor: int, chosen_total_minor: int
) -> Reconciliation | None:
    """`None` if the chosen total already matches `items + adjustments` exactly."""
    computed = items_sum_minor + adjustments_sum_minor
    if computed == chosen_total_minor:
        return None
    return Reconciliation(
        items_sum_minor=items_sum_minor,
        adjustments_sum_minor=adjustments_sum_minor,
        total_minor=chosen_total_minor,
        difference_minor=chosen_total_minor - computed,
    )


def resolve_transaction_date(
    receipt: Receipt, message_time: datetime, trip_timezone: str
) -> tuple[Literal["receipt", "message"], date]:
    """Printed date if present and within 30 days of the message time, else the
    message time in the trip timezone (spec §7).

    Returns `(date_source, transaction_date)`.
    """
    message_date = message_time.astimezone(ZoneInfo(trip_timezone)).date()
    if receipt.transaction_datetime is not None:
        printed_date = receipt.transaction_datetime.date()
        if abs((printed_date - message_date).days) <= 30:
            return "receipt", printed_date
    return "message", message_date


def resolve_receipt_currency(extraction_currency: str | None, trip: Trip) -> str | None:
    """Missing currency -> trip currency. Otherwise must be the trip or home currency
    (case-insensitive); returns `None` to mean "refuse" (spec §4/§7).
    """
    if not extraction_currency:
        return trip.trip_currency
    code = extraction_currency.strip().upper()
    if code == trip.trip_currency or code == trip.home_currency:
        return code
    return None
