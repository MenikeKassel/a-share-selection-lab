from datetime import date, time
from types import SimpleNamespace

import pandas as pd
import pytest
from app.adapters.vectorbt.adapter import VectorBTResearchAdapter
from app.api.schemas import WalkForwardRunRequest
from app.research.walk_forward import (
    RobustnessEvidence,
    WalkForwardPolicy,
    WalkForwardSplit,
    generate_annual_walk_forward_splits,
)
from app.services import walk_forward as walk_forward_service
from app.services.walk_forward import WalkForwardTaskService


def test_walk_forward_ranges_must_be_ordered() -> None:
    split = WalkForwardSplit(
        train_start=date(2021, 1, 1),
        train_end=date(2023, 12, 31),
        validation_start=date(2024, 1, 1),
        validation_end=date(2024, 12, 31),
        test_start=date(2025, 1, 1),
        test_end=date(2025, 12, 31),
    )

    assert split.test_start > split.validation_end


def test_vectorbt_and_qlib_cannot_auto_promote_to_production() -> None:
    evidence = RobustnessEvidence(
        consistent_across_periods=True,
        stable_nearby_parameters=True,
        survives_costs=True,
        not_driven_by_extremes=True,
        cross_industry=True,
        acceptable_drawdown=True,
        stable_ic_direction=True,
        out_of_sample_healthy=True,
    )

    decision = WalkForwardPolicy().evaluate(
        evidence, source_engine="vectorbt", manual_production_approval=False
    )

    assert decision.status == "production_candidate"
    assert decision.production_enabled is False


def test_annual_walk_forward_windows_roll_without_overlap() -> None:
    splits = generate_annual_walk_forward_splits(
        first_train_year=2021,
        final_test_year=2026,
        train_years=3,
        validation_years=1,
        test_years=1,
    )

    assert len(splits) == 2
    assert splits[0].train_start == date(2021, 1, 1)
    assert splits[0].test_start == date(2025, 1, 1)
    assert splits[1].train_start == date(2022, 1, 1)
    assert splits[1].test_start == date(2026, 1, 1)


def test_trend_quality_request_defaults_to_36_fixed_parameter_combinations() -> None:
    payload = WalkForwardRunRequest(experiment_code="grid-test")
    grid = payload.parameter_grid
    combinations = 1
    for values in grid.values():
        combinations *= len(values)
    assert combinations == 36
    assert grid["top_n"] == [5, 10, 20]
    assert grid["slippage_bps"] == [5.0, 10.0]


def test_walk_forward_does_not_hide_vectorbt_runtime_failures(monkeypatch) -> None:
    def fail_scan(*_args, **_kwargs):
        raise RuntimeError("vectorbt failed")

    monkeypatch.setattr(VectorBTResearchAdapter, "run_parameter_scan", fail_scan)
    prices = pd.DataFrame([{"date": "2025-01-02", "symbol": "A", "close": 10.0}])
    signals = pd.DataFrame(
        [{"signal_date": "2025-01-02", "symbol": "A", "score": 1.0}]
    )

    with pytest.raises(RuntimeError, match="vectorbt failed"):
        walk_forward_service._scan_parameters(prices, signals, {}, 1_000_000.0)


def test_trade_concentration_uses_closed_trades_and_point_in_time_industry() -> None:
    market = pd.DataFrame(
        [
            {"date": f"2025-01-{day:02d}", "symbol": "A", "industry": "tech"}
            for day in range(2, 9)
        ]
        + [{"date": "2025-01-02", "symbol": "B", "industry": "bank"}]
    )
    trades: list[dict[str, object]] = []
    for day, pnl in zip(range(2, 8), [1, 2, 3, 4, 5, 6], strict=True):
        trades.extend(
            [
                {
                    "trade_date": f"2025-01-{day:02d}",
                    "symbol": "A",
                    "side": "buy",
                    "quantity": 100,
                    "price": 1.0,
                    "gross_amount": 100.0,
                    "commission": 0.0,
                    "stamp_tax": 0.0,
                },
                {
                    "trade_date": f"2025-01-{day:02d}",
                    "symbol": "A",
                    "side": "sell",
                    "quantity": 100,
                    "price": 1.0 + pnl / 100,
                    "gross_amount": 100.0 + pnl,
                    "commission": 0.0,
                    "stamp_tax": 0.0,
                },
            ]
        )
    result = walk_forward_service._trade_concentration(trades, market)

    assert result["positive_trade_pnls"] == [1, 2, 3, 4, 5, 6]
    assert result["top_trade_contributions"]["positive_pnl"] == 20
    assert result["top_trade_contributions"]["positive_pnl_total"] == 21
    assert result["positive_industry_count"] == 1


def test_aggregate_windows_compounds_stress_and_merges_oos_concentration() -> None:
    def metrics(strategy_return: float, industry: str, pnl: float) -> dict[str, object]:
        return {
            "tradable_return": strategy_return,
            "tradable_excess_return": strategy_return,
            "benchmark_return": 0.0,
            "composite_rank_ic_positive": True,
            "closed_trade_count": 100,
            "equity_curve": [
                {"date": "2025-01-01", "equity": 100.0},
                {"date": "2025-01-02", "equity": 90.0},
            ],
            "initial_cash": 100.0,
            "positive_trade_pnls": [pnl],
            "pnl_by_industry": {industry: pnl},
        }

    windows = [
        {
            "test_metrics": metrics(0.10, "tech", 10.0),
            "stress_10bps": metrics(0.10, "tech", 10.0),
            "nearby_parameters": [{"test": {"tradable_excess_return": 0.1}}],
        },
        {
            "test_metrics": metrics(0.10, "bank", 5.0),
            "stress_10bps": metrics(0.10, "bank", 5.0),
            "nearby_parameters": [{"test": {"tradable_excess_return": 0.1}}],
        },
        {
            "test_metrics": metrics(0.10, "tech", 5.0),
            "stress_10bps": metrics(0.10, "tech", 5.0),
            "nearby_parameters": [{"test": {"tradable_excess_return": 0.1}}],
        },
        {
            "test_metrics": metrics(0.10, "tech", 5.0),
            "stress_10bps": metrics(0.10, "tech", 5.0),
            "nearby_parameters": [{"test": {"tradable_excess_return": 0.1}}],
        },
    ]
    aggregate = walk_forward_service.WalkForwardTaskService._aggregate_windows(windows, 0.20)

    assert aggregate["metrics"]["stress_10bps_excess_return"] == pytest.approx(0.4641)
    assert aggregate["gates"]["at_least_3_positive_industries"] is False
    assert aggregate["gates"]["single_industry_contribution_within_40pct"] is False
    assert aggregate["gates"]["top_5_trades_contribution_within_35pct"] is False


