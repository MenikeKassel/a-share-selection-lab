"""Market-data provider adapters.

Business services depend on the small provider contract in :mod:`base`; the
FreeStockDB wire format is intentionally confined to its adapter package.
"""

from app.adapters.market_data.base import (
    DailyBarsRequest,
    MarketDataProvider,
    MarketDataProviderStatus,
    MinuteBarsRequest,
)

__all__ = [
    "DailyBarsRequest",
    "MarketDataProvider",
    "MarketDataProviderStatus",
    "MinuteBarsRequest",
]
