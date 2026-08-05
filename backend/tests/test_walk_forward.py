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
from app.services.walk_forward import WalkForwardSnapshotError, WalkForwardTaskService


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


def test_trade_concentration_uses_realized_pnl_from_v2_sell_records() -> None:
    # PR 4.1: concentration must consume the FIFO ledger's realized_pnl and
    # industry_at_entry from v2 sell records; no manual buy/sell pairing.
    trades: list[dict[str, object]] = []
    for day, pnl in zip(range(2, 8), [1, 2, 3, 4, 5, 6], strict=True):
        trades.append(
            {
                "trade_date": f"2025-01-{day:02d}",
                "symbol": "A",
                "side": "sell",
                "quantity": 100,
                "price": 1.0 + pnl / 100,
                "gross_amount": 100.0 + pnl,
                "commission": 0.0,
                "stamp_tax": 0.0,
                "realized_pnl": pnl,
                "matched_cost": 100.0,
                "matched_lot_ids": [f"L{day:06d}"],
                "industry_at_entry": "tech",
            }
        )
    result = walk_forward_service._trade_concentration(trades, pd.DataFrame())

    assert result["positive_trade_pnls"] == [1, 2, 3, 4, 5, 6]
    assert result["top_trade_contributions"]["positive_pnl"] == 20
    assert result["top_trade_contributions"]["positive_pnl_total"] == 21
    assert result["positive_industry_count"] == 1
    assert result["pnl_by_industry"] == {"tech": 21}


def test_trade_concentration_rejects_sell_without_realized_pnl() -> None:
    trades = [
        {
            "trade_date": "2025-01-02",
            "symbol": "A",
            "side": "sell",
            "quantity": 100,
            "price": 1.0,
            "gross_amount": 100.0,
            "commission": 0.0,
            "stamp_tax": 0.0,
        }
    ]
    with pytest.raises(ValueError, match="v2 sell trade is missing realized_pnl"):
        walk_forward_service._trade_concentration(trades, pd.DataFrame())


def test_trade_concentration_uses_industry_at_entry() -> None:
    trades = [
        {
            "trade_date": "2025-01-02",
            "symbol": "A",
            "side": "sell",
            "quantity": 100,
            "price": 1.0,
            "gross_amount": 100.0,
            "commission": 0.0,
            "stamp_tax": 0.0,
            "realized_pnl": 5.0,
            "matched_cost": 100.0,
            "industry_at_entry": "bank",
        },
        {
            "trade_date": "2025-01-03",
            "symbol": "B",
            "side": "sell",
            "quantity": 100,
            "price": 1.0,
            "gross_amount": 100.0,
            "commission": 0.0,
            "stamp_tax": 0.0,
            "realized_pnl": -3.0,
            "matched_cost": 100.0,
            "industry_at_entry": "tech",
        },
    ]
    result = walk_forward_service._trade_concentration(trades, pd.DataFrame())
    assert result["pnl_by_industry"] == {"bank": 5.0, "tech": -3.0}
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


