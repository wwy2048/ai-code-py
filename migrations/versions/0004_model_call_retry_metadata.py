"""补充模型调用重试与错误摘要字段。

Revision ID: 0004_model_call_retry_metadata
Revises: 0003_model_call_log
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004_model_call_retry_metadata'
down_revision: str | Sequence[str] | None = '0003_model_call_log'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'model_call_log',
        sa.Column('error_summary', sa.String(length=256), nullable=True),
    )
    op.add_column(
        'model_call_log',
        sa.Column('retry_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('model_call_log', 'retry_count')
    op.drop_column('model_call_log', 'error_summary')
