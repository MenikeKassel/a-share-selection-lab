from dataclasses import replace
from datetime import date

import pandas as pd
from app.domain.protocols import BacktestRequest
from app.execution.ashare_daily import AshareDailyExecutionEngine


def _request() -> BacktestRequest:
    return BacktestRequest(
        strategy_code="trend_quality_v1",
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 6),
        top_n=1,
        rebalance_frequency="daily",
        holding_period=5,
        commission_rate=0.0003,
        minimum_commission=5.0,
        stamp_tax_rate=0.0005,
        slippage_bps=0,
        benchmark_symbol="000300.SH",
        initial_cash=100_000,
        max_stock_weight=1.0,
        max_industry_weight=1.0,
    )


def test_signal_after_close_buys_next_open_in_board_lots() -> None:
    market = pd.DataFrame(
        [
            {
                "date": "2026-01-02",
                "symbol": "600000.SH",
                "open": 10.0,
                "close": 10.0,
                "high": 10.2,
                "low": 9.8,
                "volume": 1_000_000,
                "suspended": False,
                "limit_up": False,
                "limit_down": False,
                "one_word_limit_up": False,
                "one_word_limit_down": False,
                "industry": "银行",
            },
            {
                "date": "2026-01-05",
                "symbol": "600000.SH",
                "open": 10.0,
                "close": 11.0,
                "high": 11.1,
                "low": 9.9,
                "volume": 1_000_000,
                "suspended": False,
                "limit_up": False,
                "limit_down": False,
                "one_word_limit_up": False,
                "one_word_limit_down": False,
                "industry": "银行",
            },
            {
                "date": "2026-01-06",
                "symbol": "600000.SH",
                "open": 11.0,
                "close": 11.0,
                "high": 11.1,
                "low": 10.8,
                "volume": 1_000_000,
                "suspended": False,
                "limit_up": False,
                "limit_down": False,
                "one_word_limit_up": False,
                "one_word_limit_down": False,
                "industry": "银行",
            },
        ]
    )
    signals = pd.DataFrame([{"signal_date": "2026-01-02", "symbol": "600000.SH", "score": 90.0}])

    result = AshareDailyExecutionEngine().run_with_data(_request(), market, signals)

    buy = next(trade for trade in result.trades if trade["side"] == "buy")
    assert buy["trade_date"] == "2026-01-05"
    assert buy["quantity"] % 100 == 0
    assert buy["price"] == 10.0
    assert result.performance["tradable_return"] < result.performance["theoretical_return"]


def test_limit_up_open_is_reported_as_untradable_instead_of_filled() -> None:
    market = pd.DataFrame(
        [
            {
                "date": "2026-01-02",
                "symbol": "600000.SH",
                "open": 10.0,
                "close": 10.0,
                "high": 10.0,
                "low": 10.0,
                "volume": 1_000_000,
                "suspended": False,
                "limit_up": False,
                "limit_down": False,
                "one_word_limit_up": False,
                "one_word_limit_down": False,
                "industry": "银行",
            },
            {
                "date": "2026-01-05",
                "symbol": "600000.SH",
                "open": 11.0,
                "close": 11.0,
                "high": 11.0,
                "low": 11.0,
                "volume": 10_000,
                "suspended": False,
                "limit_up": True,
                "limit_down": False,
                "one_word_limit_up": True,
                "one_word_limit_down": False,
                "industry": "银行",
            },
        ]
    )
    signals = pd.DataFrame([{"signal_date": "2026-01-02", "symbol": "600000.SH", "score": 90.0}])

    result = AshareDailyExecutionEngine().run_with_data(_request(), market, signals)

    assert result.trades == []
    assert result.execution_failures[0]["reason"] == "limit_up_unbuyable"


def test_t_plus_one_blocks_same_day_forced_exit() -> None:
    market = pd.DataFrame(
        [
            {
                "date": "2026-01-02",
                "symbol": "600000.SH",
                "open": 10.0,
                "close": 10.0,
                "high": 10.1,
                "low": 9.9,
                "volume": 1_000_000,
                "industry": "银行",
            },
            {
                "date": "2026-01-05",
                "symbol": "600000.SH",
                "open": 10.0,
                "close": 10.5,
                "high": 10.6,
                "low": 9.9,
                "volume": 1_000_000,
                "industry": "银行",
            },
        ]
    )
    signals = pd.DataFrame([{"signal_date": "2026-01-02", "symbol": "600000.SH", "score": 90.0}])

    result = AshareDailyExecutionEngine().run_with_data(
        replace(_request(), holding_period=0), market, signals
    )

    assert any(item["side"] == "buy" for item in result.trades)
    assert any(item["reason"] == "t_plus_one" for item in result.execution_failures)


