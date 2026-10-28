"""add is_active to users

Revision ID: a1c3f5b7d9e2
Revises: eb36231ade5b
Create Date: 2026-10-28 23:50:00.000000

给 users 表加 is_active：JWT 认证需要"禁用账号"这个状态，
否则封号只能靠删行（会连带丢会话和审计），或者根本不生效。

注意 server_default 不是可选项：表里已有数据时新增 NOT NULL 列，
数据库必须给存量行一个值，不给就会 upgrade 失败（SQLite 会报
"Cannot add a NOT NULL column with default value NULL"）。
生产环境如果 users 已经有几十万行，还要考虑加列时的表锁 ——
PostgreSQL 11+ 加带默认值的列是元数据操作，不会重写整表，可以放心。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c3f5b7d9e2'
down_revision: Union[str, None] = 'eb36231ade5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite 不支持 ALTER COLUMN，batch 模式会用"建新表 + 拷数据 + 改名"实现，
    # 所以这里必须走 batch_alter_table（alembic/env.py 里已开 render_as_batch）
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'is_active',
                sa.Boolean(),
                nullable=False,
                # 存量用户默认全部启用；用 sa.true() 而不是字符串 '1'，
                # 让 SQLite / PostgreSQL 各自渲染成合法的布尔字面量
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('is_active')
