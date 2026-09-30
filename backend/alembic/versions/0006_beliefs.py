"""0006: 信念账本独立落库（书级 Belief 实体 · v1.5）

sim 运行时 beliefs 每回合同步 upsert 到此表，成为跨场景累积的书级认知账本；
作者可在人物页查看 / 手改（edited=True）/ 删除。

Revision ID: 0006_beliefs
Revises: 0005_character_book_scope
Create Date: 2026-08-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_beliefs"
down_revision: Union[str, Sequence[str], None] = "0005_character_book_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "beliefs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("book_id", sa.String(length=64), nullable=False),
        sa.Column("char_id", sa.String(length=64), nullable=False),
        sa.Column("fact_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("source_event_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("channel", sa.String(length=20), nullable=False, server_default="perceived"),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("edited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_beliefs_book_id", "beliefs", ["book_id"])
    op.create_index("ix_beliefs_char_id", "beliefs", ["char_id"])


def downgrade() -> None:
    op.drop_index("ix_beliefs_char_id", table_name="beliefs")
    op.drop_index("ix_beliefs_book_id", table_name="beliefs")
    op.drop_table("beliefs")