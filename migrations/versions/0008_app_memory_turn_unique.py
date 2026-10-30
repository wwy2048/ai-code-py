"""保证同一 Turn 只生成一条记忆。

Revision ID: 0008_app_memory_turn_unique
Revises: 0007_app_memory
Create Date: 2026-10-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_app_memory_turn_unique"
down_revision: str | Sequence[str] | None = "0007_app_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_memory") as batch_op:
        batch_op.create_unique_constraint(
            "uq_app_memory_turn",
            ["appId", "source_turn_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("app_memory") as batch_op:
        batch_op.drop_constraint("uq_app_memory_turn", type_="unique")