def test_historical_signal_replay_uses_400_trading_day_year_chunks() -> None:
    dates = pd.bdate_range("2016-01-01", "2021-12-31")
    daily = pd.DataFrame({"date": dates, "symbol": "A"})

    class FakeGenerator:
        strategy_code = "trend_quality_v1"
        strategy_version = "test"
        factor_version = "test"
        information_cutoff = time(18, 30)

        def generate(self, *, start_date, end_date, **_kwargs):
            signal = pd.DataFrame(
                [{"signal_date": pd.Timestamp(start_date), "symbol": "A", "score": 1.0}]
            )
            return SimpleNamespace(signals=signal, rejected=pd.DataFrame())

    signals, rejected, audit = WalkForwardTaskService._generate_historical_signals(
        FakeGenerator(),
        daily=daily,
        financials=None,
        valuations=None,
        benchmark=None,
        industry=None,
        state_history=pd.DataFrame(),
        start_date=date(2018, 1, 1),
        end_date=date(2021, 12, 31),
        data_snapshot_version="test-snapshot",
    )

    assert len(signals) == 4
    assert rejected.empty
    assert audit["chunk_count"] == 4
    assert all(item["warmup_trading_days"] == 400 for item in audit["chunks"])


def test_historical_signal_replay_slices_point_in_time_inputs_per_year() -> None:
    dates = pd.bdate_range("2016-01-01", "2019-12-31")
    daily = pd.DataFrame(
        {
            "date": dates,
            "symbol": ["A"] * len(dates),
        }
    )
    point_in_time = pd.DataFrame(
        [
            {
                "symbol": "A",
                "period_end": "2017-12-31",
                "published_at": "2018-01-02 18:00:00+08:00",
                "available_at": "2018-01-02 18:30:00+08:00",
                "fetched_at": "2026-07-31 00:00:00+08:00",
                "source": "test",
                "content_hash": "old",
            },
            {
                "symbol": "A",
                "period_end": "2018-12-31",
                "published_at": "2019-01-02 18:00:00+08:00",
                "available_at": "2019-01-02 18:30:00+08:00",
                "fetched_at": "2026-07-31 00:00:00+08:00",
                "source": "test",
                "content_hash": "current",
            },
            {
                "symbol": "A",
                "period_end": "2025-12-31",
                "published_at": "2026-01-02 18:00:00+08:00",
                "available_at": "2026-01-02 18:30:00+08:00",
                "fetched_at": "2026-07-31 00:00:00+08:00",
                "source": "test",
                "content_hash": "future",
            },
            {
                "symbol": "B",
                "period_end": "2018-12-31",
                "published_at": "2019-01-02 18:00:00+08:00",
                "available_at": "2019-01-02 18:30:00+08:00",
                "fetched_at": "2026-07-31 00:00:00+08:00",
                "source": "test",
                "content_hash": "other-symbol",
            },
        ]
    )

    class RecordingGenerator:
        strategy_code = "trend_quality_v1"
        strategy_version = "test"
        factor_version = "test"
        information_cutoff = time(18, 30)

        def __init__(self) -> None:
            self.seen: list[pd.DataFrame] = []

        def generate(self, *, financials, **_kwargs):
            self.seen.append(financials.copy())
            return SimpleNamespace(signals=pd.DataFrame(), rejected=pd.DataFrame())

    generator = RecordingGenerator()
    WalkForwardTaskService._generate_historical_signals(
        generator,
        daily=daily,
        financials=point_in_time,
        valuations=point_in_time,
        benchmark=None,
        industry=None,
        state_history=pd.DataFrame(),
        start_date=date(2018, 1, 1),
        end_date=date(2019, 12, 31),
        data_snapshot_version="test-snapshot",
    )

    assert len(generator.seen) == 2
    assert all(set(frame["symbol"]) == {"A"} for frame in generator.seen)
    assert all("future" not in set(frame["content_hash"]) for frame in generator.seen)
    assert set(generator.seen[0]["content_hash"]) == {"old"}
    assert set(generator.seen[1]["content_hash"]) == {"old", "current"}


def test_execution_price_view_drops_research_only_wide_columns() -> None:
    daily = pd.DataFrame(
        [
            {
                "date": "2025-01-02",
                "symbol": "A",
                "open": 10.0,
                "close": 10.5,
                "volume": 1000,
                "adj_factor": 1.2,
                "limit_up": False,
                "limit_down": False,
                "name": "fixture",
                "adj_close": 12.6,
            }
        ]
    )

    prices = WalkForwardTaskService._prepare_execution_prices(
        daily,
        state_history=None,
        suspensions=None,
    )

    assert {"date", "symbol", "open", "close", "volume", "adj_factor"}.issubset(
        prices.columns
    )
    assert "name" not in prices
    assert "adj_close" not in prices