def test_research_scan_and_formal_run_receive_different_price_views(monkeypatch) -> None:
    payload = WalkForwardRunRequest(experiment_code="price-view-routing-test")
    split = WalkForwardSplit(
        train_start=date(2018, 1, 1),
        train_end=date(2018, 12, 31),
        validation_start=date(2019, 1, 1),
        validation_end=date(2019, 12, 31),
        test_start=date(2020, 1, 1),
        test_end=date(2020, 12, 31),
    )
    research_prices = pd.DataFrame(
        [
            {"date": "2018-01-02", "symbol": "A", "close": 10.0},
            {"date": "2019-01-02", "symbol": "A", "close": 11.0},
            {"date": "2020-01-02", "symbol": "A", "close": 12.0},
        ]
    )
    execution_prices = pd.DataFrame(
        [
            {"date": "2018-01-02", "symbol": "A", "open": 20.0, "close": 20.0, "volume": 1000},
            {"date": "2019-01-02", "symbol": "A", "open": 21.0, "close": 21.0, "volume": 1000},
            {"date": "2020-01-02", "symbol": "A", "open": 22.0, "close": 22.0, "volume": 1000},
        ]
    )
    signals = pd.DataFrame(
        [
            {"signal_date": "2018-01-02", "symbol": "A", "score": 1.0},
            {"signal_date": "2019-01-02", "symbol": "A", "score": 1.0},
            {"signal_date": "2020-01-02", "symbol": "A", "score": 1.0},
        ]
    )
    scan_closes: list[float] = []
    formal_closes: list[float] = []
    scan_result = [
        {
            "parameter_set": {
                "top_n": 1,
                "holding_period": 5,
                "rebalance_frequency": "daily",
                "commission_rate": 0.0003,
                "slippage_bps": 5.0,
            },
            "cumulative_return": 0.10,
            "annualized_return": 0.10,
            "max_drawdown": 0.05,
            "sharpe": 1.0,
            "turnover": 0.1,
            "trade_count": 2,
            "win_rate": 0.5,
            "metadata": {},
        }
    ]

    def fake_scan(prices, *_args, **_kwargs):
        scan_closes.append(float(prices["close"].iloc[0]))
        return scan_result

    def fake_formal(_payload, prices, *_args, **_kwargs):
        formal_closes.append(float(prices["close"].iloc[0]))
        return {
            **walk_forward_service._empty_formal_metrics(),
            "initial_cash": 1_000_000.0,
            "equity_curve": [{"date": "2020-01-02", "equity": 1_000_000.0}],
        }

    monkeypatch.setattr(walk_forward_service, "_scan_parameters", fake_scan)
    monkeypatch.setattr(walk_forward_service, "_formal_run", fake_formal)

    result = WalkForwardTaskService._run_split(
        payload,
        split,
        research_prices,
        execution_prices,
        signals,
        benchmark=None,
    )

    assert scan_closes == [10.0, 11.0]
    assert formal_closes and all(value == 22.0 for value in formal_closes)
    assert result["evaluation_status"] == "evaluated"


def test_aggregate_windows_requires_evaluated_oos_windows() -> None:
    windows = [
        {
            "evaluation_status": "not_evaluated",
            "failure_stage": "training_filter",
            "failure_reason": "no training parameter passed cost and drawdown filters",
            "test_metrics": walk_forward_service._empty_formal_metrics(),
            "stress_10bps": walk_forward_service._empty_formal_metrics(),
            "nearby_parameters": [],
        }
        for _ in range(4)
    ]

    aggregate = WalkForwardTaskService._aggregate_windows(windows, 0.20)

    assert aggregate["metrics"]["oos_evaluated_window_count"] == 0
    assert aggregate["metrics"]["oos_evaluation_complete"] is False
    assert aggregate["gates"]["oos_evaluation_complete"] is False
    assert aggregate["gates"]["combined_oos_max_drawdown_within_limit"] is False
    assert aggregate["gates"]["all_passed"] is False


def test_aggregate_windows_proxy_never_promotion_eligible() -> None:
    """PR 4.1: proxy execution results can never pass the promotion gate."""

    def metrics(strategy_return: float, pnl: float) -> dict[str, object]:
        return {
            "tradable_return": strategy_return,
            "tradable_excess_return": strategy_return,
            "benchmark_return": 0.0,
            "composite_rank_ic_positive": True,
            "closed_trade_count": 100,
            "equity_curve": [
                {"date": "2025-01-01", "equity": 100.0},
                {"date": "2025-01-02", "equity": 110.0},
            ],
            "initial_cash": 100.0,
            "positive_trade_pnls": [pnl],
            "pnl_by_industry": {"tech": pnl},
            "execution_result_level": "proxy",
            "strict_execution_status": "blocked",
            "production_eligible": False,
        }

    windows = [
        {
            "evaluation_status": "evaluated",
            "test_metrics": metrics(0.10, 10.0),
            "stress_10bps": metrics(0.10, 10.0),
            "nearby_parameters": [{"test": {"tradable_excess_return": 0.1}}],
        }
        for _ in range(4)
    ]
    aggregate = WalkForwardTaskService._aggregate_windows(windows, 0.20)

    assert aggregate["gates"]["strict_execution_available"] is False
    assert aggregate["gates"]["promotion_eligible"] is False
    assert aggregate["gates"]["all_passed"] is False


