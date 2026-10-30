"""区分生成调用与压缩调用。

Revision ID: 0005_model_call_kind
Revises: 0004_model_call_retry_metadata
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_model_call_kind"
down_revision: str | Sequence[str] | None = "0004_model_call_retry_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_call_log",
        sa.Column(
            "call_kind",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'generation'"),
        ),
    )
    op.create_index(
        "idx_model_call_app_kind",
        "model_call_log",
        ["appId", "call_kind", "id"],
    )


def downgrade() -> None:
    op.drop_index("idx_model_call_app_kind", table_name="model_call_log")
    op.drop_column("model_call_log", "call_kind")
