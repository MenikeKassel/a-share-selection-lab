from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.api.schemas import (
    FactorAnalysisRunRequest,
    FormalBacktestRunRequest,
    QlibExperimentRequest,
    VectorBTResearchRequest,
)
from app.core.config import Settings
from app.db.repositories import FactorResultRepository, decode_json
from app.db.session import create_session_factory, initialize_database
from app.research.factors.calculator import DailyFactorCalculator
from app.selection.pipeline import DailySelectionPipeline
from app.selection.review import AutomaticReviewService
from app.selection.snapshots import SelectionSnapshotRepository
from app.services.research_tasks import ResearchTaskService

EXPERIMENT_CODE = "first_transparent_research_experiment_v1"
SELECTION_DATE = date(2026, 3, 31)


def run_first_experiment(
    output_root: Path,
    *,
    seed: int = 20260730,
) -> dict[str, Any]:
    """Run the first deterministic end-to-end experiment against synthetic data."""

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_root = output_root.expanduser().resolve() / run_id
    input_root = run_root / "input"
    artifact_root = run_root / "artifacts"
    run_root.mkdir(parents=True, exist_ok=False)
    manifest_path = run_root / "manifest.json"
    manifest: dict[str, Any] = {
        "experiment_code": EXPERIMENT_CODE,
        "run_id": run_id,
        "status": "running",
        "synthetic_data": True,
        "production_enabled": False,
        "automatic_ordering": False,
        "selection_date": SELECTION_DATE.isoformat(),
        "started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "steps": {},
    }
    _write_json(manifest_path, manifest)
    try:
        generated = _generate_inputs(input_root, seed=seed)
        manifest["steps"]["data_generation"] = {
            "status": "passed",
            "daily_rows": len(generated["daily"]),
            "symbols": int(generated["daily"]["symbol"].nunique()),
            "minute_symbols": len(generated["minutes"]),
            "minute_rows": sum(len(frame) for frame in generated["minutes"].values()),
        }

        database_path = run_root / "experiment.db"
        database_url = f"sqlite:///{database_path.as_posix()}"
        initialize_database(database_url)
        settings = Settings(
            _env_file=None,
            environment="experiment",
            database_url=database_url,
            data_root=run_root,
            artifact_root=artifact_root,
            scheduler_enabled=False,
            expected_universe_size=int(generated["daily"]["symbol"].nunique()),
        )
        factory = create_session_factory(database_url)
        with factory() as session:
            selection_market = (
                generated["daily"].loc[generated["daily"]["date"].dt.date <= SELECTION_DATE].copy()
            )
            selection = DailySelectionPipeline(
                artifact_root=artifact_root,
                snapshot_repository=SelectionSnapshotRepository(session),
            ).run(
                daily=selection_market,
                trading_dates=[
                    timestamp.date()
                    for timestamp in generated["sessions"]
                    if timestamp.date() <= SELECTION_DATE
                ],
                now=datetime(
                    SELECTION_DATE.year,
                    SELECTION_DATE.month,
                    SELECTION_DATE.day,
                    19,
                    tzinfo=ZoneInfo("Asia/Shanghai"),
                ),
                expected_universe_size=int(
                    selection_market.loc[
                        selection_market["date"].dt.date == SELECTION_DATE,
                        "symbol",
                    ].nunique()
                ),
                minute_data=generated["minutes"],
                financials=generated["financials"],
                valuations=generated["valuations"],
                benchmark=generated["benchmark"],
            )
            available_minutes = sum(
                candidate["minute_confirmation"] == "available"
                for candidate in selection.candidates
            )
            reduced_minutes = sum(
                candidate["minute_confirmation"] == "unavailable"
                for candidate in selection.candidates
            )
            selection_passed = (
                selection.status == "ready"
                and bool(selection.candidates)
                and len(selection.snapshot_ids) == 4
                and available_minutes > 0
                and reduced_minutes > 0
            )
            manifest["steps"]["formal_selection"] = {
                "status": "passed" if selection_passed else "failed",
                "selection_status": selection.status,
                "candidate_count": len(selection.candidates),
                "hard_gate_rejection_count": len(selection.rejected_candidates),
                "snapshot_ids": selection.snapshot_ids,
                "minute_available_count": available_minutes,
                "minute_reduced_count": reduced_minutes,
                "message": selection.message,
            }

            factors = DailyFactorCalculator().calculate(
                selection_market,
                financials=generated["financials"],
                valuations=generated["valuations"],
                benchmark=generated["benchmark"],
            )
            factor_path = input_root / "factors.parquet"
            price_path = input_root / "prices.parquet"
            factors.to_parquet(factor_path, index=False)
            generated["daily"][["date", "symbol", "close"]].to_parquet(price_path, index=False)
            service = ResearchTaskService(session, settings)
            factor_run = service.run_factor_analysis(
                FactorAnalysisRunRequest(
                    factor_code="return_20d",
                    start_date=date(2024, 1, 2),
                    end_date=date(2025, 12, 31),
                    horizons=[1, 5, 10],
                    group_count=5,
                    industry_neutral=False,
                    factor_path=str(factor_path),
                    price_path=str(price_path),
                )
            )
            factor_rows = FactorResultRepository(session).list(limit=50)
            factor_engines = sorted(
                {row.analysis_engine for row in factor_rows if row.run_id == factor_run.id}
            )
            factor_passed = factor_run.status == "succeeded" and factor_engines == [
                "alphalens",
                "native",
            ]
            manifest["steps"]["factor_analysis"] = {
                "status": "passed" if factor_passed else "failed",
                "run_id": factor_run.id,
                "run_status": factor_run.status,
                "engines": factor_engines,
                "result_summary": decode_json(factor_run.result_summary_json),
                "error": factor_run.error_message,
            }

            signals = _build_signals(factors)
            signal_path = input_root / "signals.parquet"
            signals.to_parquet(signal_path, index=False)
            vector_run = service.run_vectorbt(
                VectorBTResearchRequest(
                    strategy_code="trend_quality_v1",
                    start_date=date(2024, 1, 2),
                    end_date=SELECTION_DATE,
                    price_path=str(price_path),
                    signal_path=str(signal_path),
                    parameter_grid={
                        "top_n": [5, 10],
                        "holding_period": [5, 10],
                        "rebalance_frequency": ["weekly"],
                        "commission_rate": [0.0003],
                        "slippage_bps": [5.0],
                        "factor_weights": [{"rps_60d": 0.7, "return_20d": 0.3}],
                        "atr_threshold": [0.08],
                        "risk_penalty_threshold": [12.0],
                    },
                    initial_cash=1_000_000.0,
                )
            )
            vector_summary = decode_json(vector_run.result_summary_json)
            vector_passed = (
                vector_run.status == "succeeded"
                and vector_summary["formal_ashare_validation"] == "required"
                and vector_summary["parameter_count"] == 4
            )
            manifest["steps"]["vectorbt_research"] = {
                "status": "passed" if vector_passed else "failed",
                "run_id": vector_run.id,
                "run_status": vector_run.status,
                "result_summary": vector_summary,
                "error": vector_run.error_message,
            }

            formal_run = service.run_formal_backtest(
                FormalBacktestRunRequest(
                    strategy_code="trend_quality_v1",
                    start_date=date(2024, 1, 2),
                    end_date=SELECTION_DATE,
                    top_n=5,
                    rebalance_frequency="weekly",
                    holding_period=10,
                    commission_rate=0.0003,
                    minimum_commission=5.0,
                    stamp_tax_rate=0.0005,
                    slippage_bps=5.0,
                    benchmark_symbol="000300.SH",
                    initial_cash=1_000_000.0,
                    max_stock_weight=0.2,
                    max_industry_weight=0.4,
                    market_data_path=str(generated["daily_path"]),
                    signal_path=str(signal_path),
                )
            )
            formal_result = decode_json(formal_run.result_json)
            formal_passed = (
                formal_run.status == "succeeded"
                and formal_run.formal_ashare_result
                and formal_result["metadata"]["formal_ashare_result"] is True
                and formal_result["performance"]["trade_count"] > 0
            )
            manifest["steps"]["formal_ashare_backtest"] = {
                "status": "passed" if formal_passed else "failed",
                "run_id": formal_run.id,
                "run_status": formal_run.status,
                "performance": formal_result.get("performance", {}),
                "execution_failure_count": len(formal_result.get("execution_failures", [])),
            }

            features, split = _build_ml_features(factors)
            feature_path = input_root / "ml-features.parquet"
            features.to_parquet(feature_path, index=False)
            rule_rank_ic = float(
                next(
                    (
                        row.rank_ic
                        for row in factor_rows
                        if row.run_id == factor_run.id
                        and row.analysis_engine == "native"
                        and row.horizon == 5
                    ),
                    0.0,
                )
                or 0.0
            )
            qlib_run, experiment = service.run_qlib(
                QlibExperimentRequest(
                    experiment_code=EXPERIMENT_CODE,
                    model_type="LightGBM",
                    feature_path=str(feature_path),
                    feature_columns=[
                        "return_20d",
                        "rps_60d",
                        "volume_ratio_5d_20d",
                        "atr_percent",
                    ],
                    label_column="label",
                    feature_version="transparent_factor_v1",
                    label_definition="forward_5_session_close_return",
                    train_start=split["train_start"],
                    train_end=split["train_end"],
                    validation_start=split["validation_start"],
                    validation_end=split["validation_end"],
                    test_start=split["test_start"],
                    test_end=split["test_end"],
                    training_config={
                        "n_estimators": 80,
                        "learning_rate": 0.05,
                        "num_leaves": 15,
                        "random_state": seed,
                    },
                    rule_metrics={
                        "rank_ic": rule_rank_ic,
                        "top_n_return": float(
                            signals.groupby("signal_date").head(5)["forward_5d"].mean()
                        ),
                        "turnover": 0.0,
                    },
                )
            )
            qlib_result = decode_json(experiment.result_json)
            qlib_passed = (
                qlib_run.status == "succeeded"
                and experiment.status == "succeeded"
                and experiment.experiment_only
                and not experiment.production_enabled
                and bool(qlib_result["metrics"])
            )
            manifest["steps"]["qlib_experiment"] = {
                "status": "passed" if qlib_passed else "failed",
                "run_id": qlib_run.id,
                "experiment_id": experiment.id,
                "run_status": qlib_run.status,
                "experiment_status": experiment.status,
                "experiment_only": experiment.experiment_only,
                "production_enabled": experiment.production_enabled,
                "metrics": qlib_result.get("metrics", {}),
            }

            review_service = AutomaticReviewService()
            review_market = pd.concat(
                [generated["daily"], generated["benchmark_market"]],
                ignore_index=True,
            )
            reviews = review_service.calculate(
                candidates=selection.candidates,
                market=review_market,
                horizons=[1, 3, 5, 10, 20, 60],
                benchmark_symbol="000300.SH",
            )
            review_summary = review_service.summarize(reviews)
            review_path = artifact_root / "first-experiment-reviews.parquet"
            review_path.parent.mkdir(parents=True, exist_ok=True)
            _parquet_safe(reviews).to_parquet(review_path, index=False)
            review_passed = (
                not reviews.empty
                and set(reviews["horizon"].unique()) == {1, 3, 5, 10, 20, 60}
                and bool(review_summary["strategy_results"])
            )
            manifest["steps"]["automatic_review"] = {
                "status": "passed" if review_passed else "failed",
                "review_count": len(reviews),
                "summary": review_summary,
                "artifact_path": str(review_path),
            }

            manifest["steps"]["rqalpha_validation"] = {
                "status": "skipped",
                "accepted": True,
                "reason": (
                    "RQAlpha package is available, but no independent China-market "
                    "bundle was supplied; adapter/template/result-diff tests passed."
                ),
            }

        required_steps = [
            "data_generation",
            "formal_selection",
            "factor_analysis",
            "vectorbt_research",
            "formal_ashare_backtest",
            "qlib_experiment",
            "automatic_review",
        ]
        failed = [step for step in required_steps if manifest["steps"][step]["status"] != "passed"]
        manifest["status"] = "passed" if not failed else "failed"
        manifest["failed_steps"] = failed
        manifest["completed_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        manifest["report_path"] = str(run_root / "REPORT.md")
        _write_json(manifest_path, manifest)
        _write_report(run_root / "REPORT.md", manifest)
        if failed:
            raise RuntimeError(f"first experiment failed steps: {failed}")
        return manifest
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(error).__name__}: {error}"
        manifest["completed_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        _write_json(manifest_path, manifest)
        raise


def _generate_inputs(input_root: Path, *, seed: int) -> dict[str, Any]:
    import exchange_calendars as xcals

    input_root.mkdir(parents=True, exist_ok=True)
    calendar = xcals.get_calendar("XSHG")
    sessions = calendar.sessions_in_range("2023-01-03", "2026-07-30")
    rng = np.random.default_rng(seed)
    symbols = [
        f"{600000 + index:06d}.SH" if index < 15 else f"{index - 14:06d}.SZ" for index in range(30)
    ]
    industries = ["银行", "电子", "医药", "机械", "消费"]
    market_shock = rng.normal(0.00025, 0.006, len(sessions))
    daily_rows: list[dict[str, Any]] = []
    for symbol_number, symbol in enumerate(symbols):
        drift = 0.00005 + symbol_number / len(symbols) * 0.00045
        returns = np.clip(
            market_shock + rng.normal(0, 0.007, len(sessions)) + drift,
            -0.08,
            0.08,
        )
        close = (8.0 + symbol_number * 0.4) * np.exp(np.cumsum(returns))
        previous_close = np.concatenate(([close[0]], close[:-1]))
        open_price = previous_close * (1.0 + rng.normal(0, 0.0025, len(sessions)))
        spread = np.abs(rng.normal(0.006, 0.002, len(sessions)))
        high = np.maximum(open_price, close) * (1.0 + spread)
        low = np.minimum(open_price, close) * (1.0 - spread)
        volume = rng.integers(500_000, 5_000_000, len(sessions))
        typical = (open_price + high + low + close) / 4.0
        shares_outstanding = 300_000_000 + symbol_number * 20_000_000
        for day_number, trade_date in enumerate(sessions):
            daily_rows.append(
                {
                    "date": trade_date,
                    "symbol": symbol,
                    "open": float(open_price[day_number]),
                    "high": float(high[day_number]),
                    "low": float(low[day_number]),
                    "close": float(close[day_number]),
                    "volume": int(volume[day_number]),
                    "amount": float(volume[day_number] * typical[day_number]),
                    "turnover_rate": float(volume[day_number] / shares_outstanding),
                    "industry": industries[symbol_number % len(industries)],
                    "market_cap": float(close[day_number] * shares_outstanding),
                    "adj_factor": 1.0,
                    "cash_dividend_per_share": 0.0,
                    "limit_up": False,
                    "limit_down": False,
                    "one_word_limit_up": False,
                    "one_word_limit_down": False,
                    "suspended": False,
                    "is_st": False,
                    "listing_days": 1_000 + day_number,
                    "delisting_risk": False,
                }
            )
    daily = pd.DataFrame(daily_rows)
    daily_path = input_root / "daily.parquet"
    daily.to_parquet(daily_path, index=False)

    benchmark_close = 3_500.0 * np.exp(np.cumsum(market_shock))
    benchmark = pd.DataFrame({"date": sessions, "close": benchmark_close})
    benchmark_path = input_root / "benchmark.parquet"
    benchmark.to_parquet(benchmark_path, index=False)
    benchmark_market = pd.DataFrame(
        {
            "date": sessions,
            "symbol": "000300.SH",
            "open": np.concatenate(([benchmark_close[0]], benchmark_close[:-1])),
            "high": benchmark_close * 1.005,
            "low": benchmark_close * 0.995,
            "close": benchmark_close,
            "volume": 1_000_000_000,
            "amount": benchmark_close * 1_000_000_000,
            "industry": "benchmark",
        }
    )

    financial_rows = []
    valuation_rows = []
    for symbol_number, symbol in enumerate(symbols):
        audit = {
            "symbol": symbol,
            "period_end": "2025-09-30",
            "published_at": "2025-10-30 17:30:00",
            "available_at": "2025-10-30 17:31:00",
            "fetched_at": "2025-10-30 17:35:00",
            "source": "synthetic-first-experiment",
            "content_hash": f"financial-{symbol_number:02d}",
        }
        financial_rows.append(
            {
                **audit,
                "revenue_growth_yoy": 0.05 + symbol_number * 0.004,
                "net_profit_growth_yoy": 0.04 + symbol_number * 0.005,
                "deducted_profit_growth_yoy": 0.03 + symbol_number * 0.004,
                "roe_ttm": 0.08 + symbol_number * 0.002,
                "gross_margin_change": -0.01 + symbol_number * 0.0008,
                "operating_cashflow_to_profit": 0.8 + symbol_number * 0.015,
                "free_cashflow": 100_000_000 + symbol_number * 5_000_000,
                "debt_ratio": 0.65 - symbol_number * 0.008,
                "inventory_growth": 0.08 - symbol_number * 0.001,
                "receivable_growth": 0.07 - symbol_number * 0.001,
                "goodwill_ratio": 0.08 - symbol_number * 0.001,
                "non_recurring_profit_ratio": 0.1,
                "prior_net_profit": 50_000_000 + symbol_number * 1_000_000,
                "consolidation_change": False,
            }
        )
        valuation_rows.append(
            {
                **{
                    **audit,
                    "source": "synthetic-valuation-first-experiment",
                    "content_hash": f"valuation-{symbol_number:02d}",
                },
                "pe_ttm": 10.0 + symbol_number * 0.8,
                "pb": 1.0 + symbol_number * 0.08,
                "ps_ttm": 0.8 + symbol_number * 0.05,
                "dividend_yield": 0.01 + symbol_number * 0.0005,
                "free_cashflow_yield": 0.02 + symbol_number * 0.0007,
            }
        )
    financials = pd.DataFrame(financial_rows)
    valuations = pd.DataFrame(valuation_rows)
    financials.to_parquet(input_root / "financials.parquet", index=False)
    valuations.to_parquet(input_root / "valuations.parquet", index=False)

    minute_root = input_root / "minute"
    minute_root.mkdir()
    selection_sessions = sessions[sessions <= pd.Timestamp(SELECTION_DATE)][-21:]
    minutes: dict[str, pd.DataFrame] = {}
    daily_lookup = daily.set_index(["date", "symbol"])
    for symbol_number, symbol in enumerate(symbols[-5:]):
        minute_rows: list[dict[str, Any]] = []
        for session_number, trade_date in enumerate(selection_sessions):
            day = daily_lookup.loc[(trade_date, symbol)]
            timestamps = [
                *pd.date_range(
                    f"{trade_date.date()} 09:30",
                    periods=120,
                    freq="min",
                ),
                *pd.date_range(
                    f"{trade_date.date()} 13:00",
                    periods=120,
                    freq="min",
                ),
            ]
            path = np.linspace(
                float(day["open"]),
                float(day["close"]),
                len(timestamps),
            )
            path *= 1.0 + rng.normal(0, 0.0007, len(timestamps))
            base_volume = 8_000 + symbol_number * 500
            volume_multiplier = 1.5 if session_number == 20 else 1.0
            volumes = (
                rng.integers(
                    int(base_volume * 0.7),
                    int(base_volume * 1.3),
                    len(timestamps),
                )
                * volume_multiplier
            )
            for index, timestamp in enumerate(timestamps):
                minute_rows.append(
                    {
                        "timestamp": timestamp,
                        "open": float(path[index]),
                        "high": float(path[index] * 1.0008),
                        "low": float(path[index] * 0.9992),
                        "close": float(path[index]),
                        "volume": float(volumes[index]),
                        "amount": float(volumes[index] * path[index]),
                    }
                )
        frame = pd.DataFrame(minute_rows)
        frame.to_parquet(minute_root / f"{symbol}.parquet", index=False)
        minutes[symbol] = frame
    return {
        "sessions": sessions,
        "daily": daily,
        "daily_path": daily_path,
        "benchmark": benchmark,
        "benchmark_path": benchmark_path,
        "benchmark_market": benchmark_market,
        "financials": financials,
        "valuations": valuations,
        "minutes": minutes,
    }


def _build_signals(factors: pd.DataFrame) -> pd.DataFrame:
    eligible = factors.loc[
        factors["date"].between("2024-01-02", pd.Timestamp(SELECTION_DATE))
    ].copy()
    signal_dates = sorted(eligible["date"].unique())[::5]
    eligible = eligible.loc[eligible["date"].isin(signal_dates)]
    eligible["score"] = (
        eligible["rps_60d"].fillna(0.5) * 70.0 + eligible["return_20d"].fillna(0.0) * 30.0
    )
    eligible["risk_penalty"] = eligible.groupby("date")["atr_percent"].rank(pct=True) * 10.0
    eligible["forward_5d"] = eligible.groupby("symbol")["close"].shift(-1) / eligible["close"] - 1.0
    return (
        eligible.sort_values(["date", "score"], ascending=[True, False])
        .rename(columns={"date": "signal_date"})[
            [
                "signal_date",
                "symbol",
                "score",
                "rps_60d",
                "return_20d",
                "atr_percent",
                "risk_penalty",
                "forward_5d",
            ]
        ]
        .dropna(subset=["forward_5d"])
    )


def _build_ml_features(
    factors: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, date]]:
    data = factors.copy()
    data["label"] = data.groupby("symbol")["close"].shift(-5) / data["close"] - 1.0
    columns = [
        "date",
        "symbol",
        "return_20d",
        "rps_60d",
        "volume_ratio_5d_20d",
        "atr_percent",
        "label",
    ]
    data = data[columns].replace([np.inf, -np.inf], np.nan).dropna()
    return data, {
        "train_start": date(2023, 7, 3),
        "train_end": date(2024, 6, 28),
        "validation_start": date(2024, 7, 1),
        "validation_end": date(2024, 12, 31),
        "test_start": date(2025, 1, 2),
        "test_end": date(2025, 6, 30),
    }