def test_aggregate_windows_strict_can_pass_when_all_gates_pass() -> None:
    """PR 4.1: strict windows may satisfy the promotion gate."""

    def metrics(strategy_return: float, pnl: float) -> dict[str, object]:
        return {
            "tradable_return": strategy_return,
            "tradable_excess_return": strategy_return,
            "benchmark_return": 0.0,
            "composite_rank_ic_positive": True,
            "closed_trade_count": 100,
            "equity_curve": [
                {"date": "2025-01-01", "equity": 100.0},
                {"date": "2025-01-02", "equity": 110.0},
            ],
            "initial_cash": 100.0,
            # 5 small positive trades per window: top-5 contribution across
            # 4 windows is 5/20 = 25% <= 35%.
            "positive_trade_pnls": [pnl / 5] * 5,
            "pnl_by_industry": {f"ind{i}": pnl / 4 for i in range(4)},
            "execution_result_level": "strict",
            "strict_execution_status": "available",
            "production_eligible": True,
        }

    windows = [
        {
            "evaluation_status": "evaluated",
            "test_metrics": metrics(0.10, 10.0),
            "stress_10bps": metrics(0.10, 10.0),
            "nearby_parameters": [{"test": {"tradable_excess_return": 0.1}}],
        }
        for _ in range(4)
    ]
    aggregate = WalkForwardTaskService._aggregate_windows(windows, 0.20)

    assert aggregate["gates"]["strict_execution_available"] is True
    assert aggregate["gates"]["promotion_eligible"] is True
    assert aggregate["gates"]["all_passed"] is True


def test_walk_forward_report_renders_not_evaluated_windows_as_na(tmp_path) -> None:
    service = object.__new__(WalkForwardTaskService)
    service.settings = SimpleNamespace(artifact_root=tmp_path)
    windows = [
        {
            "train_start": "2018-01-01",
            "train_end": "2020-12-31",
            "validation_start": "2021-01-01",
            "validation_end": "2021-12-31",
            "test_start": "2022-01-01",
            "test_end": "2022-12-31",
            "selected_parameters": {},
            "evaluation_status": "not_evaluated",
            "failure_stage": "training_filter",
            "failure_reason": "no training parameter passed cost and drawdown filters",
            "test_metrics": walk_forward_service._empty_formal_metrics(),
        }
    ]

    report = service._write_artifacts(
        "not-evaluated-report-test",
        {
            "strategy_code": "trend_quality_v1",
            "lifecycle_status": "experimental",
            "gates": {"all_passed": False},
            "splits": windows,
        },
        signals=None,
    )

    text = report.read_text(encoding="utf-8")
    assert "not evaluated because no training parameter passed" in text
    assert "N/A | N/A | N/A | 0" in text
    assert "0.0000%" not in text


def test_scan_summary_counts_best_and_median_metrics() -> None:
    scan = [
        {
            "parameter_set": {"top_n": 5},
            "cumulative_return": -0.10,
            "max_drawdown": 0.10,
            "sharpe": -0.5,
        },
        {
            "parameter_set": {"top_n": 10},
            "cumulative_return": 0.20,
            "max_drawdown": 0.25,
            "sharpe": 1.4,
        },
        {
            "parameter_set": {"top_n": 20},
            "cumulative_return": 0.05,
            "max_drawdown": 0.05,
            "sharpe": 0.7,
        },
    ]

    summary = walk_forward_service._scan_summary(scan, 0.20)

    assert summary["parameter_count"] == 3
    assert summary["positive_return_count"] == 2
    assert summary["drawdown_pass_count"] == 2
    assert summary["both_pass_count"] == 1
    assert summary["best_cumulative_return"] == pytest.approx(0.20)
    assert summary["best_cumulative_return_parameters"] == {"top_n": 10}
    assert summary["lowest_max_drawdown"] == pytest.approx(0.05)
    assert summary["lowest_max_drawdown_parameters"] == {"top_n": 20}
    assert summary["best_sharpe"] == pytest.approx(1.4)
    assert summary["best_sharpe_parameters"] == {"top_n": 10}
    assert summary["median_cumulative_return"] == pytest.approx(0.05)
    assert summary["median_max_drawdown"] == pytest.approx(0.10)


