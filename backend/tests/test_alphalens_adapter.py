from datetime import date

import pandas as pd
import pytest
from app.adapters.alphalens.converter import prepare_alphalens_input
from app.adapters.io import filter_date_window


def test_alphalens_input_uses_multiindex_and_historical_industry() -> None:
    factors = pd.DataFrame(
        [
            {
                "date": "2026-01-02",
                "symbol": "A",
                "spring_candidate": True,
                "industry": "旧行业",
            },
            {
                "date": "2026-01-05",
                "symbol": "A",
                "spring_candidate": False,
                "industry": "新行业",
            },
        ]
    )
    prices = pd.DataFrame(
        [
            {"date": "2026-01-02", "symbol": "A", "close": 10.0},
            {"date": "2026-01-05", "symbol": "A", "close": 11.0},
            {"date": "2026-01-06", "symbol": "A", "close": 12.0},
        ]
    )

    converted = prepare_alphalens_input(factors, prices, "spring_candidate")

    assert converted.factor.index.names == ["date", "asset"]
    assert converted.factor.tolist() == [1.0, 0.0]
    assert converted.groups is not None
    assert converted.groups.loc[(pd.Timestamp("2026-01-02"), "A")] == "旧行业"
    assert converted.groups.loc[(pd.Timestamp("2026-01-05"), "A")] == "新行业"
    assert converted.prices.loc[pd.Timestamp("2026-01-06"), "A"] == 12.0


def test_future_return_column_is_rejected_from_factor_input() -> None:
    factors = pd.DataFrame(
        [
            {
                "date": date(2026, 1, 2),
                "symbol": "A",
                "factor": 1.0,
                "forward_return_5d": 0.1,
            }
        ]
    )
    prices = pd.DataFrame([{"date": date(2026, 1, 2), "symbol": "A", "close": 10.0}])

    with pytest.raises(ValueError, match="future/forward"):
        prepare_alphalens_input(factors, prices, "factor")


def test_research_window_filters_signal_dates_inclusively() -> None:
    frame = pd.DataFrame(
        [
            {"signal_date": "2025-12-31", "symbol": "A", "score": 1},
            {"signal_date": "2026-01-02", "symbol": "A", "score": 2},
            {"signal_date": "2026-01-05", "symbol": "A", "score": 3},
        ]
    )

    filtered = filter_date_window(
        frame,
        column="signal_date",
        start=date(2026, 1, 2),
        end=date(2026, 1, 2),
    )

    assert filtered["score"].tolist() == [2]