def test_limit_down_position_remains_unsold_and_is_reported() -> None:
    rows = []
    for trade_date, a_open, a_close, a_limit_down in (
        ("2026-01-02", 10.0, 10.0, False),
        ("2026-01-05", 10.0, 10.0, False),
        ("2026-01-06", 9.0, 9.0, True),
    ):
        rows.extend(
            [
                {
                    "date": trade_date,
                    "symbol": "A",
                    "open": a_open,
                    "close": a_close,
                    "high": a_open,
                    "low": a_open,
                    "volume": 1_000_000,
                    "industry": "行业1",
                    "limit_down": a_limit_down,
                    "one_word_limit_down": a_limit_down,
                },
                {
                    "date": trade_date,
                    "symbol": "B",
                    "open": 10.0,
                    "close": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "volume": 1_000_000,
                    "industry": "行业2",
                },
            ]
        )
    signals = pd.DataFrame(
        [
            {"signal_date": "2026-01-02", "symbol": "A", "score": 90.0},
            {"signal_date": "2026-01-05", "symbol": "B", "score": 90.0},
        ]
    )

    result = AshareDailyExecutionEngine().run_with_data(
        replace(_request(), max_stock_weight=1.0), pd.DataFrame(rows), signals
    )

    assert any(item["reason"] == "limit_down_unsellable" for item in result.execution_failures)
    assert any(item["date"] == "2026-01-06" and item["symbol"] == "A" for item in result.positions)


def test_weekly_rebalance_uses_only_the_last_signal_of_each_week() -> None:
    rows = []
    for trade_date in ("2026-01-05", "2026-01-06", "2026-01-07"):
        for symbol in ("A", "B"):
            rows.append(
                {
                    "date": trade_date,
                    "symbol": symbol,
                    "open": 10.0,
                    "close": 10.0,
                    "high": 10.1,
                    "low": 9.9,
                    "volume": 1_000_000,
                    "industry": symbol,
                }
            )
    signals = pd.DataFrame(
        [
            {"signal_date": "2026-01-05", "symbol": "A", "score": 90.0},
            {"signal_date": "2026-01-06", "symbol": "B", "score": 90.0},
        ]
    )
    request = replace(
        _request(),
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 7),
        rebalance_frequency="weekly",
    )

    result = AshareDailyExecutionEngine().run_with_data(request, pd.DataFrame(rows), signals)

    assert [item["symbol"] for item in result.trades if item["side"] == "buy"] == ["B"]


def test_open_rebalance_does_not_use_the_same_days_close_for_industry_room() -> None:
    rows = []
    for trade_date, a_close in (
        ("2026-01-02", 10.0),
        ("2026-01-05", 10.0),
        ("2026-01-06", 100.0),
    ):
        rows.extend(
            [
                {
                    "date": trade_date,
                    "symbol": "A",
                    "open": 10.0,
                    "close": a_close,
                    "high": max(a_close, 10.0),
                    "low": 10.0,
                    "volume": 1_000_000,
                    "industry": "same",
                },
                {
                    "date": trade_date,
                    "symbol": "B",
                    "open": 10.0,
                    "close": 10.0,
                    "high": 10.1,
                    "low": 9.9,
                    "volume": 1_000_000,
                    "industry": "same",
                },
            ]
        )
    signals = pd.DataFrame(
        [
            {"signal_date": "2026-01-02", "symbol": "A", "score": 90.0},
            {"signal_date": "2026-01-05", "symbol": "A", "score": 90.0},
            {"signal_date": "2026-01-05", "symbol": "B", "score": 80.0},
        ]
    )
    request = replace(
        _request(),
        top_n=2,
        max_stock_weight=0.5,
        max_industry_weight=0.6,
    )

    result = AshareDailyExecutionEngine().run_with_data(request, pd.DataFrame(rows), signals)

    assert any(
        item["trade_date"] == "2026-01-06" and item["symbol"] == "B" and item["side"] == "buy"
        for item in result.trades
    )
