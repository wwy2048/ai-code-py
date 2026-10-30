"""记录单调部署版本，供异步封面条件更新。

Revision ID: 0006_deploy_revision
Revises: 0005_model_call_kind
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_deploy_revision"
down_revision: str | Sequence[str] | None = "0005_model_call_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app",
        sa.Column("deploy_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("app", "deploy_revision")
