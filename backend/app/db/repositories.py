from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import (
    BacktestRun,
    EngineComparison,
    ExternalEngineRun,
    FactorAnalysisResultRecord,
    ModelExperiment,
    WalkForwardExperiment,
)
from app.domain.protocols import FactorAnalysisResult


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)


def decode_json(value: str | None) -> Any:
    return json.loads(value) if value else None


class EngineRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        engine_type: str,
        run_type: str,
        config: dict[str, Any],
        strategy_code: str | None = None,
        factor_code: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        status: str = "running",
    ) -> ExternalEngineRun:
        record = ExternalEngineRun(
            engine_type=engine_type,
            run_type=run_type,
            strategy_code=strategy_code,
            factor_code=factor_code,
            start_date=start_date,
            end_date=end_date,
            config_json=_json(config),
            status=status,
            started_at=datetime.now(UTC),
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def finish(
        self,
        record: ExternalEngineRun,
        *,
        status: str,
        result_summary: dict[str, Any] | None = None,
        artifact_path: str | None = None,
        error_message: str | None = None,
    ) -> ExternalEngineRun:
        record.status = status
        record.result_summary_json = _json(result_summary) if result_summary is not None else None
        record.artifact_path = artifact_path
        record.error_message = error_message
        record.completed_at = datetime.now(UTC)
        self.session.commit()
        self.session.refresh(record)
        return record

    def get(self, run_id: int) -> ExternalEngineRun | None:
        return self.session.get(ExternalEngineRun, run_id)

    def list(
        self,
        *,
        engine_type: str | None = None,
        run_type: str | None = None,
        limit: int = 100,
    ) -> list[ExternalEngineRun]:
        statement = select(ExternalEngineRun)
        if engine_type:
            statement = statement.where(ExternalEngineRun.engine_type == engine_type)
        if run_type:
            statement = statement.where(ExternalEngineRun.run_type == run_type)
        statement = statement.order_by(desc(ExternalEngineRun.started_at)).limit(limit)
        return list(self.session.scalars(statement))

    def latest_by_engine(self) -> dict[str, ExternalEngineRun]:
        output: dict[str, ExternalEngineRun] = {}
        for record in self.list(limit=500):
            output.setdefault(record.engine_type, record)
        return output


class FactorResultRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save(
        self,
        result: FactorAnalysisResult,
        *,
        engine: str,
        run_id: int | None,
    ) -> list[FactorAnalysisResultRecord]:
        records: list[FactorAnalysisResultRecord] = []
        for horizon in result.ic:
            record = FactorAnalysisResultRecord(
                run_id=run_id,
                factor_code=result.factor_code,
                analysis_engine=engine,
                start_date=result.start_date,
                end_date=result.end_date,
                horizon=horizon,
                ic=result.ic.get(horizon),
                rank_ic=result.rank_ic.get(horizon),
                icir=result.icir.get(horizon),
                long_short_return=result.long_short_returns.get(horizon),
                turnover=result.turnover.get(horizon),
                coverage=result.coverage,
                result_json=_json(
                    {
                        "ic_std": result.ic_std.get(horizon),
                        "quantile_returns": result.quantile_returns.get(horizon, []),
                        "industry_results": result.industry_results,
                        "metadata": result.metadata,
                    }
                ),
            )
            self.session.add(record)
            records.append(record)
        self.session.commit()
        return records

    def list(self, limit: int = 200) -> list[FactorAnalysisResultRecord]:
        statement = (
            select(FactorAnalysisResultRecord)
            .order_by(desc(FactorAnalysisResultRecord.created_at))
            .limit(limit)
        )
        return list(self.session.scalars(statement))


class ComparisonRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        primary_run_id: int,
        comparison_run_id: int,
        comparison_type: str,
        metrics: dict[str, Any],
        differences: dict[str, Any],
    ) -> EngineComparison:
        record = EngineComparison(
            primary_run_id=primary_run_id,
            comparison_run_id=comparison_run_id,
            comparison_type=comparison_type,
            metrics_json=_json(metrics),
            differences_json=_json(differences),
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def list(self, limit: int = 100) -> list[EngineComparison]:
        statement = (
            select(EngineComparison).order_by(desc(EngineComparison.created_at)).limit(limit)
        )
        return list(self.session.scalars(statement))

    def get(self, comparison_id: int) -> EngineComparison | None:
        return self.session.get(EngineComparison, comparison_id)


class ModelExperimentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        experiment_code: str,
        model_type: str,
        train_start: date,
        train_end: date,
        validation_start: date,
        validation_end: date,
        test_start: date,
        test_end: date,
        feature_version: str,
        label_definition: str,
        config: dict[str, Any],
        status: str = "running",
    ) -> ModelExperiment:
        record = ModelExperiment(
            experiment_code=experiment_code,
            engine="qlib",
            model_type=model_type,
            train_start=train_start,
            train_end=train_end,
            validation_start=validation_start,
            validation_end=validation_end,
            test_start=test_start,
            test_end=test_end,
            feature_version=feature_version,
            label_definition=label_definition,
            config_json=_json(config),
            status=status,
            experiment_only=True,
            production_enabled=False,
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def finish(
        self,
        record: ModelExperiment,
        *,
        status: str,
        result: dict[str, Any] | None = None,
    ) -> ModelExperiment:
        record.status = status
        record.result_json = _json(result) if result is not None else None
        record.completed_at = datetime.now(UTC)
        # These are invariant even when an experiment succeeds.
        record.experiment_only = True
        record.production_enabled = False
        self.session.commit()
        self.session.refresh(record)
        return record

    def list(self, limit: int = 100) -> list[ModelExperiment]:
        statement = select(ModelExperiment).order_by(desc(ModelExperiment.created_at)).limit(limit)
        return list(self.session.scalars(statement))

    def get(self, experiment_id: int) -> ModelExperiment | None:
        return self.session.get(ModelExperiment, experiment_id)


class BacktestRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        engine_type: str,
        strategy_code: str,
        start_date: date,
        end_date: date,
        config: dict[str, Any],
        formal_ashare_result: bool,
    ) -> BacktestRun:
        record = BacktestRun(
            engine_type=engine_type,
            strategy_code=strategy_code,
            start_date=start_date,
            end_date=end_date,
            config_json=_json(config),
            status="running",
            formal_ashare_result=formal_ashare_result,
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def finish(
        self,
        record: BacktestRun,
        *,
        status: str,
        result: dict[str, Any] | None,
    ) -> BacktestRun:
        record.status = status
        record.result_json = _json(result) if result is not None else None
        record.completed_at = datetime.now(UTC)
        self.session.commit()
        self.session.refresh(record)
        return record

    def get(self, run_id: int) -> BacktestRun | None:
        return self.session.get(BacktestRun, run_id)

    def list(self, limit: int = 100) -> list[BacktestRun]:
        statement = select(BacktestRun).order_by(desc(BacktestRun.created_at)).limit(limit)
        return list(self.session.scalars(statement))


class WalkForwardRepository:
    """Persistence boundary for walk-forward runs and their compact results."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        experiment_code: str,
        strategy_code: str,
        data_snapshot_version: str,
        start_date: date,
        end_date: date,
        config: dict[str, Any],
    ) -> WalkForwardExperiment:
        record = WalkForwardExperiment(
            experiment_code=experiment_code,
            strategy_code=strategy_code,
            data_snapshot_version=data_snapshot_version,
            start_date=start_date,
            end_date=end_date,
            config_json=_json(config),
            status="running",
            lifecycle_status="experimental",
            production_enabled=False,
            created_at=datetime.now(UTC),
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def finish(
        self,
        record: WalkForwardExperiment,
        *,
        status: str,
        lifecycle_status: str = "experimental",
        result: dict[str, Any] | None = None,
        artifact_path: str | None = None,
        error_message: str | None = None,
    ) -> WalkForwardExperiment:
        record.status = status
        record.lifecycle_status = (
            lifecycle_status
            if lifecycle_status
            in {"experimental", "validated", "production_candidate", "production", "retired"}
            else "experimental"
        )
        record.production_enabled = False
        record.result_json = _json(result) if result is not None else None
        record.artifact_path = artifact_path
        record.error_message = error_message
        record.completed_at = datetime.now(UTC)
        self.session.commit()
        self.session.refresh(record)
        return record

    def get(self, experiment_id: int) -> WalkForwardExperiment | None:
        return self.session.get(WalkForwardExperiment, experiment_id)

    def get_by_code(self, experiment_code: str) -> WalkForwardExperiment | None:
        statement = select(WalkForwardExperiment).where(
            WalkForwardExperiment.experiment_code == experiment_code
        )
        return self.session.scalar(statement)

    def list(self, limit: int = 100) -> list[WalkForwardExperiment]:
        statement = (
            select(WalkForwardExperiment)
            .order_by(desc(WalkForwardExperiment.created_at))
            .limit(limit)
        )
        return list(self.session.scalars(statement))
