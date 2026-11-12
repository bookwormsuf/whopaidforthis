"""Receipt readings for the one-off bill tests (sprint 18): small builders for the
`ReceiptExtraction` a fake reader returns, so a test says what is on the receipt and
nothing else.
"""

from __future__ import annotations

from app.services.llm_schemas import ReceiptExtraction


def line(description: str, amount_minor: int, *, quantity: int = 1, kind: str = "item") -> dict:
    return {
        "description": description,
        "quantity": quantity,
        "amount_minor": amount_minor,
        "kind": kind,
    }


def total(amount_minor: int, *, guess: bool = True, label: str = "Total") -> dict:
    return {"label_as_printed": label, "amount_minor": amount_minor, "is_grand_total_guess": guess}


def extraction(
    lines: list[dict],
    totals: list[dict],
    *,
    currency: str | None = "SGD",
    description: str = "Ramen House",
    merchant: str | None = "Ramen House",
) -> ReceiptExtraction:
    return ReceiptExtraction(
        merchant=merchant,
        currency=currency,
        line_items=lines,
        candidate_totals=totals,
        description=description,
        category="food",
        confidence=0.9,
    )


def dinner() -> ReceiptExtraction:
    """Three items, tax and service that add up to the printed total: S$84.50."""
    return extraction(
        [
            line("Ramen", 2000),
            line("Gyoza", 1200),
            line("Beer", 3000),
            line("GST", 500, kind="tax"),
            line("Service", 1750, kind="service_charge"),
        ],
        [total(8450)],
    )
