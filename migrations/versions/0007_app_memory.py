"""新增按 App 隔离的持久记忆表。

Revision ID: 0007_app_memory
Revises: 0006_deploy_revision
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_app_memory"
down_revision: str | Sequence[str] | None = "0006_deploy_revision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_memory",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("appId", sa.BigInteger(), nullable=False),
        sa.Column("userId", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_turn_id", sa.String(length=64), nullable=False),
        sa.Column(
            "createTime", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updateTime", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_app_memory_app_id", "app_memory", ["appId"])


def downgrade() -> None:
    op.drop_index("idx_app_memory_app_id", table_name="app_memory")
    op.drop_table("app_memory")
