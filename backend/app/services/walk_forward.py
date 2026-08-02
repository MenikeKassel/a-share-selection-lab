"""Point-in-time trend-quality walk-forward orchestration.

This module is deliberately the only business-facing entry point for the
walk-forward experiment.  Optional research engines are called through their
adapters and the formal result is always produced by ``AshareDailyExecutionEngine``.
When the independent 2018--2025 snapshot is absent or fails its audit, the
run is persisted as ``blocked`` instead of fabricating a result.
"""

from __future__ import annotations

import gc
import hashlib
import json
import math
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.adapters.io import read_tabular
from app.adapters.vectorbt.adapter import VectorBTResearchAdapter
from app.api.schemas import WalkForwardRunRequest
from app.core.config import Settings
from app.data.history import join_historical_state
from app.data.snapshots import (
    SnapshotManifestError,
    audit_snapshot_files,
    validate_point_in_time_frame,
    validate_snapshot_manifest,
)
from app.db.repositories import WalkForwardRepository
from app.domain.protocols import BacktestRequest, FactorAnalysisRequest
from app.execution.ashare_daily import AshareDailyExecutionEngine
from app.research.factor_analysis import NativeFactorAnalysisEngine
from app.research.walk_forward import (
    WalkForwardSplit,
    generate_annual_walk_forward_splits,
)


class WalkForwardSnapshotError(ValueError):
    """The imported snapshot is missing or failed an audit gate."""


