"""add immutable market-data provider snapshots

Revision ID: 0003_market_data_snapshots
Revises: 0002_walk_forward_experiments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0003_market_data_snapshots"
down_revision = "0002_walk_forward_experiments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_data_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_code", sa.String(length=64), nullable=False),
        sa.Column("snapshot_code", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("daily_latest_date", sa.Date(), nullable=True),
        sa.Column("minute_latest_date", sa.Date(), nullable=True),
        sa.Column("daily_row_count", sa.Integer(), nullable=False),
        sa.Column("daily_symbol_count", sa.Integer(), nullable=False),
        sa.Column("daily_coverage_ratio", sa.Float(), nullable=False),
        sa.Column("minute_coverage_ratio", sa.Float(), nullable=False),
        sa.Column("manifest_path", sa.Text(), nullable=True),
        sa.Column("daily_path", sa.Text(), nullable=True),
        sa.Column("adjustment_path", sa.Text(), nullable=True),
        sa.Column("minute_directory", sa.Text(), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("walk_forward_eligible", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_code"),
    )
    op.create_index(
        "ix_market_data_snapshots_provider_code",
        "market_data_snapshots",
        ["provider_code"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_snapshots_snapshot_code",
        "market_data_snapshots",
        ["snapshot_code"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_snapshots_status",
        "market_data_snapshots",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_market_data_snapshots_status", table_name="market_data_snapshots")
    op.drop_index("ix_market_data_snapshots_snapshot_code", table_name="market_data_snapshots")
    op.drop_index("ix_market_data_snapshots_provider_code", table_name="market_data_snapshots")
    op.drop_table("market_data_snapshots")
