"""0007: 书级记忆上下文落库（BookMemory 实体 · 主笔 Agent 记忆域）

主笔从「一次性函数」升级为「有记忆的创作 Agent」的地基：
按 book_id 隔离每本书的记忆（direction/setting/constraint/history/preference），
对话/规划前装配感知上下文，多书不再串味。

Revision ID: 0007_book_memories
Revises: 0006_beliefs
Create Date: 2026-09-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_book_memories"
down_revision: Union[str, Sequence[str], None] = "0006_beliefs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "book_memories",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("book_id", sa.String(length=64), nullable=False),
        sa.Column("topic", sa.String(length=20), nullable=False, server_default="direction"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="chief"),
        sa.Column("ts", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_book_memories_book_id", "book_memories", ["book_id"])


def downgrade() -> None:
    op.drop_index("ix_book_memories_book_id", table_name="book_memories")
    op.drop_table("book_memories")