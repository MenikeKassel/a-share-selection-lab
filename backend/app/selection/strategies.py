from __future__ import annotations

from typing import Any

import pandas as pd

STRATEGY_LABELS = {
    "trend_quality_v1": "趋势质量",
    "breakout_start_v1": "突破启动",
    "fundamental_confirmation_v1": "基本面改善与价格确认",
    "emotional_momentum_watchlist_v1": "高风险情绪观察池",
}


def classify_candidate(row: pd.Series | dict[str, Any]) -> list[str]:
    value = (
        row if isinstance(row, dict) else {str(key): item for key, item in row.to_dict().items()}
    )
    strategies: list[str] = []
    trend_quality = (
        _number(value, "ma20", 0) > _number(value, "ma60", float("inf"))
        and _number(value, "ma20_slope", 0) > 0
        and _number(value, "ma60_slope", 0) > 0
        and _number(value, "rps_60d", 0) >= 0.6
        and _number(value, "rps_120d", 0) >= 0.6
        and _number(value, "higher_low", 0) > 0
        and _number(value, "distance_from_ma20_atr", 99) <= 3
        and _number(value, "high_volume_stall", 0) == 0
        and not bool(value.get("severe_financial_anomaly", False))
    )
    if trend_quality:
        strategies.append("trend_quality_v1")

    breakout = (
        max(_number(value, "breakout_20d", 0), _number(value, "breakout_60d", 0)) > 0
        and _number(value, "volatility_contraction", 0) > 0
        and _number(value, "breakout_volume_confirmation", 0) >= 1.2
        and _number(value, "close_location_value", -1) >= 0.4
        and _number(value, "ma20_slope", 0) > 0
        and _number(value, "distance_from_ma20_atr", 99) <= 2.5
        and _number(value, "relative_return_vs_industry_20d", -1) >= 0
        and _number(value, "failed_breakout", 0) == 0
    )
    if breakout:
        strategies.append("breakout_start_v1")

    fundamental = (
        (
            _number(value, "deducted_profit_growth_yoy", -1) > 0
            or _number(value, "revenue_growth_yoy", -1) > 0
            or _number(value, "operating_cashflow_to_profit", -1) >= 1
        )
        and not bool(value.get("non_recurring_growth", False))
        and not bool(value.get("cashflow_divergence", False))
        and _number(value, "close_above_ma20", 0) > 0
        and _number(value, "close_above_ma60", 0) > 0
        and _number(value, "rps_20d", 0) >= 0.5
        and _number(value, "higher_low", 0) > 0
        and _number(value, "high_volume_stall", 0) == 0
    )
    if fundamental:
        strategies.append("fundamental_confirmation_v1")

    emotional = (
        _number(value, "return_5d", 0) > 0.15
        or _number(value, "limit_up_count_20d", 0) >= 2
        or _number(value, "distance_from_ma20_atr", 0) > 4
    )
    if emotional:
        strategies.append("emotional_momentum_watchlist_v1")
    return strategies


def _number(value: dict[str, Any], key: str, default: float) -> float:
    item = value.get(key, default)
    try:
        numeric = float(item)
    except (TypeError, ValueError):
        return default
    return numeric if pd.notna(numeric) else default
