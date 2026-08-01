from __future__ import annotations

from app.research.factors.pipeline import FactorDefinition

TREND_FACTORS = (
    "close_above_ma20",
    "close_above_ma60",
    "ma20_above_ma60",
    "ma20_slope",
    "ma60_slope",
    "distance_from_ma20",
    "distance_from_ma60",
    "distance_from_52w_high",
    "breakout_20d",
    "breakout_60d",
    "trend_duration",
    "higher_high",
    "higher_low",
)
RELATIVE_STRENGTH_FACTORS = (
    "return_5d",
    "return_20d",
    "return_60d",
    "return_120d",
    "rps_20d",
    "rps_60d",
    "rps_120d",
    "relative_return_vs_csi300_20d",
    "relative_return_vs_csi300_60d",
    "relative_return_vs_industry_20d",
    "relative_return_vs_industry_60d",
    "down_market_relative_strength",
    "industry_rps_50",
    "industry_rps_120",
    "industry_rps_250",
)
VOLUME_PRICE_FACTORS = (
    "volume_ratio_5d_20d",
    "amount_ratio_5d_20d",
    "turnover_percentile_120d",
    "up_volume_down_volume_ratio",
    "breakout_volume_confirmation",
    "pullback_volume_contraction",
    "close_location_value",
    "high_volume_stall",
    "long_upper_shadow_volume",
    "amount_stability",
)
FUNDAMENTAL_FACTORS = (
    "revenue_growth_yoy",
    "net_profit_growth_yoy",
    "deducted_profit_growth_yoy",
    "roe_ttm",
    "gross_margin_change",
    "operating_cashflow_to_profit",
    "free_cashflow",
    "debt_ratio",
    "inventory_growth",
    "receivable_growth",
    "goodwill_ratio",
    "non_recurring_profit_ratio",
)
VALUATION_FACTORS = (
    "pe_ttm_percentile",
    "pb_percentile",
    "ps_ttm_percentile",
    "dividend_yield_percentile",
    "free_cashflow_yield_percentile",
)
RISK_FACTORS = (
    "atr_percent",
    "volatility_20d",
    "volatility_60d",
    "downside_volatility",
    "max_drawdown_60d",
    "gap_risk",
    "limit_up_count_20d",
    "limit_down_count_20d",
    "one_word_limit_count",
    "distance_from_ma20_atr",
    "event_risk",
    "data_quality_risk",
)

REVERSE_FACTORS = {
    "distance_from_ma20",
    "distance_from_ma60",
    "distance_from_52w_high",
    "high_volume_stall",
    "long_upper_shadow_volume",
    "debt_ratio",
    "inventory_growth",
    "receivable_growth",
    "goodwill_ratio",
    "non_recurring_profit_ratio",
    "pe_ttm_percentile",
    "pb_percentile",
    "ps_ttm_percentile",
}


def default_factor_definitions() -> list[FactorDefinition]:
    definitions: list[FactorDefinition] = []
    groups = (
        ("trend", TREND_FACTORS, 18.0),
        ("relative_strength", RELATIVE_STRENGTH_FACTORS, 15.0),
        ("volume_price", VOLUME_PRICE_FACTORS, 10.0),
        ("fundamental", FUNDAMENTAL_FACTORS, 15.0),
        ("valuation", VALUATION_FACTORS, 7.0),
    )
    for group, factors, points in groups:
        for code in factors:
            definitions.append(
                FactorDefinition(
                    code=code,
                    group=group,
                    weight=points / 70.0 / len(factors),
                    direction=-1 if code in REVERSE_FACTORS else 1,
                )
            )
    definitions.append(
        FactorDefinition(
            code="market_regime_alignment",
            group="market_adaptation",
            weight=5.0 / 70.0,
            direction=1,
        )
    )
    return definitions


def default_risk_definitions() -> list[FactorDefinition]:
    """Risk factors are normalized separately and only subtract from final scores."""

    return [
        FactorDefinition(
            code=code,
            group="risk",
            weight=1.0 / len(RISK_FACTORS),
            direction=1,
            risk=True,
        )
        for code in RISK_FACTORS
    ]
