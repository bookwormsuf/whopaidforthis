"""Shared test helpers: building a fully-wired `Deps` without repeating the
draft_service plumbing in every test file.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.bot.deps import Deps
from app.services.currencies import StaticCurrencyCatalog
from app.services.drafts import DraftService, InMemoryDraftStore
from app.services.exchange_rates import RateService, StaticRateProvider
from app.services.ledger_store import InMemoryLedgerStore, LedgerStore
from app.services.llm_schemas import ReceiptExtraction
from app.services.receipt_budget import ReceiptReadBudget
from app.services.receipt_parser import ReceiptReader


class FakeReceiptReader:
    """A `ReceiptReader` that returns a fixed `ReceiptExtraction` (or raises a fixed
    exception), never calling the real Anthropic API. `calls` records every `trip`
    passed in, for tests that assert on call count.
    """

    def __init__(self, extraction: ReceiptExtraction | None = None, error: Exception | None = None):
        self._extraction = extraction
        self._error = error
        self.calls: list[object] = []

    async def read(self, image, *, trip) -> ReceiptExtraction:
        self.calls.append(trip)
        if self._error is not None:
            raise self._error
        assert self._extraction is not None
        return self._extraction


def make_deps(
    ledger: LedgerStore | None = None,
    *,
    currencies: StaticCurrencyCatalog | None = None,
    bot_version: str = "test",
    rate: Decimal = Decimal("0.0081"),
    rate_date: date = date(2026, 9, 19),
    receipt_reader: ReceiptReader | None = None,
    receipt_budget: ReceiptReadBudget | None = None,
) -> Deps:
    ledger = ledger or InMemoryLedgerStore()
    provider = StaticRateProvider(rate, rate_date)
    rates = RateService(provider, ledger)
    draft_service = DraftService(ledger, rates, InMemoryDraftStore())
    return Deps(
        ledger=ledger,
        currencies=currencies or StaticCurrencyCatalog(frozenset({"JPY", "SGD"})),
        bot_version=bot_version,
        draft_service=draft_service,
        receipt_reader=receipt_reader or FakeReceiptReader(),
        receipt_budget=receipt_budget or ReceiptReadBudget(),
    )
