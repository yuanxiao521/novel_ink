"""0011: chat_histories（阶段⑧ 对话持久化 · 按书隔离）

主笔共创对话历史按 book_id 隔离存储，前端切换书时加载该书历史。
每条消息记录 role/user/assistant、content、时间戳。

Revision ID: 0011_chat_histories
Revises: 0010_prose_payload
Create Date: 2026-09-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_chat_histories"
down_revision: Union[str, Sequence[str], None] = "0010_prose_payload"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_histories",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("book_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("ts", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_histories_book_id", "chat_histories", ["book_id"])
    op.create_index("ix_chat_histories_book_ts", "chat_histories", ["book_id", "ts"])


def downgrade() -> None:
    op.drop_index("ix_chat_histories_book_ts")
    op.drop_index("ix_chat_histories_book_id")
    op.drop_table("chat_histories")