def test_research_prices_strictly_require_adjusted_ohlc() -> None:
    daily = pd.DataFrame(
        [
            {
                "date": "2025-01-02",
                "symbol": "A",
                "adj_open": 10.0,
                "adj_high": 10.5,
                "adj_low": 9.8,
            }
        ]
    )

    with pytest.raises(
        WalkForwardSnapshotError,
        match="strict walk-forward requires causal adjusted research prices",
    ):
        WalkForwardTaskService._prepare_research_prices(daily)


def test_historical_signal_replay_reports_progress_per_chunk() -> None:
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

    events: list[tuple[str, int, int, str]] = []

    def progress(stage: str, done: int, total: int, detail: str) -> None:
        events.append((stage, done, total, detail))

    WalkForwardTaskService._generate_historical_signals(
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
        progress=progress,
    )

    assert [item[0] for item in events] == ["signals"] * 4
    assert [(item[1], item[2]) for item in events] == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_run_split_reports_training_and_validation_scan_progress(monkeypatch) -> None:
    payload = WalkForwardRunRequest(experiment_code="progress-routing-test")
    split = WalkForwardSplit(
        train_start=date(2018, 1, 1),
        train_end=date(2018, 12, 31),
        validation_start=date(2019, 1, 1),
        validation_end=date(2019, 12, 31),
        test_start=date(2020, 1, 1),
        test_end=date(2020, 12, 31),
    )
    research_prices = pd.DataFrame(
        [
            {"date": "2018-01-02", "symbol": "A", "close": 10.0},
            {"date": "2019-01-02", "symbol": "A", "close": 11.0},
            {"date": "2020-01-02", "symbol": "A", "close": 12.0},
        ]
    )
    execution_prices = pd.DataFrame(
        [
            {"date": "2018-01-02", "symbol": "A", "open": 20.0, "close": 20.0, "volume": 1000},
            {"date": "2019-01-02", "symbol": "A", "open": 21.0, "close": 21.0, "volume": 1000},
            {"date": "2020-01-02", "symbol": "A", "open": 22.0, "close": 22.0, "volume": 1000},
        ]
    )
    signals = pd.DataFrame(
        [
            {"signal_date": "2018-01-02", "symbol": "A", "score": 1.0},
            {"signal_date": "2019-01-02", "symbol": "A", "score": 1.0},
            {"signal_date": "2020-01-02", "symbol": "A", "score": 1.0},
        ]
    )
    scan_result = [
        {
            "parameter_set": {
                "top_n": 1,
                "holding_period": 5,
                "rebalance_frequency": "daily",
                "commission_rate": 0.0003,
                "slippage_bps": 5.0,
            },
            "cumulative_return": 0.10,
            "annualized_return": 0.10,
            "max_drawdown": 0.05,
            "sharpe": 1.0,
            "turnover": 0.1,
            "trade_count": 2,
            "win_rate": 0.5,
            "metadata": {},
        }
    ]

    def fake_scan(prices, *_args, progress=None, **_kwargs):
        if progress is not None:
            progress(1, 2, "first")
            progress(2, 2, "second")
        return scan_result

    def fake_formal(_payload, prices, *_args, **_kwargs):
        return {
            **walk_forward_service._empty_formal_metrics(),
            "initial_cash": 1_000_000.0,
            "equity_curve": [{"date": "2020-01-02", "equity": 1_000_000.0}],
        }

    monkeypatch.setattr(walk_forward_service, "_scan_parameters", fake_scan)
    monkeypatch.setattr(walk_forward_service, "_formal_run", fake_formal)

    events: list[tuple[str, int, int, str]] = []

    def progress(stage: str, done: int, total: int, detail: str) -> None:
        events.append((stage, done, total, detail))

    WalkForwardTaskService._run_split(
        payload,
        split,
        research_prices,
        execution_prices,
        signals,
        benchmark=None,
        progress=progress,
    )

    stages = [item[0] for item in events]
    assert stages == ["training-scan"] * 2 + ["validation-scan"] * 2
    assert (events[0][1], events[0][2]) == (1, 2)
    assert (events[3][1], events[3][2]) == (2, 2)