class WalkForwardTaskService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repository = WalkForwardRepository(session)

    def run(self, payload: WalkForwardRunRequest) -> Any:
        existing = self.repository.get_by_code(payload.experiment_code)
        if existing is not None:
            # Experiment codes are immutable run identities; a retry must read
            # the original record instead of overwriting its artifact/result.
            return existing
        manifest_path = self._restricted_data_path(payload.snapshot_manifest_path)
        snapshot_version = self._manifest_version(manifest_path) if manifest_path else "missing"
        record = self.repository.create(
            experiment_code=payload.experiment_code,
            strategy_code=payload.strategy_code,
            data_snapshot_version=snapshot_version,
            start_date=payload.start_date,
            end_date=payload.end_date,
            config=payload.model_dump(mode="json"),
        )
        if manifest_path is None:
            report = self._blocked_report(
                payload,
                "independent 2018-2025 snapshot manifest was not found; import it before running",
            )
            artifact = self._write_artifacts(payload.experiment_code, report, None)
            return self.repository.finish(
                record,
                status="blocked",
                lifecycle_status="experimental",
                result=report,
                artifact_path=str(artifact),
                error_message=report["error"],
            )
        try:
            result = self._run_from_manifest(payload, manifest_path)
            rejected = result.pop("_rejected", None)
            artifact = self._write_artifacts(
                payload.experiment_code,
                result,
                result.pop("_signals", None),
                rejected,
            )
            lifecycle = (
                "validated" if bool(result.get("gates", {}).get("all_passed")) else "experimental"
            )
            return self.repository.finish(
                record,
                status="succeeded",
                lifecycle_status=lifecycle,
                result=result,
                artifact_path=str(artifact),
            )
        except (WalkForwardSnapshotError, SnapshotManifestError) as error:
            report = self._blocked_report(payload, str(error))
            artifact = self._write_artifacts(payload.experiment_code, report, None)
            return self.repository.finish(
                record,
                status="blocked",
                lifecycle_status="experimental",
                result=report,
                artifact_path=str(artifact),
                error_message=str(error),
            )
        except Exception as error:  # persisted failure is part of the audit trail
            message = f"{type(error).__name__}: {error}"
            report = self._blocked_report(payload, message)
            artifact = self._write_artifacts(payload.experiment_code, report, None)
            return self.repository.finish(
                record,
                status="failed",
                lifecycle_status="experimental",
                result=report,
                artifact_path=str(artifact),
                error_message=message,
            )

    def _run_from_manifest(
        self,
        payload: WalkForwardRunRequest,
        manifest_path: Path,
    ) -> dict[str, Any]:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        snapshot = validate_snapshot_manifest(
            manifest_path,
            minimum_coverage_ratio=self.settings.min_daily_coverage_ratio,
        )
        snapshot_audit = audit_snapshot_files(
            snapshot,
            minimum_coverage_ratio=self.settings.min_daily_coverage_ratio,
        )
        if pd.Timestamp(snapshot_audit["daily_min_date"]) > pd.Timestamp(
            "2018-01-01"
        ) or pd.Timestamp(snapshot_audit["daily_max_date"]) < pd.Timestamp("2025-12-31"):
            raise WalkForwardSnapshotError(
                "snapshot daily data must cover the complete 2018-2025 validation range"
            )
        manifest = snapshot.metadata
        daily = self._read_manifest_frame(manifest, manifest_path, "daily")
        if daily is None:
            raise WalkForwardSnapshotError("snapshot manifest is missing daily data")
        benchmark = self._read_manifest_frame(manifest, manifest_path, "benchmark")
        financials = self._read_manifest_frame(manifest, manifest_path, "financials")
        valuation_path = snapshot.file("valuations")
        assert valuation_path is not None
        valuations: pd.DataFrame | Path = (
            valuation_path
            if valuation_path.suffix.lower() == ".parquet"
            else read_tabular(valuation_path)
        )
        industry = self._read_manifest_frame(
            manifest, manifest_path, "industry_rps", required=False
        )
        if industry is None:
            candidate_industry = self._read_manifest_frame(
                manifest, manifest_path, "industry", required=False
            )
            if candidate_industry is not None and {"date", "industry"}.issubset(
                candidate_industry.columns
            ):
                industry = candidate_industry
        state_history = self._read_manifest_frame(
            manifest, manifest_path, "state_history", required=False
        )
        if state_history is None or "industry" not in state_history.columns:
            raise WalkForwardSnapshotError(
                "snapshot manifest is missing point-in-time historical industry state"
            )
        if financials is not None:
            validate_point_in_time_frame(financials, name="financials")
        if isinstance(valuations, pd.DataFrame):
            validate_point_in_time_frame(valuations, name="valuations")
        # The generator is intentionally imported at call time so older installs
        # still boot and report a clear blocked run while being upgraded.
        try:
            from app.research.historical_signals import HistoricalSignalGenerator
        except ImportError as error:  # pragma: no cover - only during partial upgrades
            raise WalkForwardSnapshotError(
                "historical signal generator is not installed"
            ) from error

        signals, rejected, audit = self._generate_historical_signals(
            HistoricalSignalGenerator(),
            daily=daily,
            financials=financials,
            valuations=valuations,
            benchmark=benchmark,
            industry=industry,
            state_history=state_history,
            start_date=payload.start_date,
            end_date=payload.end_date,
            data_snapshot_version=str(
                manifest.get("snapshot_id", manifest.get("version", "unknown"))
            ),
        )
        if signals.empty:
            raise WalkForwardSnapshotError(
                "historical signal generator produced no eligible signals"
            )
        suspensions = self._read_manifest_frame(
            manifest, manifest_path, "suspensions", required=False
        )
        execution_daily = _date_filter(daily, "date", payload.start_date, payload.end_date)
        prices = self._prepare_execution_prices(
            execution_daily,
            state_history=state_history,
            suspensions=suspensions,
        )
        splits = generate_annual_walk_forward_splits(
            first_train_year=2018,
            final_test_year=2025,
            train_years=3,
            validation_years=1,
            test_years=1,
        )
        windows: list[dict[str, Any]] = []
        for split in splits:
            windows.append(self._run_split(payload, split, prices, signals, benchmark))
        aggregate = self._aggregate_windows(windows, payload.max_drawdown_limit)
        factor_results = self._factor_results(signals, prices, splits, payload.factor_horizons)
        return {
            "experiment_code": payload.experiment_code,
            "strategy_code": payload.strategy_code,
            "data_snapshot_version": manifest.get(
                "snapshot_id", manifest.get("version", "unknown")
            ),
            "research_engine": "vectorbt",
            "formal_ashare_validation": "required",
            "split_count": len(splits),
            "splits": windows,
            "aggregate_metrics": aggregate["metrics"],
            "gates": aggregate["gates"],
            "lifecycle_status": "validated" if aggregate["gates"]["all_passed"] else "experimental",
            "production_enabled": False,
            "factor_results": factor_results,
            "signal_audit": audit,
            "snapshot_audit": snapshot_audit,
            "data_source_summary": self._data_source_summary(
                manifest,
                manifest_path=manifest_path,
                snapshot_audit=snapshot_audit,
            ),
            "_signals": signals,
            "_rejected": rejected,
        }

    @staticmethod
    def _generate_historical_signals(
        generator: Any,
        *,
        daily: pd.DataFrame,
        financials: pd.DataFrame | None,
        valuations: pd.DataFrame | Path | None,
        benchmark: pd.DataFrame | None,
        industry: pd.DataFrame | None,
        state_history: pd.DataFrame,
        start_date: date,
        end_date: date,
        data_snapshot_version: str,
        warmup_trading_days: int = 400,
    ) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
        """Replay one calendar year at a time with an explicit warm-up window."""

        daily_dates = pd.to_datetime(daily["date"], format="mixed", errors="raise").dt.normalize()
        dates = pd.DatetimeIndex(daily_dates.drop_duplicates().sort_values())
        signal_parts: list[pd.DataFrame] = []
        rejected_parts: list[pd.DataFrame] = []
        chunk_audits: list[dict[str, Any]] = []
        for year in range(start_date.year, end_date.year + 1):
            output_start = max(start_date, date(year, 1, 1))
            output_end = min(end_date, date(year, 12, 31))
            if output_start > output_end:
                continue
            first_output = int(dates.searchsorted(pd.Timestamp(output_start), side="left"))
            warmup_start_index = max(0, first_output - warmup_trading_days)
            if first_output - warmup_start_index < warmup_trading_days:
                raise WalkForwardSnapshotError(
                    f"historical signal chunk {year} has fewer than "
                    f"{warmup_trading_days} trading days of warm-up data"
                )
            chunk_start = dates[warmup_start_index]
            chunk = daily.loc[
                (daily_dates >= chunk_start) & (daily_dates <= pd.Timestamp(output_end))
            ].copy()
            symbols = set(chunk["symbol"].astype(str))
            chunk_financials = _slice_point_in_time_input(
                financials,
                symbols=symbols,
                start=pd.Timestamp(chunk_start),
                end=pd.Timestamp(output_end),
                cutoff=generator.information_cutoff,
            )
            chunk_valuations = _slice_point_in_time_input(
                valuations,
                symbols=symbols,
                start=pd.Timestamp(chunk_start),
                end=pd.Timestamp(output_end),
                cutoff=generator.information_cutoff,
            )
            chunk_benchmark = _slice_date_input(
                benchmark,
                start=pd.Timestamp(chunk_start),
                end=pd.Timestamp(output_end),
            )
            chunk_industry = _slice_research_input(
                industry,
                symbols=symbols,
                start=pd.Timestamp(chunk_start),
                end=pd.Timestamp(output_end),
            )
            chunk_state = _slice_effective_state(
                state_history,
                symbols=symbols,
                start=pd.Timestamp(chunk_start),
                end=pd.Timestamp(output_end),
            )
            generated = generator.generate(
                daily=chunk,
                financials=chunk_financials,
                valuations=chunk_valuations,
                benchmark=chunk_benchmark,
                industry_rps=chunk_industry,
                state_history=chunk_state,
                start_date=output_start,
                end_date=output_end,
                data_snapshot_version=data_snapshot_version,
            )
            if not generated.signals.empty:
                signal_parts.append(generated.signals)
            if not generated.rejected.empty:
                rejected_parts.append(generated.rejected)
            chunk_audits.append(
                {
                    "year": year,
                    "warmup_start": chunk_start.date().isoformat(),
                    "output_start": output_start.isoformat(),
                    "output_end": output_end.isoformat(),
                    "warmup_trading_days": first_output - warmup_start_index,
                    "daily_row_count": len(chunk),
                    "financial_row_count": _frame_length(chunk_financials),
                    "valuation_row_count": _frame_length(chunk_valuations),
                    "state_row_count": _frame_length(chunk_state),
                    "signal_row_count": len(generated.signals),
                    "rejected_row_count": len(generated.rejected),
                }
            )
            del (
                chunk,
                chunk_financials,
                chunk_valuations,
                chunk_benchmark,
                chunk_industry,
                chunk_state,
                generated,
            )
            gc.collect()
        signals = pd.concat(signal_parts, ignore_index=True) if signal_parts else pd.DataFrame()
        rejected = (
            pd.concat(rejected_parts, ignore_index=True) if rejected_parts else pd.DataFrame()
        )
        if not signals.empty:
            signals = signals.sort_values(["signal_date", "symbol"]).reset_index(drop=True)
        if not rejected.empty:
            rejected = rejected.sort_values(["signal_date", "symbol"]).reset_index(drop=True)
        audit = {
            "snapshot_version": data_snapshot_version,
            "strategy_code": generator.strategy_code,
            "strategy_version": generator.strategy_version,
            "factor_version": generator.factor_version,
            "point_in_time_cutoff": generator.information_cutoff.isoformat(),
            "daily_min_date": dates.min().date().isoformat(),
            "daily_max_date": dates.max().date().isoformat(),
            "daily_row_count": len(daily),
            "daily_symbol_count": int(daily["symbol"].astype(str).nunique()),
            "signal_row_count": len(signals),
            "rejected_row_count": len(rejected),
            "warmup_trading_days": warmup_trading_days,
            "chunk_count": len(chunk_audits),
            "chunks": chunk_audits,
            "production_enabled": False,
        }
        return signals, rejected, audit

    @staticmethod
    def _prepare_execution_prices(
        daily: pd.DataFrame,
        *,
        state_history: pd.DataFrame | None,
        suspensions: pd.DataFrame | None,
    ) -> pd.DataFrame:
        """Build the formal engine's raw-price view without future state leakage."""

        execution_columns = [
            column
            for column in (
                "date",
                "symbol",
                "open",
                "close",
                "volume",
                "adj_factor",
                "cash_dividend_per_share",
                "industry",
                "is_st",
                "delisting_risk",
                "suspended",
                "limit_up",
                "limit_down",
                "one_word_limit_up",
                "one_word_limit_down",
            )
            if column in daily
        ]
        prices = daily.loc[:, execution_columns].copy()
        prices["date"] = pd.to_datetime(
            prices["date"], format="mixed", errors="raise"
        ).dt.normalize()
        prices["symbol"] = prices["symbol"].astype(str)
        if state_history is not None and not state_history.empty:
            prices = join_historical_state(prices, state_history)
        if "list_date" in prices:
            list_dates = pd.to_datetime(prices["list_date"], format="mixed", errors="coerce")
            prices["listing_days"] = (prices["date"] - list_dates).dt.days.astype(float)
        if suspensions is not None and not suspensions.empty:
            required = {"date", "symbol"}
            if required.issubset(suspensions.columns):
                blocked = suspensions.loc[:, ["date", "symbol"]].copy()
                blocked["date"] = pd.to_datetime(
                    blocked["date"], format="mixed", errors="coerce"
                ).dt.normalize()
                blocked["symbol"] = blocked["symbol"].astype(str)
                blocked["_suspended_external"] = True
                prices = prices.merge(blocked.drop_duplicates(), on=["date", "symbol"], how="left")
                existing_suspension = (
                    prices["suspended"]
                    if "suspended" in prices
                    else pd.Series(False, index=prices.index)
                )
                prices["suspended"] = existing_suspension.fillna(False).astype(bool) | prices[
                    "_suspended_external"
                ].fillna(False).astype(bool)
                prices = prices.drop(columns=["_suspended_external"])
        for column, default in (
            ("industry", "unknown"),
            ("is_st", False),
            ("delisting_risk", False),
            ("suspended", False),
        ):
            if column not in prices:
                prices[column] = default
            else:
                prices[column] = prices[column].fillna(default)
        result_columns = [
            column
            for column in execution_columns
            if column in prices
        ]
        for column in ("industry", "is_st", "delisting_risk", "suspended"):
            if column in prices and column not in result_columns:
                result_columns.append(column)
        return prices.loc[:, result_columns].sort_values(["date", "symbol"]).reset_index(
            drop=True
        )

    @staticmethod
    def _data_source_summary(
        manifest: dict[str, Any],
        *,
        manifest_path: Path,
        snapshot_audit: dict[str, Any],
    ) -> dict[str, Any]:
        """Expose safe provenance metadata while keeping local paths private."""

        source = manifest.get("source", {})
        supplement = manifest.get("supplement", {})
        return {
            "type": source.get("type", "imported_snapshot"),
            "read_only": bool(source.get("read_only", True)),
            "snapshot_id": manifest.get("snapshot_id", manifest.get("version")),
            "warmup_start": manifest.get("date_range", {}).get("warmup_start"),
            "validation_start": manifest.get("date_range", {}).get("validation_start"),
            "validation_end": manifest.get("date_range", {}).get("validation_end"),
            "manifest_name": manifest_path.name,
            "price_conventions": manifest.get("price_conventions", {}),
            "minute_confirmation": "unavailable",
            "data_confidence": "reduced",
            "supplement_provider": supplement.get("provider"),
            "supplement_required_ready": supplement.get("required_ready"),
            "daily_coverage_ratio": snapshot_audit.get("daily_coverage_ratio"),
        }

    @staticmethod
    def _run_split(
        payload: WalkForwardRunRequest,
        split: WalkForwardSplit,
        prices: pd.DataFrame,
        signals: pd.DataFrame,
        benchmark: pd.DataFrame | None,
    ) -> dict[str, Any]:
        train_signals = _date_filter(signals, "signal_date", split.train_start, split.train_end)
        validation_signals = _date_filter(
            signals, "signal_date", split.validation_start, split.validation_end
        )
        test_signals = _date_filter(signals, "signal_date", split.test_start, split.test_end)
        train_prices = _date_filter(prices, "date", split.train_start, split.train_end)
        validation_prices = _date_filter(
            prices, "date", split.validation_start, split.validation_end
        )
        test_prices = _date_filter(prices, "date", split.test_start, split.test_end)
        test_benchmark = (
            _date_filter(benchmark, "date", split.test_start, split.test_end)
            if benchmark is not None and not benchmark.empty
            else None
        )
        grid = payload.parameter_grid
        scan = _scan_parameters(train_prices, train_signals, grid, payload.initial_cash)
        eligible = [
            item
            for item in scan
            if float(item.get("cumulative_return", 0.0)) > 0
            and float(item.get("max_drawdown", 1.0)) <= payload.max_drawdown_limit
        ]
        if not eligible:
            empty = _empty_formal_metrics()
            return {
                "train_start": split.train_start.isoformat(),
                "train_end": split.train_end.isoformat(),
                "validation_start": split.validation_start.isoformat(),
                "validation_end": split.validation_end.isoformat(),
                "test_start": split.test_start.isoformat(),
                "test_end": split.test_end.isoformat(),
                "parameter_scan_count": len(scan),
                "selected_parameters": {},
                "selected_validation": {},
                "training_filter_count": 0,
                "test_metrics": empty,
                "stress_10bps": empty,
                "nearby_parameters": [],
                "failure_reason": "no training parameter passed cost and drawdown filters",
            }
        candidates = eligible
        validation_scan = _scan_parameters(
            validation_prices, validation_signals, grid, payload.initial_cash
        )
        by_key = {_parameter_key(item["parameter_set"]): item for item in validation_scan}
        selected = max(
            candidates,
            key=lambda item: (
                float(
                    by_key.get(_parameter_key(item["parameter_set"]), {}).get("sharpe", -math.inf)
                ),
                -float(
                    by_key.get(_parameter_key(item["parameter_set"]), {}).get(
                        "max_drawdown", math.inf
                    )
                ),
                -float(
                    by_key.get(_parameter_key(item["parameter_set"]), {}).get("turnover", math.inf)
                ),
            ),
        )
        selected_params = selected["parameter_set"]
        formal = _formal_run(
            payload,
            test_prices,
            test_signals,
            selected_params,
            test_benchmark,
        )
        stress = _formal_run(
            payload,
            test_prices,
            test_signals,
            {**selected_params, "slippage_bps": 10.0},
            test_benchmark,
        )
        nearby = []
        for item in scan:
            if item["parameter_set"] == selected_params:
                continue
            if _parameter_distance(item["parameter_set"], selected_params) == 1:
                nearby.append(
                    {
                        "parameter_set": item["parameter_set"],
                        "test": _formal_run(
                            payload,
                            test_prices,
                            test_signals,
                            item["parameter_set"],
                            test_benchmark,
                        ),
                    }
                )
        return {
            "train_start": split.train_start.isoformat(),
            "train_end": split.train_end.isoformat(),
            "validation_start": split.validation_start.isoformat(),
            "validation_end": split.validation_end.isoformat(),
            "test_start": split.test_start.isoformat(),
            "test_end": split.test_end.isoformat(),
            "parameter_scan_count": len(scan),
            "selected_parameters": selected_params,
            "selected_validation": by_key.get(_parameter_key(selected_params), {}),
            "training_filter_count": len(eligible),
            "test_metrics": formal,
            "stress_10bps": stress,
            "nearby_parameters": nearby,
        }

    @staticmethod
    def _factor_results(
        signals: pd.DataFrame,
        prices: pd.DataFrame,
        splits: list[WalkForwardSplit],
        horizons: list[int],
    ) -> list[dict[str, Any]]:
        factors = signals.copy()
        factors["date"] = pd.to_datetime(factors["signal_date"]).dt.normalize()
        rows: list[dict[str, Any]] = []
        codes = {
            "composite_score": "trend_quality_composite",
            "rps_60d": "rps_60d",
            "rps_120d": "rps_120d",
            "pa_score": "pa_score",
            "risk_penalty": "risk_penalty",
        }
        for split in splits:
            test_start, test_end = split.test_start, split.test_end
            subset = _date_filter(factors, "date", test_start, test_end)
            for column, code in codes.items():
                if column not in subset:
                    continue
                request = FactorAnalysisRequest(
                    factor_code=column,
                    start_date=test_start,
                    end_date=test_end,
                    horizons=horizons,
                )
                try:
                    result = NativeFactorAnalysisEngine().analyze_frames(request, subset, prices)
                    result_row = asdict(result)
                    result_row["factor_code"] = code
                    rows.append({"window": test_start.year, **result_row})
                except (ValueError, KeyError):
                    rows.append(
                        {
                            "window": test_start.year,
                            "factor_code": code,
                            "error": "insufficient_data",
                        }
                    )
        return rows

    @staticmethod
    def _aggregate_windows(windows: list[dict[str, Any]], max_dd_limit: float) -> dict[str, Any]:
        excess = [
            float(item["test_metrics"].get("tradable_excess_return", 0.0)) for item in windows
        ]
        rank_positive = [
            bool(item.get("test_metrics", {}).get("composite_rank_ic_positive", False))
            for item in windows
        ]
        stress_strategy_returns = [
            float(item["stress_10bps"].get("tradable_return", 0.0)) for item in windows
        ]
        stress_benchmark_returns = [
            float(item["stress_10bps"].get("benchmark_return", 0.0)) for item in windows
        ]
        stress_excess = _compound_returns(stress_strategy_returns) - _compound_returns(
            stress_benchmark_returns
        )
        closed_trades = sum(
            int(item["test_metrics"].get("closed_trade_count", 0)) for item in windows
        )
        nearby_values = [
            float(neighbor["test"].get("tradable_excess_return", 0.0))
            for item in windows
            for neighbor in item.get("nearby_parameters", [])
        ]
        positive_trade_pnls = [
            float(pnl)
            for item in windows
            for pnl in item["test_metrics"].get("positive_trade_pnls", [])
            if float(pnl) > 0
        ]
        positive_pnl = sum(positive_trade_pnls)
        top5 = sum(sorted(positive_trade_pnls, reverse=True)[:5])
        industry_pnl: dict[str, float] = {}
        for item in windows:
            for industry, pnl in item["test_metrics"].get("pnl_by_industry", {}).items():
                industry_pnl[str(industry)] = industry_pnl.get(str(industry), 0.0) + float(pnl)
        positive_industry_pnl = {key: value for key, value in industry_pnl.items() if value > 0}
        industry_positive = len(positive_industry_pnl)
        positive_industry_total = sum(positive_industry_pnl.values())
        max_industry_share = (
            max(positive_industry_pnl.values()) / positive_industry_total
            if positive_industry_total > 0
            else 1.0
        )
        merged_dd = _combined_oos_max_drawdown(windows)
        gates = {
            "positive_tradable_excess_3_of_4": sum(value > 0 for value in excess) >= 3,
            "median_oos_excess_positive": float(np.median(excess)) > 0 if excess else False,
            "composite_rank_ic_positive_3_of_4": sum(rank_positive) >= 3,
            "combined_oos_max_drawdown_within_limit": merged_dd <= max_dd_limit,
            "stress_10bps_excess_positive": stress_excess > 0,
            "adjacent_parameters_positive_60pct": (
                sum(value > 0 for value in nearby_values) / len(nearby_values) >= 0.6
                if nearby_values
                else False
            ),
            "at_least_200_closed_trades": closed_trades >= 200,
            "top_5_trades_contribution_within_35pct": (
                top5 / positive_pnl <= 0.35 if positive_pnl > 0 else False
            ),
            "at_least_3_positive_industries": industry_positive >= 3,
            "single_industry_contribution_within_40pct": max_industry_share <= 0.4,
        }
        return {
            "metrics": {
                "test_window_count": len(windows),
                "tradable_excess_returns": excess,
                "median_oos_excess_return": float(np.median(excess)) if excess else 0.0,
                "stress_10bps_excess_return": stress_excess,
                "closed_trade_count": closed_trades,
                "max_drawdown": merged_dd,
            },
            "gates": {**gates, "all_passed": all(gates.values())},
        }

    def _write_artifacts(
        self,
        experiment_code: str,
        result: dict[str, Any],
        signals: pd.DataFrame | None,
        rejected: pd.DataFrame | None = None,
    ) -> Path:
        root = self.settings.artifact_root / "walk-forward" / experiment_code
        root.mkdir(parents=True, exist_ok=True)
        output = {
            key: value for key, value in result.items() if key not in {"_signals", "_rejected"}
        }
        (root / "result.json").write_text(
            json.dumps(output, ensure_ascii=False, default=str, allow_nan=False, indent=2),
            encoding="utf-8",
        )
        if signals is not None:
            signals.to_parquet(root / "signals.parquet", index=False)
        if rejected is not None:
            rejected.to_parquet(root / "rejected.parquet", index=False)
        report = root / "REPORT.md"
        gates = output.get("gates", {})
        lines = [
            "# trend_quality_v1 Walk-forward report",
            "",
            f"- lifecycle: `{output.get('lifecycle_status', 'experimental')}`",
            "- production_enabled: `false`",
            f"- all gates passed: `{gates.get('all_passed', False)}`",
            "",
            "## Windows",
            "",
            "| train | validation | test | selected parameters | failure reason | "
            "tradable return | excess | max drawdown | closed trades |",
            "|---|---|---|---|---|---:|---:|---:|---:|",
        ]
        for split in output.get("splits", []):
            metrics = split.get("test_metrics", {})
            lines.append(
                (
                    "| {train} | {validation} | {test} | `{params}` | {reason} | "
                    "{return_:.4%} | {excess:.4%} | {drawdown:.4%} | {trades} |"
                ).format(
                    train=f"{split.get('train_start')}..{split.get('train_end')}",
                    validation=f"{split.get('validation_start')}..{split.get('validation_end')}",
                    test=f"{split.get('test_start')}..{split.get('test_end')}",
                    params=json.dumps(split.get("selected_parameters", {}), ensure_ascii=False),
                    reason=str(split.get("failure_reason", "") or ""),
                    return_=float(metrics.get("tradable_return", 0.0)),
                    excess=float(
                        metrics.get("tradable_excess_return", metrics.get("excess_return", 0.0))
                    ),
                    drawdown=float(metrics.get("max_drawdown", 0.0)),
                    trades=int(metrics.get("closed_trade_count", 0)),
                )
            )
        lines.extend(["", "## Gates", ""])
        lines.extend(f"- `{name}`: **{value}**" for name, value in gates.items())
        lines.extend(
            [
                "",
                "This artifact is research-only; it is not an order or an investment "
                "recommendation.",
            ]
        )
        report.write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )
        files = {
            path.name: _sha256_file(path)
            for path in root.iterdir()
            if path.is_file() and path.name != "manifest.json"
        }
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "experiment_code": experiment_code,
                    "strategy_code": output.get("strategy_code", "trend_quality_v1"),
                    "immutable": True,
                    "production_enabled": False,
                    "files": files,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return report

    def _restricted_data_path(self, path_value: str) -> Path | None:
        path = Path(path_value).expanduser()
        if not path.is_absolute():
            # API examples use ``data/...`` while internal callers commonly
            # pass ``raw/...`` relative to the configured data root.
            path = (
                self.settings.data_root.parent / path
                if path.parts and path.parts[0].lower() == self.settings.data_root.name.lower()
                else self.settings.data_root / path
            )
        path = path.resolve()
        root = self.settings.data_root.resolve()
        return path if path.is_relative_to(root) and path.exists() else None

    @staticmethod
    def _manifest_version(path: Path | None) -> str:
        if path is None:
            return "missing"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return str(value.get("snapshot_id", value.get("version", path.parent.name)))
        except (OSError, json.JSONDecodeError):
            return path.parent.name

    @staticmethod
    def _validate_manifest(manifest: dict[str, Any], path: Path) -> None:
        if manifest.get("immutable") is False:
            raise WalkForwardSnapshotError("snapshot manifest is not immutable")
        if manifest.get("coverage_ratio", manifest.get("daily_coverage_ratio", 1.0)) < 0.95:
            raise WalkForwardSnapshotError("daily coverage ratio is below 95%")
        if manifest.get("audit_valid") is False or manifest.get("status") in {"invalid", "blocked"}:
            raise WalkForwardSnapshotError("snapshot manifest failed its audit gate")
        if not path.exists():
            raise WalkForwardSnapshotError("snapshot manifest disappeared during validation")

    @staticmethod
    def _read_manifest_frame(
        manifest: dict[str, Any],
        manifest_path: Path,
        name: str,
        *,
        required: bool = True,
    ) -> pd.DataFrame | None:
        files = manifest.get("files", {})
        value = manifest.get(f"{name}_path", files.get(name))
        if isinstance(value, dict):
            value = value.get("path")
        if value is None:
            if required:
                raise WalkForwardSnapshotError(f"snapshot manifest is missing {name} data")
            return None
        path = Path(str(value))
        if not path.is_absolute():
            path = manifest_path.parent / path
        if not path.exists():
            raise WalkForwardSnapshotError(f"snapshot file does not exist: {path}")
        return read_tabular(path)

    @staticmethod
    def _blocked_report(payload: WalkForwardRunRequest, error: str) -> dict[str, Any]:
        return {
            "experiment_code": payload.experiment_code,
            "strategy_code": payload.strategy_code,
            "status": "blocked",
            "lifecycle_status": "experimental",
            "production_enabled": False,
            "gates": {"all_passed": False},
            "error": error,
            "required_snapshot": "data/raw/imports/ashare-2018-2025-v1/manifest.json",
        }


