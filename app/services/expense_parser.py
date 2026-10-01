"""Deterministic text-expense parser. No LLM. Spec references: pinned-decisions §6, §4.

Recognises: an amount (optional thousands separators, optional decimals) with a
currency token before, after, or attached (`¥1,200`, `S$12`, `12 sgd`, `1200 yen`,
`$12`), a leading `I paid`/`paid`, a leading `for` before the description, and
trailing `split (between|among|with) <names>` / `split everyone` / `split all` and
`paid by <name>` clauses. The clauses are captured as text here; resolving the names
to trip users is `app.services.names` (spec §3), done by the draft flow.
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
# The two trailing clauses, in either order: each runs to the end of the message or
# up to the start of the other one.
_PAID_BY_RE = re.compile(r"\bpaid\s+by\s+(.+?)(?=\s+split\s|$)", re.IGNORECASE)
_SPLIT_RE = re.compile(
    r"\bsplit\s+(?:(?:between|among|with)\s+(.+?)|(everyone|all))(?=\s+paid\s+by\s|$)",
    re.IGNORECASE,
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
    """Splits off the trailing `paid by <name>` and `split ...` clauses, which may
    come in either order (`... paid by Ada split between me and Ada`).
    """
    payer_match = _PAID_BY_RE.search(text)
    split_match = _SPLIT_RE.search(text)
    payer_text = payer_match.group(1).strip() if payer_match else None
    participants_text = (
        (split_match.group(1) or split_match.group(2) or "").strip() if split_match else None
    )
    starts = [m.start() for m in (payer_match, split_match) if m is not None]
    working = text[: min(starts)].strip() if starts else text
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


@dataclass(frozen=True)
class _LocatedAmount:
    """The first amount in a word list, plus every word index it consumed (the
    amount itself and an adjacent currency word, if any). `currency` is the trip or
    home currency, or `_UNSUPPORTED`.
    """

    amount_minor: int
    currency: str
    consumed: frozenset[int]


def _locate_amount(
    words: list[str], trip_currency: str, home_currency: str, bare_currency: str | None = None
) -> _LocatedAmount | None:
    """The one amount/currency-token rule (spec §4/§6), shared by `parse_expense`,
    `parse_amount_only`, `parse_caption_description` and `parse_custom_line`. A bare
    number is in `bare_currency`, which defaults to the trip currency.
    """
    for index, word in enumerate(words):
        match = _WORD_AMOUNT_RE.match(word)
        if not match:
            continue
        prefix = match.group("prefix")
        suffix = match.group("suffix")
        number_str = match.group("number")

        consumed = {index}
        currency_token = None
        if prefix:
            currency_token = _match_currency_token(prefix, trip_currency, home_currency)
        if currency_token is None and suffix:
            currency_token = _match_currency_token(suffix, trip_currency, home_currency)
        if currency_token is None and index + 1 < len(words):
            candidate = _match_currency_token(words[index + 1], trip_currency, home_currency)
            if candidate is not None:
                currency_token = candidate
                consumed.add(index + 1)
        if currency_token is None and index - 1 >= 0:
            candidate = _match_currency_token(words[index - 1], trip_currency, home_currency)
            if candidate is not None:
                currency_token = candidate
                consumed.add(index - 1)

        if currency_token == _UNSUPPORTED:
            return _LocatedAmount(0, _UNSUPPORTED, frozenset(consumed))
        currency = currency_token if currency_token is not None else bare_currency or trip_currency
        decimals = decimals_for(currency)
        value = Decimal(number_str.replace(",", ""))
        amount_minor = int((value * (Decimal(10) ** decimals)).to_integral_value(ROUND_HALF_EVEN))
        return _LocatedAmount(amount_minor, currency, frozenset(consumed))
    return None


def _description_words(words: list[str], consumed: frozenset[int]) -> str:
    leftover = [word for index, word in enumerate(words) if index not in consumed]
    if leftover and leftover[0].lower() == "for":
        leftover = leftover[1:]
    return " ".join(leftover).strip()


def parse_caption_description(
    text: str, trip_currency: str, home_currency: str, bot_username: str
) -> tuple[str, Category] | None:
    """A receipt photo's caption, e.g. `dinner` or `¥3000 dinner`, as a `(description,
    category)` override for the LLM's reading (spec §13, sprint 4 pin). Uses the same
    rules as `parse_expense` (an amount and its currency word are dropped from the
    description) but does not require an amount, so `dinner` alone works. `None` if
    nothing is left to describe (e.g. a caption that is only an amount).
    """
    mention_stripped = _strip_bot_mention(text, bot_username)
    if not mention_stripped:
        return None
    working, _participants_text, _payer_text = _extract_trailing(mention_stripped)
    working = _LEADING_PAID_RE.sub("", working).strip()
    words = working.split()
    located = _locate_amount(words, trip_currency, home_currency)
    leftover_text = _description_words(words, located.consumed if located else frozenset())
    if not leftover_text:
        return None
    return leftover_text.title(), _category_for(leftover_text)


def parse_caption_clauses(text: str, bot_username: str) -> tuple[str | None, str | None]:
    """A receipt caption's trailing `split ...` and `paid by ...` clauses, as
    `(participants_text, payer_text)` (spec §13: the caption's participants override
    after the receipt is read). Either is `None` when absent.
    """
    _working, participants_text, payer_text = _extract_trailing(
        _strip_bot_mention(text, bot_username)
    )
    return participants_text, payer_text


def parse_amount_only(text: str, trip_currency: str, home_currency: str) -> tuple[int, str] | None:
    """Parses a short reply like `2400` or `S$24` into `(amount_minor, currency)`.

    Uses the same amount/currency-token rule as `parse_expense` (spec §6), for the
    receipt "Enter amount manually" flow (spec §7/§8, sprint 4). Returns `None` if no
    amount is found or the currency isn't the trip's or home currency.
    """
    located = _locate_amount(text.strip().split(), trip_currency, home_currency)
    if located is None or located.currency == _UNSUPPORTED:
        return None
    return located.amount_minor, located.currency


@dataclass(frozen=True)
class CustomLine:
    """One line of a custom split reply, e.g. `Amir 2000` (sprint 5 pin)."""

    name_text: str
    amount_minor: int
    currency: str


def parse_custom_line(
    line: str, trip_currency: str, home_currency: str, expense_currency: str
) -> CustomLine | None:
    """`<name> <amount>` (either order), using the same amount/currency rule as
    `parse_expense`, except that a bare number is in the expense's own currency
    (`Me 6` on an S$12 expense is S$6). `None` if the line has no amount, no name,
    or a currency that isn't the trip's or the home currency.
    """
    words = line.strip().split()
    located = _locate_amount(words, trip_currency, home_currency, expense_currency)
    if located is None or located.currency == _UNSUPPORTED:
        return None
    name_text = " ".join(w for i, w in enumerate(words) if i not in located.consumed).strip()
    name_text = name_text.strip(":-–—").strip()
    if not name_text:
        return None
    return CustomLine(name_text, located.amount_minor, located.currency)


_ITEM_COMMAND_RE = re.compile(
    r"^(?P<verb>mine|unclaim|split)\s+(?P<numbers>\d+(?:\s*,\s*\d+|\s+\d+)*)"
    r"(?P<everyone>\s+(?:everyone|all))?$",
    re.IGNORECASE,
)


def parse_item_command(line: str) -> tuple[str, list[int]] | None:
    """An item-card text shortcut (spec §9): `mine 1,4`, `unclaim 2`,
    `split 5 everyone`. Returns `(verb, item numbers)`, verb one of `mine`,
    `unclaim`, `everyone`; `None` if the line is not one of these.
    """
    match = _ITEM_COMMAND_RE.match(line.strip())
    if match is None:
        return None
    verb = match.group("verb").lower()
    has_everyone = match.group("everyone") is not None
    if (verb == "split") != has_everyone:
        return None  # `split` needs `everyone`; `mine`/`unclaim` must not have it
    numbers = [int(n) for n in re.findall(r"\d+", match.group("numbers"))]
    return ("everyone" if verb == "split" else verb), numbers


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

    located = _locate_amount(words, trip_currency, home_currency)
    if located is None:
        return None  # no amount found: not an expense candidate, stay silent

    if located.currency == _UNSUPPORTED:
        return ParsedExpense(
            amount_minor=0,
            currency="",
            description="",
            category=Category.OTHER,
            participants_text=participants_text,
            payer_text=payer_text,
            error="unsupported_currency",
        )

    leftover_text = _description_words(words, located.consumed)
    description = leftover_text.title() if leftover_text else "Expense"
    category = _category_for(leftover_text)

    return ParsedExpense(
        amount_minor=located.amount_minor,
        currency=located.currency,
        description=description,
        category=category,
        participants_text=participants_text,
        payer_text=payer_text,
    )
