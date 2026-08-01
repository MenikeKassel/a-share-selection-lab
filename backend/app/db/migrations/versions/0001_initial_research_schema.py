"""initial research, selection, review, and engine schema

Revision ID: 0001
Revises:
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_engine_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("engine_type", sa.String(length=32), nullable=False),
        sa.Column("run_type", sa.String(length=64), nullable=False),
        sa.Column("strategy_code", sa.String(length=128), nullable=True),
        sa.Column("factor_code", sa.String(length=128), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_summary_json", sa.Text(), nullable=True),
        sa.Column("artifact_path", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_external_engine_runs_engine_type", "external_engine_runs", ["engine_type"])
    op.create_index("ix_external_engine_runs_run_type", "external_engine_runs", ["run_type"])
    op.create_index("ix_external_engine_runs_status", "external_engine_runs", ["status"])

    op.create_table(
        "engine_comparisons",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("primary_run_id", sa.Integer(), nullable=False),
        sa.Column("comparison_run_id", sa.Integer(), nullable=False),
        sa.Column("comparison_type", sa.String(length=64), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("differences_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_engine_comparisons_primary_run_id", "engine_comparisons", ["primary_run_id"]
    )
    op.create_index(
        "ix_engine_comparisons_comparison_run_id", "engine_comparisons", ["comparison_run_id"]
    )
    op.create_index(
        "ix_engine_comparisons_comparison_type", "engine_comparisons", ["comparison_type"]
    )

    op.create_table(
        "factor_analysis_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("factor_code", sa.String(length=128), nullable=False),
        sa.Column("analysis_engine", sa.String(length=32), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("ic", sa.Float(), nullable=True),
        sa.Column("rank_ic", sa.Float(), nullable=True),
        sa.Column("icir", sa.Float(), nullable=True),
        sa.Column("long_short_return", sa.Float(), nullable=True),
        sa.Column("turnover", sa.Float(), nullable=True),
        sa.Column("coverage", sa.Float(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["external_engine_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_factor_analysis_results_run_id", "factor_analysis_results", ["run_id"])
    op.create_index(
        "ix_factor_analysis_results_factor_code", "factor_analysis_results", ["factor_code"]
    )
    op.create_index(
        "ix_factor_analysis_results_analysis_engine", "factor_analysis_results", ["analysis_engine"]
    )

    op.create_table(
        "model_experiments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("experiment_code", sa.String(length=128), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("model_type", sa.String(length=64), nullable=False),
        sa.Column("train_start", sa.Date(), nullable=False),
        sa.Column("train_end", sa.Date(), nullable=False),
        sa.Column("validation_start", sa.Date(), nullable=False),
        sa.Column("validation_end", sa.Date(), nullable=False),
        sa.Column("test_start", sa.Date(), nullable=False),
        sa.Column("test_end", sa.Date(), nullable=False),
        sa.Column("feature_version", sa.String(length=128), nullable=False),
        sa.Column("label_definition", sa.String(length=256), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("experiment_only", sa.Boolean(), nullable=False),
        sa.Column("production_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_code"),
    )
    op.create_index(
        "ix_model_experiments_experiment_code", "model_experiments", ["experiment_code"]
    )
    op.create_index("ix_model_experiments_status", "model_experiments", ["status"])

    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("engine_type", sa.String(length=32), nullable=False),
        sa.Column("strategy_code", sa.String(length=128), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("formal_ashare_result", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtest_runs_engine_type", "backtest_runs", ["engine_type"])
    op.create_index("ix_backtest_runs_strategy_code", "backtest_runs", ["strategy_code"])
    op.create_index("ix_backtest_runs_status", "backtest_runs", ["status"])

    op.create_table(
        "data_quality_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("expected_latest_trade_date", sa.Date(), nullable=True),
        sa.Column("daily_market_max_date", sa.Date(), nullable=True),
        sa.Column("minute_market_max_date", sa.Date(), nullable=True),
        sa.Column("daily_coverage_ratio", sa.Float(), nullable=False),
        sa.Column("minute_coverage_ratio", sa.Float(), nullable=False),
        sa.Column("selection_status", sa.String(length=64), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("as_of_date"),
    )
    op.create_index(
        "ix_data_quality_snapshots_as_of_date", "data_quality_snapshots", ["as_of_date"]
    )

    op.create_table(
        "selection_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("selection_date", sa.Date(), nullable=False),
        sa.Column("strategy_code", sa.String(length=128), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("factor_version", sa.String(length=64), nullable=False),
        sa.Column("data_snapshot_version", sa.String(length=128), nullable=False),
        sa.Column("selection_status", sa.String(length=64), nullable=False),
        sa.Column("candidates_json", sa.Text(), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "selection_date",
            "strategy_code",
            "strategy_version",
            "factor_version",
            "data_snapshot_version",
            name="uq_selection_snapshot_version",
        ),
    )
    op.create_index(
        "ix_selection_snapshots_selection_date", "selection_snapshots", ["selection_date"]
    )
    op.create_index(
        "ix_selection_snapshots_strategy_code", "selection_snapshots", ["strategy_code"]
    )

    op.create_table(
        "candidate_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["snapshot_id"], ["selection_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "symbol", "horizon", name="uq_review_horizon"),
    )
    op.create_index("ix_candidate_reviews_snapshot_id", "candidate_reviews", ["snapshot_id"])
    op.create_index("ix_candidate_reviews_symbol", "candidate_reviews", ["symbol"])


def downgrade() -> None:
    op.drop_table("candidate_reviews")
    op.drop_table("selection_snapshots")
    op.drop_table("data_quality_snapshots")
    op.drop_table("backtest_runs")
    op.drop_table("model_experiments")
    op.drop_table("factor_analysis_results")
    op.drop_table("engine_comparisons")
    op.drop_table("external_engine_runs")
