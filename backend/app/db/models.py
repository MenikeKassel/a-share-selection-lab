from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExternalEngineRun(Base):
    __tablename__ = "external_engine_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    engine_type: Mapped[str] = mapped_column(String(32), index=True)
    run_type: Mapped[str] = mapped_column(String(64), index=True)
    strategy_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    factor_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), index=True)
    result_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class EngineComparison(Base):
    __tablename__ = "engine_comparisons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The primary result can be a formal backtest_run while the comparison is an
    # external_engine_run, so these polymorphic identifiers intentionally have no FK.
    primary_run_id: Mapped[int] = mapped_column(Integer, index=True)
    comparison_run_id: Mapped[int] = mapped_column(Integer, index=True)
    comparison_type: Mapped[str] = mapped_column(String(64), index=True)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    differences_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FactorAnalysisResultRecord(Base):
    __tablename__ = "factor_analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("external_engine_runs.id"), nullable=True, index=True
    )
    factor_code: Mapped[str] = mapped_column(String(128), index=True)
    analysis_engine: Mapped[str] = mapped_column(String(32), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    horizon: Mapped[int] = mapped_column(Integer)
    ic: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank_ic: Mapped[float | None] = mapped_column(Float, nullable=True)
    icir: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_short_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage: Mapped[float] = mapped_column(Float)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelExperiment(Base):
    __tablename__ = "model_experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_code: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    engine: Mapped[str] = mapped_column(String(32), default="qlib")
    model_type: Mapped[str] = mapped_column(String(64))
    train_start: Mapped[date] = mapped_column(Date)
    train_end: Mapped[date] = mapped_column(Date)
    validation_start: Mapped[date] = mapped_column(Date)
    validation_end: Mapped[date] = mapped_column(Date)
    test_start: Mapped[date] = mapped_column(Date)
    test_end: Mapped[date] = mapped_column(Date)
    feature_version: Mapped[str] = mapped_column(String(128))
    label_definition: Mapped[str] = mapped_column(String(256))
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    experiment_only: Mapped[bool] = mapped_column(Boolean, default=True)
    production_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    engine_type: Mapped[str] = mapped_column(String(32), index=True)
    strategy_code: Mapped[str] = mapped_column(String(128), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    formal_ashare_result: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WalkForwardExperiment(Base):
    """Immutable record for a point-in-time walk-forward validation run.

    The large daily signal/equity artifacts stay outside SQLite.  This table
    stores the reproducibility pointers and the compact window/gate summary.
    ``production_enabled`` is intentionally persisted as a hard invariant so
    a research result cannot silently become a live strategy.
    """

    __tablename__ = "walk_forward_experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_code: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    strategy_code: Mapped[str] = mapped_column(String(128), index=True)
    data_snapshot_version: Mapped[str] = mapped_column(String(128), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(32), index=True, default="experimental")
    production_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class DataQualitySnapshot(Base):
    __tablename__ = "data_quality_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    expected_latest_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    daily_market_max_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    minute_market_max_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    daily_coverage_ratio: Mapped[float] = mapped_column(Float)
    minute_coverage_ratio: Mapped[float] = mapped_column(Float)
    selection_status: Mapped[str] = mapped_column(String(64))
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SelectionSnapshot(Base):
    __tablename__ = "selection_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "selection_date",
            "strategy_code",
            "strategy_version",
            "factor_version",
            "data_snapshot_version",
            name="uq_selection_snapshot_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    selection_date: Mapped[date] = mapped_column(Date, index=True)
    strategy_code: Mapped[str] = mapped_column(String(128), index=True)
    strategy_version: Mapped[str] = mapped_column(String(64))
    factor_version: Mapped[str] = mapped_column(String(64))
    data_snapshot_version: Mapped[str] = mapped_column(String(128))
    selection_status: Mapped[str] = mapped_column(String(64))
    candidates_json: Mapped[str] = mapped_column(Text)
    artifact_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CandidateReview(Base):
    __tablename__ = "candidate_reviews"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "symbol", "horizon", name="uq_review_horizon"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("selection_snapshots.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    horizon: Mapped[int] = mapped_column(Integer)
    metrics_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


JsonRecord = dict[str, Any]
