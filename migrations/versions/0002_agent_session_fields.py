"""扩展 chat_history 以承载 Agent 运行时消息。

Revision ID: 0002_agent_session_fields
Revises: 0001_baseline
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002_agent_session_fields'
down_revision: str | Sequence[str] | None = '0001_baseline'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('chat_history', sa.Column('turn_id', sa.String(length=64), nullable=True))
    op.add_column('chat_history', sa.Column('sequence', sa.Integer(), nullable=True))
    op.add_column(
        'chat_history',
        sa.Column(
            'message_kind',
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'legacy'"),
        ),
    )
    op.add_column('chat_history', sa.Column('payload', sa.JSON(), nullable=True))
    op.add_column(
        'chat_history',
        sa.Column('visible', sa.Boolean(), nullable=False, server_default=sa.text('1')),
    )
    op.add_column(
        'chat_history',
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
    )
    op.add_column(
        'chat_history', sa.Column('tool_call_id', sa.String(length=128), nullable=True)
    )
    op.add_column('chat_history', sa.Column('tool_name', sa.String(length=64), nullable=True))
    op.add_column(
        'chat_history',
        sa.Column('is_error', sa.Boolean(), nullable=False, server_default=sa.text('0')),
    )
    op.add_column('chat_history', sa.Column('compacted_by', sa.BigInteger(), nullable=True))
    op.execute('UPDATE chat_history SET visible = 1, active = 1 WHERE visible IS NULL')

    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('chat_history')}
    if {'appId', 'active', 'sequence'} <= columns:
        op.create_index(
            'idx_chat_app_active_seq',
            'chat_history',
            ['appId', 'active', 'sequence'],
        )
    if {'appId', 'visible', 'createTime', 'id'} <= columns:
        op.create_index(
            'idx_chat_app_visible_time',
            'chat_history',
            ['appId', 'visible', 'createTime', 'id'],
        )


def downgrade() -> None:
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('chat_history')}
    if {'appId', 'active', 'sequence'} <= columns:
        op.drop_index('idx_chat_app_active_seq', table_name='chat_history')
    if {'appId', 'visible', 'createTime', 'id'} <= columns:
        op.drop_index('idx_chat_app_visible_time', table_name='chat_history')
    op.drop_column('chat_history', 'compacted_by')
    op.drop_column('chat_history', 'is_error')
    op.drop_column('chat_history', 'tool_name')
    op.drop_column('chat_history', 'tool_call_id')
    op.drop_column('chat_history', 'active')
    op.drop_column('chat_history', 'visible')
    op.drop_column('chat_history', 'payload')
    op.drop_column('chat_history', 'message_kind')
    op.drop_column('chat_history', 'sequence')
    op.drop_column('chat_history', 'turn_id')
