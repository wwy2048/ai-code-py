"""为模型调用记录冗余用户 ID，支持按用户统计消费。

Revision ID: 0010_model_call_user_id
Revises: 0009_model_call_cache_cost
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_model_call_user_id"
down_revision: str | Sequence[str] | None = "0009_model_call_cache_cost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_call_log",
        sa.Column("userId", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    app_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("app")
    }
    if "userId" in app_columns:
        op.execute(
            sa.text(
                "UPDATE model_call_log "
                "SET `userId` = COALESCE(("
                "SELECT `userId` FROM app WHERE app.id = model_call_log.`appId`"
                "), 0)"
            )
        )
    op.create_index(
        "idx_model_call_user_time",
        "model_call_log",
        ["userId", "createTime"],
    )


def downgrade() -> None:
    op.drop_index("idx_model_call_user_time", table_name="model_call_log")
    op.drop_column("model_call_log", "userId")
