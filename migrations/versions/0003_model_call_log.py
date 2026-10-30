"""新增模型调用元数据表。

Revision ID: 0003_model_call_log
Revises: 0002_agent_session_fields
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003_model_call_log'
down_revision: str | Sequence[str] | None = '0002_agent_session_fields'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'model_call_log',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('appId', sa.BigInteger(), nullable=False),
        sa.Column('turn_id', sa.String(length=64), nullable=False),
        sa.Column('model_name', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('input_tokens', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('output_tokens', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('error_type', sa.String(length=128), nullable=True),
        sa.Column(
            'createTime',
            sa.DateTime(),
            server_default=sa.text('CURRENT_TIMESTAMP'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_model_call_app_turn', 'model_call_log', ['appId', 'turn_id'])


def downgrade() -> None:
    op.drop_index('idx_model_call_app_turn', table_name='model_call_log')
    op.drop_table('model_call_log')
