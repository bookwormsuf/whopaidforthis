"""The dependency seam: handlers reach services only through `Deps`.

Handlers never import gspread or httpx directly; they get a `LedgerStore` and a
`CurrencyCatalog` from `context.application.bot_data["deps"]`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from telegram.ext import ContextTypes

from app.bot.session_state import SeenUsersThrottle, SettripSessionStore
from app.services.currencies import CurrencyCatalog
from app.services.ledger_store import LedgerStore


@dataclass(frozen=True)
class Deps:
    """Everything a handler needs that isn't already on `Update`/`Context`."""

    ledger: LedgerStore
    currencies: CurrencyCatalog
    service_account_email: str
    bot_version: str
    settrip_sessions: SettripSessionStore = field(default_factory=SettripSessionStore)
    seen_users: SeenUsersThrottle = field(default_factory=SeenUsersThrottle)


def get_deps(context: ContextTypes.DEFAULT_TYPE) -> Deps:
    return context.application.bot_data["deps"]
