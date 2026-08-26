"""0003: inspiration_cards 灵感卡账本（主笔共创工作台，Book 级）

- 主笔 LLM 生成（source=chief）或作者自建（source=author）
- adopted 采纳状态持久化，供主笔规划引用

Revision ID: 0003_inspiration_cards
Revises: 0002_agent_columns
Create Date: 2026-08-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_inspiration_cards"
down_revision: Union[str, Sequence[str], None] = "0002_agent_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inspiration_cards",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("book_id", sa.String(64), sa.ForeignKey("books.id"), nullable=False, index=True),
        sa.Column("icon", sa.String(16), nullable=False, server_default="✦"),
        sa.Column("title", sa.String(64), nullable=False),
        sa.Column("desc", sa.Text(), nullable=False, server_default=""),
        sa.Column("type", sa.String(20), nullable=False, server_default="plot"),
        sa.Column("source", sa.String(16), nullable=False, server_default="chief"),
        sa.Column("adopted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("inspiration_cards")