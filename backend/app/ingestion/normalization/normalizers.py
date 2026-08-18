"""
Field-level normalization — turning messy CSV text into clean typed values.
Kept as small pure functions so they're trivially unit-testable and reusable
across sales/inventory/products row processors.
"""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dateutil import parser as dateutil_parser


def normalize_date(value: str | None, date_format: str | None = None) -> date:
    """Tries the channel's configured format first (fast, unambiguous);
    falls back to dateutil's flexible parser for messy real-world CSVs
    that don't match exactly. Raises ValueError if truly unparseable —
    callers should catch this and treat the row as an error, not guess."""
    if not value:
        raise ValueError("date value is empty")

    value = value.strip()

    if date_format:
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            pass  # fall through to the flexible parser

    try:
        return dateutil_parser.parse(value).date()
    except (ValueError, OverflowError) as e:
        raise ValueError(f"could not parse date: {value!r}") from e


_CURRENCY_STRIP_RE = re.compile(r"[₹$,\s]")


def normalize_currency(value: str | float | int | None) -> Decimal:
    """Strips currency symbols/commas/whitespace and returns a Decimal.
    Empty/None becomes Decimal('0') — many optional money columns (e.g.
    shipping, tax) are legitimately absent for a given channel."""
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, (int, float)):
        return Decimal(str(value))

    cleaned = _CURRENCY_STRIP_RE.sub("", str(value))
    if cleaned in ("", "-"):
        return Decimal("0")

    try:
        return Decimal(cleaned)
    except InvalidOperation as e:
        raise ValueError(f"could not parse currency value: {value!r}") from e


def normalize_sku(value: str | None) -> str | None:
    """Trims and uppercases for matching purposes. We still store the
    original external_sku as given in channel_products — this normalized
    form is only used to compare/look up, not to overwrite source data."""
    if value is None:
        return None
    cleaned = value.strip().upper()
    return cleaned or None
