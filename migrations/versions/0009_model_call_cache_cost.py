"""记录缓存命中 token 与模型调用费用。

Revision ID: 0009_model_call_cache_cost
Revises: 0008_app_memory_turn_unique
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_model_call_cache_cost"
down_revision: str | Sequence[str] | None = "0008_app_memory_turn_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_call_log",
        sa.Column(
            "cached_input_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "model_call_log",
        sa.Column(
            "cost_cny",
            sa.Numeric(precision=18, scale=8),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("model_call_log", "cost_cny")
    op.drop_column("model_call_log", "cached_input_tokens")
