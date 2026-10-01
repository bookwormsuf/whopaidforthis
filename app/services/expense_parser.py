"""Deterministic text-expense parser. No LLM. Spec references: pinned-decisions §6, §4.

Recognises: an amount (optional thousands separators, optional decimals) with a
currency token before, after, or attached (`¥1,200`, `S$12`, `12 sgd`, `1200 yen`,
`$12`), a leading `I paid`/`paid`, a leading `for` before the description, and
trailing `split (between|among|with) <names>` / `split everyone` / `split all` and
`paid by <name>` clauses (captured but not resolved this sprint -- name resolution
is a later sprint's concern, spec §3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from app.models.category import Category
from app.services.currencies import HOME_OR_USD, decimals_for, resolve_alias

_MAX_WORDS = 12

# "S$" must be tried before the lone "$" so it isn't split into "S" + "$".
_CURRENCY_PREFIX_ALT = r"S\$|[¥$£€₩฿]"
_WORD_AMOUNT_RE = re.compile(
    rf"^(?P<prefix>{_CURRENCY_PREFIX_ALT})?"
    r"(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suffix>[A-Za-z]{2,5})?$",
    re.IGNORECASE,
)

_LEADING_PAID_RE = re.compile(r"^(i\s+paid|paid)\s+", re.IGNORECASE)
_PAID_BY_RE = re.compile(r"\bpaid\s+by\s+(.+)$", re.IGNORECASE)
_SPLIT_RE = re.compile(
    r"\bsplit\s+(?:(?:between|among|with)\s+(.+)|(everyone|all))$", re.IGNORECASE
)

_CATEGORY_KEYWORDS: list[tuple[re.Pattern[str], Category]] = [
    (re.compile(r"\b(taxi|uber|grab|train|jr|metro|bus)\b", re.IGNORECASE), Category.TRANSPORT),
    (
        re.compile(r"\b(ramen|dinner|lunch|breakfast|sushi|food|snack)\b", re.IGNORECASE),
        Category.FOOD,
    ),
    (re.compile(r"\b(coffee|beer|drinks|bar|sake)\b", re.IGNORECASE), Category.DRINKS),
    (re.compile(r"\b(hotel|airbnb|ryokan)\b", re.IGNORECASE), Category.ACCOMMODATION),
    (re.compile(r"\b(ticket|museum|onsen|entry)\b", re.IGNORECASE), Category.ACTIVITIES),
    (
        re.compile(r"\b(konbini|7-eleven|lawson|familymart|supermarket)\b", re.IGNORECASE),
        Category.GROCERIES,
    ),
    (re.compile(r"\b(shop|uniqlo|donki|mall)\b", re.IGNORECASE), Category.SHOPPING),
]

_UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class ParsedExpense:
    """The result of `parse_expense` for a candidate expense message.

    `error="unsupported_currency"` means an amount and a currency token were both
    found, but the currency isn't the trip's or the home currency; `amount_minor`
    and `currency` are meaningless in that case.
    """

    amount_minor: int
    currency: str
    description: str
    category: Category
    participants_text: str | None = None
    payer_text: str | None = None
    error: str | None = None


def _strip_bot_mention(text: str, bot_username: str) -> str:
    pattern = re.compile(rf"@{re.escape(bot_username)}\b", re.IGNORECASE)
    return pattern.sub("", text).strip()


def _extract_trailing(text: str) -> tuple[str, str | None, str | None]:
    working = text
    payer_text: str | None = None
    participants_text: str | None = None

    match = _PAID_BY_RE.search(working)
    if match:
        payer_text = match.group(1).strip()
        working = working[: match.start()].strip()

    match = _SPLIT_RE.search(working)
    if match:
        participants_text = (match.group(1) or match.group(2) or "").strip()
        working = working[: match.start()].strip()

    return working, participants_text, payer_text


def _match_currency_token(token: str, trip_currency: str, home_currency: str) -> str | None:
    """Resolve a possible currency token to `trip_currency`, `home_currency`,
    `_UNSUPPORTED` (a real currency, but not this trip's), or None (not a currency
    token at all).
    """
    stripped = token.strip(".,!?").strip()
    if not stripped:
        return None
    upper = stripped.upper()
    if upper == trip_currency:
        return trip_currency
    if upper == home_currency:
        return home_currency
    alias = resolve_alias(stripped.lower())
    if alias is None:
        return None
    if alias == HOME_OR_USD:
        # Spec §4: "$" -> home currency when home is SGD or USD, else USD.
        alias = home_currency if home_currency in ("SGD", "USD") else "USD"
    if alias == trip_currency:
        return trip_currency
    if alias == home_currency:
        return home_currency
    return _UNSUPPORTED


def _category_for(text: str) -> Category:
    for pattern, category in _CATEGORY_KEYWORDS:
        if pattern.search(text):
            return category
    return Category.OTHER


def parse_expense(
    text: str, trip_currency: str, home_currency: str, bot_username: str
) -> ParsedExpense | None:
    mention_stripped = _strip_bot_mention(text, bot_username)
    if not mention_stripped:
        return None
    if len(mention_stripped.split()) > _MAX_WORDS:
        return None

    working, participants_text, payer_text = _extract_trailing(mention_stripped)
    working = _LEADING_PAID_RE.sub("", working).strip()
    words = working.split()
    if not words:
        return None

    amount_index: int | None = None
    prefix = suffix = number_str = None
    for index, word in enumerate(words):
        match = _WORD_AMOUNT_RE.match(word)
        if match:
            amount_index = index
            prefix = match.group("prefix")
            suffix = match.group("suffix")
            number_str = match.group("number")
            break

    if amount_index is None or number_str is None:
        return None  # no amount found: not an expense candidate, stay silent

    consumed = {amount_index}
    currency_token = None
    if prefix:
        currency_token = _match_currency_token(prefix, trip_currency, home_currency)
    if currency_token is None and suffix:
        currency_token = _match_currency_token(suffix, trip_currency, home_currency)
    if currency_token is None and amount_index + 1 < len(words):
        candidate = _match_currency_token(words[amount_index + 1], trip_currency, home_currency)
        if candidate is not None:
            currency_token = candidate
            consumed.add(amount_index + 1)
    if currency_token is None and amount_index - 1 >= 0:
        candidate = _match_currency_token(words[amount_index - 1], trip_currency, home_currency)
        if candidate is not None:
            currency_token = candidate
            consumed.add(amount_index - 1)

    if currency_token == _UNSUPPORTED:
        return ParsedExpense(
            amount_minor=0,
            currency="",
            description="",
            category=Category.OTHER,
            participants_text=participants_text,
            payer_text=payer_text,
            error="unsupported_currency",
        )

    currency = currency_token if currency_token is not None else trip_currency
    decimals = decimals_for(currency)
    value = Decimal(number_str.replace(",", ""))
    amount_minor = int((value * (Decimal(10) ** decimals)).to_integral_value(ROUND_HALF_EVEN))

    leftover = [word for index, word in enumerate(words) if index not in consumed]
    if leftover and leftover[0].lower() == "for":
        leftover = leftover[1:]
    leftover_text = " ".join(leftover).strip()
    description = leftover_text.title() if leftover_text else "Expense"
    category = _category_for(leftover_text)

    return ParsedExpense(
        amount_minor=amount_minor,
        currency=currency,
        description=description,
        category=category,
        participants_text=participants_text,
        payer_text=payer_text,
    )
