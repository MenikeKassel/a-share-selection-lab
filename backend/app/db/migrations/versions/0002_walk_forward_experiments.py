"""add immutable walk-forward experiment records

Revision ID: 0002_walk_forward_experiments
Revises: 0001_initial_research_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0002_walk_forward_experiments"
down_revision = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "walk_forward_experiments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("experiment_code", sa.String(length=128), nullable=False),
        sa.Column("strategy_code", sa.String(length=128), nullable=False),
        sa.Column("data_snapshot_version", sa.String(length=128), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("artifact_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=32), nullable=False),
        sa.Column("production_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_code"),
    )
    op.create_index(
        "ix_walk_forward_experiments_experiment_code",
        "walk_forward_experiments",
        ["experiment_code"],
        unique=False,
    )
    op.create_index(
        "ix_walk_forward_experiments_strategy_code",
        "walk_forward_experiments",
        ["strategy_code"],
        unique=False,
    )
    op.create_index(
        "ix_walk_forward_experiments_data_snapshot_version",
        "walk_forward_experiments",
        ["data_snapshot_version"],
        unique=False,
    )
    op.create_index(
        "ix_walk_forward_experiments_status",
        "walk_forward_experiments",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_walk_forward_experiments_lifecycle_status",
        "walk_forward_experiments",
        ["lifecycle_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_walk_forward_experiments_lifecycle_status", table_name="walk_forward_experiments")
    op.drop_index("ix_walk_forward_experiments_status", table_name="walk_forward_experiments")
    op.drop_index("ix_walk_forward_experiments_data_snapshot_version", table_name="walk_forward_experiments")
    op.drop_index("ix_walk_forward_experiments_strategy_code", table_name="walk_forward_experiments")
    op.drop_index("ix_walk_forward_experiments_experiment_code", table_name="walk_forward_experiments")
    op.drop_table("walk_forward_experiments")
