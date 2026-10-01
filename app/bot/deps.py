"""The dependency seam: handlers reach services only through `Deps`.

Handlers never import gspread or httpx directly; they get a `LedgerStore` and a
`CurrencyCatalog` from `context.application.bot_data["deps"]`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from telegram.ext import ContextTypes

from app.bot.session_state import RecentUpdates, SeenUsersThrottle, SettripSessionStore
from app.services.currencies import CurrencyCatalog
from app.services.drafts import DraftService
from app.services.ledger_store import LedgerStore
from app.services.receipt_parser import ReceiptReader


@dataclass(frozen=True)
class Deps:
    """Everything a handler needs that isn't already on `Update`/`Context`.

    Dedup (spec §10) lives entirely inside `draft_service`: no handler reads it
    directly, so it is not a `Deps` field -- it is wired up once, alongside
    `draft_service`, at construction time (`app.bot.app.run`, `tests/helpers.py`).
    """

    ledger: LedgerStore
    currencies: CurrencyCatalog
    service_account_email: str
    bot_version: str
    draft_service: DraftService
    receipt_reader: ReceiptReader
    settrip_sessions: SettripSessionStore = field(default_factory=SettripSessionStore)
    seen_users: SeenUsersThrottle = field(default_factory=SeenUsersThrottle)
    recent_updates: RecentUpdates = field(default_factory=RecentUpdates)


def get_deps(context: ContextTypes.DEFAULT_TYPE) -> Deps:
    return context.application.bot_data["deps"]
