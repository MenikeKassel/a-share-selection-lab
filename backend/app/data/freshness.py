from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

import pandas as pd


def estimate_expected_universe_size(
    daily: pd.DataFrame,
    *,
    configured_size: int = 0,
    lookback_sessions: int = 20,
) -> int:
    """Estimate coverage denominator without trusting an incomplete latest slice."""

    if configured_size > 0:
        return configured_size
    if {"date", "symbol"}.difference(daily.columns):
        return 0
    counts = (
        daily.assign(_date=pd.to_datetime(daily["date"]).dt.normalize())
        .groupby("_date")["symbol"]
        .nunique()
        .sort_index()
        .tail(lookback_sessions)
    )
    return int(counts.max()) if not counts.empty else 0


@dataclass(frozen=True, slots=True)
class FreshnessInput:
    now: datetime
    trading_dates: list[date]
    daily_market_max_date: date | None
    minute_market_max_date: date | None
    daily_symbol_count: int
    minute_symbol_count: int
    expected_universe_size: int


@dataclass(frozen=True, slots=True)
class FreshnessResult:
    expected_latest_trade_date: date | None
    daily_market_max_date: date | None
    minute_market_max_date: date | None
    daily_coverage_ratio: float
    minute_coverage_ratio: float
    selection_status: str
    latest_valid_selection_date: date | None
    minute_confirmation: str
    data_confidence: str
    minute_score: float | None
    message: str


class DataFreshnessGate:
    """Blocks formal selection when daily bars are stale or materially incomplete."""

    def __init__(
        self,
        *,
        min_daily_coverage_ratio: float = 0.95,
        market_data_ready_at: time = time(16, 30),
    ) -> None:
        self.min_daily_coverage_ratio = min_daily_coverage_ratio
        self.market_data_ready_at = market_data_ready_at

    def evaluate(self, data: FreshnessInput) -> FreshnessResult:
        expected = self._expected_latest_trade_date(data.now, data.trading_dates)
        daily_coverage = self._coverage(data.daily_symbol_count, data.expected_universe_size)
        minute_coverage = self._coverage(data.minute_symbol_count, data.expected_universe_size)
        daily_is_current = (
            expected is not None
            and data.daily_market_max_date is not None
            and data.daily_market_max_date >= expected
        )
        daily_is_complete = daily_coverage >= self.min_daily_coverage_ratio

        if not daily_is_current:
            status = "blocked_stale_daily_data"
            message = (
                "行情尚未更新至最新交易日。"
                f"当前最新有效选股日期：{self._format_date(data.daily_market_max_date)}。"
            )
        elif not daily_is_complete:
            status = "blocked_daily_coverage"
            message = (
                f"日线覆盖率 {daily_coverage:.2%} 低于质量闸门 "
                f"{self.min_daily_coverage_ratio:.2%}，正式选股已阻止。"
            )
        else:
            status = "ready"
            message = "日线数据新鲜度与覆盖率检查通过。"

        minute_available = (
            expected is not None
            and data.minute_market_max_date is not None
            and data.minute_market_max_date >= expected
            and data.minute_symbol_count > 0
        )
        return FreshnessResult(
            expected_latest_trade_date=expected,
            daily_market_max_date=data.daily_market_max_date,
            minute_market_max_date=data.minute_market_max_date,
            daily_coverage_ratio=daily_coverage,
            minute_coverage_ratio=minute_coverage,
            selection_status=status,
            latest_valid_selection_date=data.daily_market_max_date,
            minute_confirmation="available" if minute_available else "unavailable",
            data_confidence="normal" if minute_available else "reduced",
            minute_score=None,
            message=message,
        )

    def _expected_latest_trade_date(self, now: datetime, trading_dates: list[date]) -> date | None:
        ordered = sorted(set(trading_dates))
        if not ordered:
            return None
        cutoff_date = now.date()
        if now.timetz().replace(tzinfo=None) < self.market_data_ready_at:
            eligible = [item for item in ordered if item < cutoff_date]
        else:
            eligible = [item for item in ordered if item <= cutoff_date]
        return eligible[-1] if eligible else None

    @staticmethod
    def _coverage(actual: int, expected: int) -> float:
        if expected <= 0:
            return 1.0 if actual > 0 else 0.0
        return min(max(actual / expected, 0.0), 1.0)

    @staticmethod
    def _format_date(value: date | None) -> str:
        return value.isoformat() if value is not None else "无"
