"""Shared test helpers: building a fully-wired `Deps` without repeating the
draft_service/dedup plumbing in every test file.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.bot.deps import Deps
from app.bot.session_state import Dedup
from app.services.currencies import StaticCurrencyCatalog
from app.services.drafts import DraftService, InMemoryDraftStore
from app.services.exchange_rates import RateService, StaticRateProvider
from app.services.ledger_store import InMemoryLedgerStore, LedgerStore


def make_deps(
    ledger: LedgerStore | None = None,
    *,
    currencies: StaticCurrencyCatalog | None = None,
    service_account_email: str = "whopaid-bot@example.iam.gserviceaccount.com",
    bot_version: str = "test",
    rate: Decimal = Decimal("0.0081"),
    rate_date: date = date(2026, 9, 19),
    dedup: Dedup | None = None,
) -> Deps:
    ledger = ledger or InMemoryLedgerStore()
    provider = StaticRateProvider(rate, rate_date)
    rates = RateService(provider, ledger)
    draft_service = DraftService(ledger, rates, InMemoryDraftStore(), dedup or Dedup())
    return Deps(
        ledger=ledger,
        currencies=currencies or StaticCurrencyCatalog(frozenset({"JPY", "SGD"})),
        service_account_email=service_account_email,
        bot_version=bot_version,
        draft_service=draft_service,
    )
