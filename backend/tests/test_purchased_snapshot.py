import hashlib

import pandas as pd
import pytest
from app.services.walk_forward import WalkForwardSnapshotError, WalkForwardTaskService


def _frame_hash(frame: pd.DataFrame) -> str:
    normalized = frame.sort_index(axis=1).reset_index(drop=True)
    values = pd.util.hash_pandas_object(normalized, index=False).to_numpy().tobytes()
    return hashlib.sha256(values).hexdigest()


def test_research_prices_use_causal_adjusted_view_and_execution_uses_raw_view() -> None:
    daily = pd.DataFrame(
        [
            {
                "date": "2020-01-02",
                "symbol": "A",
                "open": 20.0,
                "high": 20.0,
                "low": 20.0,
                "close": 20.0,
                "adj_open": 10.0,
                "adj_high": 10.0,
                "adj_low": 10.0,
                "adj_close": 10.0,
                "adj_pre_close": 10.0,
                "adj_factor": 1.0,
                "volume": 1000,
            },
            {
                "date": "2020-01-03",
                "symbol": "A",
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "adj_open": 10.0,
                "adj_high": 10.0,
                "adj_low": 10.0,
                "adj_close": 10.0,
                "adj_pre_close": 10.0,
                "adj_factor": 2.0,
                "volume": 1000,
            },
            {
                "date": "2020-01-06",
                "symbol": "A",
                "open": 10.5,
                "high": 10.5,
                "low": 10.5,
                "close": 10.5,
                "adj_open": 10.5,
                "adj_high": 10.5,
                "adj_low": 10.5,
                "adj_close": 10.5,
                "adj_pre_close": 10.0,
                "adj_factor": 2.0,
                "volume": 1000,
            },
        ]
    )

    research = WalkForwardTaskService._prepare_research_prices(daily)
    execution = WalkForwardTaskService._prepare_execution_prices(
        daily,
        state_history=None,
        suspensions=None,
    )

    assert research["close"].tolist() == [10.0, 10.0, 10.5]
    assert execution["close"].tolist() == [20.0, 10.0, 10.5]
    assert research["price_basis"].unique().tolist() == ["causal_hfq"]
    assert {"adj_open", "adj_high", "adj_low", "adj_close"}.isdisjoint(execution.columns)
    assert execution["adj_factor"].tolist() == [1.0, 2.0, 2.0]


def test_future_corporate_action_does_not_modify_historical_research_price_hash() -> None:
    history = pd.DataFrame(
        [
            {
                "date": "2020-01-02",
                "symbol": "A",
                "adj_open": 10.0,
                "adj_high": 10.2,
                "adj_low": 9.8,
                "adj_close": 10.0,
                "adj_factor": 1.0,
            },
            {
                "date": "2020-01-03",
                "symbol": "A",
                "adj_open": 10.1,
                "adj_high": 10.3,
                "adj_low": 10.0,
                "adj_close": 10.2,
                "adj_factor": 1.0,
            },
        ]
    )
    future = pd.DataFrame(
        [
            {
                "date": "2020-01-06",
                "symbol": "A",
                "adj_open": 5.2,
                "adj_high": 5.3,
                "adj_low": 5.1,
                "adj_close": 5.2,
                "adj_factor": 2.0,
            }
        ]
    )
    signal_history = pd.DataFrame(
        [{"signal_date": "2020-01-03", "symbol": "A", "score": 1.0}]
    )
    cutoff = pd.Timestamp("2020-01-03")

    base_research = WalkForwardTaskService._prepare_research_prices(history)
    extended = pd.concat([history, future], ignore_index=True)
    extended_history = extended.loc[pd.to_datetime(extended["date"]) <= cutoff]
    extended_research = WalkForwardTaskService._prepare_research_prices(extended_history)

    assert _frame_hash(history[["date", "symbol", "adj_close", "adj_factor"]]) == _frame_hash(
        extended_history[["date", "symbol", "adj_close", "adj_factor"]]
    )
    assert _frame_hash(signal_history) == _frame_hash(signal_history.copy())
    assert _frame_hash(base_research) == _frame_hash(extended_research)


def test_strict_walk_forward_blocks_missing_adjusted_fields() -> None:
    daily = pd.DataFrame(
        [
            {
                "date": "2020-01-02",
                "symbol": "A",
                "adj_open": 10.0,
                "adj_high": 10.2,
                "adj_low": 9.8,
            }
        ]
    )

    with pytest.raises(
        WalkForwardSnapshotError,
        match="strict walk-forward requires causal adjusted research prices",
    ):
        WalkForwardTaskService._prepare_research_prices(daily)
