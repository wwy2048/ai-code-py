"""建立现有 ai-code 三表基线，不破坏已有数据。

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0001_baseline'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REQUIRED_TABLES = {'user', 'app', 'chat_history'}


def _timestamp_defaults() -> tuple[sa.TextClause, sa.TextClause]:
    bind = op.get_bind()
    current = sa.text('CURRENT_TIMESTAMP')
    if bind.dialect.name == 'mysql':
        return current, sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')
    return current, current


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    present_required = existing_tables & _REQUIRED_TABLES
    if present_required == _REQUIRED_TABLES:
        return
    if present_required:
        missing = ', '.join(sorted(_REQUIRED_TABLES - present_required))
        raise RuntimeError(f'数据库 schema 不完整，缺少表: {missing}')

    timestamp_default, update_default = _timestamp_defaults()

    op.create_table(
        'user',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('userAccount', sa.String(length=256), nullable=False),
        sa.Column('userPassword', sa.String(length=512), nullable=False),
        sa.Column('userName', sa.String(length=256), nullable=True),
        sa.Column('userAvatar', sa.String(length=1024), nullable=True),
        sa.Column('userProfile', sa.String(length=512), nullable=True),
        sa.Column(
            'userRole', sa.String(length=256), server_default=sa.text("'user'"), nullable=False
        ),
        sa.Column('editTime', sa.DateTime(), server_default=timestamp_default, nullable=False),
        sa.Column('createTime', sa.DateTime(), server_default=timestamp_default, nullable=False),
        sa.Column('updateTime', sa.DateTime(), server_default=update_default, nullable=False),
        sa.Column('isDelete', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('userAccount', name='uk_userAccount'),
    )
    op.create_index('idx_userName', 'user', ['userName'])

    op.create_table(
        'app',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('appName', sa.String(length=256), nullable=True),
        sa.Column('cover', sa.String(length=512), nullable=True),
        sa.Column('initPrompt', sa.Text(), nullable=True),
        sa.Column('codeGenType', sa.String(length=64), nullable=True),
        sa.Column('deployKey', sa.String(length=64), nullable=True),
        sa.Column('deployedTime', sa.DateTime(), nullable=True),
        sa.Column('priority', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('userId', sa.BigInteger(), nullable=False),
        sa.Column('editTime', sa.DateTime(), server_default=timestamp_default, nullable=False),
        sa.Column('createTime', sa.DateTime(), server_default=timestamp_default, nullable=False),
        sa.Column('updateTime', sa.DateTime(), server_default=update_default, nullable=False),
        sa.Column('isDelete', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('deployKey', name='uk_deployKey'),
    )
    op.create_index('idx_appName', 'app', ['appName'])
    op.create_index('idx_userId', 'app', ['userId'])

    op.create_table(
        'chat_history',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('messageType', sa.String(length=32), nullable=False),
        sa.Column('appId', sa.BigInteger(), nullable=False),
        sa.Column('userId', sa.BigInteger(), nullable=False),
        sa.Column('createTime', sa.DateTime(), server_default=timestamp_default, nullable=False),
        sa.Column('updateTime', sa.DateTime(), server_default=timestamp_default, nullable=False),
        sa.Column('isDelete', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_appId', 'chat_history', ['appId'])
    op.create_index('idx_appId_createTime', 'chat_history', ['appId', 'createTime'])
    op.create_index('idx_createTime', 'chat_history', ['createTime'])


def downgrade() -> None:
    # 基线用于接管既有数据库；降级不允许删除业务表。
    pass
