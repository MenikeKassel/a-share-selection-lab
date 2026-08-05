from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class FactorAnalysisRequest:
    factor_code: str
    start_date: date
    end_date: date
    horizons: list[int]
    group_count: int = 5
    industry_neutral: bool = False
    factor_path: str | None = None
    price_path: str | None = None


@dataclass(frozen=True, slots=True)
class FactorAnalysisResult:
    factor_code: str
    start_date: date
    end_date: date
    ic: dict[int, float]
    rank_ic: dict[int, float]
    ic_std: dict[int, float]
    icir: dict[int, float]
    quantile_returns: dict[int, list[float]]
    long_short_returns: dict[int, float]
    turnover: dict[int, float]
    coverage: float
    industry_results: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class FactorAnalysisEngine(Protocol):
    def analyze(self, request: FactorAnalysisRequest) -> FactorAnalysisResult:
        """Analyze one factor without exposing a third-party API to callers."""


@dataclass(frozen=True, slots=True)
class BacktestRequest:
    strategy_code: str
    start_date: date
    end_date: date
    top_n: int
    rebalance_frequency: str
    holding_period: int
    commission_rate: float
    stamp_tax_rate: float
    slippage_bps: float
    benchmark_symbol: str
    minimum_commission: float = 5.0
    initial_cash: float = 1_000_000.0
    max_stock_weight: float = 0.1
    max_industry_weight: float = 0.3
    market_data_path: str | None = None
    signal_path: str | None = None
    execution_policy: str = "daily_conservative"


@dataclass(frozen=True, slots=True)
class BacktestResult:
    equity_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    performance: dict[str, Any]
    execution_failures: list[dict[str, Any]]
    metadata: dict[str, Any]


class BacktestEngine(Protocol):
    def run(self, request: BacktestRequest) -> BacktestResult:
        """Run a backtest through the engine's stable system interface."""
