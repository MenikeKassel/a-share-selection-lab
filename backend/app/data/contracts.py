from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

DAILY_REQUIRED_COLUMNS = {
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
}
MINUTE_REQUIRED_COLUMNS = {
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
}
POINT_IN_TIME_REQUIRED_COLUMNS = {
    "period_end",
    "published_at",
    "available_at",
    "fetched_at",
    "source",
    "content_hash",
}
# PR 5.2: explicit precision marker written at import time.  Parsed
# timestamps lose the date-only/timestamp distinction after a Parquet
# round-trip ("2024-01-02" becomes "2024-01-02 00:00:00"), so consumers
# must read this column instead of re-deriving precision from strings.
POINT_IN_TIME_PRECISION_COLUMN = "available_at_precision"
UNAVAILABLE_MICROSTRUCTURE = {
    "cvd": "unavailable",
    "bid_ask_delta": "unavailable",
    "footprint": "unavailable",
    "absorption": "unavailable",
    "iceberg_order": "unavailable",
    "level2_orderbook": "unavailable",
    "option_wall": "unavailable",
}


@dataclass(frozen=True, slots=True)
class DataValidationResult:
    valid: bool
    missing_columns: list[str]
    duplicate_rows: int
    invalid_price_rows: int
    message: str


def validate_market_frame(frame: pd.DataFrame, *, granularity: str) -> DataValidationResult:
    required = DAILY_REQUIRED_COLUMNS if granularity == "daily" else MINUTE_REQUIRED_COLUMNS
    missing = sorted(required.difference(frame.columns))
    key_columns = ["date", "symbol"] if granularity == "daily" else ["timestamp"]
    duplicate_rows = (
        int(frame.duplicated(key_columns).sum())
        if not set(key_columns).difference(frame.columns)
        else 0
    )
    price_columns = {"open", "high", "low", "close"}
    invalid_prices = 0
    if not price_columns.difference(frame.columns):
        invalid_prices = int(
            (
                (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
                | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
                | (frame[list(price_columns)] <= 0).any(axis=1)
            ).sum()
        )
    valid = not missing and duplicate_rows == 0 and invalid_prices == 0
    return DataValidationResult(
        valid=valid,
        missing_columns=missing,
        duplicate_rows=duplicate_rows,
        invalid_price_rows=invalid_prices,
        message="数据质量检查通过。" if valid else "数据质量检查失败，正式选股不可运行。",
    )