def _date_filter(frame: pd.DataFrame, column: str, start: date, end: date) -> pd.DataFrame:
    dates = pd.to_datetime(frame[column]).dt.normalize()
    return frame.loc[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))].copy()


def _slice_point_in_time_input(
    source: pd.DataFrame | Path | None,
    *,
    symbols: set[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    cutoff: Any,
) -> pd.DataFrame | None:
    """Read only records that can affect one causal historical replay chunk."""

    if source is None:
        return None
    if isinstance(source, Path):
        if source.suffix.lower() != ".parquet":
            frame = read_tabular(source)
        else:
            import pyarrow.parquet as parquet  # type: ignore[import-untyped]

            schema = parquet.read_schema(source)
            filters: list[tuple[str, str, Any]] | None = None
            if "date" in schema.names:
                filters = [
                    ("date", ">=", start.normalize().to_pydatetime()),
                    ("date", "<=", end.normalize().to_pydatetime()),
                ]
            frame = pd.read_parquet(source, filters=filters)
    else:
        frame = source
    if frame.empty:
        return frame.copy()
    required = {"symbol", "available_at"}
    if missing := required.difference(frame.columns):
        raise WalkForwardSnapshotError(
            f"point-in-time input is missing columns: {sorted(missing)}"
        )
    relevant = frame.loc[frame["symbol"].astype(str).isin(symbols)].copy()
    if relevant.empty:
        return relevant
    available = _local_naive_timestamps(relevant["available_at"])
    start_cutoff = start.normalize() + pd.Timedelta(
        hours=cutoff.hour,
        minutes=cutoff.minute,
        seconds=cutoff.second,
    )
    end_cutoff = end.normalize() + pd.Timedelta(
        hours=cutoff.hour,
        minutes=cutoff.minute,
        seconds=cutoff.second,
    )
    relevant["_available_sort"] = available
    prior = (
        relevant.loc[available < start_cutoff]
        .sort_values(["symbol", "_available_sort"])
        .groupby("symbol", sort=False)
        .tail(1)
    )
    current = relevant.loc[available.between(start_cutoff, end_cutoff, inclusive="both")]
    return (
        pd.concat([prior, current], ignore_index=True)
        .sort_values(["symbol", "_available_sort"])
        .drop(columns=["_available_sort"])
        .reset_index(drop=True)
    )


def _slice_date_input(
    frame: pd.DataFrame | None,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame | None:
    if frame is None or frame.empty:
        return None if frame is None else frame.copy()
    if "date" not in frame:
        return frame.copy()
    dates = _local_naive_timestamps(frame["date"]).dt.normalize()
    return pd.DataFrame(frame.loc[dates.between(start.normalize(), end.normalize())].copy())


def _slice_research_input(
    frame: pd.DataFrame | None,
    *,
    symbols: set[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame | None:
    if frame is None or frame.empty:
        return None if frame is None else frame.copy()
    relevant = (
        frame.loc[frame["symbol"].astype(str).isin(symbols)].copy()
        if "symbol" in frame
        else frame.copy()
    )
    if "date" in relevant:
        return _slice_date_input(relevant, start=start, end=end)
    if "effective_date" in relevant:
        return _slice_effective_state(relevant, symbols=symbols, start=start, end=end)
    return relevant


def _slice_effective_state(
    frame: pd.DataFrame | None,
    *,
    symbols: set[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame | None:
    if frame is None or frame.empty:
        return None if frame is None else frame.copy()
    if not {"symbol", "effective_date"}.issubset(frame.columns):
        return frame.copy()
    relevant = frame.loc[frame["symbol"].astype(str).isin(symbols)].copy()
    effective = _local_naive_timestamps(relevant["effective_date"]).dt.normalize()
    relevant["_effective_sort"] = effective
    prior = (
        relevant.loc[effective < start.normalize()]
        .sort_values(["symbol", "_effective_sort"])
        .groupby("symbol", sort=False)
        .tail(1)
    )
    current = relevant.loc[effective.between(start.normalize(), end.normalize())]
    output = (
        pd.concat([prior, current], ignore_index=True)
        .sort_values(["symbol", "_effective_sort"])
        .drop(columns=["_effective_sort"])
        .reset_index(drop=True)
    )
    return pd.DataFrame(output)


def _local_naive_timestamps(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, format="mixed", errors="raise")
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        return parsed.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    return parsed


def _frame_length(frame: pd.DataFrame | None) -> int:
    return 0 if frame is None else len(frame)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parameter_key(params: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(sorted((str(key), str(value)) for key, value in params.items()))


def _parameter_distance(left: dict[str, Any], right: dict[str, Any]) -> int:
    keys = {"top_n", "holding_period", "rebalance_frequency", "slippage_bps"}
    return sum(left.get(key) != right.get(key) for key in keys)


def _scan_parameters(
    prices: pd.DataFrame,
    signals: pd.DataFrame,
    grid: dict[str, list[Any]],
    initial_cash: float,
) -> list[dict[str, Any]]:
    if prices.empty or signals.empty:
        return []
    return [
        asdict(item)
        for item in VectorBTResearchAdapter().run_parameter_scan(
            prices,
            signals,
            grid,
            initial_cash=initial_cash,
        )
    ]


def _formal_run(
    payload: WalkForwardRunRequest,
    prices: pd.DataFrame,
    signals: pd.DataFrame,
    params: dict[str, Any],
    benchmark: pd.DataFrame | None = None,
) -> dict[str, Any]:
    if prices.empty or signals.empty:
        return _empty_formal_metrics()
    market = prices.copy()
    market["date"] = pd.to_datetime(market["date"]).dt.normalize()
    market["symbol"] = market["symbol"].astype(str)
    signals = signals.copy()
    signals["signal_date"] = pd.to_datetime(signals["signal_date"]).dt.normalize()
    request = BacktestRequest(
        strategy_code=payload.strategy_code,
        start_date=market["date"].min().date(),
        end_date=market["date"].max().date(),
        top_n=int(params["top_n"]),
        rebalance_frequency=str(params["rebalance_frequency"]),
        holding_period=int(params["holding_period"]),
        commission_rate=payload.commission_rate,
        minimum_commission=payload.minimum_commission,
        stamp_tax_rate=payload.stamp_tax_rate,
        slippage_bps=float(params.get("slippage_bps", 5.0)),
        benchmark_symbol=payload.benchmark_symbol,
        initial_cash=payload.initial_cash,
        max_stock_weight=payload.max_stock_weight,
        max_industry_weight=payload.max_industry_weight,
    )
    result = AshareDailyExecutionEngine().run_with_data(request, market, signals)
    performance = dict(result.performance)
    performance.setdefault("execution_failures", result.execution_failures)
    performance["equity_curve"] = result.equity_curve
    performance["initial_cash"] = request.initial_cash
    performance["trades"] = result.trades
    performance["positions"] = result.positions
    benchmark_return = _benchmark_return(benchmark)
    tradable_return = float(performance.get("tradable_return", 0.0))
    performance["max_drawdown"] = abs(float(performance.get("max_drawdown", 0.0)))
    performance["benchmark_return"] = benchmark_return
    performance["excess_return"] = tradable_return - benchmark_return
    performance["tradable_excess_return"] = performance["excess_return"]
    performance["composite_rank_ic_positive"] = _rank_ic_positive(signals, market)
    performance.update(_trade_concentration(result.trades, market))
    return performance


def _empty_formal_metrics() -> dict[str, Any]:
    return {
        "theoretical_return": 0.0,
        "tradable_return": 0.0,
        "benchmark_return": 0.0,
        "excess_return": 0.0,
        "tradable_excess_return": 0.0,
        "max_drawdown": 0.0,
        "equity_curve": [],
        "initial_cash": 0.0,
        "trade_count": 0,
        "closed_trade_count": 0,
        "execution_failures": [],
        "composite_rank_ic_positive": False,
        "positive_industry_count": 0,
        "max_industry_positive_share": 1.0,
        "top_trade_contributions": {"positive_pnl": 0.0, "positive_pnl_total": 0.0},
        "positive_trade_pnls": [],
        "pnl_by_industry": {},
    }


def _compound_returns(values: list[float]) -> float:
    compounded = 1.0
    for value in values:
        compounded *= 1.0 + value
    return compounded - 1.0


def _combined_oos_max_drawdown(windows: list[dict[str, Any]]) -> float:
    wealth = 1.0
    combined: list[float] = []
    for item in windows:
        metrics = item.get("test_metrics", {})
        curve = metrics.get("equity_curve", [])
        if not isinstance(curve, list) or not curve:
            continue
        initial_cash = float(metrics.get("initial_cash", 0.0))
        if initial_cash <= 0:
            initial_cash = float(curve[0].get("equity", 0.0))
        if initial_cash <= 0:
            continue
        normalized = [
            float(point.get("equity", 0.0)) / initial_cash
            for point in curve
            if float(point.get("equity", 0.0)) >= 0
        ]
        if not normalized:
            continue
        combined.extend(wealth * value for value in normalized)
        wealth = combined[-1]
    if not combined:
        return 0.0
    series = pd.Series(combined, dtype=float)
    drawdown = series / series.cummax() - 1.0
    return abs(float(drawdown.min()))


def _benchmark_return(benchmark: pd.DataFrame | None) -> float:
    if benchmark is None or benchmark.empty or "close" not in benchmark:
        return 0.0
    frame = benchmark.copy()
    if "date" not in frame:
        return 0.0
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).sort_values("date")
    if frame.empty:
        return 0.0
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    return float(close.iloc[-1] / close.iloc[0] - 1.0) if len(close) >= 2 else 0.0


def _rank_ic_positive(signals: pd.DataFrame, market: pd.DataFrame) -> bool:
    if "composite_score" not in signals or signals.empty:
        return False
    close = market.pivot(index="date", columns="symbol", values="close").sort_index()
    factors = signals.copy()
    factors["signal_date"] = pd.to_datetime(factors["signal_date"]).dt.normalize()
    values: list[float] = []
    for signal_date, section in factors.groupby("signal_date"):
        signal_timestamp = pd.Timestamp(str(signal_date))
        next_dates = close.index[close.index > signal_timestamp]
        if len(next_dates) == 0:
            continue
        next_date = next_dates[0]
        common = section.loc[section["symbol"].isin(close.columns)].copy()
        forward_values: list[float] = []
        for symbol_value in common["symbol"].astype(str):
            if signal_timestamp not in close.index or symbol_value not in close.columns:
                forward_values.append(np.nan)
                continue
            current_price = close.loc[signal_timestamp, symbol_value]
            next_price = close.loc[next_date, symbol_value]
            forward_values.append(
                float(next_price / current_price - 1.0)
                if pd.notna(current_price) and pd.notna(next_price)
                else np.nan
            )
        common["forward"] = forward_values
        common = common.dropna(subset=["composite_score", "forward"])
        if len(common) >= 2:
            values.append(
                float(common["composite_score"].corr(common["forward"], method="spearman"))
            )
    return bool(values and float(np.nanmean(values)) > 0)


def _trade_concentration(trades: list[dict[str, Any]], market: pd.DataFrame) -> dict[str, Any]:
    buys: dict[str, list[dict[str, Any]]] = {}
    closed_trade_pnls: list[float] = []
    industry_pnl: dict[str, float] = {}
    market_frame = market.copy()
    market_frame["date"] = pd.to_datetime(market_frame["date"], errors="coerce").dt.normalize()
    market_frame["symbol"] = market_frame["symbol"].astype(str)
    industry_by_key = (
        market_frame.drop_duplicates(["date", "symbol"], keep="last")
        .set_index(["date", "symbol"])
        .get("industry", pd.Series(dtype=str))
        .to_dict()
    )
    industry_by_symbol = (
        market_frame.drop_duplicates("symbol", keep="last")
        .set_index("symbol")
        .get("industry", pd.Series(dtype=str))
        .to_dict()
    )
    for trade in trades:
        symbol = str(trade.get("symbol", ""))
        if trade.get("side") == "buy":
            buys.setdefault(symbol, []).append(trade)
            continue
        if trade.get("side") != "sell" or not buys.get(symbol):
            continue
        buy = buys[symbol].pop(0)
        buy_gross = float(buy.get("gross_amount", 0.0))
        sell_gross = float(trade.get("gross_amount", 0.0))
        if buy_gross == 0.0:
            buy_gross = float(buy.get("price", 0.0)) * float(buy.get("quantity", 0.0))
        if sell_gross == 0.0:
            sell_gross = float(trade.get("price", 0.0)) * float(trade.get("quantity", 0.0))
        pnl = (
            sell_gross
            - float(trade.get("commission", 0.0))
            - float(trade.get("stamp_tax", 0.0))
            - buy_gross
            - float(buy.get("commission", 0.0))
            - float(buy.get("stamp_tax", 0.0))
        )
        closed_trade_pnls.append(pnl)
        try:
            trade_day: pd.Timestamp | None = pd.Timestamp(
                str(trade.get("trade_date", ""))
            ).normalize()
        except (TypeError, ValueError):
            trade_day = None
        industry_value = industry_by_symbol.get(symbol, "unknown")
        if trade_day is not None:
            industry_value = industry_by_key.get((trade_day, symbol), industry_value)
        industry = str(industry_value)
        industry_pnl[industry] = industry_pnl.get(industry, 0.0) + pnl
    positive = [value for value in closed_trade_pnls if value > 0]
    positive_total = sum(positive)
    positive_industry_pnl = {key: value for key, value in industry_pnl.items() if value > 0}
    positive_industry_total = sum(positive_industry_pnl.values())
    return {
        "positive_industry_count": len(positive_industry_pnl),
        "max_industry_positive_share": (
            max(positive_industry_pnl.values()) / positive_industry_total
            if positive_industry_total > 0
            else 1.0
        ),
        "positive_trade_pnls": positive,
        "pnl_by_industry": industry_pnl,
        "top_trade_contributions": {
            "positive_pnl": sum(sorted(positive, reverse=True)[:5]),
            "positive_pnl_total": positive_total,
        },
    }