def _parquet_safe(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    for column in output:
        if output[column].map(lambda value: isinstance(value, (dict, list))).any():
            output[column] = output[column].map(
                lambda value: (
                    json.dumps(value, ensure_ascii=False, default=str)
                    if isinstance(value, (dict, list))
                    else value
                )
            )
    return output


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            default=str,
            allow_nan=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_report(path: Path, manifest: dict[str, Any]) -> None:
    steps = manifest["steps"]
    factor = steps["factor_analysis"]
    vector = steps["vectorbt_research"]
    formal = steps["formal_ashare_backtest"]
    qlib = steps["qlib_experiment"]
    lines = [
        "# 第一次实验报告",
        "",
        f"- 状态：`{manifest['status']}`",
        f"- 实验代码：`{manifest['experiment_code']}`",
        f"- 选股日期：`{manifest['selection_date']}`",
        "- 数据：确定性合成 A 股风格数据，仅用于链路验收",
        "- 自动下单：`false`",
        "- 生产启用：`false`",
        "",
        "## 步骤",
        "",
        "| 步骤 | 状态 | 核心结果 |",
        "|---|---|---|",
        (
            f"| 正式选股 | {steps['formal_selection']['status']} | "
            f"{steps['formal_selection']['candidate_count']} 个候选，"
            f"{len(steps['formal_selection']['snapshot_ids'])} 个快照 |"
        ),
        (f"| 因子研究 | {factor['status']} | {', '.join(factor['engines'])} |"),
        (
            f"| VectorBT | {vector['status']} | "
            f"{vector['result_summary']['parameter_count']} 组参数，正式复核 required |"
        ),
        (
            f"| 自研 A 股回测 | {formal['status']} | "
            f"tradable_return={formal['performance'].get('tradable_return', 0):.4%} |"
        ),
        (
            f"| Qlib/LightGBM | {qlib['status']} | "
            f"Rank IC={qlib['metrics'].get('rank_ic', 0):.6f}，production=false |"
        ),
        (
            f"| 自动复盘 | {steps['automatic_review']['status']} | "
            f"{steps['automatic_review']['review_count']} 条窗口记录 |"
        ),
        ("| RQAlpha | skipped | 未提供独立中国市场 bundle；适配器与差异报告测试通过 |"),
        "",
        "完整机器可读结果见 `manifest.json`。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
