from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.adapters import OptionalEngineUnavailableError
from app.adapters.alphalens.adapter import AlphalensFactorAnalysisAdapter
from app.adapters.io import filter_date_window, read_tabular
from app.adapters.qlib.dataset_exporter import QlibDatasetExporter, QlibTimeSplit
from app.adapters.qlib.experiment_runner import QlibExperimentRunner
from app.adapters.rqalpha.adapter import RQAlphaValidationAdapter
from app.adapters.rqalpha.result_converter import (
    compare_engine_results,
    import_rqalpha_result,
)
from app.adapters.vectorbt.adapter import VectorBTResearchAdapter
from app.api.schemas import (
    FactorAnalysisRunRequest,
    FormalBacktestRunRequest,
    QlibExperimentRequest,
    RQAlphaValidationRequest,
    VectorBTResearchRequest,
)
from app.core.config import Settings
from app.db.repositories import (
    BacktestRunRepository,
    ComparisonRepository,
    EngineRunRepository,
    FactorResultRepository,
    ModelExperimentRepository,
)
from app.domain.protocols import BacktestRequest, FactorAnalysisRequest
from app.execution.ashare_daily import AshareDailyExecutionEngine
from app.research.factor_analysis import NativeFactorAnalysisEngine


class ResearchTaskService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.engine_runs = EngineRunRepository(session)
        self.factor_results = FactorResultRepository(session)
        self.model_experiments = ModelExperimentRepository(session)
        self.backtest_runs = BacktestRunRepository(session)
        self.comparisons = ComparisonRepository(session)

    def run_factor_analysis(self, payload: FactorAnalysisRunRequest) -> Any:
        config = payload.model_dump(mode="json")
        record = self.engine_runs.create(
            engine_type="alphalens",
            run_type="factor_analysis",
            factor_code=payload.factor_code,
            start_date=payload.start_date,
            end_date=payload.end_date,
            config=config,
        )
        native_result = None
        try:
            request = FactorAnalysisRequest(
                factor_code=payload.factor_code,
                start_date=payload.start_date,
                end_date=payload.end_date,
                horizons=payload.horizons,
                group_count=payload.group_count,
                industry_neutral=payload.industry_neutral,
                factor_path=str(self._input_path(payload.factor_path)),
                price_path=str(self._input_path(payload.price_path)),
            )
            if payload.include_native_baseline:
                native_result = NativeFactorAnalysisEngine().analyze(request)
                self.factor_results.save(native_result, engine="native", run_id=record.id)
            result = AlphalensFactorAnalysisAdapter().analyze(request)
            self.factor_results.save(result, engine="alphalens", run_id=record.id)
            summary = {
                **asdict(result),
                "native_baseline": (asdict(native_result) if native_result is not None else None),
            }
            artifact_path = self._write_json_artifact(
                record.id, "factor-analysis-result.json", summary
            )
            return self.engine_runs.finish(
                record,
                status="succeeded",
                result_summary=summary,
                artifact_path=str(artifact_path),
            )
        except OptionalEngineUnavailableError as error:
            return self.engine_runs.finish(
                record,
                status="unavailable",
                result_summary=(
                    {
                        "native_baseline": asdict(native_result),
                        "alphalens": None,
                    }
                    if native_result is not None
                    else None
                ),
                error_message=str(error),
            )
        except Exception as error:
            return self.engine_runs.finish(
                record, status="failed", error_message=f"{type(error).__name__}: {error}"
            )

    def run_vectorbt(self, payload: VectorBTResearchRequest) -> Any:
        config = payload.model_dump(mode="json")
        record = self.engine_runs.create(
            engine_type="vectorbt",
            run_type="parameter_research",
            strategy_code=payload.strategy_code,
            start_date=payload.start_date,
            end_date=payload.end_date,
            config=config,
        )
        try:
            prices = filter_date_window(
                read_tabular(self._input_path(payload.price_path)),
                column="date",
                start=payload.start_date,
                end=payload.end_date,
            )
            signals = filter_date_window(
                read_tabular(self._input_path(payload.signal_path)),
                column="signal_date",
                start=payload.start_date,
                end=payload.end_date,
            )
            results = VectorBTResearchAdapter().run_parameter_scan(
                prices,
                signals,
                payload.parameter_grid,
                initial_cash=payload.initial_cash,
            )
            serialized = [asdict(item) for item in results]
            artifact_path = self._write_json_artifact(record.id, "vectorbt-scan.json", serialized)
            best = max(
                serialized,
                key=lambda item: item["sharpe"],
                default={},
            )
            return self.engine_runs.finish(
                record,
                status="succeeded",
                result_summary={
                    "research_engine": "vectorbt",
                    "formal_ashare_validation": "required",
                    "parameter_count": len(serialized),
                    "best_by_sharpe": best,
                },
                artifact_path=str(artifact_path),
            )
        except OptionalEngineUnavailableError as error:
            return self.engine_runs.finish(record, status="unavailable", error_message=str(error))
        except Exception as error:
            return self.engine_runs.finish(
                record, status="failed", error_message=f"{type(error).__name__}: {error}"
            )

    def run_rqalpha(self, payload: RQAlphaValidationRequest) -> Any:
        config = payload.model_dump(mode="json")
        record = self.engine_runs.create(
            engine_type="rqalpha",
            run_type="strategy_validation",
            strategy_code=payload.strategy_code,
            start_date=payload.start_date,
            end_date=payload.end_date,
            config=config,
        )
        try:
            signals = read_tabular(self._input_path(payload.signal_path))
            if "target_weight" not in signals:
                signals = self._target_weights(signals, payload.top_n)
            artifact_dir = self.settings.artifact_root / "engine_runs" / str(record.id)
            result = RQAlphaValidationAdapter().run(
                signals=signals,
                artifact_dir=artifact_dir,
                start_date=payload.start_date.isoformat(),
                end_date=payload.end_date.isoformat(),
                benchmark=payload.benchmark_symbol,
                data_bundle_path=(
                    str(self._input_path(payload.data_bundle_path))
                    if payload.data_bundle_path
                    else None
                ),
                initial_cash=payload.initial_cash,
                stock_commission_multiplier=payload.stock_commission_multiplier,
                minimum_commission=payload.minimum_commission,
                tax_multiplier=payload.tax_multiplier,
                slippage=payload.slippage,
            )
            summary: dict[str, Any] = dict(result)
            self_result: dict[str, Any] | None = None
            if payload.formal_backtest_run_id is not None:
                formal_run = self.backtest_runs.get(payload.formal_backtest_run_id)
                if (
                    formal_run is None
                    or not formal_run.formal_ashare_result
                    or formal_run.status != "succeeded"
                    or formal_run.result_json is None
                ):
                    raise ValueError(
                        "formal_backtest_run_id must reference a succeeded formal A-share backtest"
                    )
                self_result = json.loads(formal_run.result_json)
            elif payload.self_result_path:
                self_result = json.loads(
                    self._data_or_artifact_path(payload.self_result_path).read_text(
                        encoding="utf-8"
                    )
                )
            if self_result is not None:
                rqalpha_result = import_rqalpha_result(Path(result["result_path"]))
                difference_report = compare_engine_results(self_result, rqalpha_result)
                summary["difference_report"] = difference_report
                if payload.formal_backtest_run_id is not None:
                    metric_keys = {
                        "self_engine_return",
                        "rqalpha_return",
                        "return_difference",
                        "trade_count_difference",
                        "fee_difference",
                    }
                    comparison = self.comparisons.create(
                        primary_run_id=payload.formal_backtest_run_id,
                        comparison_run_id=record.id,
                        comparison_type="ashare_daily_vs_rqalpha",
                        metrics={key: difference_report[key] for key in metric_keys},
                        differences={
                            key: value
                            for key, value in difference_report.items()
                            if key not in metric_keys
                        },
                    )
                    summary["comparison_id"] = comparison.id
            return self.engine_runs.finish(
                record,
                status="succeeded",
                result_summary=summary,
                artifact_path=result["result_path"],
            )
        except OptionalEngineUnavailableError as error:
            return self.engine_runs.finish(record, status="unavailable", error_message=str(error))
        except Exception as error:
            return self.engine_runs.finish(
                record, status="failed", error_message=f"{type(error).__name__}: {error}"
            )

    def run_qlib(self, payload: QlibExperimentRequest) -> tuple[Any, Any]:
        config = payload.model_dump(mode="json")
        run = self.engine_runs.create(
            engine_type="qlib",
            run_type="ml_experiment",
            start_date=payload.train_start,
            end_date=payload.test_end,
            config=config,
        )
        experiment = self.model_experiments.create(
            experiment_code=payload.experiment_code,
            model_type=payload.model_type,
            train_start=payload.train_start,
            train_end=payload.train_end,
            validation_start=payload.validation_start,
            validation_end=payload.validation_end,
            test_start=payload.test_start,
            test_end=payload.test_end,
            feature_version=payload.feature_version,
            label_definition=payload.label_definition,
            config=config,
        )
        try:
            source = read_tabular(self._input_path(payload.feature_path))
            artifact_dir = self.settings.artifact_root / "engine_runs" / str(run.id)
            dataset_path = artifact_dir / "qlib-dataset.parquet"
            export = QlibDatasetExporter().export(
                source,
                dataset_path,
                split=QlibTimeSplit(
                    train_start=payload.train_start,
                    train_end=payload.train_end,
                    validation_start=payload.validation_start,
                    validation_end=payload.validation_end,
                    test_start=payload.test_start,
                    test_end=payload.test_end,
                ),
                feature_columns=payload.feature_columns,
                label_column=payload.label_column,
            )
            result = QlibExperimentRunner().run(
                export.path,
                feature_columns=payload.feature_columns,
                label_column=payload.label_column,
                model_config=payload.training_config,
            )
            predictions_path = artifact_dir / "qlib-predictions.parquet"
            result.predictions.to_parquet(predictions_path, index=False)
            summary = {
                "metrics": {
                    **result.metrics,
                    **{f"rule_{key}": value for key, value in payload.rule_metrics.items()},
                },
                "metadata": result.metadata,
                "dataset_metadata": export.metadata,
                "prediction_path": str(predictions_path),
            }
            self.model_experiments.finish(experiment, status="succeeded", result=summary)
            self.engine_runs.finish(
                run,
                status="succeeded",
                result_summary=summary,
                artifact_path=str(predictions_path),
            )
        except OptionalEngineUnavailableError as error:
            self.model_experiments.finish(experiment, status="unavailable")
            self.engine_runs.finish(run, status="unavailable", error_message=str(error))
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            self.model_experiments.finish(experiment, status="failed")
            self.engine_runs.finish(run, status="failed", error_message=message)
        return run, experiment

    def run_formal_backtest(self, payload: FormalBacktestRunRequest) -> Any:
        config = payload.model_dump(mode="json")
        record = self.backtest_runs.create(
            engine_type="ashare_daily_v1",
            strategy_code=payload.strategy_code,
            start_date=payload.start_date,
            end_date=payload.end_date,
            config=config,
            formal_ashare_result=True,
        )
        try:
            request_data = payload.model_dump()
            request_data["market_data_path"] = str(self._input_path(payload.market_data_path))
            request_data["signal_path"] = str(self._input_path(payload.signal_path))
            request = BacktestRequest(**request_data)
            result = AshareDailyExecutionEngine().run(request)
            self.backtest_runs.finish(record, status="succeeded", result=asdict(result))
        except Exception as error:
            self.backtest_runs.finish(
                record,
                status="failed",
                result={"error": f"{type(error).__name__}: {error}"},
            )
        return record

    def _write_json_artifact(self, run_id: int, filename: str, value: Any) -> Path:
        path = self.settings.artifact_root / "engine_runs" / str(run_id) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, default=str, allow_nan=False, indent=2),
            encoding="utf-8",
        )
        return path

    def _input_path(self, path_value: str) -> Path:
        return self._restricted_path(path_value, [self.settings.data_root])

    def _data_or_artifact_path(self, path_value: str) -> Path:
        return self._restricted_path(
            path_value, [self.settings.data_root, self.settings.artifact_root]
        )

    @staticmethod
    def _restricted_path(path_value: str, roots: list[Path]) -> Path:
        path = Path(path_value).expanduser().resolve()
        resolved_roots = [root.resolve() for root in roots]
        if not any(path.is_relative_to(root) for root in resolved_roots):
            raise ValueError(
                "input path is outside this repository's configured data/artifact roots"
            )
        if not path.exists():
            raise FileNotFoundError(path)
        return path

    @staticmethod
    def _target_weights(signals: pd.DataFrame, top_n: int) -> pd.DataFrame:
        required = {"signal_date", "symbol", "score"}
        if missing := required.difference(signals.columns):
            raise ValueError(
                f"signals need target_weight or signal_date/symbol/score; missing {sorted(missing)}"
            )
        selected = (
            signals.sort_values(["signal_date", "score"], ascending=[True, False])
            .groupby("signal_date")
            .head(top_n)
            .copy()
        )
        counts = selected.groupby("signal_date")["symbol"].transform("count")
        selected["target_weight"] = 1.0 / counts
        return selected[["signal_date", "symbol", "target_weight"]]
