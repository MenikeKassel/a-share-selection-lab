"""Versioned A-share price-limit rules (PR 2.4).

Rules are selected by board, ST status, listing age and trade date so that
historical regime changes (ChiNext 2020-08-24 20%, STAR 2019-07-22 20%, BSE
30%, ST 5%) are expressed as data, not as hard-coded prefixes inside the
importer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# Decimal per-share price tick used for limit-price rounding.
TICK_SIZE = Decimal("0.01")

LIMIT_TOLERANCE = Decimal("0.0015")


@dataclass(frozen=True, slots=True)
class PriceLimitRule:
    """One historical price-limit regime."""

    board: str
    effective_from: date | None
    effective_to: date | None
    limit_ratio: Decimal  # e.g. Decimal("0.10") = 10%
    applies_to_st: bool = True
    st_ratio: Decimal = Decimal("0.05")
    note: str = ""


# Board derivation from a symbol: 300/301 -> ChiNext, 688/689 -> STAR,
# 8/4 -> BSE, else main board (SZ/SH).
def board_of(symbol: str) -> str:
    code = symbol.split(".")[0]
    if code.startswith(("300", "301")):
        return "chinext"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("8", "4")):
        return "bse"
    return "main"


_MAIN = PriceLimitRule("main", None, None, Decimal("0.10"), st_ratio=Decimal("0.05"))
_CHINEXT_20 = PriceLimitRule(
    "chinext",
    date(2020, 8, 24),
    None,
    Decimal("0.20"),
    st_ratio=Decimal("0.20"),
    note="ChiNext registration reform effective 2020-08-24",
)
_CHINEXT_10 = PriceLimitRule(
    "chinext",
    None,
    date(2020, 8, 21),
    Decimal("0.10"),
    st_ratio=Decimal("0.05"),
)
_STAR = PriceLimitRule(
    "star",
    date(2019, 7, 22),
    None,
    Decimal("0.20"),
    st_ratio=Decimal("0.20"),
)
_BSE = PriceLimitRule(
    "bse",
    date(2021, 11, 15),
    None,
    Decimal("0.30"),
    st_ratio=Decimal("0.30"),
    note="BSE price limit 30% since listing",
)
_BSE_LEGACY = PriceLimitRule(
    "bse",
    None,
    date(2021, 11, 12),
    Decimal("0.10"),
    st_ratio=Decimal("0.05"),
    note="NEEQ Select pre-BSE; 10% regime",
)

_RULES: tuple[PriceLimitRule, ...] = (
    _MAIN,
    _CHINEXT_20,
    _CHINEXT_10,
    _STAR,
    _BSE,
    _BSE_LEGACY,
)


def price_limit_ratio(
    symbol: str,
    trade_date: date,
    *,
    is_st: bool = False,
    listing_days: int | None = None,
) -> Decimal:
    """Return the symmetric limit ratio in effect for the symbol/date.

    ``listing_days`` is accepted for future new-listing regimes (first-day
    rules) but is not used yet; the mechanism is data-ready.
    """
    board = board_of(symbol)
    candidates = [rule for rule in _RULES if rule.board == board]
    for rule in sorted(
        candidates,
        key=lambda r: (
            r.effective_from is not None,
            r.effective_from or date.min,
        ),
    ):
        if rule.effective_from is not None and trade_date < rule.effective_from:
            continue
        if rule.effective_to is not None and trade_date > rule.effective_to:
            continue
        if is_st:
            return rule.st_ratio
        return rule.limit_ratio
    return Decimal("0.10")


def limit_prices(
    pre_close: Decimal,
    ratio: Decimal,
    *,
    tick: Decimal = TICK_SIZE,
) -> tuple[Decimal, Decimal]:
    """Compute (limit_up_price, limit_down_price) with A-share rounding.

    PR 4.1: SSE and SZSE rules require rounding the theoretical limit
    price to the nearest price tick (half-up), for both directions.
    """
    raw_up = pre_close * (1 + ratio)
    raw_down = pre_close * (1 - ratio)
    limit_up = raw_up.quantize(tick, rounding=ROUND_HALF_UP)
    limit_down = raw_down.quantize(tick, rounding=ROUND_HALF_UP)
    return limit_up, limit_down


def derive_limit_flags(
    frame: Any,
    *,
    open_col: str = "open",
    close_col: str = "close",
    pre_close_col: str = "pre_close",
    is_st_col: str = "is_st",
) -> tuple[Any, Any]:
    """Derive close-at-limit flags from a daily frame using the rule table.

    Returns (close_at_limit_up, close_at_limit_down) boolean Series.
    A close is at-limit when it equals (within tolerance) the *computed*
    limit price.  Uses Decimal rounding through ``limit_prices``.
    """
    import pandas as pd

    ups: list[bool] = []
    downs: list[bool] = []
    for _, row in frame.iterrows():
        symbol = str(row["symbol"])
        trade_date = pd.Timestamp(row["date"]).date()
        pre_close = Decimal(str(row[pre_close_col]))
        ratio = price_limit_ratio(
            symbol,
            trade_date,
            is_st=bool(row.get(is_st_col, False)),
        )
        if pre_close <= 0:
            ups.append(False)
            downs.append(False)
            continue
        limit_up, limit_down = limit_prices(pre_close, ratio)
        close = Decimal(str(row[close_col]))
        tolerance = pre_close * LIMIT_TOLERANCE
        ups.append(bool(close >= limit_up and close - limit_up <= tolerance))
        downs.append(bool(close <= limit_down and limit_down - close <= tolerance))
    return pd.Series(ups, index=frame.index), pd.Series(downs, index=frame.index)
