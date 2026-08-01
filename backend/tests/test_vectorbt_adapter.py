import pandas as pd
from app.adapters.vectorbt.adapter import VectorBTResearchAdapter
from app.adapters.vectorbt.schemas import VectorBTParameterSet
from app.adapters.vectorbt.signal_converter import convert_scores_to_signals


def test_vectorbt_signal_executes_after_signal_date() -> None:
    prices = pd.DataFrame(
        [
            {"date": "2026-01-02", "symbol": "A", "close": 10.0},
            {"date": "2026-01-02", "symbol": "B", "close": 10.0},
            {"date": "2026-01-05", "symbol": "A", "close": 11.0},
            {"date": "2026-01-05", "symbol": "B", "close": 9.0},
            {"date": "2026-01-06", "symbol": "A", "close": 12.0},
            {"date": "2026-01-06", "symbol": "B", "close": 8.0},
        ]
    )
    scores = pd.DataFrame(
        [
            {"signal_date": "2026-01-02", "symbol": "A", "score": 90.0},
            {"signal_date": "2026-01-02", "symbol": "B", "score": 10.0},
        ]
    )

    converted = convert_scores_to_signals(
        scores,
        prices,
        top_n=1,
        holding_period=1,
        rebalance_frequency="daily",
    )

    assert not bool(converted.entries.loc[pd.Timestamp("2026-01-02"), "A"])
    assert bool(converted.entries.loc[pd.Timestamp("2026-01-05"), "A"])
    assert not bool(converted.entries.loc[pd.Timestamp("2026-01-05"), "B"])
    assert converted.metadata["signal_lag_bars"] == 1


def test_vectorbt_parameter_set_changes_scores_and_applies_research_filters() -> None:
    scores = pd.DataFrame(
        [
            {
                "signal_date": "2026-01-02",
                "symbol": "A",
                "score": 1.0,
                "trend": 1.0,
                "quality": 0.0,
                "atr_percent": 0.02,
                "breakout_volume_confirmation": 1.4,
                "pa_score": 8.0,
                "risk_penalty": 1.0,
            },
            {
                "signal_date": "2026-01-02",
                "symbol": "B",
                "score": 2.0,
                "trend": 0.0,
                "quality": 1.0,
                "atr_percent": 0.08,
                "breakout_volume_confirmation": 0.8,
                "pa_score": 3.0,
                "risk_penalty": 5.0,
            },
        ]
    )
    parameters = VectorBTParameterSet(
        top_n=1,
        holding_period=5,
        rebalance_frequency="daily",
        commission_rate=0.0003,
        slippage_bps=5.0,
        factor_weights={"trend": 0.8, "quality": 0.2},
        atr_threshold=0.05,
        breakout_volume_ratio=1.2,
        pa_score_threshold=5.0,
        risk_penalty_threshold=2.0,
    )

    prepared, metadata = VectorBTResearchAdapter.prepare_scores(scores, parameters)

    assert prepared["symbol"].tolist() == ["A"]
    assert prepared["score"].tolist() == [0.8]
    assert metadata["factor_weights_applied"] == {"trend": 0.8, "quality": 0.2}
    assert set(metadata["filters_applied"]) == {
        "atr_percent<=0.05",
        "breakout_volume_confirmation>=1.2",
        "pa_score>=5.0",
        "risk_penalty<=2.0",
    }
