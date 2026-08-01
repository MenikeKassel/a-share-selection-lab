from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def _default_vectorbt_grid() -> dict[str, list[Any]]:
    return {
        "top_n": [10, 20, 30],
        "holding_period": [5, 10, 20],
        "rebalance_frequency": ["daily", "weekly"],
        "commission_rate": [0.0003],
        "slippage_bps": [5.0, 10.0],
    }


def _default_walk_forward_grid() -> dict[str, list[Any]]:
    """The fixed 36-combination research grid for trend_quality_v1."""
    return {
        "top_n": [5, 10, 20],
        "holding_period": [5, 10, 20],
        "rebalance_frequency": ["daily", "weekly"],
        "slippage_bps": [5.0, 10.0],
        "commission_rate": [0.0003],
    }


class FactorAnalysisRunRequest(BaseModel):
    factor_code: str
    start_date: date
    end_date: date
    horizons: list[int] = Field(default_factory=lambda: [1, 5, 10, 20])
    group_count: int = Field(default=5, ge=2, le=10)
    industry_neutral: bool = False
    include_native_baseline: bool = True
    factor_path: str
    price_path: str


class VectorBTResearchRequest(BaseModel):
    strategy_code: str
    start_date: date
    end_date: date
    price_path: str
    signal_path: str
    parameter_grid: dict[str, list[Any]] = Field(default_factory=_default_vectorbt_grid)
    initial_cash: float = Field(default=1_000_000.0, gt=0)


class RQAlphaValidationRequest(BaseModel):
    strategy_code: str
    start_date: date
    end_date: date
    signal_path: str
    benchmark_symbol: str = "000300.XSHG"
    top_n: int = Field(default=20, ge=1, le=500)
    self_result_path: str | None = None
    formal_backtest_run_id: int | None = Field(default=None, ge=1)
    data_bundle_path: str | None = None
    initial_cash: float = Field(default=1_000_000.0, gt=0)
    stock_commission_multiplier: float = Field(default=1.0, ge=0)
    minimum_commission: float = Field(default=5.0, ge=0)
    tax_multiplier: float = Field(default=1.0, ge=0)
    slippage: float = Field(default=0.0, ge=0)


class QlibExperimentRequest(BaseModel):
    experiment_code: str
    model_type: Literal["LightGBM"] = "LightGBM"
    feature_path: str
    feature_columns: list[str]
    label_column: str = "label"
    feature_version: str
    label_definition: str
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date
    training_config: dict[str, Any] = Field(default_factory=dict)
    rule_metrics: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_splits(self) -> QlibExperimentRequest:
        if not (
            self.train_start
            <= self.train_end
            < self.validation_start
            <= self.validation_end
            < self.test_start
            <= self.test_end
        ):
            raise ValueError("train/validation/test ranges must be strictly ordered")
        return self


class FormalBacktestRunRequest(BaseModel):
    strategy_code: str
    start_date: date
    end_date: date
    top_n: int = Field(default=20, ge=1, le=500)
    rebalance_frequency: Literal["daily", "weekly"] = "daily"
    holding_period: int = Field(default=5, ge=1, le=252)
    commission_rate: float = Field(default=0.0003, ge=0)
    minimum_commission: float = Field(default=5.0, ge=0)
    stamp_tax_rate: float = Field(default=0.0005, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0)
    benchmark_symbol: str = "000300.SH"
    initial_cash: float = Field(default=1_000_000.0, gt=0)
    max_stock_weight: float = Field(default=0.1, gt=0, le=1)
    max_industry_weight: float = Field(default=0.3, gt=0, le=1)
    market_data_path: str
    signal_path: str


class SelectionRunRequest(BaseModel):
    daily_path: str
    minute_directory: str | None = None
    minute_volume_unit: Literal["shares", "lots"] = "shares"
    financial_path: str | None = None
    valuation_path: str | None = None
    benchmark_path: str | None = None
    industry_rps_path: str | None = None
    expected_trade_date: date | None = None
    expected_universe_size: int = Field(default=0, ge=0)


class ReviewRunRequest(BaseModel):
    snapshot_id: int
    market_data_path: str
    benchmark_symbol: str = "000300.SH"
    horizons: list[int] = Field(default_factory=lambda: [1, 3, 5, 10, 20, 60])


class WalkForwardRunRequest(BaseModel):
    """Request for the strictly point-in-time trend-quality validation run."""

    experiment_code: str = Field(min_length=1, max_length=128)
    strategy_code: Literal["trend_quality_v1"] = "trend_quality_v1"
    snapshot_manifest_path: str = "data/raw/imports/ashare-2018-2025-v1/manifest.json"
    start_date: date = date(2018, 1, 1)
    end_date: date = date(2025, 12, 31)
    benchmark_symbol: str = "000300.SH"
    parameter_grid: dict[str, list[Any]] = Field(default_factory=_default_walk_forward_grid)
    max_drawdown_limit: float = Field(default=0.20, gt=0, le=1)
    initial_cash: float = Field(default=1_000_000.0, gt=0)
    commission_rate: float = Field(default=0.0003, ge=0)
    minimum_commission: float = Field(default=5.0, ge=0)
    stamp_tax_rate: float = Field(default=0.0005, ge=0)
    max_stock_weight: float = Field(default=0.1, gt=0, le=1)
    max_industry_weight: float = Field(default=0.3, gt=0, le=1)
    factor_horizons: list[int] = Field(default_factory=lambda: [1, 5, 10, 20])
    information_cutoff: str = "18:30"

    @model_validator(mode="after")
    def validate_window(self) -> WalkForwardRunRequest:
        if self.start_date > date(2018, 1, 1) or self.end_date < date(2025, 12, 31):
            raise ValueError("trend_quality_v1 validation requires the full 2018-2025 window")
        required = {"top_n", "holding_period", "rebalance_frequency", "slippage_bps"}
        if missing := required.difference(self.parameter_grid):
            raise ValueError(f"parameter_grid is missing {sorted(missing)}")
        if self.factor_horizons != [1, 5, 10, 20]:
            raise ValueError("factor_horizons must be [1, 5, 10, 20] for this validation")
        return self
