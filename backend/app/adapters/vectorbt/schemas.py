from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class VectorBTParameterSet:
    top_n: int
    holding_period: int
    rebalance_frequency: str
    commission_rate: float
    slippage_bps: float
    factor_weights: dict[str, float] | None = None
    atr_threshold: float | None = None
    breakout_volume_ratio: float | None = None
    pa_score_threshold: float | None = None
    risk_penalty_threshold: float | None = None


@dataclass(frozen=True, slots=True)
class VectorBTResearchResult:
    parameter_set: dict[str, Any]
    cumulative_return: float
    annualized_return: float
    max_drawdown: float
    sharpe: float
    turnover: float
    trade_count: int
    win_rate: float
    metadata: dict[str, Any]
