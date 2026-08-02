from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

import pandas as pd


@dataclass(frozen=True, slots=True)
class DailyBarsRequest:
    start_date: date
    end_date: date
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MinuteBarsRequest:
    start_timestamp: str
    end_timestamp: str
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketDataProviderStatus:
    provider_code: str
    configured: bool
    reachable: bool
    endpoint: str
    read_only: bool
    daily_latest_date: date | None = None
    minute_latest_date: date | None = None
    daily_instrument_count: int = 0
    minute_instrument_count: int = 0
    capabilities: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    checked_at: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class MarketDataProvider(Protocol):
    """Provider seam used by snapshot and selection services."""

    provider_code: str

    def status(self) -> MarketDataProviderStatus:
        ...

    def fetch_daily(self, request: DailyBarsRequest) -> pd.DataFrame:
        ...

    def fetch_minute(self, request: MinuteBarsRequest) -> dict[str, pd.DataFrame]:
        ...
