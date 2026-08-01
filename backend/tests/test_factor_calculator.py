import pandas as pd
from app.research.factors.calculator import (
    DailyFactorCalculator,
    asof_join_available_data,
)


def test_financial_data_is_not_visible_before_available_at() -> None:
    daily = pd.DataFrame(
        [
            {"date": "2026-04-29", "symbol": "A", "close": 10.0},
            {"date": "2026-04-30", "symbol": "A", "close": 11.0},
        ]
    )
    financials = pd.DataFrame(
        [
            {
                "symbol": "A",
                "period_end": "2026-03-31",
                "published_at": "2026-04-30 08:00:00",
                "available_at": "2026-04-30 08:05:00",
                "fetched_at": "2026-04-30 08:06:00",
                "source": "fixture",
                "content_hash": "abc",
                "revenue_growth_yoy": 0.25,
            }
        ]
    )

    joined = asof_join_available_data(daily, financials)

    before_publication = joined.loc[
        joined["date"] == pd.Timestamp("2026-04-29"), "revenue_growth_yoy"
    ]
    assert pd.isna(before_publication).all()
    assert (
        joined.loc[joined["date"] == pd.Timestamp("2026-04-30"), "revenue_growth_yoy"].iloc[0]
        == 0.25
    )


def test_after_cutoff_announcement_is_only_visible_on_the_next_trade_date() -> None:
    daily = pd.DataFrame(
        [
            {"date": "2026-04-30", "symbol": "A", "close": 10.0},
            {"date": "2026-05-06", "symbol": "A", "close": 11.0},
        ]
    )
    financials = pd.DataFrame(
        [
            {
                "symbol": "A",
                "period_end": "2026-03-31",
                "published_at": "2026-04-30 19:59:00",
                "available_at": "2026-04-30 20:00:00",
                "fetched_at": "2026-04-30 20:01:00",
                "source": "fixture",
                "content_hash": "late",
                "revenue_growth_yoy": 0.30,
            }
        ]
    )

    joined = asof_join_available_data(daily, financials)

    same_day = joined.loc[joined["date"] == pd.Timestamp("2026-04-30"), "revenue_growth_yoy"]
    next_session = joined.loc[joined["date"] == pd.Timestamp("2026-05-06"), "revenue_growth_yoy"]
    assert pd.isna(same_day).all()
    assert next_session.iloc[0] == 0.30


def test_daily_factor_calculator_emits_transparent_factor_catalog() -> None:
    rows = []
    for index, trade_date in enumerate(pd.bdate_range("2025-01-02", periods=260)):
        rows.append(
            {
                "date": trade_date,
                "symbol": "A",
                "open": 10 + index * 0.01,
                "high": 10.2 + index * 0.01,
                "low": 9.8 + index * 0.01,
                "close": 10.1 + index * 0.01,
                "volume": 1_000_000 + index,
                "amount": (10.1 + index * 0.01) * (1_000_000 + index),
                "turnover_rate": 0.01,
                "industry": "测试行业",
                "limit_up": False,
                "limit_down": False,
                "one_word_limit_up": False,
                "one_word_limit_down": False,
            }
        )

    factors = DailyFactorCalculator().calculate(pd.DataFrame(rows))

    expected = {
        "close_above_ma20",
        "ma20_slope",
        "distance_from_52w_high",
        "return_120d",
        "rps_120d",
        "volume_ratio_5d_20d",
        "close_location_value",
        "atr_percent",
        "max_drawdown_60d",
        "one_word_limit_count",
    }
    assert expected.issubset(factors.columns)
    assert len(factors) == 260
