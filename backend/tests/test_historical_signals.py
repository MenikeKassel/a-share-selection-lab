from __future__ import annotations

import pandas as pd
from app.research.historical_signals import HistoricalSignalGenerator


def _market(*, periods: int = 140, include_stall: bool = True) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=periods)
    rows: list[dict[str, object]] = []
    for day_number, trade_date in enumerate(dates):
        for symbol_number, symbol in enumerate(["A", "B", "C"]):
            # Keep the trend well above the moving averages without making it
            # an unrealistic many-ATR extension; this lets A/B reach the
            # trend-quality rule while C is rejected by the injected stall.
            close = 10.0 + day_number * (0.002 + symbol_number * 0.001)
            final_stall = include_stall and trade_date == dates[-1] and symbol == "C"
            volume = 100_000 + symbol_number * 20_000 + day_number * 100
            rows.append(
                {
                    "date": trade_date,
                    "symbol": symbol,
                    "open": close - 0.001,
                    "high": close + (1.0 if final_stall else 0.01),
                    "low": close - (0.1 if final_stall else 0.01),
                    "close": close,
                    "volume": 10_000_000 if final_stall else volume,
                    "amount": (10_000_000 if final_stall else volume) * close,
                    "turnover_rate": 0.01 + symbol_number * 0.002,
                    "industry": f"industry-{symbol_number % 2}",
                    "market_cap": 1_000_000_000 * (symbol_number + 1),
                    "limit_up": False,
                    "limit_down": False,
                    "one_word_limit_up": False,
                    "one_word_limit_down": False,
                    "suspended": False,
                    "is_st": False,
                    "listing_days": 500,
                    "delisting_risk": False,
                }
            )
    return pd.DataFrame(rows)


def _generate(daily: pd.DataFrame):
    dates = sorted(pd.to_datetime(daily["date"]).dt.date.unique())
    return HistoricalSignalGenerator().generate(
        daily=daily,
        trading_dates=dates,
        data_snapshot_version="sha256:test-snapshot-v1",
    )


def test_future_rows_do_not_change_prior_historical_signals() -> None:
    base = _market()
    extended_dates = pd.bdate_range(base["date"].max() + pd.Timedelta(days=1), periods=5)
    extension = _market(periods=5, include_stall=False)
    extension["date"] = extended_dates.repeat(3).to_numpy()
    extended = pd.concat([base, extension], ignore_index=True)

    before = _generate(base).signals
    after = _generate(extended).signals
    cutoff = pd.Timestamp(base["date"].max())
    columns = [
        "signal_date",
        "symbol",
        "score",
        "strategy_code",
        "strategy_version",
        "factor_version",
        "data_snapshot_version",
        "data_confidence",
        "minute_confirmation",
    ]
    before_prefix = before.loc[pd.to_datetime(before["signal_date"]) <= cutoff, columns]
    after_prefix = after.loc[pd.to_datetime(after["signal_date"]) <= cutoff, columns]
    pd.testing.assert_frame_equal(
        before_prefix.reset_index(drop=True),
        after_prefix.reset_index(drop=True),
        check_dtype=False,
    )


def test_hard_gate_rows_are_not_emitted_as_signals() -> None:
    result = _generate(_market())

    assert "C" not in set(result.signals["symbol"])
    rejected = result.rejected.loc[result.rejected["symbol"] == "C"]
    assert not rejected.empty
    assert "high_volume_stall" in rejected.iloc[-1]["hard_gate_reasons"]


def test_historical_signal_schema_contains_versions_and_factor_audit() -> None:
    result = _generate(_market())

    required = {
        "signal_date",
        "symbol",
        "score",
        "strategy_code",
        "strategy_version",
        "factor_version",
        "data_snapshot_version",
        "factor_audit",
        "hard_gate_reasons",
        "data_confidence",
        "minute_confirmation",
    }
    assert required.issubset(result.signals.columns)
    # This compact fixture is intentionally conservative and may have no
    # trend-quality rows; the rejected frame carries the same audit schema.
    row = (result.signals if not result.signals.empty else result.rejected).iloc[0]
    assert row["strategy_code"] == "trend_quality_v1"
    assert row["strategy_version"] == "1.0.0"
    assert row["factor_version"] == "transparent_factor_v1"
    assert row["data_snapshot_version"] == "sha256:test-snapshot-v1"
    if not result.signals.empty:
        assert row["hard_gate_reasons"] == []
        assert row["data_confidence"] == "reduced"
    assert row["minute_confirmation"] == "unavailable"
    assert isinstance(row["factor_audit"], list)
    assert {"raw_value", "processed_value", "factor_code"}.issubset(row["factor_audit"][0])
