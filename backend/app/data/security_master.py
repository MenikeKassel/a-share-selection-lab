"""Security master data (PR 5).

The purchased archive ships a real security master (``universe.parquet``)
with true listing dates (e.g. 000001.SZ -> 1991-04-03).  This module:

- normalises that master into ``security_master.parquet`` shape
  (symbol / list_date / delist_date / exchange / board / security_type),
- derives exchange and board from the symbol suffix/prefix,
- computes ``listing_days`` from the REAL listing date instead of the
  first CSV row (the old importer behaviour), and
- marks ``real_listing_dates`` / ``listing_date_source`` so a fallback
  (first-observed-date) universe is never silently treated as real.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.market_rules.price_limits import board_of

SECURITY_MASTER_COLUMNS = [
    "symbol",
    "list_date",
    "delist_date",
    "exchange",
    "board",
    "security_type",
    "is_real_listing_date",
    "listing_date_source",
]


@dataclass(frozen=True, slots=True)
class SecurityMasterStatus:
    """Whether the master carries true listing dates or a fallback."""

    real_listing_dates: bool
    listing_date_source: str
    row_count: int


def _exchange_of(symbol: str) -> str:
    if symbol.endswith(".SH"):
        return "SSE"
    if symbol.endswith(".SZ"):
        return "SZSE"
    if symbol.endswith(".BJ"):
        return "BSE"
    return "UNKNOWN"


def normalise_security_master(universe: pd.DataFrame) -> tuple[pd.DataFrame, SecurityMasterStatus]:
    """Normalise a purchased universe.parquet into security_master shape.

    ``real_listing_dates`` is only true when the source universe was not
    derived from the daily window itself (i.e. it carries a name/market
    column or listing dates that precede the data window) - the shipped
    purchased universe has real 1990s listing dates.
    """
    required = {"symbol", "list_date"}
    if missing := required.difference(universe.columns):
        raise ValueError(f"universe is missing columns: {sorted(missing)}")

    master = universe.copy()
    master["symbol"] = master["symbol"].astype(str)
    master["list_date"] = pd.to_datetime(master["list_date"], errors="coerce")
    if "delist_date" in master.columns:
        master["delist_date"] = pd.to_datetime(master["delist_date"], errors="coerce")
    else:
        master["delist_date"] = pd.NaT
    master["exchange"] = master["symbol"].map(_exchange_of)
    master["board"] = master["symbol"].map(board_of)
    master["security_type"] = master.get("security_type", pd.Series("stock", index=master.index))

    # A real master is one that was not built from the daily window: the
    # purchased master carries name/market identity columns and no
    # last_observed_date; importer-built fallback universes carry
    # last_observed_date and no identity.
    has_identity = "name" in master.columns or "market" in master.columns
    fallback = "last_observed_date" in master.columns and not has_identity
    real = has_identity and not fallback

    status = SecurityMasterStatus(
        real_listing_dates=real,
        listing_date_source="purchased_security_master" if real else "daily_first_observed",
        row_count=len(master),
    )
    # PR 5.1: propagate the capability per row so listing_days_for() can
    # read it back when no explicit status is passed.
    master["is_real_listing_date"] = real
    master["listing_date_source"] = status.listing_date_source
    return master[SECURITY_MASTER_COLUMNS].sort_values("symbol").reset_index(drop=True), status


def listing_days_for(
    market: pd.DataFrame,
    security_master: pd.DataFrame,
    status: SecurityMasterStatus | None = None,
) -> tuple[pd.Series, SecurityMasterStatus]:
    """Compute listing_days from the security master's list_date.

    Missing list_date produces NaN (never silently the data window start).
    The returned status propagates the master's real-listing-date
    capability: a fallback master stays marked as fallback (PR 5.1).
    """
    if security_master.empty:
        raise ValueError("security master is empty; cannot compute real listing days")
    master = security_master.copy()
    master["symbol"] = master["symbol"].astype(str)
    master["list_date"] = pd.to_datetime(master["list_date"], errors="coerce")
    lookup = master.set_index("symbol")["list_date"]
    market_symbols = market["symbol"].astype(str)
    list_dates = market_symbols.map(lookup)
    market_dates = pd.to_datetime(market["date"], format="mixed", errors="coerce")
    if status is None:
        source_column = master.get("listing_date_source")
        source_value = (
            str(pd.Series(source_column).iloc[0])
            if source_column is not None and not pd.Series(source_column).empty
            else "purchased_security_master"
        )
        real = bool(master.get("is_real_listing_date", pd.Series(True)).all())
        status = SecurityMasterStatus(
            real_listing_dates=real,
            listing_date_source=source_value,
            row_count=len(master),
        )
    return (market_dates - list_dates).dt.days.astype(float), status


def load_security_master(path: str | Any) -> pd.DataFrame:
    """Load a security_master.parquet and normalise it."""
    frame = pd.read_parquet(path)
    if missing := set(SECURITY_MASTER_COLUMNS).difference(frame.columns):
        raise ValueError(f"security master is missing columns: {sorted(missing)}")
    return frame[SECURITY_MASTER_COLUMNS].copy()
