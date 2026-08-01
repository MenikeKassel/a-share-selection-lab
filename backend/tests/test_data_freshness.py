from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
from app.data.freshness import DataFreshnessGate, FreshnessInput, estimate_expected_universe_size


def test_stale_daily_data_blocks_formal_selection() -> None:
    gate = DataFreshnessGate(min_daily_coverage_ratio=0.95)

    result = gate.evaluate(
        FreshnessInput(
            now=datetime(2026, 7, 30, 19, tzinfo=ZoneInfo("Asia/Shanghai")),
            trading_dates=[date(2026, 7, 29), date(2026, 7, 30)],
            daily_market_max_date=date(2026, 7, 29),
            minute_market_max_date=date(2026, 7, 29),
            daily_symbol_count=5_000,
            minute_symbol_count=128,
            expected_universe_size=5_000,
        )
    )

    assert result.selection_status == "blocked_stale_daily_data"
    assert result.expected_latest_trade_date == date(2026, 7, 30)
    assert result.latest_valid_selection_date == date(2026, 7, 29)
    assert "行情尚未更新至最新交易日" in result.message


def test_missing_minute_data_reduces_confidence_without_zero_score() -> None:
    gate = DataFreshnessGate(min_daily_coverage_ratio=0.95)

    result = gate.evaluate(
        FreshnessInput(
            now=datetime(2026, 7, 30, 19, tzinfo=ZoneInfo("Asia/Shanghai")),
            trading_dates=[date(2026, 7, 30)],
            daily_market_max_date=date(2026, 7, 30),
            minute_market_max_date=None,
            daily_symbol_count=5_000,
            minute_symbol_count=0,
            expected_universe_size=5_000,
        )
    )

    assert result.selection_status == "ready"
    assert result.minute_confirmation == "unavailable"
    assert result.data_confidence == "reduced"
    assert result.minute_score is None


def test_expected_universe_uses_recent_history_not_the_incomplete_latest_day() -> None:
    daily = pd.DataFrame(
        [{"date": "2026-07-29", "symbol": symbol} for symbol in ("A", "B", "C")]
        + [{"date": "2026-07-30", "symbol": symbol} for symbol in ("A", "B")]
    )

    assert estimate_expected_universe_size(daily) == 3
    assert estimate_expected_universe_size(daily, configured_size=5_000) == 5_000
